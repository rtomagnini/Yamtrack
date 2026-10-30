"""Request a scan of the configured Plex YouTube library after TA deletion."""

# These exceptions contain redacted messages intended for the status page.
# ruff: noqa: EM101, EM102, TRY003

from http import HTTPStatus
from urllib.parse import urlsplit

import requests
from django.conf import settings


class PlexError(Exception):
    """A Plex API failure that can be safely displayed to the user."""

    def __init__(self, message, *, permanent=False):
        """Flag configuration failures that need a manual retry."""
        super().__init__(message)
        self.permanent = permanent


class PlexLibraryClient:
    """Resolve exactly one named library and request its file scan."""

    def __init__(self):
        """Validate the server URL and owner token without connecting."""
        self.url = (settings.PLEX_SERVER_URL or "").strip().rstrip("/")
        self.token = (settings.PLEX_TOKEN or "").strip()
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
        if not valid or not self.token:
            raise PlexError(
                "Plex: configure PLEX_SERVER_URL and PLEX_TOKEN.",
                permanent=True,
            )

    def request(self, method, path):
        """Keep credentials in headers and redact server and network errors."""
        try:
            response = requests.request(
                method,
                f"{self.url}{path}",
                headers={"X-Plex-Token": self.token, "Accept": "application/json"},
                timeout=(5, 15),
                allow_redirects=False,
            )
        except requests.RequestException:
            raise PlexError("Plex: cannot connect to the server.") from None
        if response.status_code not in {200, 201, 202, 204}:
            raise PlexError(
                f"Plex returned HTTP {response.status_code}.",
                permanent=response.status_code in {400, 401, 403, 404, 405}
                or HTTPStatus.MULTIPLE_CHOICES
                <= response.status_code
                < HTTPStatus.BAD_REQUEST,
            )
        return response

    def scan(self, library_name):
        """Discover the section ID and request a file scan of that library."""
        response = self.request("GET", "/library/sections")
        try:
            payload = response.json()
            sections = payload["MediaContainer"].get("Directory", [])
        except (ValueError, TypeError, KeyError, AttributeError):
            raise PlexError("Plex: invalid library list response.") from None
        if not isinstance(sections, list) or not all(
            isinstance(section, dict) for section in sections
        ):
            raise PlexError("Plex: invalid library list response.")
        matches = [
            section
            for section in sections
            if str(section.get("title", "")).casefold() == library_name.casefold()
        ]
        if len(matches) != 1:
            raise PlexError(
                "Plex: the configured YouTube library is missing or ambiguous.",
                permanent=True,
            )
        section_id = str(matches[0].get("key", ""))
        if not section_id.isascii() or not section_id.isdigit():
            raise PlexError("Plex: invalid library section ID.", permanent=True)
        self.request("POST", f"/library/sections/{section_id}/refresh")
