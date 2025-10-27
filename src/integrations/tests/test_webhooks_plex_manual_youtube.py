import json
from django.test import TestCase, Client
from django.urls import reverse
from app.models import Item, MediaTypes, Sources, Season, Status
from django.contrib.auth import get_user_model

class PlexWebhookManualYoutubeIdTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.credentials = {
            "username": "testuser",
            "token": "test-token",
            "plex_usernames": "testuser",
        }
        self.user = get_user_model().objects.create_superuser(**self.credentials)
        self.url = reverse("plex_webhook", kwargs={"token": "test-token"})

        # Create manual TV Show Item
        self.manual_tv = Item.objects.create(
            media_id="manual_show_1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.TV.value,
            title="Manual TV Show",
        )
        # Create manual TV Show episode with youtube_video_id
        self.manual_episode = Item.objects.create(
            media_id="manual_show_1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.EPISODE.value,
            title="Manual Episode with YouTube ID",
            season_number=1,
            episode_number=1,
            youtube_video_id="dQw4w9WgXcQ",
        )
        # Create manual Season for the episode and user
        from app.models import Season, Status
        self.season = Season.objects.create(
            item=self.manual_episode,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

    def test_mark_manual_episode_watched_by_youtube_id(self):
        payload = {
            "event": "media.scrobble",
            "Account": {"title": "testuser"},
            "Metadata": {
                "type": "episode",
                "title": "Manual Episode with YouTube ID",
                "Media": [
                    {"Part": [
                        {"file": f"/some/path/dQw4w9WgXcQ.mp4"}
                    ]}
                ],
            },
        }
        data = {"payload": json.dumps(payload)}
        response = self.client.post(self.url, data=data, format="multipart")
        self.assertEqual(response.status_code, 200)
        # Check that the episode was marked as watched (Episode object created)
        from app.models import Episode
        episode = Episode.objects.filter(item=self.manual_episode).first()
        self.assertIsNotNone(episode, "Manual TV Show episode should be marked as watched by youtube_video_id fallback.")
