from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from app.models import Item

pytestmark = pytest.mark.django_db


@pytest.fixture
def video(client, settings):
    client.force_login(get_user_model().objects.create_user(username="comments-reader"))
    settings.INVIDIOUS_URL = "http://invidious:3000"
    settings.INVIDIOUS_TOKEN = ""
    return Item.objects.create(
        source="youtube", media_type="episode", media_id="7", season_number=2026,
        episode_number=1, title="Video with comments", youtube_video_id="abcdefghijk",
    )


def test_comments_load_on_demand_and_paginate(client, video):
    url = reverse("youtube_comments", args=[video.pk])
    with patch("integrations.invidious.requests.get", autospec=True) as upstream:
        listing = client.get(reverse("youtubes"))
        assert b"data-youtube-comments-url" in listing.content
        upstream.assert_not_called()

        upstream.return_value.status_code = 200
        upstream.return_value.json.side_effect = [
            {
                "comments": [{
                    "author": "First viewer", "content": "<b>Text</b>",
                    "contentHtml": "<script>unsafe</script>",
                    "replies": {"replyCount": 2, "continuation": "reply+cursor"},
                }],
                "continuation": "next+cursor",
            },
            {"comments": [{"author": "Second viewer", "content": "Next comment"}]},
            {"comments": [{"author": "First viewer", "content": "A reply"}]},
        ]

        first = client.get(url)
        assert first.status_code == 200
        assert b"&lt;b&gt;Text&lt;/b&gt;" in first.content
        assert b"<script>unsafe</script>" not in first.content
        assert b"continuation=next%2Bcursor" in first.content
        assert b"replies=1&amp;continuation=reply%2Bcursor" in first.content
        assert upstream.call_args.args == ("http://invidious:3000/api/v1/comments/abcdefghijk",)
        assert upstream.call_args.kwargs["headers"] == {"Accept": "application/json"}

        assert client.get(url).content == first.content
        assert upstream.call_count == 1

        second = client.get(url, {"continuation": "next+cursor"})
        assert b"Next comment" in second.content
        assert upstream.call_args.kwargs["params"]["continuation"] == "next+cursor"

        replies = client.get(url, {"continuation": "reply+cursor", "replies": "1"})
        assert b"A reply" in replies.content
        assert upstream.call_args.kwargs["params"]["continuation"] == "reply+cursor"


def test_comments_separate_empty_results_from_upstream_errors(client, video):
    Item.objects.filter(pk=video.pk).update(youtube_video_id="zyxwvutsrqp")
    url = reverse("youtube_comments", args=[video.pk])
    with patch("integrations.invidious.requests.get", autospec=True) as upstream:
        upstream.return_value.status_code = 429
        assert client.get(url).status_code == 502
        upstream.return_value.status_code = 200
        upstream.return_value.json.return_value = {"comments": []}
        empty = client.get(url)
        assert empty.status_code == 200
        assert b"No comments available." in empty.content
        assert upstream.call_count == 2


def test_comments_require_a_session_and_a_valid_video(client, video):
    url = reverse("youtube_comments", args=[video.pk])
    client.logout()
    with patch("integrations.invidious.requests.get", autospec=True) as upstream:
        assert client.get(url).status_code == 302
        client.force_login(get_user_model().objects.get(username="comments-reader"))
        Item.objects.filter(pk=video.pk).update(youtube_video_id=None)
        assert client.get(url).status_code == 404
        assert client.get(reverse("youtube_comments", args=[video.pk + 1])).status_code == 404
        upstream.assert_not_called()
