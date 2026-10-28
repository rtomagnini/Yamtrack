"""Personal metadata must survive syncs and consistently affect watch statistics."""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone

from app import statistics
from app.forms import SeasonForm
from app.metadata import display_metadata, runtime_expression
from app.models import TV, BasicMedia, Episode, Item, ItemMetadataOverride, Movie, Season
from app.providers import tmdb
from users.models import MediaStatusChoices


pytestmark = pytest.mark.django_db


@pytest.fixture
def user():
    return get_user_model().objects.create_user(username="metadata-user")


@pytest.fixture
def movie_metadata():
    return {
        "source": "tmdb", "media_type": "movie", "media_id": "123",
        "title": "Original movie", "image": "https://example.com/original.jpg",
        "runtime": 90, "max_progress": 1, "details": {"runtime": "1h 30min"},
        "related": {},
    }


@pytest.fixture
def movie_item(movie_metadata):
    return Item.objects.create(**{
        key: movie_metadata[key]
        for key in ("source", "media_type", "media_id", "title", "image", "runtime")
    })


@pytest.fixture
def provider(movie_metadata):
    with patch("app.providers.services.get_media_metadata", return_value=movie_metadata) as mocked:
        yield mocked


def edit_url(media_type="movie", source="tmdb", media_id="123"):
    return reverse("metadata_edit", args=[source, media_type, media_id])


def test_movie_edit_is_personal_and_does_not_mutate_provider_cache(client, user, movie_item, movie_metadata, provider):
    client.force_login(user)
    response = client.post(edit_url(), {
        "title": "My movie", "image": "https://example.com/personal.jpg", "runtime": "125",
        "next": "/statistics",
    })
    assert response.status_code == 302
    assert response.url == "/statistics"
    correction = ItemMetadataOverride.objects.get(user=user, item=movie_item)
    assert (correction.title, correction.runtime) == ("My movie", 125)
    movie_item.refresh_from_db()
    assert (movie_item.title, movie_item.runtime) == ("Original movie", 90)
    effective = display_metadata(movie_metadata, user)
    assert effective["title"] == "My movie"
    assert effective["details"]["runtime"] == "125 min"
    assert movie_metadata["details"]["runtime"] == "1h 30min"
    other = get_user_model().objects.create_user(username="other-viewer")
    assert display_metadata(movie_metadata, other) == movie_metadata


def test_sync_updates_originals_without_overwriting_personal_fields(client, user, movie_item, movie_metadata, provider):
    ItemMetadataOverride.objects.create(user=user, item=movie_item, title="Personal", runtime=105)
    client.force_login(user)
    movie_metadata["title"] = "Fresh from TMDB"
    movie_metadata["image"] = "https://example.com/new.jpg"
    with patch("app.views.cache.ttl", return_value=0), patch.object(Item, "fetch_releases"):
        response = client.post(reverse("sync_metadata", args=["tmdb", "movie", "123"]), {"next": "/"})
    assert response.status_code == 302
    movie_item.refresh_from_db()
    assert movie_item.title == "Fresh from TMDB"
    effective = display_metadata(movie_item, user)
    assert effective.title == "Personal"
    assert effective.runtime == 105
    assert effective.image == "https://example.com/new.jpg"


def test_untracked_episode_can_be_corrected_before_tracking(client, user, provider):
    provider.return_value = {
        "title": "Series", "episode_title": "Pilot", "image": "https://example.com/episode.jpg", "runtime": 42,
    }
    client.force_login(user)
    response = client.post(edit_url("episode") + "?season_number=0&episode_number=1", {
        "title": "Special pilot", "image": "", "runtime": "55",
    })
    assert response.status_code == 302
    item = Item.objects.get(media_type="episode", season_number=0, episode_number=1)
    assert item.title == "Pilot"
    assert item.runtime == 42
    assert ItemMetadataOverride.objects.get(user=user, item=item).runtime == 55
    assert not Episode.objects.exists()


def test_unchanged_fields_follow_provider_and_blank_fields_restore(client, user, movie_item, provider):
    client.force_login(user)
    client.post(edit_url(), {"title": "Personal", "image": movie_item.image, "runtime": "90"})
    correction = ItemMetadataOverride.objects.get(user=user, item=movie_item)
    assert correction.title == "Personal"
    assert correction.image is None
    assert correction.runtime is None
    client.post(edit_url(), {"title": "", "image": "", "runtime": ""})
    assert not ItemMetadataOverride.objects.exists()


def test_reset_preserves_original_item_and_tracking(client, user, movie_item, provider):
    Movie.objects.bulk_create([Movie(user=user, item=movie_item, status="Planning")])
    ItemMetadataOverride.objects.create(user=user, item=movie_item, runtime=130)
    client.force_login(user)
    response = client.post(edit_url(), {"action": "reset", "next": "/statistics"})
    assert response.url == "/statistics"
    assert not ItemMetadataOverride.objects.exists()
    assert Movie.objects.filter(user=user, item=movie_item).exists()
    movie_item.refresh_from_db()
    assert movie_item.runtime == 90


def test_accepting_provider_runtime_fills_missing_catalog_duration(client, user, movie_item, provider):
    Item.objects.filter(pk=movie_item.pk).update(runtime=None)
    client.force_login(user)
    response = client.post(edit_url(), {
        "title": movie_item.title, "image": movie_item.image, "runtime": "90",
    })
    assert response.status_code == 302
    movie_item.refresh_from_db()
    assert movie_item.runtime == 90
    assert not ItemMetadataOverride.objects.exists()


@pytest.mark.parametrize("payload", [{"runtime": "-1"}, {"runtime": "0"}, {"image": "ftp://example.com/cover.jpg"}])
def test_invalid_values_are_rejected(client, user, movie_item, provider, payload):
    client.force_login(user)
    response = client.post(edit_url(), payload)
    assert response.status_code == 200
    assert response.context["form"].errors
    assert not ItemMetadataOverride.objects.exists()


def test_edit_requires_login_and_rejects_unsafe_redirect(client, user, provider):
    assert client.get(edit_url()).status_code == 302
    client.force_login(user)
    response = client.post(edit_url(), {"title": "Corrected", "next": "https://other.example/"})
    assert response.url == reverse("home")
    assert client.get(edit_url("tv")).status_code == 404
    assert client.get(edit_url("episode") + "?season_number=-1&episode_number=0").status_code == 404


def test_manual_movie_of_another_user_cannot_be_edited(client, user):
    Item.objects.create(source="manual", media_type="movie", media_id="7", title="Private", image="https://example.com/a.jpg")
    client.force_login(user)
    assert client.post(edit_url(source="manual", media_id="7"), {"title": "Other"}).status_code == 404


def test_statistics_use_runtime_even_when_catalog_runtime_is_missing(user, movie_item):
    now = timezone.now()
    Item.objects.filter(pk=movie_item.pk).update(runtime=None)
    Movie.objects.bulk_create([Movie(user=user, item=movie_item, status="Completed", end_date=now)])
    ItemMetadataOverride.objects.create(user=user, item=movie_item, runtime=120, title="Corrected movie")
    series = statistics.get_watch_time_timeseries(user, now, now)
    assert sum(series["datasets"][0]["data"]) == 120
    assert statistics.calculate_day_of_week_stats(user, None, None)[1] == 100
    user_media, _, _, minutes = statistics.get_user_media(user, None, None)
    assert minutes == 120
    pie = statistics.get_watch_time_distribution_pie_chart_data(user_media, user)
    assert sum(pie["datasets"][0]["data"]) == 120
    timeline = statistics.get_timeline(user_media, user=user)
    entry = next(iter(timeline.values()))[0]
    assert entry.runtime == 120
    assert entry.item.title == "Corrected movie"


def test_episode_runtime_is_scoped_to_user_and_specific_episode(user):
    tv_item = Item.objects.create(source="manual", media_type="tv", media_id="9", title="Series")
    tv = TV(user=user, item=tv_item, status="In progress")
    TV.objects.bulk_create([tv])
    season_item = Item.objects.create(source="manual", media_type="season", media_id="9", title="Series", season_number=1)
    season = Season(user=user, item=season_item, related_tv=tv, status="In progress")
    Season.objects.bulk_create([season])
    item = Item.objects.create(source="manual", media_type="episode", media_id="9", title="Pilot", season_number=1, episode_number=1)
    Episode.objects.bulk_create([Episode(related_season=season, item=item, end_date=timezone.now())])
    ItemMetadataOverride.objects.create(user=user, item=item, runtime=45)
    episodes = Episode.objects.filter(related_season__user=user)
    assert episodes.aggregate(total=Sum(runtime_expression(user)))["total"] == 45
    other = get_user_model().objects.create_user(username="another")
    assert episodes.aggregate(total=Sum(runtime_expression(other)))["total"] is None
    series = statistics.get_watch_time_timeseries(user, None, None)
    assert sum(series["datasets"][0]["data"]) == 45


def test_movie_list_search_and_order_use_corrected_title(user, movie_item):
    Movie.objects.bulk_create([Movie(user=user, item=movie_item, status="Planning")])
    ItemMetadataOverride.objects.create(user=user, item=movie_item, title="My correction")
    rows = BasicMedia.objects.get_media_list(user, "movie", MediaStatusChoices.ALL, "title", search="correction")
    assert [row.item_id for row in rows] == [movie_item.pk]


def test_movie_detail_and_form_render_corrected_values(client, user, movie_item, provider):
    ItemMetadataOverride.objects.create(user=user, item=movie_item, title="Custom title", image="https://example.com/custom.jpg", runtime=100)
    client.force_login(user)
    response = client.get(reverse("media_details", args=["tmdb", "movie", "123", "original"]))
    assert response.status_code == 200
    assert response.context["media"]["title"] == "Custom title"
    assert b"Edit metadata" in response.content
    response = client.get(edit_url())
    assert response.status_code == 200
    assert response.context["form"]["runtime"].value() == 100
    assert b"Restore original values" in response.content


def test_movie_grid_table_and_home_render_personal_metadata(client, user, movie_item, provider):
    Movie.objects.bulk_create([Movie(user=user, item=movie_item, status="In progress")])
    ItemMetadataOverride.objects.create(user=user, item=movie_item, title="My visible title", image="https://example.com/custom.jpg")
    client.force_login(user)
    for layout in ("grid", "table"):
        response = client.get(reverse("medialist", args=["movie"]), {"layout": layout, "status": "All"})
        assert response.status_code == 200
        assert b"My visible title" in response.content
        assert b"https://example.com/custom.jpg" in response.content
    response = client.get(reverse("home"))
    assert response.status_code == 200
    assert b"My visible title" in response.content


def test_season_cards_use_corrected_episode_metadata_without_mutating_provider(client, user, provider):
    item = Item.objects.create(source="tmdb", media_type="episode", media_id="123", season_number=1, episode_number=1, title="Pilot", image="https://example.com/base.jpg")
    ItemMetadataOverride.objects.create(user=user, item=item, title="Corrected pilot", image="https://example.com/custom.jpg", runtime=55)
    episode = dict(source="tmdb", media_type="episode", media_id="123", season_number=1, episode_number=1, title="Pilot", image=item.image, runtime=42)
    season = dict(source="tmdb", media_type="season", media_id="123", season_number=1, title="Series", season_title="Season 1", image=item.image, episodes=[])
    provider.return_value = {"season/1": season}
    client.force_login(user)
    with patch("app.providers.tmdb.process_episodes", return_value=[episode]):
        response = client.get(reverse("season_details", args=["tmdb", "123", "series", 1]))
    assert response.status_code == 200
    assert b"Corrected pilot" in response.content
    assert b"55 min" in response.content
    assert b"Edit metadata" in response.content
    assert episode["title"] == "Pilot"
    assert season["episodes"] == []


def test_tmdb_episode_cards_use_numeric_minutes():
    season = {
        "media_id": "123", "season_number": 1,
        "episodes": [{"episode_number": 1, "name": "Pilot", "runtime": 77,
                      "air_date": "2026-01-01", "still_path": None, "overview": ""}],
    }
    with patch("app.providers.tmdb.fetch_season_multilingual", return_value={}):
        episodes = tmdb.process_episodes(season, [])
    assert episodes[0]["runtime"] == 77


@pytest.fixture(params=["tmdb", "manual"])
def poster_season(user, request):
    source = request.param
    tv_item = Item.objects.create(source=source, media_type="tv", media_id="123", title="Series")
    tv = TV(user=user, item=tv_item, status="Planning")
    TV.objects.bulk_create([tv])
    item = Item.objects.create(
        source=source, media_type="season", media_id="123", season_number=0,
        title="Series", image="https://example.com/original-season.jpg",
    )
    season = Season(user=user, item=item, related_tv=tv, status="Planning")
    Season.objects.bulk_create([season])
    return season


def poster_payload(season, **changes):
    return {
        "instance_id": str(season.pk), "media_type": "season",
        "source": season.item.source, "media_id": season.item.media_id,
        "season_number": season.item.season_number, "status": "Planning", "notes": "",
        **changes,
    }


def test_season_tracking_modal_saves_personal_poster_and_status(client, user, poster_season):
    season = poster_season
    client.force_login(user)
    response = client.get(reverse("track_modal", args=[season.item.source, "season", "123", 0]), {
        "return_url": "/", "instance_id": season.pk,
    })
    assert response.status_code == 200
    assert b"Season poster URL" in response.content
    assert b"Season poster preview" in response.content
    assert response.context["form"]["poster_url"].value() == ""
    assert response.context["original_poster"] == season.item.image
    with patch.object(Item, "fetch_releases"):
        response = client.post(reverse("media_save"), poster_payload(
            season, status="Paused", poster_url="https://example.com/my-season.jpg",
        ))
    assert response.status_code == 302
    season.refresh_from_db()
    assert season.status == "Paused"
    correction = ItemMetadataOverride.objects.get(user=user, item=season.item)
    assert correction.image == "https://example.com/my-season.jpg"
    assert season.item.image == "https://example.com/original-season.jpg"
    other = get_user_model().objects.create_user(username="other-season-viewer")
    assert display_metadata(season.item, other).image == season.item.image
    response = client.get(reverse("track_modal", args=[season.item.source, "season", "123", 0]), {
        "return_url": "/", "instance_id": season.pk,
    })
    assert response.context["form"]["poster_url"].value() == correction.image


def test_tracking_poster_survives_season_sync_and_renders_in_series_and_season(
    client, user, poster_season, provider,
):
    item = poster_season.item
    ItemMetadataOverride.objects.create(user=user, item=item, image="https://example.com/personal-season.jpg")
    metadata = {
        "source": item.source, "media_type": "season", "media_id": "123",
        "season_number": 0, "title": "Series", "season_title": "Specials",
        "image": "https://example.com/new-original.jpg", "episodes": [],
    }
    client.force_login(user)
    if item.source == "tmdb":
        provider.return_value = metadata
        with patch("app.views.cache.ttl", return_value=0), patch.object(Item, "fetch_releases"), patch(
            "app.views.tmdb.process_episodes", return_value=[],
        ):
            response = client.post(reverse("sync_metadata", args=["tmdb", "season", "123", 0]), {"next": "/"})
        assert response.status_code == 302
        item.refresh_from_db()
        assert item.image == metadata["image"]
    provider.return_value = {"season/0": metadata}
    with patch("app.views.tmdb.process_episodes", return_value=[]), patch("app.views.manual.process_episodes", return_value=[]):
        response = client.get(reverse("season_details", args=[item.source, "123", "series", 0]))
    assert response.status_code == 200
    assert response.context["media"]["image"] == "https://example.com/personal-season.jpg"
    assert metadata["image"] == "https://example.com/new-original.jpg"
    provider.return_value = {
        "source": item.source, "media_type": "tv", "media_id": "123", "title": "Series",
        "image": "https://example.com/tv.jpg", "related": {"seasons": [metadata]},
    }
    response = client.get(reverse("media_details", args=[item.source, "tv", "123", "series"]))
    assert response.status_code == 200
    assert b"https://example.com/personal-season.jpg" in response.content
    assert metadata["image"] == "https://example.com/new-original.jpg"


def test_blank_poster_restores_original_but_omitted_field_preserves_correction(client, user, poster_season):
    season = poster_season
    ItemMetadataOverride.objects.create(user=user, item=season.item, image="https://example.com/personal.jpg")
    client.force_login(user)
    with patch.object(Item, "fetch_releases"):
        client.post(reverse("media_save"), poster_payload(season))
        assert ItemMetadataOverride.objects.get(user=user, item=season.item).image == "https://example.com/personal.jpg"
        response = client.post(reverse("media_save"), poster_payload(season, poster_url=""))
    assert response.status_code == 302
    assert not ItemMetadataOverride.objects.filter(user=user, item=season.item).exists()
    assert Season.objects.filter(pk=season.pk).exists()


def test_invalid_poster_does_not_update_tracking(client, user, poster_season):
    client.force_login(user)
    response = client.post(reverse("media_save"), poster_payload(
        poster_season, status="Paused", poster_url="ftp://example.com/image.jpg",
    ))
    assert response.status_code == 302
    poster_season.refresh_from_db()
    assert poster_season.status == "Planning"
    assert not ItemMetadataOverride.objects.exists()


def test_untracked_season_can_set_poster_when_added(client, user, poster_season, provider):
    source = poster_season.item.source
    provider.return_value = {
        "source": source, "media_type": "season", "media_id": "123", "season_number": 2,
        "title": "Series", "image": "https://example.com/season-two.jpg", "episodes": [],
    }
    client.force_login(user)
    response = client.get(reverse("track_modal", args=[source, "season", "123", 2]), {"return_url": "/"})
    assert response.status_code == 200
    assert "poster_url" in response.context["form"].fields
    with patch.object(Item, "fetch_releases"):
        response = client.post(reverse("media_save"), poster_payload(
            poster_season, instance_id="", season_number=2, poster_url="https://example.com/custom-two.jpg",
        ))
    assert response.status_code == 302
    season = Season.objects.get(user=user, item__season_number=2)
    assert season.item.image == "https://example.com/season-two.jpg"
    assert ItemMetadataOverride.objects.get(user=user, item=season.item).image == "https://example.com/custom-two.jpg"


def test_youtube_seasons_do_not_offer_poster_editing():
    assert "poster_url" not in SeasonForm(initial={"source": "youtube"}).fields
