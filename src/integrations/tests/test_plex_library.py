"""Tests for the Plex step of the durable TA deletion workflow."""

# All tokens are fake fixtures; descriptive test names explain each case.
# ruff: noqa: D102, S106

from datetime import timedelta
from unittest.mock import patch

import requests
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from app.mixins import disable_fetch_releases
from app.models import Item, MediaTypes, Sources
from integrations.models import TubeArchivistDeletion
from integrations.plex_library import PlexError, PlexLibraryClient
from integrations.tests.test_tubearchivist import VIDEO_ID, api_response
from integrations.tubearchivist import TubeArchivistError, process_deletion
from users.models import User


@override_settings(
    TUBEARCHIVIST_URL="http://ta:8000",
    TUBEARCHIVIST_TOKEN="ta-test-token",
    PLEX_SERVER_URL="http://plex:32400",
    PLEX_TOKEN="plex-test-token",
    PLEX_YOUTUBE_LIBRARY="Youtube",
)
class PlexLibraryTest(TestCase):
    """Verify library selection, API failures and independent checkpoint recovery."""

    def setUp(self):
        self.ta = patch(
            "integrations.tubearchivist.TubeArchivistClient.request",
        ).start()
        self.http = patch("integrations.plex_library.requests.request").start()
        self.addCleanup(patch.stopall)

    def job(self, **kwargs):
        return TubeArchivistDeletion.objects.create(
            youtube_id=VIDEO_ID,
            title="Video",
            plex_library="Youtube",
            **kwargs,
        )

    def section_list(self, sections=None):
        if sections is None:
            sections = [
                {"title": "Movies", "key": "1"},
                {"title": "Youtube", "key": "7"},
            ]
        return api_response(200, {"MediaContainer": {"Directory": sections}})

    def test_scan_runs_only_after_confirmed_ta_deletion(self):
        job = self.job()
        self.ta.side_effect = [True, {"status": "ignore"}, None]

        def plex_response(method, _url, **_kwargs):
            checkpoint = TubeArchivistDeletion.objects.get(pk=job.pk)
            self.assertIsNotNone(checkpoint.ta_completed_at)
            self.assertEqual(checkpoint.last_error, "")
            return self.section_list() if method == "GET" else api_response(200)

        self.http.side_effect = plex_response
        process_deletion(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, "completed")
        self.assertIsNotNone(job.ta_completed_at)
        self.assertIsNotNone(job.plex_scan_requested_at)
        calls = self.http.call_args_list
        self.assertEqual(calls[0].args, ("GET", "http://plex:32400/library/sections"))
        self.assertEqual(
            calls[1].args,
            ("POST", "http://plex:32400/library/sections/7/refresh"),
        )
        for call in calls:
            self.assertEqual(call.kwargs["headers"]["X-Plex-Token"], "plex-test-token")
            self.assertFalse(call.kwargs["allow_redirects"])
            self.assertNotIn("plex-test-token", call.args[1])

    def test_pending_ignore_does_not_trigger_plex(self):
        job = self.job()
        self.ta.side_effect = [True, None, True]
        process_deletion(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, "pending")
        self.assertIsNone(job.ta_completed_at)
        self.http.assert_not_called()

    def test_ta_failure_does_not_trigger_plex(self):
        job = self.job()
        self.ta.side_effect = TubeArchivistError("TA unavailable")
        process_deletion(job.pk)
        self.http.assert_not_called()

    def test_plex_failure_keeps_ta_checkpoint_and_retries_only_plex(self):
        job = self.job()
        self.ta.side_effect = [True, {"status": "ignore"}, None]
        self.http.side_effect = [self.section_list(), api_response(503)]
        process_deletion(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, "pending")
        self.assertIsNotNone(job.ta_completed_at)
        self.assertIsNone(job.plex_scan_requested_at)
        self.assertIn("Plex", job.last_error)
        self.ta.reset_mock()
        TubeArchivistDeletion.objects.filter(pk=job.pk).update(
            next_attempt_at=timezone.now() - timedelta(seconds=1),
        )
        self.http.side_effect = [self.section_list(), api_response(200)]
        process_deletion(job.pk)
        self.ta.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.status, "completed")

    @override_settings(TUBEARCHIVIST_URL="", TUBEARCHIVIST_TOKEN="")
    def test_manual_retry_after_ta_completion_does_not_require_ta_credentials(self):
        job = self.job(status="failed", ta_completed_at=timezone.now())
        user = User.objects.create(username="plex-user")
        self.client.force_login(user)
        response = self.client.get(reverse("tubearchivist_status"))
        self.assertContains(response, "Retry")
        with patch("integrations.tubearchivist_views.enqueue_deletion") as send:
            self.client.post(reverse("tubearchivist_retry", args=[job.pk]))
        send.assert_called_once_with(job.pk)
        self.http.side_effect = [self.section_list(), api_response(200)]
        process_deletion(job.pk)
        self.ta.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.status, "completed")

    @override_settings(PLEX_TOKEN="")
    def test_missing_plex_token_fails_after_saving_ta_checkpoint(self):
        job = self.job()
        self.ta.side_effect = [True, {"status": "ignore"}, None]
        process_deletion(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertIsNotNone(job.ta_completed_at)
        self.http.assert_not_called()

    def test_missing_or_duplicate_library_never_scans_other_libraries(self):
        for sections in (
            [{"title": "Movies", "key": "1"}],
            [{"title": "Youtube", "key": "7"}, {"title": "Youtube", "key": "8"}],
        ):
            with self.subTest(sections=sections):
                self.http.reset_mock()
                self.http.return_value = self.section_list(sections)
                with self.assertRaises(PlexError):
                    PlexLibraryClient().scan("Youtube")
                self.assertEqual(self.http.call_count, 1)

    def test_invalid_section_id_does_not_form_an_arbitrary_url(self):
        self.http.return_value = self.section_list(
            [{"title": "Youtube", "key": "../all"}],
        )
        with self.assertRaises(PlexError):
            PlexLibraryClient().scan("Youtube")
        self.assertEqual(self.http.call_count, 1)

    def test_authentication_failure_requires_manual_retry(self):
        job = self.job(ta_completed_at=timezone.now())
        self.http.return_value = api_response(401)
        process_deletion(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertIn("401", job.last_error)
        self.ta.assert_not_called()

    def test_network_exception_is_redacted(self):
        self.http.side_effect = requests.ConnectionError(
            "plex-test-token private details",
        )
        with self.assertRaises(PlexError) as error:
            PlexLibraryClient().scan("Youtube")
        self.assertNotIn("plex-test-token", str(error.exception))

    def test_invalid_library_response_is_not_accepted(self):
        for payload in (None, [], {}, {"MediaContainer": {"Directory": "invalid"}}):
            with self.subTest(payload=payload):
                self.http.return_value = api_response(200, payload)
                with self.assertRaises(PlexError):
                    PlexLibraryClient().scan("Youtube")

    def test_new_deletion_snapshots_library_and_readded_video_resets_checkpoint(self):
        user = User.objects.create(username="plex-delete-user")
        self.client.force_login(user)
        job = self.job(
            status="completed",
            ta_completed_at=timezone.now(),
            plex_scan_requested_at=timezone.now(),
        )
        with disable_fetch_releases():
            video = Item.objects.create(
                media_id="yt-channel",
                title="Video",
                source=Sources.YOUTUBE.value,
                media_type=MediaTypes.EPISODE.value,
                season_number=2026,
                episode_number=1,
                youtube_video_id=VIDEO_ID,
            )
        response = self.client.delete(reverse("delete_youtube_video", args=[video.pk]))
        self.assertEqual(response.status_code, 200)
        job.refresh_from_db()
        self.assertEqual(job.plex_library, "Youtube")
        self.assertIsNone(job.ta_completed_at)
        self.assertIsNone(job.plex_scan_requested_at)

    def test_new_job_records_the_configured_plex_library(self):
        user = User.objects.create(username="new-plex-delete-user")
        self.client.force_login(user)
        with disable_fetch_releases():
            video = Item.objects.create(
                media_id="yt-channel",
                title="Video",
                source=Sources.YOUTUBE.value,
                media_type=MediaTypes.EPISODE.value,
                season_number=2026,
                episode_number=1,
                youtube_video_id=VIDEO_ID,
            )
        self.client.delete(reverse("delete_youtube_video", args=[video.pk]))
        self.assertEqual(TubeArchivistDeletion.objects.get().plex_library, "Youtube")
        self.http.assert_not_called()

    def test_status_page_distinguishes_scan_request_from_finished_scan(self):
        self.job(
            status="completed",
            ta_completed_at=timezone.now(),
            plex_scan_requested_at=timezone.now(),
        )
        user = User.objects.create(username="plex-status-user")
        self.client.force_login(user)
        response = self.client.get(reverse("tubearchivist_status"))
        self.assertContains(response, "Scan requested in Plex (Youtube)")
        self.assertContains(response, "Plex finishes it in the background")
