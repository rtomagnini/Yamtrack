import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect, sync_playwright

from app.models import Item, Manga, MediaTypes, Sources, Status
from app.tests.test_views import _create_home_season
from events.models import Event


class IntegrationTest(StaticLiveServerTestCase):
    """Integration tests for the application."""

    @classmethod
    def setUpClass(cls):
        """Set up the test class."""
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
        super().setUpClass()
        cls.playwright = sync_playwright().start()
        # use headless=False, slow_mo=200 to see the browser
        cls.browser = cls.playwright.chromium.launch()
        cls.page = cls.browser.new_page()

    def setUp(self):
        """Set up test data for CustomList model."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.page.goto(f"{self.live_server_url}/")
        self.page.get_by_placeholder("Enter your username").fill(
            self.credentials["username"],
        )
        self.page.get_by_placeholder("Enter your password").fill(
            self.credentials["password"],
        )
        self.page.get_by_role("button", name="Sign in").click()

    @classmethod
    def tearDownClass(cls):
        """Tear down the test class."""
        super().tearDownClass()
        cls.browser.close()
        cls.playwright.stop()

    def test_header_search_and_reload_on_mobile(self):
        with self.browser.new_context(
            viewport={"width": 320, "height": 720}, is_mobile=True, has_touch=True,
        ) as context:
            context.add_cookies(self.page.context.cookies())
            page = context.new_page()
            page.goto(f"{self.live_server_url}/")
            refresh = page.get_by_role("button", name="Recargar página")
            expect(refresh).to_be_in_viewport(ratio=1)
            with page.expect_navigation(timeout=5000):
                refresh.tap(timeout=5000)
            expect(page).to_have_url(f"{self.live_server_url}/")

            media_type = page.get_by_role("combobox", name="Media type")
            media_type.select_option("manga")
            query = page.get_by_role("searchbox", name="Search query")
            expect(query).to_have_attribute("placeholder", "Search manga...")
            query.fill("Berserk")
            search_url = f"{self.live_server_url}{reverse('search')}?q=Berserk&media_type=manga"
            page.route(search_url, lambda route: route.fulfill(body="Search received"))
            query.press("Enter")
            expect(page).to_have_url(search_url)

    @patch("app.providers.services.get_media_metadata", side_effect=OSError)
    def test_season_progress_edit(self, provider):
        """Track pending episodes from the Home without a page reload."""
        season = _create_home_season(
            self.user, "101", [1, 3], watched=(1, 3), source=Sources.TMDB.value,
        )
        Event.objects.bulk_create([
            Event(item=season.item, content_number=number, datetime=timezone.now())
            for number in (1, 2, 3, 4)
        ])
        self.page.goto(f"{self.live_server_url}/", wait_until="domcontentloaded")
        navigations = []

        def record_navigation(frame):
            if frame == self.page.main_frame:
                navigations.append(frame.url)

        self.page.on("framenavigated", record_navigation)
        section = self.page.locator("#home-section-season")
        watch = section.get_by_role("button", name="Watch next episode")
        try:
            expect(section.get_by_text("2 / 4", exact=True)).to_be_visible()
            watch.focus()
            self.page.keyboard.press("Enter")
            expect(section.get_by_text("3 / 4", exact=True)).to_be_visible()
            expect(watch).to_be_focused()
            watch.click()
            expect(section.get_by_text("Mark this season as completed?")).to_be_visible()
            section.get_by_role("button", name="No", exact=True).click()
            expect(section).to_have_count(0)
            expect(self.page.get_by_text("No media in progress")).to_be_visible()
        finally:
            self.page.remove_listener("framenavigated", record_navigation)

        self.assertEqual(navigations, [])
        provider.assert_not_called()
        self.assertEqual(
            set(season.episodes.values_list("item__episode_number", flat=True)),
            {1, 2, 3, 4},
        )

    def test_home_show_more_and_sort_without_navigation(self):
        """Retry a batch, then keep expanded sections when sorting and tracking."""
        for i in range(24):
            _create_home_season(self.user, str(2000 + i), [1, 2, 3])
        self.page.goto(f"{self.live_server_url}/?sort=recent", wait_until="domcontentloaded")
        time_origin = self.page.evaluate("performance.timeOrigin")
        grid = self.page.locator("#media-grid-season > div")
        pager = self.page.locator("#home-pager-season")
        more = pager.get_by_role("button", name="Show more TV Seasons")
        expect(grid).to_have_count(14)

        self.page.route(
            "**/*load_media_type=season*",
            lambda route: route.fulfill(status=503, body="Try again"),
            times=1,
        )
        more.click()
        expect(pager.get_by_role("alert")).to_contain_text("Could not load more")
        expect(grid).to_have_count(14)
        more.click()
        expect(grid).to_have_count(22)
        expect(pager.get_by_role("alert")).to_be_hidden()

        self.page.locator("#home-sort-button").click()
        self.page.evaluate("""() => {
          window.scrollTo(0, 60);
          document.addEventListener('htmx:beforeRequest', event => {
            if (event.detail.elt.matches('[data-home-sort-option]')) {
              window.sortScrollPosition = window.scrollY;
            }
          }, { once: true });
        }""")
        self.page.get_by_role("link", name="Title", exact=True).click()
        expect(self.page.locator("#home-sort-button")).to_have_text("Title")
        expect(self.page).to_have_url(f"{self.live_server_url}/?sort=title")
        expect(grid).to_have_count(22)
        expect(grid.first.get_by_role("link", name="Show 2000 S1")).to_be_visible()
        scroll_position = self.page.evaluate("window.sortScrollPosition")
        self.assertGreater(scroll_position, 0)
        self.page.wait_for_function("window.scrollY === window.sortScrollPosition", timeout=5000)

        grid.first.get_by_role("button", name="Watch next episode").click()
        expect(grid.first.get_by_text("1 / 3", exact=True)).to_be_visible()
        expect(grid).to_have_count(22)
        more.click()
        expect(grid).to_have_count(24)
        expect(more).to_have_count(0)

        ids = self.page.locator('[id^="progress-season-"]').evaluate_all(
            "(nodes) => nodes.map((node) => node.id)",
        )
        self.assertEqual(len(set(ids)), 24)
        self.assertEqual(self.page.evaluate("performance.timeOrigin"), time_origin)

    def test_home_card_actions_support_touch_and_keyboard(self):
        _create_home_season(self.user, "2000", [1, 2, 3], watched=(1,))
        with self.browser.new_context(
            viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True,
        ) as context:
            context.add_cookies(self.page.context.cookies())
            page = context.new_page()
            page.goto(f"{self.live_server_url}/", wait_until="domcontentloaded")
            actions = page.get_by_role("button", name="Actions for Show 2000 S1")
            expect(actions).to_have_attribute("aria-expanded", "false")
            expect(actions).to_have_css("opacity", "0")
            page.locator("[data-home-poster-link]").tap()
            expect(page).to_have_url(f"{self.live_server_url}/")
            expect(actions).to_have_css("opacity", "1")
            actions.tap()
            expect(page.get_by_role("button", name="Lists", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="History", exact=True)).to_be_visible()
            page.keyboard.press("Escape")
            expect(actions).to_be_focused()
            expect(page.get_by_role("button", name="Edit tracking")).to_be_hidden()

            page.keyboard.press("Enter")
            edit = page.get_by_role("button", name="Edit tracking")
            expect(edit).to_be_focused()
            page.keyboard.press("Enter")
            expect(page.get_by_label("Status", exact=True)).to_be_visible()
            page.keyboard.press("Escape")
            expect(actions).to_be_focused()
            bounds = page.get_by_role("button", name="Watch next episode").bounding_box()
            self.assertGreaterEqual(bounds["height"], 44)
            self.assertGreaterEqual(bounds["width"], 40)

    def test_tv_completed(self):
        """Test the completed status of a TV show."""
        self.page.get_by_placeholder("Search tv shows...").click()
        self.page.get_by_placeholder("Search tv shows...").fill("breaking bad")
        self.page.locator("form").filter(has_text="TV Shows TV").get_by_role(
            "button",
        ).first.click()
        expect(self.page.locator("h2")).to_contain_text("Search Results")
        self.page.get_by_role("link", name="Breaking Bad", exact=True).click()
        expect(self.page.get_by_role("main")).to_contain_text("Breaking Bad")
        self.page.locator("button").filter(has_text="Add to tracker").click()
        expect(self.page.locator("#track-tv-1396")).to_contain_text("Score")
        self.page.get_by_label("Status").select_option("Completed")
        self.page.get_by_role("button", name="Add", exact=True).click()
        self.page.get_by_role("link", name="TV Shows").click()
        self.page.get_by_role("link", name="Table View").click()
        expect(self.page.locator("tbody")).to_contain_text("62")

    @patch("app.providers.services.get_media_metadata", return_value={"max_progress": 100})
    def test_manga_reading_sessions_from_home(self, _provider):
        self.user.manga_enabled = True
        self.user.save(update_fields=["manga_enabled"])
        item = Item.objects.create(
            title="Reading Manga", media_id="123", media_type=MediaTypes.MANGA.value,
            source=Sources.MAL.value,
        )
        manga, = Manga.objects.bulk_create([
            Manga(user=self.user, item=item, progress=7, status=Status.IN_PROGRESS.value),
        ])
        self.page.goto(f"{self.live_server_url}/", wait_until="domcontentloaded")
        card = self.page.locator("#media-grid-manga")
        increase = card.get_by_role("button", name="Increase progress")
        minutes = card.get_by_label("Reading time (minutes)")
        save = card.get_by_role("button", name="OK", exact=True)

        increase.click()
        minutes.fill("18")
        save.click()
        expect(card.get_by_text("8 Chapters", exact=True)).to_be_visible()
        increase.click()
        expect(minutes).to_be_visible()
        save.click()
        expect(card.get_by_text("9 Chapters", exact=True)).to_be_visible()

        manga.refresh_from_db()
        self.assertEqual(manga.reading_time, 18)
        self.assertEqual(
            list(manga.sessions.order_by("id").values_list("chapter_number", "minutes")),
            [(8, 18), (9, 0)],
        )
        self.page.goto(f"{self.live_server_url}{reverse('statistics')}")
        count = self.page.get_by_role("heading", name="Items Watched/Read/Played")
        expect(count.locator("+ p")).to_have_text("2")
        chapter = self.page.get_by_role("heading", name="Reading Manga Chapter 8")
        expect(chapter).to_be_visible()
        expect(self.page.get_by_text("Chapter 9", exact=True)).to_be_visible()
        expect(chapter.locator("..").get_by_text("18 min", exact=True)).to_be_visible()
        expect(self.page.locator("#watch-time-bars").get_by_text("18 min", exact=True)).to_be_visible()

    def test_season_completed(self):
        """The tracker can close a season without marking pending episodes watched."""
        season = _create_home_season(
            self.user, "101", range(1, 13), watched=(1, 2), source=Sources.TMDB.value,
        )
        watches = season.episodes.values_list("pk", "item__episode_number", "end_date")
        before = list(watches)
        with patch("app.providers.services.get_media_metadata", side_effect=OSError) as provider:
            self.page.goto(f"{self.live_server_url}/", wait_until="domcontentloaded")
            card = self.page.locator("#media-grid-season > div")
            actions = card.get_by_role("button", name="Actions for Show 101 S1")
            expect(actions).to_have_attribute("aria-expanded", "false")
            card.locator("[data-home-poster-link]").hover()
            expect(actions).to_have_css("opacity", "1")
            actions.click()
            card.get_by_title("Add to tracker").click()
            status = self.page.get_by_label("Status", exact=True)
            save = self.page.get_by_role("button", name="Update", exact=True)
            more = self.page.get_by_text("More options", exact=True)
            poster = self.page.get_by_label("Season poster URL")
            delete = self.page.get_by_role("button", name="Delete", exact=True)
            hint = self.page.get_by_text("Your watch history is kept.")
            expect(save).to_be_visible()
            expect(poster).to_be_hidden()
            expect(delete).to_be_hidden()
            expect(hint).to_be_hidden()
            status.select_option(Status.COMPLETED.value)
            expect(hint).to_be_visible()
            more.click()
            expect(poster).to_be_visible()
            expect(delete).to_be_visible()
            more.click()
            save.click()
            expect(card).to_have_count(0)
        provider.assert_not_called()

        season.refresh_from_db()
        self.assertEqual(season.status, Status.COMPLETED.value)
        self.assertEqual(list(watches.all()), before)

        metadata = self._season_metadata(season, 12)
        details = reverse("season_details", args=[Sources.TMDB.value, "101", "show-101", 1])
        with (
            patch("app.providers.services.get_media_metadata", return_value=metadata),
            patch("app.providers.tmdb.fetch_season_multilingual", return_value={}),
        ):
            self.page.goto(f"{self.live_server_url}{details}?filter=unwatched", wait_until="domcontentloaded")
            expect(self.page.get_by_role("heading", level=2, name="Episode 3", exact=True)).to_be_visible()
            expect(self.page.get_by_role("heading", level=2, name="Episode 12", exact=True)).to_be_visible()
            expect(self.page.get_by_role("heading", level=2, name="Episode 1", exact=True)).to_have_count(0)

    @staticmethod
    def _season_metadata(season, episode_count):
        return {
            "title": season.item.title,
            "season/1": {
                "title": season.item.title,
                "media_id": season.item.media_id,
                "media_type": "season",
                "source": Sources.TMDB.value,
                "season_number": 1,
                "episodes": [
                    {
                        "episode_number": number, "name": f"Episode {number}",
                        "air_date": "2020-01-01", "still_path": None,
                        "overview": "", "runtime": 30,
                    }
                    for number in range(1, episode_count + 1)
                ],
            },
        }

    def test_mark_episode_watched_from_season_details(self):
        season = _create_home_season(
            self.user, "101", [1, 2, 3], watched=(1,), source=Sources.TMDB.value,
        )
        metadata = self._season_metadata(season, 3)

        def get_metadata(media_type, *args):
            return metadata["season/1"] if media_type == "season" else metadata

        details = reverse("season_details", args=[Sources.TMDB.value, "101", "show-101", 1])
        with (
            patch("app.providers.services.get_media_metadata", side_effect=get_metadata),
            patch("app.providers.tmdb.fetch_season_multilingual", return_value={}),
            patch("app.providers.tmdb.tv_with_seasons", return_value=metadata),
        ):
            url = f"{self.live_server_url}{details}?filter=unwatched&sort=desc"
            self.page.goto(url, wait_until="domcontentloaded")
            navigation = self.page.evaluate("performance.timeOrigin")
            self.page.evaluate("window.nextEpisodeCard = document.getElementById('season-episode-3')")
            started_at = timezone.now()
            watch = self.page.get_by_role("button", name="Mark episode 2 watched")
            watch.click()
            expect(watch).to_have_count(0)
            tracked = season.episodes.get(item__episode_number=2)
            self.assertGreaterEqual(tracked.end_date, started_at)
            expect(self.page.locator("#season-tracking-history")).to_contain_text("2 / 3")
            self.assertTrue(self.page.evaluate(
                "window.nextEpisodeCard === document.getElementById('season-episode-3')",
            ))

            self.page.get_by_role("button", name="Mark episode 3 watched").click()
            expect(self.page.get_by_role("heading", name="Complete Season?")).to_be_visible()
            self.assertFalse(season.episodes.filter(item__episode_number=3).exists())
            self.page.get_by_role("button", name="No, keep as In Progress").click()
            expect(self.page.get_by_role("button", name="Mark episode 3 watched")).to_have_count(0)
            self.assertTrue(season.episodes.filter(item__episode_number=3).exists())
            expect(self.page).to_have_url(url)
            self.assertEqual(self.page.evaluate("performance.timeOrigin"), navigation)

        season.refresh_from_db()
        self.assertEqual(season.status, Status.IN_PROGRESS.value)

    def test_season_watch_refresh_preserves_scroll_and_retry_does_not_record_again(self):
        season = _create_home_season(
            self.user, "101", range(1, 10), watched=(1,), source=Sources.TMDB.value,
        )
        metadata = self._season_metadata(season, 9)

        def get_metadata(media_type, *args):
            return metadata["season/1"] if media_type == "season" else metadata

        def fail_refresh_once(route):
            if route.request.headers.get("x-season-fragment") == "1":
                route.fulfill(status=503, body="Temporarily unavailable")
            else:
                route.continue_()

        details = reverse("season_details", args=[Sources.TMDB.value, "101", "show-101", 1])
        with (
            patch("app.providers.services.get_media_metadata", side_effect=get_metadata),
            patch("app.providers.tmdb.fetch_season_multilingual", return_value={}),
            patch("app.providers.tmdb.tv_with_seasons", return_value=metadata),
        ):
            self.page.goto(
                f"{self.live_server_url}{details}?filter=unwatched&sort=desc",
                wait_until="domcontentloaded",
            )
            navigation = self.page.evaluate("performance.timeOrigin")
            self.page.route(
                lambda url: url.startswith(f"{self.live_server_url}{details}?"),
                fail_refresh_once,
                times=1,
            )
            watch = self.page.get_by_role("button", name="Mark episode 6 watched")
            watch.click()
            expect(self.page.get_by_text("Watch saved. The episode list could not refresh.")).to_be_visible()
            self.assertEqual(season.episodes.filter(item__episode_number=6).count(), 1)
            self.page.evaluate("""() => {
              const card = document.getElementById('season-episode-6');
              window.scrollBy(0, card.getBoundingClientRect().top - 160);
              window.watchCardTop = card.getBoundingClientRect().top;
              document.querySelector('#season-watch-refresh button').click();
            }""")
            expect(watch).to_have_count(0)
            expect(self.page.locator("#season-watch-refresh")).to_be_hidden()
            self.page.wait_for_function(
                """Math.abs(document.getElementById('season-episode-5').getBoundingClientRect().top
                   - window.watchCardTop) < 2""",
            )
            self.assertEqual(season.episodes.filter(item__episode_number=6).count(), 1)
            self.assertEqual(self.page.evaluate("performance.timeOrigin"), navigation)

    def test_tv_manual(self):
        """Test the manual creation of a TV show."""
        # Create TV show
        self.page.get_by_role("link", name="Create Custom").click()
        self.page.get_by_placeholder("Enter title").click()
        self.page.get_by_placeholder("Enter title").fill("Friends")
        self.page.get_by_placeholder("Enter image URL").click()
        self.page.get_by_placeholder("Enter image URL").fill(
            "https://media.themoviedb.org/t/p/w300_and_h450_bestv2/2koX1xLkpTQM4IZebYvKysFW1Nh.jpg",
        )
        self.page.get_by_role("combobox").select_option("In progress")
        self.page.get_by_role("button", name="Create Entry").click()
        expect(self.page.locator(".scheme-dark")).to_contain_text(
            "Friends added successfully.",
        )

        # Create season
        self.page.get_by_role("button", name="Season").click()
        expect(self.page.get_by_role("main")).to_contain_text("Parent TV Show")
        self.page.get_by_placeholder("Search for a TV show...").click()
        self.page.get_by_placeholder("Search for a TV show...").type("fri")
        expect(self.page.locator("#parent-tv-results")).to_contain_text("Friends")
        self.page.get_by_role("button", name="Friends").click()
        self.page.get_by_placeholder("Enter image URL").click()
        self.page.get_by_placeholder("Enter image URL").fill(
            "https://media.themoviedb.org/t/p/w130_and_h195_bestv2/odCW88Cq5hAF0ZFVOkeJmeQv1nV.jpg",
        )
        self.page.get_by_role("button", name="Create Entry").click()
        expect(self.page.locator("body")).to_contain_text(
            "Friends S1 added successfully.",
        )

        # Create episode
        self.page.get_by_role("button", name="Episode").click()
        expect(self.page.get_by_role("main")).to_contain_text("Parent Season")
        self.page.get_by_placeholder("Search for a season...").click()
        self.page.get_by_placeholder("Search for a season...").type("frien")
        expect(self.page.locator("#parent-season-results")).to_contain_text(
            "Friends - Season 1",
        )
        self.page.get_by_role("button", name="Friends - Season").click()
        self.page.get_by_placeholder("Enter image URL").click()
        self.page.get_by_placeholder("Enter image URL").fill(
            "https://media.themoviedb.org/t/p/w227_and_h127_bestv2/v6Elr1W2elOyGi1MClgV0mIBVHC.jpg",
        )
        self.page.locator('input[name="end_date"]').fill("2025-03-07")
        self.page.get_by_role("button", name="Create Entry").click()
        expect(self.page.locator("body")).to_contain_text(
            "Friends S1E1 added successfully.",
        )

        # Check visibility
        self.page.get_by_role("link", name="TV Shows").click()
        self.page.get_by_role("link", name="Grid View").click()
        expect(self.page.get_by_role("main")).to_contain_text("Friends")
        self.page.get_by_role("link", name="TV Seasons").click()
        self.page.get_by_role("link", name="Grid View").click()
        expect(self.page.get_by_role("main")).to_contain_text("Friends S1")
        self.page.get_by_role("link", name="TV Shows").click()
        self.page.get_by_role("link", name="Friends").click()
        expect(self.page.get_by_role("main")).to_contain_text("Friends")
        expect(self.page.get_by_role("main")).to_contain_text("Season 1")
        self.page.get_by_role("link", name="Season 1").click()
        expect(self.page.get_by_role("main")).to_contain_text("Season 1")
        expect(self.page.get_by_role("main")).to_contain_text(
            "Episode 1 • Unknown air date",
        )
