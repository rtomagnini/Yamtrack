import pytest
from django.utils import timezone
from app.models import Item, Season, MediaTypes, Sources
from users.models import User


@pytest.mark.django_db
def test_create_episode_item_with_tmdb_fields(monkeypatch):
    # Prepare season metadata with one episode including air_date and runtime
    season_metadata = {
        "episodes": [
            {
                "episode_number": 1,
                "air_date": "2020-01-01",
                "runtime": 42,
                "still_path": None,
                "name": "Pilot",
                "overview": "",
            }
        ]
    }

    # Create a season item and season instance (create a user first)
    user = User.objects.create(username="tester")
    item = Item.objects.create(
        media_id="123",
        source=Sources.TMDB.value,
        media_type=MediaTypes.SEASON.value,
        title="Test Show",
        image="http://example.com/image.jpg",
        season_number=1,
    )

    season = Season.objects.create(
        item=item,
        user=user,
        status="In progress",
    )

    # Call get_episode_item to create the episode Item
    ep_item = season.get_episode_item(1, season_metadata)

    assert ep_item is not None
    assert ep_item.air_date == "2020-01-01"
    assert ep_item.runtime == 42


@pytest.mark.django_db
def test_update_existing_episode_item_with_tmdb_fields(monkeypatch):
    # Create an existing episode Item without air_date/runtime
    item = Item.objects.create(
        media_id="200",
        source=Sources.TMDB.value,
        media_type=MediaTypes.SEASON.value,
        title="Test Show 2",
        image="http://example.com/image2.jpg",
        season_number=2,
    )

    ep_item = Item.objects.create(
        media_id="200",
        source=Sources.TMDB.value,
        media_type=MediaTypes.EPISODE.value,
        title="Test Show 2",
        image="http://example.com/ep.jpg",
        season_number=2,
        episode_number=1,
    )

    # Season instance
    user = User.objects.create(username="tester2")

    season = Season.objects.create(
        item=item,
        user=user,
        status="In progress",
    )

    # TMDB provides air_date/runtime
    season_metadata = {
        "episodes": [
            {
                "episode_number": 1,
                "air_date": "2021-05-05",
                "runtime": 55,
                "still_path": None,
                "name": "Episode 1",
                "overview": "",
            }
        ]
    }

    updated_item = season.get_episode_item(1, season_metadata)

    assert updated_item.id == ep_item.id
    assert updated_item.air_date == "2021-05-05"
    assert updated_item.runtime == 55
