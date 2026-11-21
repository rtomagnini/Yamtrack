import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect, sync_playwright

from app.models import Sources, Status
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
            expect(actions).to_be_visible()
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
            card.get_by_role("button", name="Actions for Show 101 S1").click()
            card.get_by_title("Add to tracker").click()
            self.page.get_by_label("Status", exact=True).select_option(Status.COMPLETED.value)
            self.page.get_by_role("button", name="Update", exact=True).click()
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
        ):
            self.page.goto(f"{self.live_server_url}{details}?filter=unwatched", wait_until="domcontentloaded")
            started_at = timezone.now()
            watch = self.page.get_by_role("button", name="Mark episode 2 watched")
            watch.click()
            expect(watch).to_have_count(0)
            tracked = season.episodes.get(item__episode_number=2)
            self.assertGreaterEqual(tracked.end_date, started_at)

            self.page.get_by_role("button", name="Mark episode 3 watched").click()
            expect(self.page.get_by_role("heading", name="Complete Season?")).to_be_visible()
            self.assertFalse(season.episodes.filter(item__episode_number=3).exists())
            self.page.get_by_role("button", name="No, keep as In Progress").click()
            expect(self.page.get_by_role("button", name="Mark episode 3 watched")).to_have_count(0)
            self.assertTrue(season.episodes.filter(item__episode_number=3).exists())

        season.refresh_from_db()
        self.assertEqual(season.status, Status.IN_PROGRESS.value)

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
