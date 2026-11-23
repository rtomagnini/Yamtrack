import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from app.models import (
    TV,
    Anime,
    BasicMedia,
    Episode,
    Item,
    MediaTypes,
    Movie,
    Season,
    Sources,
    Status,
)
from app.templatetags import app_tags
from events.calendar import date_parser
from events.models import Event
from users.models import HomeSortChoices


def _create_home_season(
    user,
    media_id,
    episode_numbers,
    *,
    watched=(),
    source=Sources.MANUAL.value,
):
    fields = {"media_id": media_id, "source": source, "title": f"Show {media_id}"}
    tv_item = Item.objects.create(**fields, media_type=MediaTypes.TV.value)
    tv = TV.objects.bulk_create(
        [TV(user=user, item=tv_item, status=Status.IN_PROGRESS.value)]
    )[0]
    season_item = Item.objects.create(
        **fields, media_type=MediaTypes.SEASON.value, season_number=1
    )
    season = Season.objects.bulk_create(
        [
            Season(
                user=user,
                item=season_item,
                related_tv=tv,
                status=Status.IN_PROGRESS.value,
            ),
        ]
    )[0]
    episodes = Item.objects.bulk_create(
        [
            Item(
                **fields,
                media_type=MediaTypes.EPISODE.value,
                season_number=1,
                episode_number=number,
            )
            for number in episode_numbers
        ]
    )
    Episode.objects.bulk_create(
        [
            Episode(item=episode, related_season=season, end_date=timezone.now())
            for episode in episodes
            if episode.episode_number in watched
        ]
    )
    return season


class HomeViewTests(TestCase):
    """Test Home sections and their progress controls."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="test")
        self.client.force_login(self.user)
        self.season = _create_home_season(
            self.user, "1668", range(1, 7), watched=(1, 2, 4)
        )
        self.progress_url = reverse(
            "progress_edit", args=[MediaTypes.SEASON.value, self.season.pk]
        )

    def test_cannot_skip_watched_or_another_users_episode(self):
        url = reverse("episode_skip", args=[self.season.pk])
        response = self.client.post(url, {"skip_action": "skip", "episode_number": 1})
        self.assertEqual(response.status_code, 400)
        another_user = get_user_model().objects.create_user(username="another")
        self.client.force_login(another_user)
        response = self.client.post(url, {"skip_action": "skip", "episode_number": 3})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(self.season.skipped_episodes.exists())

    def test_home_view(self):
        manager = BasicMedia.objects
        with patch.object(
            manager, "get_media_list", wraps=manager.get_media_list
        ) as query:
            response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/home.html")
        self.assertEqual(response.context["sort_choices"], HomeSortChoices.choices)
        self.assertEqual(
            sum(
                call.kwargs["media_type"] == MediaTypes.SEASON.value
                for call in query.call_args_list
            ),
            1,
        )
        season = response.context["list_by_type"][MediaTypes.SEASON.value]["items"][0]
        self.assertEqual(season.progress, 3)
        self.assertEqual(season.max_progress, 6)
        self.assertEqual(season.pending_episode_numbers, [3, 5, 6])
        self.assertContains(response, "3 / 6")

    def test_home_poster_loading_and_responsive_sizes(self):
        tmdb_poster = "https://image.tmdb.org/t/p/w500/test.jpg"
        custom_poster = "https://example.com/custom.jpg"
        Item.objects.filter(pk=self.season.item_id).update(image=tmdb_poster)
        second = _create_home_season(self.user, "2000", [1])
        Item.objects.filter(pk=second.item_id).update(image=custom_poster)
        third = _create_home_season(self.user, "3000", [1])
        Item.objects.filter(pk=third.item_id).update(image=tmdb_poster)

        response = self.client.get(reverse("home"), {"sort": "title"})

        self.assertContains(response, 'loading="eager"', count=2)
        self.assertContains(response, 'fetchpriority="high"', count=1)
        self.assertContains(response, 'loading="lazy"', count=1)
        self.assertContains(response, 'data-sizes="auto"', count=1)
        self.assertContains(response, 'data-expand="200"', count=1)
        self.assertContains(response, "https://image.tmdb.org/t/p/w342/test.jpg 342w", count=2)
        self.assertContains(response, f'src="{custom_poster}"')
        self.assertNotContains(response, f'srcset="{custom_poster}')

    def test_home_view_with_sort(self):
        one_pending = _create_home_season(self.user, "42", [1])
        for sort, first in [
            ("completion", self.season),
            ("episodes_left", one_pending),
        ]:
            with self.subTest(sort=sort):
                response = self.client.get(reverse("home"), {"sort": sort})
                self.assertEqual(response.context["current_sort"], sort)
                ordered = response.context["list_by_type"][MediaTypes.SEASON.value][
                    "items"
                ]
                self.assertEqual(ordered[0].pk, first.pk)
                self.user.refresh_from_db()
                self.assertEqual(self.user.home_sort, sort)

    def test_home_reads_calendar_releases_with_tmdb_unavailable(self):
        season = _create_home_season(
            self.user, "97", [1], watched=(1,), source=Sources.TMDB.value,
        )
        season.broadcast_time = datetime.time(20)
        Season.objects.filter(pk=season.pk).update(broadcast_time=season.broadcast_time)
        local_now = timezone.localtime()
        local_date = local_now.date()
        Event.objects.bulk_create([
            Event(
                item=season.item, content_number=2,
                datetime=datetime.datetime.min.replace(tzinfo=datetime.UTC),
            ),
            Event(
                item=season.item, content_number=3,
                datetime=date_parser(local_date.isoformat()),
            ),
            Event(
                item=season.item, content_number=4,
                datetime=date_parser((local_date + datetime.timedelta(days=1)).isoformat()),
            ),
        ])

        with patch("app.providers.tmdb.tv_with_seasons", side_effect=OSError) as tmdb:
            for hour, pending in [(19, [2]), (21, [2, 3])]:
                with (
                    self.subTest(hour=hour),
                    patch("app.models.timezone.now", return_value=local_now.replace(hour=hour)),
                ):
                    response = self.client.get(reverse("home"))
                    self.assertEqual(response.status_code, 200)
                    entries = response.context["list_by_type"][MediaTypes.SEASON.value]["items"]
                    entry = next(media for media in entries if media.pk == season.pk)
                    self.assertEqual(entry.pending_episode_numbers, pending)
                    self.assertEqual(entry.max_progress, 1 + len(pending))
                    self.assertFalse(entry.completes_on_next_episode)
        tmdb.assert_not_called()

    def test_unsynced_tmdb_season_still_shows_saved_progress(self):
        season = _create_home_season(
            self.user, "98", [1], watched=(1,), source=Sources.TMDB.value,
        )

        with patch("app.providers.tmdb.tv_with_seasons", side_effect=OSError) as tmdb:
            response = self.client.get(reverse("home"))

        tmdb.assert_not_called()
        self.assertContains(response, f'id="progress-season-{season.pk}"')
        self.assertContains(response, ">1 watched<")
        entries = response.context["list_by_type"][MediaTypes.SEASON.value]["items"]
        entry = next(media for media in entries if media.pk == season.pk)
        self.assertIsNone(entry.max_progress)
        self.assertEqual(entry.pending_episode_numbers, [])

    def test_home_view_htmx_load_more(self):
        for i in range(31):
            _create_home_season(self.user, str(2000 + i), [1, 2])

        response = self.client.get(reverse("home"), {"sort": "title"})
        page = response.context["list_by_type"]["season"]
        self.assertEqual(page["shown"], 14)
        self.assertContains(response, "Show more")
        seen = {item.pk for item in page["items"]}

        for batch_size in (8, 8, 2):
            response = self.client.get(
                reverse("home"),
                {
                    "load_media_type": "season",
                    "sort": "title",
                    "offset": page["shown"],
                    "signature": page["signature"],
                },
                headers={"HX-Request": "true"},
            )
            self.assertTemplateUsed(response, "app/components/home_more.html")
            self.assertContains(response, 'hx-swap-oob="beforeend:#media-grid-season"')
            self.assertContains(response, 'loading="lazy"')
            self.assertNotContains(response, 'fetchpriority="high"')
            page = response.context["media_list"]
            added = {item.pk for item in page["items"]}
            self.assertEqual(len(added), batch_size)
            self.assertTrue(seen.isdisjoint(added))
            seen.update(added)
            self.assertEqual(page["shown"], len(seen))

        self.assertEqual(len(seen), 32)
        self.assertNotContains(response, "Show more")

    def test_sort_fragment_and_progress_preserve_sections_loaded_count(self):
        for i in range(31):
            _create_home_season(self.user, str(2000 + i), [1, 2])

        response = self.client.get(
            reverse("home"),
            {"sort": "title", "shown_season": 22},
            headers={"HX-Request": "true", "HX-Target": "home-feed"},
        )
        self.assertTemplateUsed(response, "app/components/home_sorted.html")
        self.assertNotContains(response, "<html")
        self.assertContains(response, 'id="home-sort"')
        self.assertContains(response, 'hx-swap-oob="outerHTML"')
        self.assertEqual(response.headers["HX-Replace-Url"], f'{reverse("home")}?sort=title')
        self.assertEqual(response.context["list_by_type"]["season"]["shown"], 22)
        self.user.refresh_from_db()
        self.assertEqual(self.user.home_sort, "title")

        updated = self.client.post(
            self.progress_url,
            {
                "operation": "increase",
                "episode_number": 3,
                "home_section": "initial",
                "shown": 22,
                "sort": "title",
            },
        )
        self.assertEqual(updated.context["media_list"]["shown"], 22)
        self.assertEqual(len(updated.context["media_list"]["items"]), 22)
        self.assertContains(updated, "Show more")

    def test_load_more_refreshes_section_if_a_card_was_added_before_the_offset(self):
        for i in range(23):
            _create_home_season(self.user, str(2000 + i), [1, 2])
        initial = self.client.get(reverse("home"), {"sort": "title"})
        page = initial.context["list_by_type"]["season"]
        added = _create_home_season(self.user, "100", [1, 2])

        response = self.client.get(
            reverse("home"),
            {"load_media_type": "season", "sort": "title", "offset": 14, "signature": page["signature"]},
            headers={"HX-Request": "true"},
        )
        self.assertEqual(response.headers["HX-Retarget"], "#home-section-season")
        self.assertTemplateUsed(response, "app/components/home_section.html")
        refreshed = response.context["media_list"]["items"]
        self.assertEqual(len(refreshed), 22)
        self.assertEqual(refreshed[0].pk, added.pk)
        self.assertEqual(len({item.pk for item in refreshed}), 22)

    def test_progress_fills_the_first_gap_and_rejects_a_stale_retry(self):
        request = {
            "operation": "increase",
            "episode_number": 3,
            "home_section": "initial",
        }
        response = self.client.post(self.progress_url, request)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/home_section.html")
        self.assertContains(response, "4 / 6")
        self.assertContains(response, f'"return_url": "{reverse("home")}"')
        self.assertTrue(self.season.episodes.filter(item__episode_number=3).exists())

        retry = self.client.post(self.progress_url, request)
        self.assertEqual(retry.status_code, 409)
        self.assertFalse(self.season.episodes.filter(item__episode_number=5).exists())

    def test_last_available_episode_does_not_complete_a_season_with_future_releases(
        self,
    ):
        Item.objects.filter(
            media_id=self.season.item.media_id,
            media_type=MediaTypes.EPISODE.value,
            episode_number__in=[5, 6],
        ).update(air_date=timezone.localdate() + datetime.timedelta(days=1))
        response = self.client.post(
            self.progress_url,
            {
                "operation": "increase",
                "episode_number": 3,
                "home_section": "initial",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "home-section-season")
        self.season.refresh_from_db()
        self.assertEqual(self.season.status, Status.IN_PROGRESS.value)
        self.assertFalse(
            self.season.episodes.filter(item__episode_number__in=[5, 6]).exists()
        )

    def test_last_episode_requires_a_choice_and_updates_the_section(self):
        for idx, answer in enumerate(("no", "yes"), start=20):
            with self.subTest(answer=answer):
                season = _create_home_season(self.user, str(idx), [1, 4], watched=(4,))
                url = reverse(
                    "progress_edit", args=[MediaTypes.SEASON.value, season.pk]
                )
                request = {
                    "operation": "increase",
                    "episode_number": 1,
                    "home_section": "initial",
                }

                prompt = self.client.post(url, request)
                self.assertTrue(prompt.json()["requires_confirmation"])
                self.assertEqual(season.episodes.count(), 1)

                result = self.client.post(
                    url, {**request, "confirm_completion": answer}
                )
                self.assertEqual(result.status_code, 200)
                self.assertNotContains(result, f'id="progress-season-{season.pk}"')
                season.refresh_from_db()
                expected = Status.COMPLETED if answer == "yes" else Status.IN_PROGRESS
                self.assertEqual(season.status, expected)
                self.assertEqual(
                    season.episodes.filter(item__episode_number=1).count(), 1
                )

    def test_tmdb_watch_creates_missing_episode_item_offline(self):
        season = _create_home_season(
            self.user,
            "99",
            [1],
            watched=(1,),
            source=Sources.TMDB.value,
        )
        Event.objects.bulk_create([
            Event(item=season.item, content_number=number, datetime=timezone.now())
            for number in (1, 2, 3)
        ])
        url = reverse("progress_edit", args=[MediaTypes.SEASON.value, season.pk])
        data = {"operation": "increase", "episode_number": 2, "home_section": "initial"}
        with (
            patch("app.providers.tmdb.tv_with_seasons", side_effect=OSError) as tmdb,
            patch("app.providers.services.get_media_metadata", side_effect=OSError) as provider,
        ):
            result = self.client.post(url, data)
            retry = self.client.post(url, data)

        tmdb.assert_not_called()
        provider.assert_not_called()
        self.assertContains(result, "2 / 3")
        self.assertEqual(retry.status_code, 409)
        last_watch = season.episodes.get(item__episode_number=2)
        self.assertIsNotNone(last_watch.end_date)
        self.assertIsNone(last_watch.item.runtime)
        self.assertEqual(last_watch.history.count(), 1)

    def test_last_tmdb_episode_respects_the_choice_offline(self):
        with patch("app.providers.services.get_media_metadata", side_effect=OSError) as provider:
            for show, answer in enumerate(("no", "yes"), start=100):
                with self.subTest(answer=answer):
                    season = _create_home_season(
                        self.user, str(show), [1], watched=(1,), source=Sources.TMDB.value,
                    )
                    Event.objects.bulk_create([
                        Event(item=season.item, content_number=number, datetime=timezone.now())
                        for number in (1, 2)
                    ])
                    url = reverse("progress_edit", args=[MediaTypes.SEASON.value, season.pk])
                    data = {
                        "operation": "increase", "episode_number": 2, "home_section": "initial",
                    }
                    self.assertTrue(self.client.post(url, data).json()["requires_confirmation"])
                    self.assertEqual(season.episodes.count(), 1)

                    result = self.client.post(url, {**data, "confirm_completion": answer})
                    self.assertNotContains(result, f'id="progress-season-{season.pk}"')
                    season.refresh_from_db()
                    status = Status.COMPLETED if answer == "yes" else Status.IN_PROGRESS
                    self.assertEqual(season.status, status)
                    self.assertEqual(season.home_completion_pending, answer == "yes")
                    self.assertEqual(season.related_tv.status, Status.IN_PROGRESS.value)
                    self.assertEqual(season.episodes.get(item__episode_number=2).history.count(), 1)
        provider.assert_not_called()

    @override_settings(YOUTUBE_WATCH_SYNC_ENABLED=False)
    def test_youtube_progress_updates_the_pending_count_after_each_action(self):
        fields = {
            "media_id": "42",
            "source": Sources.YOUTUBE.value,
            "title": "Example channel",
        }
        channel_item = Item.objects.create(
            **fields, media_type=MediaTypes.YOUTUBE.value
        )
        channel = TV.objects.bulk_create(
            [
                TV(user=self.user, item=channel_item, status=Status.IN_PROGRESS.value),
            ]
        )[0]
        Item.objects.bulk_create(
            [
                Item(
                    **fields,
                    media_type=MediaTypes.EPISODE.value,
                    season_number=2025,
                    episode_number=i,
                )
                for i in (1, 2)
            ]
        )
        url = reverse("progress_edit", args=[MediaTypes.YOUTUBE.value, channel.pk])

        self.assertContains(self.client.get(reverse("home")), "2 videos")
        watched = self.client.post(
            url,
            {
                "operation": "increase",
                "pending_videos": 2,
                "home_section": "initial",
            },
        )
        self.assertEqual(watched.status_code, 200)
        self.assertContains(watched, 'data-pending-videos="1"')
        self.assertContains(self.client.get(reverse("home")), 'data-pending-videos="1"')
        self.assertTrue(
            Episode.objects.filter(
                related_season__related_tv=channel,
                item__episode_number=1,
            ).exists()
        )

        undo = self.client.post(
            url,
            {
                "operation": "decrease",
                "pending_videos": 1,
                "home_section": "initial",
            },
        )
        self.assertContains(undo, 'data-pending-videos="2"')

        for count in (2, 1):
            result = self.client.post(
                url,
                {
                    "operation": "increase",
                    "pending_videos": count,
                    "home_section": "initial",
                },
            )
            self.assertEqual(result.status_code, 200)
        self.assertNotContains(result, "home-section-youtube")
        self.assertNotContains(
            self.client.get(reverse("home")), 'id="home-section-youtube"'
        )


class MediaListViewTests(TestCase):
    """Test the media list view."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        movies_id = ["278", "238", "129", "424", "680"]
        num_completed = 3
        # Create some media items for testing
        for i in range(1, 6):
            item = Item.objects.create(
                media_id=movies_id[i - 1],
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                title=f"Test Movie {i}",
                image="http://example.com/image.jpg",
            )
            status = (
                Status.COMPLETED.value
                if i < num_completed
                else Status.IN_PROGRESS.value
            )
            Movie.objects.create(
                item=item,
                user=self.user,
                status=status,
                progress=1 if i < num_completed else 0,
                score=i,
            )

    def test_media_list_view(self):
        """Test the media list view displays media items."""
        response = self.client.get(reverse("medialist", args=[MediaTypes.MOVIE.value]))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/media_list.html")

        # Check that media items are in the context
        self.assertIn("media_list", response.context)
        self.assertEqual(response.context["media_list"].paginator.count, 5)

        # Check that filter options are in the context
        self.assertIn("sort_choices", response.context)
        self.assertIn("status_choices", response.context)
        self.assertEqual(response.context["media_type"], MediaTypes.MOVIE.value)
        self.assertEqual(
            response.context["media_type_plural"],
            app_tags.media_type_readable_plural(MediaTypes.MOVIE.value).lower(),
        )

    def test_media_list_with_filters(self):
        """Test the media list view with filters."""
        response = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value])
            + "?status=Completed&sort=score&layout=table",
        )

        self.assertEqual(response.status_code, 200)

        # Check that filters are applied
        self.assertEqual(
            response.context["current_status"],
            Status.COMPLETED.value,
        )
        self.assertEqual(response.context["current_sort"], "score")
        self.assertEqual(response.context["current_layout"], "table")

        # Check that only completed items are shown
        self.assertEqual(response.context["media_list"].paginator.count, 2)

        # Check that user preferences were updated
        self.user.refresh_from_db()
        self.assertEqual(self.user.movie_status, Status.COMPLETED.value)
        self.assertEqual(self.user.movie_sort, "score")
        self.assertEqual(self.user.movie_layout, "table")

    def test_media_list_htmx_request(self):
        """Test the media list view with HTMX request."""
        headers = {"HTTP_HX_REQUEST": "true"}

        # Test grid layout
        response = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value]) + "?layout=grid",
            **headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/media_grid_items.html")

        # Test table layout
        response = self.client.get(
            reverse("medialist", args=[MediaTypes.MOVIE.value]) + "?layout=table",
            **headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/media_table_items.html")


class MediaSearchViewTests(TestCase):
    """Test the media search view."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    @patch("app.providers.services.search")
    def test_media_search_view(self, mock_search):
        """Test the media search view."""
        # Mock the search results
        mock_search.return_value = {
            "page": 1,
            "total_results": 1,
            "total_pages": 1,
            "results": [
                {
                    "media_id": "238",
                    "title": "Test Movie",
                    "media_type": MediaTypes.MOVIE.value,
                    "source": Sources.TMDB.value,
                    "image": "http://example.com/image.jpg",
                },
            ],
        }

        response = self.client.get(
            reverse("search") + "?media_type=movie&q=test",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/search.html")

        # Check that user preference was updated
        self.user.refresh_from_db()
        self.assertEqual(self.user.last_search_type, MediaTypes.MOVIE.value)

        # Verify the search function was called with correct parameters
        mock_search.assert_called_once_with(MediaTypes.MOVIE.value, "test", 1, None)


class MediaDetailsViewTests(TestCase):
    """Test the media details views."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_view(self, mock_get_metadata):
        """Test the media details view."""
        # Mock the metadata
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "overview": "Test overview",
            "release_date": "2023-01-01",
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                    "title": "test-movie",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/media_details.html")

        # Check that media metadata is in the context
        self.assertIn("media", response.context)
        self.assertEqual(response.context["media"]["title"], "Test Movie")

        # Verify the get_media_metadata function was called with correct parameters
        mock_get_metadata.assert_called_once_with(
            MediaTypes.MOVIE.value,
            "238",
            Sources.TMDB.value,
        )

    @patch("app.providers.services.get_media_metadata")
    @patch("app.providers.tmdb.process_episodes")
    def test_season_details_view(self, mock_process_episodes, mock_get_metadata):
        """Test the season details view."""
        # Mock the metadata
        mock_get_metadata.return_value = {
            "title": "Test TV Show",
            "media_id": "1668",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "image": "http://example.com/image.jpg",
            "season/1": {
                "title": "Season 1",
                "media_id": "1668",
                "media_type": MediaTypes.SEASON.value,
                "source": Sources.TMDB.value,
                "image": "http://example.com/season.jpg",
                "episodes": [],
            },
        }

        # Mock the processed episodes
        mock_process_episodes.return_value = [
            {
                "media_id": "1668",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.EPISODE.value,
                "season_number": 1,
                "episode_number": 1,
                "name": "Episode 1",
                "air_date": "2023-01-01",
                "watched": False,
            },
        ]

        # Use the correct URL pattern for season_details
        response = self.client.get(
            reverse(
                "season_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "1668",
                    "title": "test-tv-show",
                    "season_number": 1,
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/media_details.html")

        # Check that season metadata is in the context
        self.assertIn("media", response.context)
        self.assertEqual(response.context["media"]["title"], "Season 1")
        self.assertEqual(len(response.context["media"]["episodes"]), 1)

        # Verify the get_media_metadata function was called with correct parameters
        mock_get_metadata.assert_called_once_with(
            "tv_with_seasons",
            "1668",
            Sources.TMDB.value,
            [1],
        )


class TrackModalViewTests(TestCase):
    """Test the track modal view."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        # Create a media item for testing
        self.item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
        )
        self.movie = Movie.objects.create(
            item=self.item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=0,
        )

    def test_track_modal_view_existing_media(self):
        """Test the track modal view for existing media."""
        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                },
            )
            + "?return_url=/home",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/fill_track.html")

        # Check that form and media are in the context
        self.assertIn("form", response.context)
        self.assertIn("media", response.context)
        self.assertEqual(response.context["media"], self.movie)
        self.assertEqual(response.context["return_url"], "/home")

    @patch("app.providers.services.get_media_metadata")
    def test_track_modal_view_new_media(self, mock_get_metadata):
        """Test the track modal view for new media."""
        # Mock the metadata
        mock_get_metadata.return_value = {
            "media_id": "278",
            "title": "New Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "max_progress": 1,
        }

        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "278",
                },
            )
            + "?return_url=/home&title=New+Movie",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/fill_track.html")

        # Check that form is in the context with initial data
        self.assertIn("form", response.context)
        self.assertEqual(response.context["form"].initial["media_id"], "278")
        self.assertEqual(
            response.context["form"].initial["media_type"],
            MediaTypes.MOVIE.value,
        )


class HistoryModalViewTests(TestCase):
    """Test the history modal view."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        # Create a media item with history
        self.item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
        )
        self.movie = Movie.objects.create(
            item=self.item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=0,
        )

        # Update the movie to create history
        self.movie.status = Status.COMPLETED.value
        self.movie.progress = 1
        self.movie.score = 8
        self.movie.save()

    def test_history_modal_view(self):
        """Test the history modal view."""
        response = self.client.get(
            reverse(
                "history_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                },
            )
            + "?return_url=/home",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/fill_history.html")

        # Check that timeline entries are in the context
        self.assertIn("timeline", response.context)
        self.assertGreater(len(response.context["timeline"]), 0)

        # Check that the first entry has changes
        first_entry = response.context["timeline"][0]
        self.assertIn("changes", first_entry)
        self.assertGreater(len(first_entry["changes"]), 0)


class DeleteHistoryRecordViewTests(TestCase):
    """Test the delete history record view."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        # Create a media item with history
        self.item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
        )
        self.movie = Movie.objects.create(
            item=self.item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=0,
        )

        # Update the movie to create history
        self.movie.status = Status.COMPLETED.value
        self.movie.progress = 1
        self.movie.score = 8
        self.movie.save()

        # Get the history record and manually set the history_user
        self.history = self.movie.history.first()
        self.history_id = self.history.history_id

        # Manually update the history_user field
        self.history.history_user = self.user
        self.history.save()

    def test_delete_history_record(self):
        """Test deleting a history record."""
        response = self.client.delete(
            reverse(
                "delete_history_record",
                kwargs={
                    "media_type": MediaTypes.MOVIE.value,
                    "history_id": self.history_id,
                },
            ),
        )

        self.assertEqual(response.status_code, 200)

        # Check that the history record was deleted
        self.assertEqual(
            self.movie.history.filter(history_id=self.history_id).count(),
            0,
        )

    def test_delete_nonexistent_history_record(self):
        """Test deleting a nonexistent history record."""
        response = self.client.delete(
            reverse(
                "delete_history_record",
                kwargs={
                    "media_type": MediaTypes.MOVIE.value,
                    "history_id": 999999,
                },
            ),
        )

        self.assertEqual(response.status_code, 404)


class StatisticsViewTests(TestCase):
    """Test the statistics view."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_statistics_view_default_date_range(self):
        """Test the statistics view with default date range (last year)."""
        # Call the view
        response = self.client.get(reverse("statistics"))

        # Check response
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/statistics.html")

        # Check that all expected context variables are present
        self.assertIn("media_count", response.context)
        self.assertIn("activity_data", response.context)
        self.assertIn("media_type_distribution", response.context)
        self.assertIn("score_distribution", response.context)
        self.assertIn("status_distribution", response.context)
        self.assertIn("status_pie_chart_data", response.context)
        self.assertIn("timeline", response.context)

    def test_statistics_view_custom_date_range(self):
        """Test the statistics view with custom date range."""
        # Custom date range
        start_date = "2023-01-01"
        end_date = "2023-12-31"

        # Call the view with custom date range
        response = self.client.get(
            reverse("statistics") + f"?start-date={start_date}&end-date={end_date}",
        )

        # Check response
        self.assertEqual(response.status_code, 200)

        # Check that all expected context variables are present
        self.assertIn("media_count", response.context)
        self.assertIn("activity_data", response.context)
        self.assertIn("media_type_distribution", response.context)
        self.assertIn("score_distribution", response.context)
        self.assertIn("status_distribution", response.context)
        self.assertIn("status_pie_chart_data", response.context)
        self.assertIn("timeline", response.context)

    def test_statistics_view_invalid_date_format(self):
        """Test the statistics view with invalid date format."""
        # Invalid date format
        start_date = "01/01/2023"  # MM/DD/YYYY instead of YYYY-MM-DD
        end_date = "2023/12/31"

        # Call the view with invalid date format
        response = self.client.get(
            reverse("statistics") + f"?start-date={start_date}&end-date={end_date}",
        )

        # If we get here, the view handled the invalid date format
        self.assertEqual(response.status_code, 200)

        date_is_none = (
            response.context["start_date"] is None
            and response.context["end_date"] is None
        )

        self.assertTrue(date_is_none)


class CreateMedia(TestCase):
    """Test the creation of media objects through views."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    @override_settings(MEDIA_ROOT=("create_media"))
    def test_create_anime(self):
        """Test the creation of a TV object."""
        Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Test Anime",
            image="http://example.com/image.jpg",
        )
        self.client.post(
            reverse("media_save"),
            {
                "media_id": "1",
                "source": Sources.MAL.value,
                "media_type": MediaTypes.ANIME.value,
                "status": Status.PLANNING.value,
                "progress": 0,
                "repeats": 0,
            },
        )
        self.assertEqual(
            Anime.objects.filter(item__media_id="1", user=self.user).exists(),
            True,
        )

    @override_settings(MEDIA_ROOT=("create_media"))
    def test_create_tv(self):
        """Test the creation of a TV object through views."""
        Item.objects.create(
            media_id="5895",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Friends",
            image="http://example.com/image.jpg",
        )
        self.client.post(
            reverse("media_save"),
            {
                "media_id": "5895",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.TV.value,
                "status": Status.PLANNING.value,
            },
        )
        self.assertEqual(
            TV.objects.filter(item__media_id="5895", user=self.user).exists(),
            True,
        )

    def test_create_season(self):
        """Test the creation of a Season through views."""
        Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
        )
        self.client.post(
            reverse("media_save"),
            {
                "media_id": "1668",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.SEASON.value,
                "season_number": 1,
                "status": Status.PLANNING.value,
            },
        )
        self.assertEqual(
            Season.objects.filter(item__media_id="1668", user=self.user).exists(),
            True,
        )

    def test_create_episodes(self):
        """Test the creation of Episode through views."""
        self.client.post(
            reverse("episode_save"),
            {
                "media_id": "1668",
                "season_number": 1,
                "episode_number": 1,
                "source": Sources.TMDB.value,
                "date": "2023-06-01T00:00",
            },
        )
        self.assertEqual(
            Episode.objects.filter(
                item__media_id="1668",
                related_season__user=self.user,
                item__episode_number=1,
            ).exists(),
            True,
        )


class EditMedia(TestCase):
    """Test the editing of media objects through views."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_edit_movie_score(self):
        """Test the editing of a movie score."""
        item = Item.objects.create(
            media_id="10494",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Perfect Blue",
            image="http://example.com/image.jpg",
        )
        movie = Movie.objects.create(
            item=item,
            user=self.user,
            score=9,
            progress=1,
            status=Status.COMPLETED.value,
            notes="Nice",
            start_date=datetime.datetime(2023, 6, 1, 0, 0, tzinfo=datetime.UTC),
            end_date=datetime.datetime(2023, 6, 1, 0, 0, tzinfo=datetime.UTC),
        )

        self.client.post(
            reverse("media_save"),
            {
                "instance_id": movie.id,
                "media_id": "10494",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "score": 10,
                "progress": 1,
                "status": Status.COMPLETED.value,
                "notes": "Nice",
            },
        )
        self.assertEqual(Movie.objects.get(item__media_id="10494").score, 10)


class DeleteMedia(TestCase):
    """Test the deletion of media objects through views."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        self.item_season = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
        )
        self.season = Season.objects.create(
            item=self.item_season,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        self.item_ep = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=1,
        )
        self.episode = Episode.objects.create(
            item=self.item_ep,
            related_season=self.season,
            end_date=datetime.datetime(2023, 6, 1, 0, 0, tzinfo=datetime.UTC),
        )

    def test_delete_tv(self):
        """Test the deletion of a tv through views."""
        self.assertEqual(TV.objects.filter(user=self.user).count(), 1)
        tv_obj = TV.objects.get(user=self.user)

        self.client.post(
            reverse("media_delete"),
            data={
                "instance_id": tv_obj.id,
                "media_type": MediaTypes.TV.value,
            },
        )

        self.assertEqual(Movie.objects.filter(user=self.user).count(), 0)

    def test_delete_season(self):
        """Test the deletion of a season through views."""
        self.client.post(
            reverse(
                "media_delete",
            ),
            data={"instance_id": self.season.id, "media_type": MediaTypes.SEASON.value},
        )

        self.assertEqual(Season.objects.filter(user=self.user).count(), 0)
        self.assertEqual(
            Episode.objects.filter(related_season__user=self.user).count(),
            0,
        )

    def test_unwatch_episode(self):
        """Test unwatching of an episode through views."""
        self.client.post(
            reverse("media_delete"),
            data={
                "instance_id": self.episode.id,
                "media_type": MediaTypes.EPISODE.value,
            },
        )

        self.assertEqual(
            Episode.objects.filter(related_season__user=self.user).count(),
            0,
        )


class ProgressEditSeason(TestCase):
    """Test for editing a season progress through views."""

    def setUp(self):
        """Prepare the database with a season and an episode."""
        self.user = get_user_model().objects.create_user(username="test")
        self.client.force_login(self.user)
        self.season = _create_home_season(
            self.user, "1668", [1], watched=(1,), source=Sources.TMDB.value,
        )
        Event.objects.bulk_create([
            Event(item=self.season.item, content_number=number, datetime=timezone.now())
            for number in (1, 2, 3)
        ])

    def test_progress_increase(self):
        """Test the increase of progress for a season."""
        self.client.post(
            reverse(
                "progress_edit",
                kwargs={
                    "media_type": MediaTypes.SEASON.value,
                    "instance_id": self.season.id,
                },
            ),
            {
                "operation": "increase",
            },
        )

        self.assertEqual(
            Episode.objects.filter(item__media_id="1668").count(),
            2,
        )

        # episode with media_id "1668" and episode_number 2 should exist
        self.assertTrue(
            Episode.objects.filter(
                item__media_id="1668",
                item__episode_number=2,
            ).exists(),
        )

    def test_progress_decrease(self):
        """Test the decrease of progress for a season."""
        self.client.post(
            reverse(
                "progress_edit",
                kwargs={
                    "media_type": MediaTypes.SEASON.value,
                    "instance_id": self.season.id,
                },
            ),
            {
                "operation": "decrease",
            },
        )

        self.assertEqual(
            Episode.objects.filter(item__media_id="1668").count(),
            0,
        )


class ProgressEditAnime(TestCase):
    """Test for editing an anime progress through views."""

    def setUp(self):
        """Prepare the database with an anime."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        self.item = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Cowboy Bebop",
            image="http://example.com/image.jpg",
        )
        self.anime = Anime.objects.create(
            item=self.item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=2,
        )

    def test_progress_increase(self):
        """Test the increase of progress for an anime."""
        self.client.post(
            reverse(
                "progress_edit",
                kwargs={
                    "media_type": MediaTypes.ANIME.value,
                    "instance_id": self.anime.id,
                },
            ),
            {
                "operation": "increase",
            },
        )

        self.assertEqual(Anime.objects.get(item__media_id="1").progress, 3)

    def test_progress_decrease(self):
        """Test the decrease of progress for an anime."""
        self.client.post(
            reverse(
                "progress_edit",
                kwargs={
                    "media_type": MediaTypes.ANIME.value,
                    "instance_id": self.anime.id,
                },
            ),
            {
                "operation": "decrease",
            },
        )

        self.assertEqual(Anime.objects.get(item__media_id="1").progress, 1)


class CreateEntryViewTests(TestCase):
    """Test the create entry view."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_create_entry_get(self):
        """Test the GET method of create_entry view."""
        response = self.client.get(reverse("create_entry"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/create_entry.html")
        self.assertIn("media_types", response.context)

        # Verify media_types contains expected values
        self.assertEqual(response.context["media_types"], MediaTypes.values)

    def test_create_entry_post_movie(self):
        """Test creating a movie entry."""
        form_data = {
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "status": Status.COMPLETED.value,
            "score": 8,
            "progress": 1,
            "start_date": "2023-01-01T00:00",
            "end_date": "2023-01-02T00:00",
        }

        response = self.client.post(reverse("create_entry"), form_data, follow=True)

        # Check redirect
        self.assertRedirects(response, reverse("create_entry"))

        # Verify item was created
        self.assertTrue(
            Item.objects.filter(
                title="Test Movie",
                media_type=MediaTypes.MOVIE.value,
            ).exists(),
        )

        # Verify media was created
        movie = Movie.objects.get(item__title="Test Movie")
        self.assertEqual(movie.status, Status.COMPLETED.value)
        self.assertEqual(movie.score, 8)
        self.assertEqual(movie.progress, 1)
        self.assertEqual(movie.user, self.user)

    def test_create_entry_post_tv(self):
        """Test creating a TV show entry."""
        form_data = {
            "title": "Test TV Show",
            "media_type": MediaTypes.TV.value,
            "status": Status.IN_PROGRESS.value,
            "score": 7,
        }

        response = self.client.post(reverse("create_entry"), form_data, follow=True)

        # Check redirect
        self.assertRedirects(response, reverse("create_entry"))

        # Verify item was created
        self.assertTrue(
            Item.objects.filter(
                title="Test TV Show",
                media_type=MediaTypes.TV.value,
            ).exists(),
        )

        # Verify media was created
        tv = TV.objects.get(item__title="Test TV Show")
        self.assertEqual(tv.status, Status.IN_PROGRESS.value)
        self.assertEqual(tv.score, 7)
        self.assertEqual(tv.user, self.user)

    def test_create_entry_post_season(self):
        """Test creating a season entry with parent TV."""
        # First create a parent TV show
        tv_item = Item.objects.create(
            media_id="1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.TV.value,
            title="TV Show",
        )
        parent_tv = TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        form_data = {
            "title": "TV Show",
            "media_type": MediaTypes.SEASON.value,
            "season_number": 1,
            "parent_tv": parent_tv.id,
            "status": Status.IN_PROGRESS.value,
            "score": 7,
        }

        response = self.client.post(reverse("create_entry"), form_data, follow=True)

        # Check redirect
        self.assertRedirects(response, reverse("create_entry"))

        # Verify item was created
        self.assertTrue(
            Item.objects.filter(
                title="TV Show",
                media_type=MediaTypes.SEASON.value,
                season_number=1,
            ).exists(),
        )

        # Verify media was created with correct relationship
        season = Season.objects.get(item__title="TV Show")
        self.assertEqual(season.status, Status.IN_PROGRESS.value)
        self.assertEqual(season.score, 7)
        self.assertEqual(season.user, self.user)
        self.assertEqual(season.related_tv, parent_tv)

    def test_create_entry_post_episode(self):
        """Test creating an episode entry with parent season."""
        # First create a parent TV show and season
        tv_item = Item.objects.create(
            media_id="1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.TV.value,
            title="TV Show",
        )
        parent_tv = TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        season_item = Item.objects.create(
            media_id="1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.SEASON.value,
            title="TV Show",
            season_number=1,
        )
        parent_season = Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=parent_tv,
            status=Status.IN_PROGRESS.value,
        )

        form_data = {
            "title": "TV Show",
            "media_type": MediaTypes.EPISODE.value,
            "season_number": 1,
            "episode_number": 1,
            "parent_season": parent_season.id,
            "end_date": "2023-01-02T00:00",
        }

        response = self.client.post(reverse("create_entry"), form_data, follow=True)

        # Check redirect
        self.assertRedirects(response, reverse("create_entry") + "?media_type=episode")

        # Verify item was created
        self.assertTrue(
            Item.objects.filter(
                title="TV Show",
                media_type=MediaTypes.EPISODE.value,
                season_number=1,
                episode_number=1,
            ).exists(),
        )

        # Creating a custom episode adds its catalog entry, not a viewing.
        episode_item = Item.objects.get(
            title="TV Show", media_type=MediaTypes.EPISODE.value,
        )
        self.assertEqual(episode_item.media_id, parent_season.item.media_id)
        self.assertEqual(episode_item.season_number, parent_season.item.season_number)
        self.assertFalse(Episode.objects.filter(item=episode_item).exists())

    def test_create_entry_post_duplicate_item(self):
        """Test creating a duplicate item."""
        # First create an item
        tv_item = Item.objects.create(
            media_id="1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.TV.value,
            title="TV Show",
        )
        parent_tv = TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        season_item = Item.objects.create(
            media_id="1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.SEASON.value,
            title="TV Show",
            season_number=1,
        )
        Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=parent_tv,
            status=Status.IN_PROGRESS.value,
        )

        # Count items before the test
        initial_count = Item.objects.count()

        # Try to create the same season again
        form_data = {
            "title": "TV Show",
            "media_type": MediaTypes.SEASON.value,
            "season_number": 1,
            "parent_tv": parent_tv.id,
            "status": Status.IN_PROGRESS.value,
            "score": 7,
            "repeats": 0,
        }

        with transaction.atomic():
            self.client.post(reverse("create_entry"), form_data)

        # No new item should be created
        self.assertEqual(Item.objects.count(), initial_count)


class SearchParentViewTests(TestCase):
    """Test the parent search views."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        # Create some manual TV shows and seasons for testing
        tv_item1 = Item.objects.create(
            media_id="111",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.TV.value,
            title="Test TV Show",
        )
        self.tv1 = TV.objects.create(
            item=tv_item1,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        tv_item2 = Item.objects.create(
            media_id="222",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.TV.value,
            title="Another TV Show",
        )
        self.tv2 = TV.objects.create(
            item=tv_item2,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        season_item1 = Item.objects.create(
            media_id="111",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.SEASON.value,
            title="Test Season",
            season_number=1,
        )
        self.season1 = Season.objects.create(
            item=season_item1,
            user=self.user,
            related_tv=self.tv1,
            status=Status.IN_PROGRESS.value,
        )

        season_item2 = Item.objects.create(
            media_id="222",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.SEASON.value,
            title="Another Season",
            season_number=1,
        )
        self.season2 = Season.objects.create(
            item=season_item2,
            user=self.user,
            related_tv=self.tv2,
            status=Status.IN_PROGRESS.value,
        )

    def test_search_parent_tv_short_query(self):
        """Test search_parent_tv with a query that's too short."""
        response = self.client.get(reverse("search_parent_tv") + "?q=T")

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/search_parent_tv.html")
        self.assertNotIn("results", response.context)

    def test_search_parent_tv_valid_query(self):
        """Test search_parent_tv with a valid query."""
        response = self.client.get(reverse("search_parent_tv") + "?q=Test")

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/search_parent_tv.html")
        self.assertIn("results", response.context)
        self.assertIn("query", response.context)

        # Should find the "Test TV Show"
        self.assertEqual(len(response.context["results"]), 1)
        self.assertEqual(response.context["results"][0], self.tv1)
        self.assertEqual(response.context["query"], "Test")

    def test_search_parent_tv_no_results(self):
        """Test search_parent_tv with a query that returns no results."""
        response = self.client.get(reverse("search_parent_tv") + "?q=NonExistent")

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/search_parent_tv.html")
        self.assertIn("results", response.context)

        # Should find no results
        self.assertEqual(len(response.context["results"]), 0)

    def test_search_parent_season_short_query(self):
        """Test search_parent_season with a query that's too short."""
        response = self.client.get(reverse("search_parent_season") + "?q=T")

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/search_parent_tv.html")
        self.assertNotIn("results", response.context)

    def test_search_parent_season_valid_query(self):
        """Test search_parent_season with a valid query."""
        response = self.client.get(reverse("search_parent_season") + "?q=Test")

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/search_parent_season.html")
        self.assertIn("results", response.context)
        self.assertIn("query", response.context)

        # Should find the "Test Season"
        self.assertEqual(len(response.context["results"]), 1)
        self.assertEqual(response.context["results"][0], self.season1)
        self.assertEqual(response.context["query"], "Test")

    def test_search_parent_season_no_results(self):
        """Test search_parent_season with a query that returns no results."""
        response = self.client.get(reverse("search_parent_season") + "?q=NonExistent")

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/search_parent_season.html")
        self.assertIn("results", response.context)

        # Should find no results
        self.assertEqual(len(response.context["results"]), 0)
