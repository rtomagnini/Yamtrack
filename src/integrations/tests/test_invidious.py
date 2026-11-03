"""New watched and delete actions must reliably update Invidious history."""

# Test names describe each scenario; token signatures are fake test fixtures.
# ruff: noqa: D102, S106

import json
from datetime import timedelta
from unittest.mock import Mock, patch
from urllib.parse import quote, unquote

import requests
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from app.models import TV, Episode, Item, Season
from integrations.invidious import InvidiousClient, InvidiousError
from integrations.models import TubeArchivistDeletion, YouTubeWatchSync
from integrations.youtube_watched import due_watches, process_watched, queue_watched
from users.models import User

VIDEO_ID = "dQw4w9WgXcQ"
TOKEN = {
    "session": "v1:test-session",
    "scopes": ["POST:history/*"],
    "signature": "test-signature",
}


@override_settings(
    INVIDIOUS_URL="http://invidious:3000",
    INVIDIOUS_TOKEN=json.dumps(TOKEN),
    TUBEARCHIVIST_URL="",
    TUBEARCHIVIST_TOKEN="",
    PLEX_YOUTUBE_LIBRARY="",
    YOUTUBE_WATCH_SYNC_ENABLED=True,
    TRACK_TIME=False,
)
class InvidiousTests(TestCase):
    """Exercise only mocked HTTP; never write to the user's real history."""

    def setUp(self):
        self.user = User.objects.create(username="invidious-user")
        self.client.force_login(self.user)
        patch("app.models.Item.fetch_releases").start()
        self.http = patch(
            "requests.request", return_value=Mock(status_code=204),
        ).start()
        self.addCleanup(patch.stopall)
        channel_item = Item.objects.create(
            source="youtube", media_type="youtube", media_id="55", title="Channel",
        )
        channel = TV(user=self.user, item=channel_item, status="In progress")
        TV.objects.bulk_create([channel])
        season_item = Item.objects.create(
            source="youtube",
            media_type="season",
            media_id="55",
            season_number=2026,
            title="2026",
        )
        self.season = Season(
            user=self.user, item=season_item, related_tv=channel, status="In progress",
        )
        Season.objects.bulk_create([self.season])
        self.item = Item.objects.create(
            source="youtube",
            media_type="episode",
            media_id="55",
            season_number=2026,
            episode_number=1,
            title="Video",
            youtube_video_id=VIDEO_ID,
        )

    def job(self, **kwargs):
        return YouTubeWatchSync.objects.create(
            requested_by=self.user,
            youtube_id=VIDEO_ID,
            title="Video",
            target="invidious",
            **kwargs,
        )

    def mark(self):
        return self.client.post(
            reverse("episode_save"),
            {
                "media_id": "55",
                "source": "youtube",
                "season_number": "2026",
                "episode_number": "1",
                "end_date": timezone.now().date().isoformat(),
            },
        )

    def delete(self):
        return self.client.delete(reverse("delete_youtube_video", args=[self.item.pk]))

    def test_post_history_uses_bearer_header_and_exact_204_confirmation(self):
        InvidiousClient().mark_watched(VIDEO_ID)
        args, kwargs = self.http.call_args
        self.assertEqual(
            args, ("POST", f"http://invidious:3000/api/v1/auth/history/{VIDEO_ID}"),
        )
        bearer = kwargs["headers"]["Authorization"]
        self.assertTrue(bearer.startswith("Bearer "))
        self.assertEqual(json.loads(unquote(bearer.removeprefix("Bearer "))), TOKEN)
        self.assertNotIn("test-signature", args[1])
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["timeout"], (5, 15))
        self.http.return_value.json.assert_not_called()

    def test_encoded_and_multiline_json_tokens_are_supported(self):
        for token in (quote(json.dumps(TOKEN), safe=""), json.dumps(TOKEN, indent=2)):
            with (
                self.subTest(
                    token_format="encoded" if token.startswith("%") else "multiline",
                ),
                override_settings(INVIDIOUS_TOKEN=token),
            ):
                client = InvidiousClient()
                self.assertNotIn("\n", client.token)
                self.assertEqual(json.loads(unquote(client.token)), TOKEN)

    def test_invalid_tokens_and_partial_configuration_do_not_contact_server(self):
        for token in (
            "v1:session-only",
            "not-json",
            "[]",
            "{}",
            '{"signature":"test-signature"}',
        ):
            with (
                self.subTest(token_format="invalid"),
                override_settings(INVIDIOUS_TOKEN=token),
            ):
                with self.assertRaises(InvidiousError) as caught:
                    InvidiousClient()
                self.assertTrue(caught.exception.permanent)
                self.assertNotIn("test-signature", str(caught.exception))
        with override_settings(INVIDIOUS_TOKEN=""), self.assertRaises(InvidiousError):
            InvidiousClient()
        self.http.assert_not_called()

    def test_invalid_urls_do_not_send_credentials(self):
        for url in (
            "ftp://invidious",
            "http://user:secret@invidious",
            "http://invidious/?token=secret",
            "http://invidious/#secret",
            "not-a-url",
        ):
            with (
                self.subTest(url_format="invalid"),
                override_settings(INVIDIOUS_URL=url),
            ):
                with self.assertRaises(InvidiousError) as caught:
                    InvidiousClient()
                self.assertTrue(caught.exception.permanent)
                self.assertNotIn("secret", str(caught.exception))
        self.http.assert_not_called()

    def test_invalid_youtube_ids_are_rejected_before_request(self):
        for video_id in (None, "55", "../somewhere", "abcdefghijk?token=secret"):
            with (
                self.subTest(video_id_format="invalid"),
                self.assertRaises(InvidiousError),
            ):
                InvidiousClient().mark_watched(video_id)
        self.http.assert_not_called()

    def test_http_failures_are_classified_and_redacted(self):
        for status, permanent in (
            (401, True),
            (403, True),
            (404, True),
            (409, True),
            (302, True),
            (200, True),
            (408, False),
            (429, False),
            (500, False),
            (503, False),
        ):
            with self.subTest(status=status):
                self.http.return_value = Mock(
                    status_code=status, text="secret response body",
                )
                with self.assertRaises(InvidiousError) as caught:
                    InvidiousClient().mark_watched(VIDEO_ID)
                self.assertEqual(caught.exception.permanent, permanent)
                self.assertNotIn("secret", str(caught.exception))
                self.http.return_value.json.assert_not_called()

    def test_history_disabled_has_actionable_error(self):
        self.http.return_value.status_code = 409
        job = self.job()
        process_watched(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertIn("enable watch history", job.last_error)

    def test_transient_error_retries_without_losing_job_or_credentials(self):
        job = self.job()
        self.http.side_effect = requests.ConnectionError(
            "test-signature private-server secret",
        )
        process_watched(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, "pending")
        self.assertEqual(job.failures, 1)
        self.assertNotIn("secret", job.last_error)
        self.assertNotIn("test-signature", job.last_error)
        self.http.side_effect = None
        YouTubeWatchSync.objects.filter(pk=job.pk).update(
            next_attempt_at=timezone.now() - timedelta(seconds=1),
        )
        self.assertIn(job.pk, due_watches())
        process_watched(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, "completed")

    def test_new_watched_action_enqueues_only_after_local_commit(self):
        with (
            patch("integrations.youtube_watched.enqueue_watch") as enqueue,
            self.captureOnCommitCallbacks(execute=True),
        ):
            result = self.mark()
            self.assertTrue(Episode.objects.filter(item=self.item).exists())
            enqueue.assert_not_called()
        self.assertEqual(result.status_code, 302)
        self.assertEqual(YouTubeWatchSync.objects.get().target, "invidious")
        enqueue.assert_called_once()
        self.http.assert_not_called()

    def test_new_deletion_keeps_job_after_local_item_and_history_are_deleted(self):
        Episode.objects.bulk_create(
            [
                Episode(
                    item=self.item, related_season=self.season, end_date=timezone.now(),
                ),
            ],
        )
        with (
            patch("integrations.youtube_watched.enqueue_watch") as enqueue,
            self.captureOnCommitCallbacks(execute=True),
        ):
            result = self.delete()
            enqueue.assert_not_called()
        self.assertEqual(result.status_code, 200)
        self.assertFalse(Item.objects.filter(pk=self.item.pk).exists())
        self.assertFalse(Episode.objects.exists())
        job = YouTubeWatchSync.objects.get()
        self.assertEqual((job.youtube_id, job.target), (VIDEO_ID, "invidious"))
        enqueue.assert_called_once_with(job.pk)
        process_watched(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, "completed")

    @override_settings(
        TUBEARCHIVIST_URL="http://ta:8000",
        TUBEARCHIVIST_TOKEN="ta-token",
        PLEX_YOUTUBE_LIBRARY="Youtube",
    )
    def test_deletion_marks_invidious_even_with_ta_deletion_record(self):
        with (
            patch("integrations.tubearchivist.enqueue_deletion"),
            patch("integrations.youtube_watched.enqueue_watch"),
            self.captureOnCommitCallbacks(execute=True),
        ):
            result = self.delete()
        self.assertEqual(result.status_code, 200)
        self.assertTrue(
            TubeArchivistDeletion.objects.filter(youtube_id=VIDEO_ID).exists(),
        )
        job = YouTubeWatchSync.objects.get()
        self.assertEqual(job.target, "invidious")
        with patch("integrations.youtube_watched._sync_plex") as plex:
            process_watched(job.pk)
            plex.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.status, "completed")

    def test_no_retroactive_synchronization(self):
        Episode.objects.bulk_create(
            [
                Episode(
                    item=self.item, related_season=self.season, end_date=timezone.now(),
                ),
            ],
        )
        TubeArchivistDeletion.objects.create(
            youtube_id="abcdefghijk", title="Old deleted video", requested_by=self.user,
        )
        self.assertEqual(
            self.client.get(reverse("youtube_watched_status")).status_code, 200,
        )
        self.assertEqual(list(due_watches()), [])
        self.assertFalse(YouTubeWatchSync.objects.exists())
        self.http.assert_not_called()

    def test_watched_then_deleted_keeps_one_independent_invidious_delivery(self):
        with (
            patch("integrations.youtube_watched.enqueue_watch"),
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.mark()
            self.delete()
        self.assertEqual(YouTubeWatchSync.objects.count(), 1)
        job = YouTubeWatchSync.objects.get()
        process_watched(job.pk)
        process_watched(job.pk)
        self.assertEqual(self.http.call_count, 1)

    @override_settings(INVIDIOUS_URL="", INVIDIOUS_TOKEN="")
    def test_unconfigured_instance_preserves_local_actions(self):
        self.assertEqual(self.mark().status_code, 302)
        self.assertEqual(self.delete().status_code, 200)
        self.assertFalse(YouTubeWatchSync.objects.exists())
        self.http.assert_not_called()

    @override_settings(YOUTUBE_WATCH_SYNC_ENABLED=False)
    def test_disabled_sync_does_not_export_deletion(self):
        self.assertEqual(self.delete().status_code, 200)
        self.assertFalse(YouTubeWatchSync.objects.exists())

    @override_settings(INVIDIOUS_TOKEN="")
    def test_missing_token_does_not_block_local_delete(self):
        with (
            patch("integrations.youtube_watched.enqueue_watch"),
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.assertEqual(self.delete().status_code, 200)
        job = YouTubeWatchSync.objects.get()
        process_watched(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.http.assert_not_called()

    @override_settings(
        TUBEARCHIVIST_URL="http://ta:8000",
        TUBEARCHIVIST_TOKEN="ta-token",
        PLEX_YOUTUBE_LIBRARY="Youtube",
    )
    def test_new_watched_action_queues_three_independent_destinations(self):
        queue_watched(self.item, self.user)
        self.assertEqual(
            set(YouTubeWatchSync.objects.values_list("target", flat=True)),
            {"invidious", "plex", "tubearchivist"},
        )
        job = YouTubeWatchSync.objects.get(target="invidious")
        self.http.return_value.status_code = 500
        process_watched(job.pk)
        self.assertEqual(YouTubeWatchSync.objects.filter(status="pending").count(), 3)

    def test_retry_and_status_include_only_current_users_jobs(self):
        job = self.job(status="failed", last_error="History disabled")
        result = self.client.get(reverse("youtube_watched_status"))
        self.assertContains(result, "Invidious")
        self.assertNotContains(result, "test-signature")
        with patch("integrations.youtube_watched_views.enqueue_watch") as enqueue:
            self.assertEqual(
                self.client.post(
                    reverse("youtube_watched_retry", args=[job.pk]),
                ).status_code,
                302,
            )
            enqueue.assert_called_once_with(job.pk)
        other = User.objects.create(username="other-invidious-user")
        self.client.force_login(other)
        self.assertEqual(
            self.client.post(
                reverse("youtube_watched_retry", args=[job.pk]),
            ).status_code,
            404,
        )
