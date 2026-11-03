"""Custom episode creation should prioritize recent seasons and retain its type."""

from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from app.forms import ManualItemForm
from app.models import TV, Item, Season
from users.models import User


class CustomEpisodeTests(TestCase):
    """Check real form submission and the AJAX parent-season search."""

    def setUp(self):
        """Create tracked manual seasons without contacting metadata services."""
        self.user = User.objects.create(username="custom-episode-user")
        self.client.force_login(self.user)
        patch("app.models.Item.fetch_releases").start()
        self.addCleanup(patch.stopall)
        item = Item.objects.create(
            source="manual", media_type="tv", media_id="77", title="Manual series",
        )
        self.tv = TV(user=self.user, item=item, status="Planning")
        TV.objects.bulk_create([self.tv])
        self.seasons = []
        for number in (1, 2025, 2, 2026):
            item = Item.objects.create(
                source="manual",
                media_type="season",
                media_id="77",
                season_number=number,
                title="Manual series",
            )
            season = Season(
                user=self.user, item=item, related_tv=self.tv, status="Planning",
            )
            Season.objects.bulk_create([season])
            self.seasons.append(season)

    def test_parent_season_search_and_form_order_are_descending(self):
        """Search results and the form's allowed choices use the same order."""
        response = self.client.get(reverse("search_parent_season"), {"q": "Manual"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [season.item.season_number for season in response.context["results"]],
            [2026, 2025, 2, 1],
        )
        form = ManualItemForm(user=self.user)
        self.assertEqual(
            list(
                form.fields["parent_season"].queryset.values_list(
                    "item__season_number", flat=True,
                ),
            ),
            [2026, 2025, 2, 1],
        )

    def test_saved_episode_returns_to_episode_form(self):
        """Saving an episode keeps Episode selected and clears the old inputs."""
        response = self.client.post(
            reverse("create_entry"),
            {
                "media_type": "episode",
                "title": "New episode",
                "episode_number": 1,
                "parent_season": self.seasons[-1].pk,
            },
            follow=True,
        )
        self.assertRedirects(response, reverse("create_entry") + "?media_type=episode")
        self.assertEqual(response.context["initial_media_type"], "episode")
        self.assertContains(response, "mediaType: 'episode'")
        item = Item.objects.get(
            source="manual", media_type="episode", title="New episode",
        )
        self.assertEqual(
            (item.media_id, item.season_number, item.episode_number), ("77", 2026, 1),
        )

    def test_invalid_episode_also_keeps_episode_selected(self):
        """Validation errors do not send the user to YouTube Video."""
        response = self.client.post(
            reverse("create_entry"),
            {
                "media_type": "episode",
                "title": "",
                "episode_number": 1,
                "parent_season": self.seasons[-1].pk,
            },
            follow=True,
        )
        self.assertRedirects(response, reverse("create_entry") + "?media_type=episode")
        self.assertEqual(response.context["initial_media_type"], "episode")
        self.assertFalse(Item.objects.filter(media_type="episode").exists())

    def test_default_create_form_keeps_existing_default(self):
        """An ordinary initial visit still offers the existing YouTube default."""
        response = self.client.get(reverse("create_entry"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "mediaType: 'youtube_video'")
