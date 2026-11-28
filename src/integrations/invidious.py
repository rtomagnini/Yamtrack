"""Invidious history updates and public comments."""

# These API failures are short messages safe to display on the status page.
# ruff: noqa: EM101, TRY003

import json
from hashlib import sha256
from http import HTTPStatus
from urllib.parse import quote, unquote, urlsplit

import requests
from django.conf import settings
from django.core.cache import cache

from integrations.tubearchivist import TubeArchivistError, validate_video_id


class InvidiousError(Exception):
    """Report a delivery failure without exposing tokens or response bodies."""

    def __init__(self, message, *, permanent=False):
        """Flag failures requiring configuration changes and manual retry."""
        super().__init__(message)
        self.permanent = permanent


def _invidious_url():
    url = settings.INVIDIOUS_URL.strip().rstrip("/")
    try:
        parsed = urlsplit(url)
        valid = (
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and not (parsed.username or parsed.password or parsed.query or parsed.fragment)
        )
    except ValueError:
        valid = False
    if not valid:
        raise InvidiousError("Invalid Invidious URL.", permanent=True)
    return url


def get_video_comments(video_id, continuation=""):
    """Fetch a page of public comments or replies without account credentials."""
    validate_video_id(video_id)
    url = _invidious_url()
    cache_key = "invidious_comments:" + sha256(
        f"{url}\n{video_id}\n{continuation}".encode(),
    ).hexdigest()
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    params = {"source": "youtube", "sort_by": "top"}
    if continuation:
        params["continuation"] = continuation
    try:
        response = requests.get(
            f"{url}/api/v1/comments/{video_id}",
            params=params,
            headers={"Accept": "application/json"},
            timeout=(3, 8),
            allow_redirects=False,
        )
        page = response.json() if response.status_code == HTTPStatus.OK else None
    except (requests.RequestException, ValueError):
        page = None
    if not isinstance(page, dict) or not isinstance(page.get("comments"), list):
        raise InvidiousError("Could not load comments from Invidious.")

    result = {
        "comments": [comment for comment in page["comments"] if isinstance(comment, dict)],
        "continuation": page.get("continuation"),
    }
    cache.set(cache_key, result, timeout=180)
    return result


class InvidiousClient:
    """Mark history for the account belonging to the configured API token."""

    def __init__(self):
        """Validate configuration without contacting the server."""
        raw_token = settings.INVIDIOUS_TOKEN.strip()
        if not settings.INVIDIOUS_URL.strip().rstrip("/") or not raw_token:
            raise InvidiousError(
                "Configure INVIDIOUS_URL and INVIDIOUS_TOKEN.", permanent=True,
            )
        self.url = _invidious_url()
        self.token = self._token(raw_token)

    @staticmethod
    def _token(raw_token):
        # Invidious signs a JSON object, not just the SID/session identifier.
        try:
            value = json.loads(unquote(raw_token))
        except (ValueError, TypeError):
            raise InvidiousError(
                "Use the complete Invidious API token JSON, not just its session ID.",
                permanent=True,
            ) from None
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("session"), str)
            or not value["session"]
            or not isinstance(value.get("signature"), str)
            or not value["signature"]
            or not isinstance(value.get("scopes"), list)
            or not all(isinstance(scope, str) for scope in value["scopes"])
        ):
            raise InvidiousError("Invalid Invidious API token JSON.", permanent=True)
        # The server URL-decodes before parsing. Encoding also makes multiline
        # secret files safe to use in an HTTP header without changing signatures.
        return quote(json.dumps(value, separators=(",", ":")), safe="")

    def mark_watched(self, video_id):
        """Use the idempotent history endpoint; 204 confirms its database write."""
        try:
            validate_video_id(video_id)
        except TubeArchivistError:
            raise InvidiousError("Invalid YouTube video ID.", permanent=True) from None
        try:
            response = requests.request(
                "POST",
                f"{self.url}/api/v1/auth/history/{video_id}",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/json",
                },
                timeout=(5, 15),
                allow_redirects=False,
            )
        except requests.RequestException:
            raise InvidiousError("Invidious: cannot connect to the server.") from None
        if response.status_code == HTTPStatus.NO_CONTENT:
            return
        if response.status_code == HTTPStatus.CONFLICT:
            raise InvidiousError(
                "Invidious: enable watch history in the token account's preferences.",
                permanent=True,
            )
        if response.status_code in {401, 403}:
            raise InvidiousError(
                "Invidious: token invalid, expired or missing history permissions.",
                permanent=True,
            )
        if response.status_code == HTTPStatus.NOT_FOUND:
            raise InvidiousError(
                "Invidious history API not found. Check the URL and version.",
                permanent=True,
            )
        message = f"Invidious returned HTTP {response.status_code}; expected 204."
        raise InvidiousError(
            message,
            permanent=response.status_code < HTTPStatus.INTERNAL_SERVER_ERROR
            and response.status_code not in {408, 429},
        )
