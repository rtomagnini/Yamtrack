"""Tests for Tautulli webhook integration."""

import json
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from app.models import Item, TV, Season, Episode, MediaTypes, Sources, Status
from users.models import User


class TautulliWebhookTest(TestCase):
    """Test cases for Tautulli webhook processor."""

    def setUp(self):
        """Set up test fixtures."""
        self.user = User.objects.create(
            username="testuser",
            password="testpass",
        )
        self.url = reverse("tautulli_webhook", kwargs={"token": self.user.token})

    def test_tautulli_recently_added_youtube_video_creation(self):
        """Test webhook creates YouTube video when Recently Added event received."""
        # Mock YouTube API responses
        mock_video_metadata = {
            "video_id": "dQw4w9WgXcQ",
            "title": "Test YouTube Video",
            "channel_id": "UCtest123",
            "thumbnail": "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg",
            "duration_minutes": 5,
            "published_date": "2023-06-15",
        }
        
        mock_channel_metadata = {
            "title": "Test Channel",
            "thumbnail": "https://yt3.ggpht.com/channel_thumb.jpg",
        }
        
        # Tautulli webhook payload (JSON format)
        # This simulates a "Recently Added" notification from Tautulli
        payload = {
            "action": "created",  # Recently Added event
            "media_type": "movie",  # Tautulli may report YouTube videos as movies
            "title": "Test YouTube Video",
            "file": "/volume1/Servidor/tubearchivist/UCtest123/dQw4w9WgXcQ.mp4",
            "filename": "dQw4w9WgXcQ.mp4",
        }
        
        # Mock YouTube provider functions
        with patch('app.providers.youtube.fetch_video_metadata') as mock_fetch_video, \
             patch('app.providers.youtube.fetch_channel_metadata') as mock_fetch_channel:
            
            mock_fetch_video.return_value = mock_video_metadata
            mock_fetch_channel.return_value = mock_channel_metadata
            
            # Send webhook (Tautulli sends JSON in body, not form data)
            response = self.client.post(
                self.url,
                data=json.dumps(payload),
                content_type="application/json",
            )
            
            self.assertEqual(response.status_code, 200)
            
            # Verify YouTube video Item was created
            episode_item = Item.objects.filter(
                source=Sources.YOUTUBE.value,
                media_type=MediaTypes.EPISODE.value,
                youtube_video_id="dQw4w9WgXcQ",
            ).first()
            
            self.assertIsNotNone(episode_item, "YouTube video Item should be created")
            self.assertEqual(episode_item.title, "Test YouTube Video")
            self.assertEqual(episode_item.runtime, 5)
            self.assertEqual(episode_item.air_date, "2023-06-15")
            
            # Verify channel (TV) was created
            channel_item = Item.objects.filter(
                source=Sources.YOUTUBE.value,
                media_type=MediaTypes.YOUTUBE.value,
            ).first()
            
            self.assertIsNotNone(channel_item, "YouTube channel Item should be created")
            self.assertEqual(channel_item.title, "Test Channel")
            
            # Verify TV instance exists for user
            tv = TV.objects.filter(
                item=channel_item,
                user=self.user,
            ).first()
            
            self.assertIsNotNone(tv, "TV instance should be created for user")
            self.assertIn("YouTube Channel ID: UCtest123", tv.notes)
            
            # Verify season was created (year 2023)
            season_item = Item.objects.filter(
                source=Sources.YOUTUBE.value,
                media_type=MediaTypes.SEASON.value,
                season_number=2023,
            ).first()
            
            self.assertIsNotNone(season_item, "Season Item should be created")
            
            # Verify Season instance exists
            season = Season.objects.filter(
                item=season_item,
                user=self.user,
            ).first()
            
            self.assertIsNotNone(season, "Season instance should be created")
            
            # Verify Episode instance was created
            episode = Episode.objects.filter(
                item=episode_item,
                user=self.user,
            ).first()
            
            self.assertIsNotNone(episode, "Episode instance should be created")
            self.assertEqual(episode.status, Status.COMPLETED.value, "Episode should be marked as completed")

    def test_tautulli_recently_added_duplicate_prevention(self):
        """Test that duplicate YouTube videos are not created."""
        # Create existing video
        existing_channel = Item.objects.create(
            media_id="yt_001",
            source=Sources.YOUTUBE.value,
            media_type=MediaTypes.YOUTUBE.value,
            title="Existing Channel",
        )
        
        existing_season = Item.objects.create(
            media_id="yt_001",
            source=Sources.YOUTUBE.value,
            media_type=MediaTypes.SEASON.value,
            season_number=2023,
            title="Existing Channel - 2023",
        )
        
        existing_video = Item.objects.create(
            media_id="yt_001",
            source=Sources.YOUTUBE.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=2023,
            episode_number=1,
            title="Existing Video",
            youtube_video_id="existing123",
        )
        
        payload = {
            "action": "created",
            "media_type": "movie",
            "title": "Existing Video",
            "file": "/volume1/Servidor/tubearchivist/UCtest123/existing123.mp4",
        }
        
        # Send webhook
        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )
        
        self.assertEqual(response.status_code, 200)
        
        # Verify no duplicate was created
        video_count = Item.objects.filter(
            source=Sources.YOUTUBE.value,
            media_type=MediaTypes.EPISODE.value,
            youtube_video_id="existing123",
        ).count()
        
        self.assertEqual(video_count, 1, "Should not create duplicate video")

    def test_tautulli_recently_added_tubearchivist_pattern(self):
        """Test YouTube video detection from TubeArchivist file path pattern."""
        # Mock YouTube API responses
        mock_video_metadata = {
            "video_id": "S3HTZSTcieQ",
            "title": "SHOW COMPLETO EM MARÍLIA",
            "channel_id": "UC_ATWjZ2hVwEVh4JiDpKccA",
            "thumbnail": "https://i.ytimg.com/vi/S3HTZSTcieQ/maxresdefault.jpg",
            "duration_minutes": 71,
            "published_date": "2024-10-22",
        }
        
        mock_channel_metadata = {
            "title": "Raphael Ghanem",
            "thumbnail": "https://yt3.ggpht.com/channel.jpg",
        }
        
        # Tautulli payload with TubeArchivist file path
        payload = {
            "action": "created",
            "media_type": "movie",
            "title": "SHOW COMPLETO EM MARÍLIA",
            "file": "/volume1/Servidor/tubearchivist/UC_ATWjZ2hVwEVh4JiDpKccA/S3HTZSTcieQ.mp4",
        }
        
        # Mock YouTube provider functions
        with patch('app.providers.youtube.fetch_video_metadata') as mock_fetch_video, \
             patch('app.providers.youtube.fetch_channel_metadata') as mock_fetch_channel:
            
            mock_fetch_video.return_value = mock_video_metadata
            mock_fetch_channel.return_value = mock_channel_metadata
            
            # Send webhook
            response = self.client.post(
                self.url,
                data=json.dumps(payload),
                content_type="application/json",
            )
            
            self.assertEqual(response.status_code, 200)
            
            # Verify YouTube video Item was created from file path
            episode_item = Item.objects.filter(
                source=Sources.YOUTUBE.value,
                media_type=MediaTypes.EPISODE.value,
                youtube_video_id="S3HTZSTcieQ",
            ).first()
            
            self.assertIsNotNone(episode_item, "YouTube video Item should be created from file path")
            self.assertEqual(episode_item.title, "SHOW COMPLETO EM MARÍLIA")
            
            # Verify channel was created
            channel_item = Item.objects.filter(
                source=Sources.YOUTUBE.value,
                media_type=MediaTypes.YOUTUBE.value,
            ).first()
            
            self.assertIsNotNone(channel_item, "YouTube channel Item should be created")
            self.assertEqual(channel_item.title, "Raphael Ghanem")

    def test_tautulli_ignores_non_youtube_files(self):
        """Test that non-YouTube files are ignored."""
        payload = {
            "action": "created",
            "media_type": "movie",
            "title": "Regular Movie",
            "file": "/volume1/Peliculas/Movie (2023)/Movie.mkv",  # Not a YouTube pattern
        }
        
        # Send webhook
        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )
        
        self.assertEqual(response.status_code, 200)
        
        # Verify no YouTube video was created
        video_count = Item.objects.filter(
            source=Sources.YOUTUBE.value,
            media_type=MediaTypes.EPISODE.value,
        ).count()
        
        self.assertEqual(video_count, 0, "Should not create video for non-YouTube file")

    def test_tautulli_ignores_non_created_actions(self):
        """Test that non-created actions are ignored."""
        payload = {
            "action": "play",  # Not a "created" event
            "media_type": "movie",
            "title": "Test Video",
            "file": "/volume1/tubearchivist/UCtest123/dQw4w9WgXcQ.mp4",
        }
        
        # Send webhook
        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )
        
        self.assertEqual(response.status_code, 200)
        
        # Verify no YouTube video was created
        video_count = Item.objects.filter(
            source=Sources.YOUTUBE.value,
        ).count()
        
        self.assertEqual(video_count, 0, "Should not create video for non-created action")

    def test_tautulli_invalid_token(self):
        """Test webhook rejects invalid tokens."""
        invalid_url = reverse("tautulli_webhook", kwargs={"token": "invalid_token"})
        
        payload = {
            "action": "created",
            "media_type": "movie",
            "file": "/volume1/tubearchivist/UCtest123/dQw4w9WgXcQ.mp4",
        }
        
        response = self.client.post(
            invalid_url,
            data=json.dumps(payload),
            content_type="application/json",
        )
        
        self.assertEqual(response.status_code, 401, "Should reject invalid token")

    def test_tautulli_invalid_json(self):
        """Test webhook rejects invalid JSON."""
        response = self.client.post(
            self.url,
            data="not valid json{",
            content_type="application/json",
        )
        
        self.assertEqual(response.status_code, 400, "Should reject invalid JSON")

    def test_tautulli_filtered_channel_blocked(self):
        """Test that videos from filtered/blocked channels are not created."""
        from app.models import YouTubeChannelFilter
        
        # Create a channel filter for this user
        YouTubeChannelFilter.objects.create(
            user=self.user,
            channel_id="UCtest123",
            channel_name="Blocked Test Channel",
        )
        
        # Mock YouTube API responses
        mock_video_metadata = {
            "video_id": "dQw4w9WgXcQ",
            "title": "Test YouTube Video",
            "channel_id": "UCtest123",  # This channel is blocked
            "thumbnail": "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg",
            "duration_minutes": 5,
            "published_date": "2023-06-15",
        }
        
        payload = {
            "action": "created",
            "media_type": "movie",
            "title": "Test YouTube Video",
            "file": "/volume1/Servidor/tubearchivist/UCtest123/dQw4w9WgXcQ.mp4",
            "filename": "dQw4w9WgXcQ.mp4",
        }
        
        with patch("app.providers.youtube.fetch_video_metadata") as mock_video:
            mock_video.return_value = mock_video_metadata
            
            # Send webhook
            response = self.client.post(
                self.url,
                data=json.dumps(payload),
                content_type="application/json",
            )
            
            self.assertEqual(response.status_code, 200)
        
        # Verify NO YouTube channel/season/video was created (blocked)
        channel_count = Item.objects.filter(
            source=Sources.YOUTUBE.value,
            media_type=MediaTypes.YOUTUBE.value,
        ).count()
        
        video_count = Item.objects.filter(
            source=Sources.YOUTUBE.value,
            media_type=MediaTypes.EPISODE.value,
        ).count()
        
        self.assertEqual(channel_count, 0, "Should not create channel for filtered channel")
        self.assertEqual(video_count, 0, "Should not create video for filtered channel")
