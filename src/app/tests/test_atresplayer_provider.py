"""Tests for Atresplayer provider."""
import pytest
from app.providers import atresplayer


class TestAtresplayerProvider:
    """Test Atresplayer metadata extraction."""

    def test_extract_episode_id_valid_url(self):
        """Test extracting episode ID from valid Atresplayer URL."""
        url = "https://www.atresplayer.com/antena3/programas/el-hormiguero/temporada-15/dani-martin_68fa66404046aa0007817314/"
        episode_id = atresplayer.extract_episode_id(url)
        assert episode_id == "68fa66404046aa0007817314"

    def test_extract_episode_id_with_trailing_slash(self):
        """Test extracting episode ID with trailing slash."""
        url = "https://www.atresplayer.com/antena3/programas/el-hormiguero/temporada-15/dani-martin_68fa66404046aa0007817314/"
        episode_id = atresplayer.extract_episode_id(url)
        assert episode_id == "68fa66404046aa0007817314"

    def test_extract_episode_id_without_trailing_slash(self):
        """Test extracting episode ID without trailing slash."""
        url = "https://www.atresplayer.com/antena3/programas/el-hormiguero/temporada-15/dani-martin_68fa66404046aa0007817314"
        episode_id = atresplayer.extract_episode_id(url)
        assert episode_id == "68fa66404046aa0007817314"

    def test_extract_episode_id_invalid_url(self):
        """Test extracting episode ID from invalid URL."""
        url = "https://www.atresplayer.com/antena3/programas/el-hormiguero/"
        episode_id = atresplayer.extract_episode_id(url)
        assert episode_id is None

    def test_extract_episode_id_empty_url(self):
        """Test extracting episode ID from empty URL."""
        episode_id = atresplayer.extract_episode_id("")
        assert episode_id is None

    def test_extract_episode_id_none_url(self):
        """Test extracting episode ID from None URL."""
        episode_id = atresplayer.extract_episode_id(None)
        assert episode_id is None

    @pytest.mark.django_db
    def test_fetch_video_metadata_valid_id(self):
        """Test fetching metadata from Atresplayer API with valid episode ID."""
        # Using the example URL provided by the user
        episode_id = "68fa66404046aa0007817314"
        metadata = atresplayer.fetch_video_metadata(episode_id)
        
        # Check that we got metadata back
        assert metadata is not None
        assert isinstance(metadata, dict)
        
        # Check expected keys exist
        assert 'title' in metadata
        assert 'thumbnail' in metadata
        assert 'duration_minutes' in metadata
        assert 'air_date' in metadata
        
        # Check that title is not empty
        assert metadata['title']
        print(f"\nExtracted metadata:")
        print(f"  Title: {metadata['title']}")
        print(f"  Thumbnail: {metadata['thumbnail']}")
        print(f"  Duration: {metadata['duration_minutes']} minutes")
        print(f"  Air date: {metadata['air_date']}")

    @pytest.mark.django_db
    def test_fetch_video_metadata_invalid_id(self):
        """Test fetching metadata with invalid episode ID."""
        episode_id = "invalid_id_123"
        metadata = atresplayer.fetch_video_metadata(episode_id)
        
        # Should return None for invalid ID
        assert metadata is None

    def test_fetch_video_metadata_empty_id(self):
        """Test fetching metadata with empty episode ID."""
        metadata = atresplayer.fetch_video_metadata("")
        assert metadata is None

    def test_fetch_video_metadata_none_id(self):
        """Test fetching metadata with None episode ID."""
        metadata = atresplayer.fetch_video_metadata(None)
        assert metadata is None
