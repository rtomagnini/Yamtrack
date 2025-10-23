import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from app.models import TV, Anime, Episode, Item, MediaTypes, Movie, Season, Status
from integrations.webhooks.plex import PlexWebhookProcessor


class PlexWebhookTests(TestCase):
    """Tests for Plex webhook."""

    def setUp(self):
        """Set up test data."""
        self.client = Client()
        self.credentials = {
            "username": "testuser",
            "token": "test-token",
            "plex_usernames": "testuser",
        }
        self.user = get_user_model().objects.create_superuser(**self.credentials)
        self.url = reverse("plex_webhook", kwargs={"token": "test-token"})

    def test_invalid_token(self):
        """Test webhook with invalid token returns 401."""
        url = reverse("plex_webhook", kwargs={"token": "invalid-token"})
        response = self.client.post(url, data={}, content_type="application/json")
        self.assertEqual(response.status_code, 401)

    def test_tv_episode_mark_played(self):
        """Test webhook handles TV episode mark played event."""
        payload = {
            "event": "media.scrobble",
            "Account": {
                "title": "testuser",
            },
            "Metadata": {
                "type": "episode",
                "grandparentTitle": "Friends",
                "index": 1,
                "parentIndex": 1,
                "Guid": [
                    {
                        "id": "imdb://tt0583459",
                    },
                    {
                        "id": "tmdb://85987",
                    },
                    {
                        "id": "tvdb://303821",
                    },
                ],
            },
        }

        data = {
            "payload": json.dumps(payload),
        }

        response = self.client.post(
            self.url,
            data=data,
            format="multipart",
        )

        self.assertEqual(response.status_code, 200)

        # Verify objects were created
        tv_item = Item.objects.get(media_type=MediaTypes.TV.value, media_id="1668")
        self.assertEqual(tv_item.title, "Friends")

        tv = TV.objects.get(item=tv_item, user=self.user)
        self.assertEqual(tv.status, Status.IN_PROGRESS.value)

        season = Season.objects.get(
            item__media_id="1668",
            item__season_number=1,
        )
        self.assertEqual(season.status, Status.IN_PROGRESS.value)

        episode = Episode.objects.get(
            item__media_id="1668",
            item__season_number=1,
            item__episode_number=1,
        )
        self.assertIsNotNone(episode.end_date)

    def test_movie_mark_played(self):
        """Test webhook handles movie mark played event."""
        payload = {
            "event": "media.scrobble",
            "Account": {
                "title": "testuser",
            },
            "Metadata": {
                "type": "movie",
                "title": "The Matrix",
                "Guid": [
                    {
                        "id": "imdb://tt0133093",
                    },
                    {
                        "id": "tmdb://603",
                    },
                    {
                        "id": "tvdb://169",
                    },
                ],
            },
        }

        data = {
            "payload": json.dumps(payload),
        }

        response = self.client.post(
            self.url,
            data=data,
            format="multipart",
        )

        self.assertEqual(response.status_code, 200)

        # Verify movie was created and marked as completed
        movie = Movie.objects.get(
            item__media_id="603",
            user=self.user,
        )
        self.assertEqual(movie.status, Status.COMPLETED.value)
        self.assertEqual(movie.progress, 1)

    def test_anime_movie_mark_played(self):
        """Test webhook handles movie mark played event."""
        payload = {
            "event": "media.scrobble",
            "Account": {
                "title": "testuser",
            },
            "Metadata": {
                "type": "movie",
                "title": "Perfect Blue",
                "Guid": [
                    {
                        "id": "imdb://tt0156887",
                    },
                    {
                        "id": "tmdb://10494",
                    },
                    {
                        "id": "tvdb://3807",
                    },
                ],
            },
        }

        data = {
            "payload": json.dumps(payload),
        }

        response = self.client.post(
            self.url,
            data=data,
            format="multipart",
        )

        self.assertEqual(response.status_code, 200)

        # Verify movie was created and marked as completed
        movie = Anime.objects.get(
            item__media_id="437",
            user=self.user,
        )
        self.assertEqual(movie.status, Status.COMPLETED.value)
        self.assertEqual(movie.progress, 1)

    def test_anime_episode_mark_played(self):
        """Test webhook handles anime episode mark played event."""
        payload = {
            "event": "media.scrobble",
            "Account": {
                "title": "testuser",
            },
            "Metadata": {
                "type": "episode",
                "grandparentTitle": "Frieren: Beyond Journey's End",
                "index": 1,
                "parentIndex": 1,
                "Guid": [
                    {
                        "id": "imdb://tt23861604",
                    },
                    {
                        "id": "tmdb://3946240",
                    },
                    {
                        "id": "tvdb://9350138",
                    },
                ],
            },
        }

        data = {
            "payload": json.dumps(payload),
        }

        response = self.client.post(
            self.url,
            data=data,
            format="multipart",
        )

        self.assertEqual(response.status_code, 200)

        # Verify anime was created and marked as in progress
        anime = Anime.objects.get(
            item__media_id="52991",
            user=self.user,
        )
        self.assertEqual(anime.status, Status.IN_PROGRESS.value)
        self.assertEqual(anime.progress, 1)

    def test_ignored_event_types(self):
        """Test webhook ignores irrelevant event types."""
        payload = {
            "event": "media.something_else",
            "Account": {
                "title": "testuser",
            },
            "Metadata": {
                "type": "movie",
                "title": "Movie",
                "Guid": [
                    {
                        "id": "imdb://tt12345",
                    },
                    {
                        "id": "tmdb://12345",
                    },
                    {
                        "id": "tvdb://12345",
                    },
                ],
            },
        }

        data = {
            "payload": json.dumps(payload),
        }

        response = self.client.post(
            self.url,
            data=data,
            format="multipart",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Movie.objects.count(), 0)

    def test_missing_tmdb_id(self):
        """Test webhook handles missing TMDB ID gracefully."""
        payload = {
            "event": "media.scrobble",
            "Account": {
                "title": "testuser",
            },
            "Metadata": {
                "type": "movie",
                "title": "The Matrix",
                "Guid": [],
            },
        }
        data = {
            "payload": json.dumps(payload),
        }

        response = self.client.post(
            self.url,
            data=data,
            format="multipart",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Movie.objects.count(), 0)

    def test_repeated_watch(self):
        """Test webhook handles repeated watches."""
        payload = {
            "event": "media.scrobble",
            "Account": {
                "title": "testuser",
            },
            "Metadata": {
                "type": "movie",
                "title": "The Matrix",
                "Guid": [
                    {
                        "id": "imdb://tt0133093",
                    },
                    {
                        "id": "tmdb://603",
                    },
                    {
                        "id": "tvdb://169",
                    },
                ],
            },
        }

        data = {
            "payload": json.dumps(payload),
        }

        # First watch
        response = self.client.post(
            self.url,
            data=data,
            format="multipart",
        )

        # Second watch
        response = self.client.post(
            self.url,
            data=data,
            format="multipart",
        )

        self.assertEqual(response.status_code, 200)
        movie = Movie.objects.filter(item__media_id="603")
        self.assertEqual(movie.count(), 2)
        self.assertEqual(movie[0].status, Status.COMPLETED.value)
        self.assertEqual(movie[1].status, Status.COMPLETED.value)

    def test_username_matching(self):
        """Test Plex username matching functionality."""
        test_cases = [
            # stored, incoming, should_match
            ("testuser", "testuser", True),  # Exact match
            ("testuser", "TestUser", True),  # Case insensitive
            ("testuser", " testuser ", True),  # Whitespace handling
            ("testuser", "testuser2", False),  # Different username
            ("testuser1,testuser2", "testuser1", True),  # First in list
            ("testuser1, testuser2", "testuser1", True),  # comma and space
            ("testuser1,testuser2", "testuser3", False),  # Not in list
        ]

        base_payload = {
            "event": "media.scrobble",
            "Metadata": {
                "type": "movie",
                "title": "Test Movie",
                "Guid": [{"id": "tmdb://123"}],
            },
        }

        for i, (stored_usernames, incoming_username, should_match) in enumerate(
            test_cases,
        ):
            with self.subTest(
                f"Case {i + 1}: {stored_usernames} vs {incoming_username}",
            ):
                self.user.plex_usernames = stored_usernames
                self.user.save()
                payload = base_payload.copy()
                payload["Account"] = {"title": incoming_username}

                response = self.client.post(
                    self.url,
                    data={"payload": json.dumps(payload)},
                    format="multipart",
                )

                if should_match:
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(Movie.objects.count(), 1)
                    Movie.objects.all().delete()  # Clean up for next test
                else:
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(Movie.objects.count(), 0)

    def test_extract_external_ids(self):
        """Test extraction of external IDs from Plex webhook payload."""
        # Setup test payload
        payload = {
            "Metadata": {
                "Guid": [
                    {"id": "tmdb://12345"},
                    {"id": "imdb://tt67890"},
                    {"id": "tvdb://98765"},
                ],
            },
        }

        # Execute
        result = PlexWebhookProcessor()._extract_external_ids(payload)

        # Assert
        expected = {
            "tmdb_id": "12345",
            "imdb_id": "tt67890",
            "tvdb_id": "98765",
        }

        if result != expected:
            msg = f"Expected {expected}, got {result}"
            raise AssertionError(msg)

    def test_extract_external_ids_missing_data(self):
        """Test handling of missing or empty data."""
        payload = {"Metadata": {"Guid": []}}

        result = PlexWebhookProcessor()._extract_external_ids(payload)

        expected = {
            "tmdb_id": None,
            "imdb_id": None,
            "tvdb_id": None,
        }
        if result != expected:
            msg = f"Expected {expected}, got {result}"
            raise AssertionError(msg)

    def test_library_new_youtube_video_creation(self):
        """Test webhook creates YouTube video when library.new event received."""
        from unittest.mock import patch, MagicMock
        
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
        
        payload = {
            "event": "library.new",
            "Account": {
                "title": "testuser",
            },
            "Metadata": {
                "type": "clip",  # Plex uses "clip" for YouTube videos sometimes
                "title": "Test YouTube Video",
                "Guid": [
                    {
                        "id": "youtube://dQw4w9WgXcQ",
                    },
                ],
            },
        }
        
        data = {
            "payload": json.dumps(payload),
        }
        
        # Mock YouTube provider functions
        with patch('app.providers.youtube.fetch_video_metadata') as mock_fetch_video, \
             patch('app.providers.youtube.fetch_channel_metadata') as mock_fetch_channel:
            
            mock_fetch_video.return_value = mock_video_metadata
            mock_fetch_channel.return_value = mock_channel_metadata
            
            # Send webhook
            response = self.client.post(
                self.url,
                data=data,
                format="multipart",
            )
            
            self.assertEqual(response.status_code, 200)
            
            # Verify YouTube video Item was created
            from app.models import Sources
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
    
    def test_library_new_youtube_video_duplicate_prevention(self):
        """Test that duplicate YouTube videos are not created."""
        from unittest.mock import patch
        from app.models import Sources
        
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
            "event": "library.new",
            "Account": {
                "title": "testuser",
            },
            "Metadata": {
                "type": "clip",
                "title": "Existing Video",
                "Guid": [
                    {
                        "id": "youtube://existing123",
                    },
                ],
            },
        }
        
        data = {
            "payload": json.dumps(payload),
        }
        
        # Send webhook
        response = self.client.post(
            self.url,
            data=data,
            format="multipart",
        )
        
        self.assertEqual(response.status_code, 200)
        
        # Verify no duplicate was created
        video_count = Item.objects.filter(
            source=Sources.YOUTUBE.value,
            media_type=MediaTypes.EPISODE.value,
            youtube_video_id="existing123",
        ).count()
        
        self.assertEqual(video_count, 1, "Should not create duplicate video")
    
    def test_library_new_youtube_video_from_tubearchivist(self):
        """Test that YouTube videos from TubeArchivist (file path only, no YouTube GUIDs) are detected."""
        from unittest.mock import patch
        from app.models import Sources
        
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
        
        # Payload similar to real TubeArchivist XML (no YouTube GUID, uses tv.plex.agents.none)
        payload = {
            "event": "library.new",
            "Account": {
                "title": "testuser",
            },
            "Metadata": {
                "type": "movie",
                "subtype": "clip",
                "title": "SHOW COMPLETO EM MARÍLIA",
                "Guid": [
                    {
                        "id": "tv.plex.agents.none://53701",  # No YouTube GUID
                    },
                ],
                "Media": [
                    {
                        "Part": [
                            {
                                "file": "/volume1/Servidor/tubearchivist/UC_ATWjZ2hVwEVh4JiDpKccA/S3HTZSTcieQ.mp4",
                            },
                        ],
                    },
                ],
            },
        }
        
        data = {
            "payload": json.dumps(payload),
        }
        
        # Mock YouTube provider functions
        with patch('app.providers.youtube.fetch_video_metadata') as mock_fetch_video, \
             patch('app.providers.youtube.fetch_channel_metadata') as mock_fetch_channel:
            
            mock_fetch_video.return_value = mock_video_metadata
            mock_fetch_channel.return_value = mock_channel_metadata
            
            # Send webhook
            response = self.client.post(
                self.url,
                data=data,
                format="multipart",
            )
            
            self.assertEqual(response.status_code, 200)
            
            # Verify YouTube video Item was created
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

