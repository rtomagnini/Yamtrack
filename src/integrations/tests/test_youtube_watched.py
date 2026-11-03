"""Watched deliveries must survive outages and never duplicate Plex callbacks."""

# Descriptive test names document each scenario; credentials are test fixtures.
# ruff: noqa: D102, S106

from datetime import timedelta
from unittest.mock import Mock, patch

import requests
from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from app.models import TV, Episode, Item, Season
from integrations.models import YouTubeWatchSync
from integrations.plex_library import PlexError, PlexLibraryClient
from integrations.tubearchivist import TubeArchivistClient, TubeArchivistError
from integrations.webhooks.plex import PlexWebhookProcessor
from integrations.youtube_watched import (
    consume_plex_echo,
    due_watches,
    enqueue_watch,
    process_watched,
    queue_watched,
)
from users.models import User

VIDEO_ID = "dQw4w9WgXcQ"


def response(payload=None, status=200):
    """Create an API response without contacting an external server."""
    result = Mock(status_code=status)
    result.json.return_value = payload
    return result


def plex_video(key="42", watched=0, filename=None):
    """Represent a Plex leaf item with its canonical TubeArchivist filename."""
    return {
        "ratingKey": key,
        "viewCount": watched,
        "Media": [
            {
                "Part": [
                    {"file": filename or f"/archive/UCChannel/{VIDEO_ID}.mp4"},
                ],
            },
        ],
    }


@override_settings(
    TUBEARCHIVIST_URL="http://ta:8000",
    TUBEARCHIVIST_TOKEN="test-token",
    PLEX_SERVER_URL="http://plex:32400",
    PLEX_TOKEN="plex-test-token",
    PLEX_YOUTUBE_LIBRARY="Youtube",
    YOUTUBE_WATCH_SYNC_ENABLED=True,
    INVIDIOUS_URL="",
    INVIDIOUS_TOKEN="",
    TRACK_TIME=False,
)
class YouTubeWatchedTests(TestCase):
    """Cover durable delivery, idempotence, callback echoes and local tracking."""

    def setUp(self):
        self.user = User.objects.create(username="watched-user")
        self.client.force_login(self.user)
        patch("app.models.Item.fetch_releases").start()
        self.http = patch("requests.request").start()
        self.addCleanup(patch.stopall)
        channel_item = Item.objects.create(
            source="youtube",
            media_type="youtube",
            media_id="55",
            title="Channel",
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
            user=self.user,
            item=season_item,
            related_tv=channel,
            status="In progress",
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

    def job(self, target="tubearchivist", **kwargs):
        return YouTubeWatchSync.objects.create(
            requested_by=self.user,
            youtube_id=VIDEO_ID,
            title="Video",
            target=target,
            plex_library="Youtube" if target == "plex" else "",
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

    def due(self, job):
        YouTubeWatchSync.objects.filter(pk=job.pk).update(
            next_attempt_at=timezone.now() - timedelta(seconds=1),
        )

    def test_local_mark_and_both_jobs_are_saved_before_delivery(self):
        with (
            patch("integrations.youtube_watched.enqueue_watch") as enqueue,
            self.captureOnCommitCallbacks(execute=True),
        ):
            result = self.mark()
            self.assertTrue(Episode.objects.filter(item=self.item).exists())
            self.assertEqual(YouTubeWatchSync.objects.count(), 2)
            enqueue.assert_not_called()
        self.assertEqual(result.status_code, 302)
        self.assertEqual(enqueue.call_count, 2)
        self.http.assert_not_called()

    def test_queue_deduplicates_jobs_and_rollback_discards_delivery(self):
        with (
            patch("integrations.youtube_watched.enqueue_watch") as enqueue,
            self.captureOnCommitCallbacks(execute=True),
        ):
            try:
                with transaction.atomic():
                    queue_watched(self.item, self.user)
                    raise RuntimeError("rollback")  # noqa: EM101, TRY301
            except RuntimeError:
                pass
        self.assertFalse(YouTubeWatchSync.objects.exists())
        enqueue.assert_not_called()
        queue_watched(self.item, self.user)
        queue_watched(self.item, self.user)
        self.assertEqual(YouTubeWatchSync.objects.count(), 2)

    @override_settings(YOUTUBE_WATCH_SYNC_ENABLED=False)
    def test_disabled_sync_still_marks_locally(self):
        self.assertEqual(self.mark().status_code, 302)
        self.assertTrue(Episode.objects.filter(item=self.item).exists())
        self.assertFalse(YouTubeWatchSync.objects.exists())
        self.http.assert_not_called()

    def test_missing_external_id_still_marks_locally(self):
        self.item.youtube_video_id = None
        self.item.save()
        self.assertEqual(self.mark().status_code, 302)
        self.assertTrue(Episode.objects.filter(item=self.item).exists())
        self.assertFalse(YouTubeWatchSync.objects.exists())

    @override_settings(TUBEARCHIVIST_URL="", TUBEARCHIVIST_TOKEN="")
    def test_plex_can_be_enabled_independently(self):
        queue_watched(self.item, self.user)
        self.assertEqual(
            list(YouTubeWatchSync.objects.values_list("target", flat=True)),
            ["plex"],
        )

    def test_ta_watched_is_set_and_verified_without_deletion(self):
        job = self.job()
        self.http.side_effect = [
            response({"player": {"watched": False}}),
            response(),
            response({"player": {"watched": True}}),
        ]
        process_watched(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, "completed")
        self.assertEqual(
            self.http.call_args_list[1].args,
            ("POST", "http://ta:8000/api/watched/"),
        )
        self.assertEqual(
            self.http.call_args_list[1].kwargs["json"],
            {"id": VIDEO_ID, "is_watched": True},
        )
        self.assertTrue(
            all(call.args[0] != "DELETE" for call in self.http.call_args_list),
        )

    def test_ta_already_watched_does_not_resend(self):
        self.http.return_value = response({"data": {"player": {"watched": True}}})
        TubeArchivistClient().mark_watched(VIDEO_ID)
        self.assertEqual(self.http.call_count, 1)

    def test_ta_missing_video_requires_manual_retry(self):
        job = self.job()
        self.http.return_value = response(status=404)
        process_watched(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertIn("not found", job.last_error)

    def test_ta_does_not_report_completion_without_confirmation(self):
        self.http.side_effect = [
            response({"player": {"watched": False}}),
            response(),
            response({"player": {"watched": False}}),
        ]
        job = self.job()
        process_watched(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, "pending")
        self.assertEqual(job.failures, 1)

    def test_ta_invalid_watched_response_is_not_reported_as_complete(self):
        job = self.job()
        self.http.return_value = response({"player": {"watched": "false"}})
        process_watched(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, "pending")
        self.assertEqual(self.http.call_count, 1)

    def test_new_local_viewing_resynchronizes_completed_destinations(self):
        job = self.job("plex", status="completed", plex_rating_key="42")
        queue_watched(self.item, self.user)
        job.refresh_from_db()
        self.assertEqual(job.status, "pending")
        self.assertEqual(job.plex_rating_key, "")
        self.assertEqual(YouTubeWatchSync.objects.count(), 2)

    def test_deletion_record_prevents_outgoing_watch_requests(self):
        from integrations.models import TubeArchivistDeletion  # noqa: PLC0415

        TubeArchivistDeletion.objects.create(youtube_id=VIDEO_ID, title="Deleted")
        job = self.job()
        process_watched(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.http.assert_not_called()

    def test_plex_pagination_and_exact_filename_matching(self):
        self.http.side_effect = [
            response(
                {
                    "MediaContainer": {
                        "Directory": [{"key": "3", "title": "Youtube", "type": "show"}],
                    },
                },
            ),
            response(
                {
                    "MediaContainer": {
                        "totalSize": 2,
                        "Metadata": [
                            plex_video("9", filename=f"/files/extra{VIDEO_ID}.mp4"),
                        ],
                    },
                },
            ),
            response({"MediaContainer": {"totalSize": 2, "Metadata": [plex_video()]}}),
        ]
        self.assertEqual(PlexLibraryClient().resolve_video("Youtube", VIDEO_ID), "42")
        self.assertEqual(
            self.http.call_args.kwargs["params"]["X-Plex-Container-Start"],
            1,
        )
        self.assertEqual(self.http.call_args.kwargs["params"]["type"], 4)

    def test_plex_ambiguous_matches_are_never_marked(self):
        self.http.side_effect = [
            response(
                {"MediaContainer": {"Directory": [{"key": "3", "title": "Youtube"}]}},
            ),
            response(
                {
                    "MediaContainer": {
                        "totalSize": 2,
                        "Metadata": [plex_video("42"), plex_video("43")],
                    },
                },
            ),
        ]
        with self.assertRaises(PlexError):
            PlexLibraryClient().resolve_video("Youtube", VIDEO_ID)
        self.assertFalse(
            any("scrobble" in call.args[1] for call in self.http.call_args_list),
        )

    def test_plex_title_is_not_used_as_an_identifier(self):
        self.assertFalse(PlexLibraryClient.matches_video({"title": VIDEO_ID}, VIDEO_ID))

    def test_plex_marks_and_verifies_watched_state(self):
        job = self.job("plex")
        self.http.side_effect = [
            response(
                {
                    "MediaContainer": {
                        "Directory": [{"key": "3", "title": "Youtube", "type": "show"}],
                    },
                },
            ),
            response({"MediaContainer": {"totalSize": 1, "Metadata": [plex_video()]}}),
            response({"MediaContainer": {"Metadata": [plex_video()]}}),
            response(),
            response({"MediaContainer": {"Metadata": [plex_video(watched=1)]}}),
        ]
        process_watched(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, "completed")
        self.assertEqual(job.plex_rating_key, "42")
        scrobble = self.http.call_args_list[3]
        self.assertEqual(scrobble.args, ("GET", "http://plex:32400/:/scrobble"))
        self.assertEqual(scrobble.kwargs["params"]["key"], "42")
        self.assertEqual(scrobble.kwargs["headers"]["X-Plex-Token"], "plex-test-token")

    def test_retry_after_plex_lost_response_does_not_increment_view_count(self):
        job = self.job("plex", plex_rating_key="42")
        self.http.side_effect = [
            response({"MediaContainer": {"Metadata": [plex_video()]}}),
            requests.ConnectionError("secret"),
        ]
        process_watched(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, "pending")
        self.due(job)
        self.http.reset_mock()
        self.http.side_effect = [
            response({"MediaContainer": {"Metadata": [plex_video(watched=1)]}}),
        ]
        process_watched(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, "completed")
        self.assertEqual(self.http.call_count, 1)

    def test_destinations_fail_independently(self):
        ta = self.job()
        plex = self.job("plex", plex_rating_key="42")
        with (
            patch.object(
                TubeArchivistClient,
                "mark_watched",
                side_effect=TubeArchivistError("TA unavailable"),
            ),
            patch.object(
                PlexLibraryClient,
                "video",
                return_value=plex_video(watched=1),
            ),
        ):
            process_watched(ta.pk)
            process_watched(plex.pk)
        ta.refresh_from_db()
        plex.refresh_from_db()
        self.assertEqual(ta.status, "pending")
        self.assertEqual(plex.status, "completed")

    def test_worker_lease_and_completed_job_prevent_duplicate_calls(self):
        job = self.job(locked_until=timezone.now() + timedelta(minutes=5))
        process_watched(job.pk)
        self.http.assert_not_called()
        self.assertNotIn(job.pk, due_watches())
        YouTubeWatchSync.objects.filter(pk=job.pk).update(
            locked_until=timezone.now() - timedelta(seconds=1),
        )
        self.assertIn(job.pk, due_watches())
        self.http.return_value = response({"data": {"player": {"watched": True}}})
        process_watched(job.pk)
        self.http.reset_mock()
        process_watched(job.pk)
        self.http.assert_not_called()

    def test_broker_outage_keeps_durable_job_for_beat(self):
        job = self.job()
        with patch(
            "integrations.tasks.sync_youtube_watched.apply_async",
            side_effect=RuntimeError("secret"),
        ):
            enqueue_watch(job.pk)
        self.assertIn(job.pk, due_watches())

    def test_auth_failure_is_redacted_and_exhausted_retries_stop(self):
        job = self.job()
        self.http.return_value = response(status=401)
        process_watched(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertNotIn("test-token", job.last_error)
        job.status = "pending"
        job.failures = 7
        job.save()
        self.http.return_value = response(status=503)
        process_watched(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")

    def test_outbound_plex_echo_is_consumed_once_without_duplicate_history(self):
        Episode.objects.bulk_create(
            [
                Episode(
                    item=self.item,
                    related_season=self.season,
                    end_date=timezone.now(),
                ),
            ],
        )
        job = self.job(
            "plex",
            plex_rating_key="42",
            plex_echo_until=timezone.now() + timedelta(minutes=5),
        )
        payload = {
            "event": "media.scrobble",
            "Metadata": {
                "ratingKey": "42",
                "Media": [{"Part": [{"file": f"/files/{VIDEO_ID}.mp4"}]}],
            },
        }
        self.assertTrue(
            PlexWebhookProcessor()._attempt_handle_youtube(payload, self.user),
        )
        self.assertEqual(Episode.objects.filter(item=self.item).count(), 1)
        job.refresh_from_db()
        self.assertIsNone(job.plex_echo_until)
        # An actual later playback still records a new viewing.
        self.assertTrue(
            PlexWebhookProcessor()._attempt_handle_youtube(payload, self.user),
        )
        self.assertEqual(Episode.objects.filter(item=self.item).count(), 2)
        self.assertEqual(YouTubeWatchSync.objects.count(), 1)

    def test_real_playback_is_not_suppressed_by_an_unused_echo_guard(self):
        Episode.objects.bulk_create(
            [
                Episode(
                    item=self.item, related_season=self.season, end_date=timezone.now(),
                ),
            ],
        )
        self.job(
            "plex",
            plex_rating_key="42",
            plex_echo_until=timezone.now() + timedelta(minutes=5),
        )
        payload = {
            "event": "media.scrobble",
            "Player": {"uuid": "actual-player"},
            "Metadata": {
                "ratingKey": "42",
                "Media": [{"Part": [{"file": f"/files/{VIDEO_ID}.mp4"}]}],
            },
        }
        self.assertTrue(
            PlexWebhookProcessor()._attempt_handle_youtube(payload, self.user),
        )
        self.assertEqual(Episode.objects.filter(item=self.item).count(), 2)

    def test_worker_does_not_restore_echo_guard_consumed_during_request(self):
        job = self.job("plex", plex_rating_key="42")
        with (
            patch.object(
                PlexLibraryClient,
                "video",
                side_effect=[plex_video(), plex_video(watched=1)],
            ),
            patch.object(
                PlexLibraryClient,
                "mark_watched",
                side_effect=lambda key: consume_plex_echo(VIDEO_ID, self.user, key),
            ),
        ):
            process_watched(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, "completed")
        self.assertIsNone(job.plex_echo_until)

    def test_status_and_retry_are_user_scoped(self):
        job = self.job(status="failed", last_error="Failure")
        other = User.objects.create(username="other-watched-user")
        hidden = YouTubeWatchSync.objects.create(
            requested_by=other,
            youtube_id="abcdefghijk",
            target="plex",
            title="Private",
            status="failed",
        )
        result = self.client.get(reverse("youtube_watched_status"))
        self.assertEqual(result.status_code, 200)
        self.assertContains(result, "Video")
        self.assertNotContains(result, "Private")
        with patch("integrations.youtube_watched_views.enqueue_watch") as enqueue:
            self.assertEqual(
                self.client.post(
                    reverse("youtube_watched_retry", args=[job.pk]),
                ).status_code,
                302,
            )
            enqueue.assert_called_once_with(job.pk)
        self.assertEqual(
            self.client.post(
                reverse("youtube_watched_retry", args=[hidden.pk]),
            ).status_code,
            404,
        )
        self.client.logout()
        self.assertEqual(
            self.client.get(reverse("youtube_watched_status")).status_code,
            302,
        )
