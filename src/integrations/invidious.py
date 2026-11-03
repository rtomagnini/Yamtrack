"""Authenticated Invidious history updates with redacted errors."""

# These API failures are short messages safe to display on the status page.
# ruff: noqa: EM101, TRY003

import json
from http import HTTPStatus
from urllib.parse import quote, unquote, urlsplit

import requests
from django.conf import settings

from integrations.tubearchivist import TubeArchivistError, validate_video_id


class InvidiousError(Exception):
    """Report a delivery failure without exposing tokens or response bodies."""

    def __init__(self, message, *, permanent=False):
        """Flag failures requiring configuration changes and manual retry."""
        super().__init__(message)
        self.permanent = permanent


class InvidiousClient:
    """Mark history for the account belonging to the configured API token."""

    def __init__(self):
        """Validate configuration without contacting the server."""
        self.url = settings.INVIDIOUS_URL.strip().rstrip("/")
        raw_token = settings.INVIDIOUS_TOKEN.strip()
        if not self.url or not raw_token:
            raise InvidiousError(
                "Configure INVIDIOUS_URL and INVIDIOUS_TOKEN.", permanent=True,
            )
        try:
            parsed = urlsplit(self.url)
            valid = (
                parsed.scheme in {"http", "https"}
                and parsed.hostname
                and not (
                    parsed.username
                    or parsed.password
                    or parsed.query
                    or parsed.fragment
                )
            )
        except ValueError:
            valid = False
        if not valid:
            raise InvidiousError("Invalid Invidious URL.", permanent=True)
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
