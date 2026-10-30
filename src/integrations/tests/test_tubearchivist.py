"""Regression tests for external deletion, durability and reimport prevention."""

# Descriptive test method names document each case; all credentials are test fixtures.
# ruff: noqa: D102, S106

from datetime import timedelta
from unittest.mock import Mock, patch

import requests
from django.db import IntegrityError
from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from app.mixins import disable_fetch_releases
from app.models import Item, MediaTypes, Sources
from integrations.models import TubeArchivistDeletion
from integrations.tubearchivist import (
    TubeArchivistClient,
    TubeArchivistError,
    due_deletions,
    enqueue_deletion,
    process_deletion,
)
from integrations.webhooks.tautulli import TautulliWebhookProcessor
from users.models import User

VIDEO_ID = "dQw4w9WgXcQ"


def api_response(status, data=None):
    """Construct an HTTP response without accessing a live TA instance."""
    response = Mock(status_code=status)
    response.json.return_value = data
    return response


@override_settings(TUBEARCHIVIST_URL="http://ta:8000", TUBEARCHIVIST_TOKEN="test-token")
class TubeArchivistTest(TestCase):
    """Exercise deletion transactions and TA failures with all HTTP calls mocked."""

    def setUp(self):
        self.user = User.objects.create(username="ta-user")
        self.client.force_login(self.user)
        self.request = patch("integrations.tubearchivist.requests.request").start()
        self.addCleanup(patch.stopall)

    def job(self, **kwargs):
        return TubeArchivistDeletion.objects.create(
            youtube_id=VIDEO_ID,
            title="Video",
            requested_by=self.user,
            **kwargs,
        )

    def video(self, **kwargs):
        with disable_fetch_releases():
            return Item.objects.create(
                media_id="test-channel",
                title="Video",
                source=Sources.YOUTUBE.value,
                media_type=MediaTypes.EPISODE.value,
                season_number=2026,
                episode_number=1,
                youtube_video_id=kwargs.get("youtube_id", VIDEO_ID),
            )

    def make_due(self, job):
        TubeArchivistDeletion.objects.filter(pk=job.pk).update(
            next_attempt_at=timezone.now() - timedelta(seconds=1),
        )

    def test_delete_ignore_is_only_complete_after_verification(self):
        job = self.job()
        self.request.side_effect = [
            api_response(204),
            api_response(404),
            api_response(200),
        ]
        process_deletion(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, "pending")
        self.assertTrue(job.file_deleted)
        self.assertIsNotNone(job.ignore_requested_at)
        self.assertEqual(
            self.request.call_args.kwargs["json"], {"status": "ignore-force"},
        )
        self.make_due(job)
        self.request.side_effect = [
            api_response(404),
            api_response(200, {"status": "ignore"}),
            api_response(404),
        ]
        process_deletion(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, "completed")
        self.assertEqual(job.last_error, "")
        for call in self.request.call_args_list:
            self.assertNotIn("watched", call.args[1])
            self.assertEqual(
                call.kwargs["headers"], {"Authorization": "Token test-token"},
            )
            self.assertFalse(call.kwargs["allow_redirects"])

    def test_does_not_resubmit_ignore_while_ta_is_processing_it(self):
        job = self.job(ignore_requested_at=timezone.now())
        self.request.side_effect = [api_response(404), api_response(404)]
        process_deletion(job.pk)
        self.assertEqual(self.request.call_count, 2)
        job.refresh_from_db()
        self.assertEqual(job.status, "pending")
        self.assertEqual(job.failures, 0)

    def test_ignore_timeout_requires_manual_retry(self):
        job = self.job(ignore_requested_at=timezone.now() - timedelta(minutes=11))
        self.request.side_effect = [api_response(404), api_response(404)]
        process_deletion(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertIn("10 minutes", job.last_error)

    def test_transport_failure_is_durable_and_redacted(self):
        job = self.job()
        self.request.side_effect = requests.ConnectionError(
            "secret-token-server-details",
        )
        process_deletion(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, "pending")
        self.assertEqual(job.failures, 1)
        self.assertGreater(job.next_attempt_at, timezone.now())
        self.assertNotIn("secret", job.last_error)

    def test_invalid_token_fails_without_deleting_further(self):
        job = self.job()
        self.request.return_value = api_response(403)
        process_deletion(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertFalse(job.file_deleted)
        self.assertEqual(self.request.call_count, 1)

    def test_repeated_server_failures_stop(self):
        job = self.job(failures=7)
        self.request.return_value = api_response(500)
        process_deletion(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")

    def test_worker_lease_prevents_duplicate_processing(self):
        job = self.job(locked_until=timezone.now() + timedelta(minutes=1))
        process_deletion(job.pk)
        self.request.assert_not_called()

    def test_restarted_worker_can_reclaim_expired_lease(self):
        job = self.job(locked_until=timezone.now() - timedelta(minutes=1))
        self.request.side_effect = [
            api_response(404),
            api_response(200, {"status": "ignore"}),
            api_response(404),
        ]
        process_deletion(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, "completed")

    def test_cannot_complete_if_video_still_exists(self):
        job = self.job()
        self.request.side_effect = [
            api_response(204),
            api_response(200, {"status": "ignore"}),
            api_response(200, {}),
        ]
        process_deletion(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, "pending")
        self.assertEqual(job.failures, 1)

    def test_non_json_response_is_not_success(self):
        response = api_response(200)
        response.json.side_effect = ValueError("html")
        self.request.return_value = response
        with self.assertRaises(TubeArchivistError):
            TubeArchivistClient().request("GET", "download/test/")

    def test_delete_saves_job_before_sending_after_commit(self):
        video = self.video()
        with patch("integrations.tubearchivist.enqueue_deletion") as send:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.delete(
                    reverse("delete_youtube_video", args=[video.pk]),
                )
                self.assertEqual(response.status_code, 200)
                self.assertFalse(Item.objects.filter(pk=video.pk).exists())
                self.assertTrue(
                    TubeArchivistDeletion.objects.filter(youtube_id=VIDEO_ID).exists(),
                )
                send.assert_not_called()
            send.assert_called_once()
        self.request.assert_not_called()

    def test_broker_outage_keeps_persisted_job_for_sweeper(self):
        job = self.job()
        with patch(
            "integrations.tasks.sync_tubearchivist_deletion.apply_async",
            side_effect=OSError,
        ):
            enqueue_deletion(job.pk)
        self.assertIn(job.pk, due_deletions())

    def test_local_failure_rolls_back_job_and_does_not_send(self):
        video = self.video()
        with (
            patch.object(Item, "delete", side_effect=IntegrityError),
            patch("integrations.tubearchivist.enqueue_deletion") as send,
        ):
            with self.assertRaises(IntegrityError):
                self.client.delete(reverse("delete_youtube_video", args=[video.pk]))
            send.assert_not_called()
        self.assertFalse(TubeArchivistDeletion.objects.exists())
        self.assertTrue(Item.objects.filter(pk=video.pk).exists())

    @override_settings(TUBEARCHIVIST_TOKEN="")
    def test_partial_config_does_not_delete_local_video(self):
        video = self.video()
        response = self.client.delete(reverse("delete_youtube_video", args=[video.pk]))
        self.assertEqual(response.status_code, 400)
        self.assertTrue(Item.objects.filter(pk=video.pk).exists())

    @override_settings(TUBEARCHIVIST_URL="", TUBEARCHIVIST_TOKEN="")
    def test_disabled_integration_preserves_local_delete(self):
        video = self.video()
        response = self.client.delete(reverse("delete_youtube_video", args=[video.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Item.objects.filter(pk=video.pk).exists())
        self.assertFalse(TubeArchivistDeletion.objects.exists())
        self.request.assert_not_called()

    def test_invalid_youtube_id_does_not_delete(self):
        video = self.video(youtube_id="123")
        response = self.client.delete(reverse("delete_youtube_video", args=[video.pk]))
        self.assertEqual(response.status_code, 400)
        self.assertTrue(Item.objects.filter(pk=video.pk).exists())
        self.request.assert_not_called()

    def test_repeated_delete_does_not_duplicate_job(self):
        video = self.video()
        url = reverse("delete_youtube_video", args=[video.pk])
        with patch("integrations.tubearchivist.enqueue_deletion"):
            self.assertEqual(self.client.delete(url).status_code, 200)
            self.assertEqual(self.client.delete(url).status_code, 200)
        self.assertEqual(TubeArchivistDeletion.objects.count(), 1)

    def test_deleting_a_manually_readded_video_reopens_completed_job(self):
        job = self.job(status="completed", file_deleted=True)
        video = self.video()
        self.client.delete(reverse("delete_youtube_video", args=[video.pk]))
        job.refresh_from_db()
        self.assertEqual(job.status, "pending")
        self.assertFalse(job.file_deleted)
        self.assertEqual(TubeArchivistDeletion.objects.count(), 1)

    def test_sweeper_dispatches_only_due_pending_jobs(self):
        job = self.job()
        from integrations.tasks import retry_tubearchivist_deletions  # noqa: PLC0415

        with patch("integrations.tubearchivist.enqueue_deletion") as send:
            retry_tubearchivist_deletions.run()
        send.assert_called_once_with(job.pk)
        TubeArchivistDeletion.objects.filter(pk=job.pk).update(
            next_attempt_at=timezone.now() + timedelta(hours=1),
        )
        self.assertNotIn(job.pk, due_deletions())

    def test_tautulli_does_not_recreate_deleted_video_for_another_user(self):
        self.job()
        other = User.objects.create(username="another-user")
        with patch("app.providers.youtube.fetch_video_metadata") as metadata:
            handled = TautulliWebhookProcessor()._create_youtube_video_from_tautulli(
                {"file": f"/archive/UCtest/{VIDEO_ID}.mp4"},
                other,
            )
        self.assertTrue(handled)
        metadata.assert_not_called()
        self.assertFalse(Item.objects.filter(youtube_video_id=VIDEO_ID).exists())

    def test_tautulli_checks_for_deletion_committed_during_metadata_fetch(self):
        def metadata(_video_id):
            self.job()
            return {"channel_id": "UCtest", "title": "Video"}

        with patch("app.providers.youtube.fetch_video_metadata", side_effect=metadata):
            handled = TautulliWebhookProcessor()._create_youtube_video_from_tautulli(
                {"file": f"/archive/UCtest/{VIDEO_ID}.mp4"}, self.user,
            )
        self.assertTrue(handled)
        self.assertFalse(Item.objects.exists())

    def test_delete_confirmation_explains_shared_file_removal(self):
        template = "app/components/tubearchivist_delete_notice.html"
        enabled = render_to_string(template, {"TUBEARCHIVIST_ENABLED": True})
        disabled = render_to_string(template, {"TUBEARCHIVIST_ENABLED": False})
        self.assertIn("for everyone", enabled)
        self.assertNotIn("for everyone", disabled)

    def test_status_page_and_retry_are_available_to_any_logged_in_user(self):
        job = self.job(status="failed", last_error="TubeArchivist returned HTTP 403.")
        response = self.client.get(reverse("tubearchivist_status"))
        self.assertContains(response, "HTTP 403")
        self.assertNotContains(response, "test-token")
        with patch("integrations.tubearchivist_views.enqueue_deletion") as send:
            response = self.client.post(reverse("tubearchivist_retry", args=[job.pk]))
        self.assertEqual(response.status_code, 302)
        job.refresh_from_db()
        self.assertEqual(job.status, "pending")
        send.assert_called_once_with(job.pk)

    def test_anonymous_requests_cannot_delete_view_status_or_retry(self):
        video = self.video()
        job = self.job()
        self.client.logout()
        self.assertEqual(
            self.client.delete(
                reverse("delete_youtube_video", args=[video.pk]),
            ).status_code,
            302,
        )
        self.assertEqual(
            self.client.get(reverse("tubearchivist_status")).status_code, 302,
        )
        self.assertEqual(
            self.client.post(reverse("tubearchivist_retry", args=[job.pk])).status_code,
            302,
        )
        self.assertTrue(Item.objects.filter(pk=video.pk).exists())
