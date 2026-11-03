"""Request a scan of the configured Plex YouTube library after TA deletion."""

# These exceptions contain redacted messages intended for the status page.
# ruff: noqa: EM101, EM102, TRY003

import re
from http import HTTPStatus
from urllib.parse import urlsplit

import requests
from django.conf import settings

PLEX_PAGE_SIZE = 500


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

    def request(self, method, path, *, params=None):
        """Keep credentials in headers and redact server and network errors."""
        try:
            options = {"params": params} if params is not None else {}
            response = requests.request(
                method,
                f"{self.url}{path}",
                headers={"X-Plex-Token": self.token, "Accept": "application/json"},
                timeout=(5, 15),
                allow_redirects=False,
                **options,
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

    def section(self, library_name):
        """Resolve exactly one library; never silently select another section."""
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
        return matches[0]

    def scan(self, library_name):
        """Discover the section ID and request a file scan of that library."""
        section = self.section(library_name)
        self.request("POST", f"/library/sections/{section['key']}/refresh")

    @staticmethod
    def _metadata(response):
        try:
            container = response.json()["MediaContainer"]
            items = container.get("Metadata", [])
        except (ValueError, TypeError, KeyError, AttributeError):
            raise PlexError("Plex: invalid video metadata response.") from None
        if not isinstance(items, list) or not all(
            isinstance(item, dict) for item in items
        ):
            raise PlexError("Plex: invalid video metadata response.")
        return container, items

    @staticmethod
    def matches_video(item, video_id):
        """Match a complete external ID in a media filename, never a title."""
        pattern = re.compile(
            rf"(?<![A-Za-z0-9_-]){re.escape(video_id)}(?![A-Za-z0-9_-])",
        )
        for media in item.get("Media", []):
            for part in media.get("Part", []):
                filename = (
                    str(part.get("file", "")).replace("\\", "/").rsplit("/", 1)[-1]
                )
                if pattern.search(filename):
                    return True
        return False

    def _matching_keys(self, items, video_id):
        found = set()
        for item in items:
            if self.matches_video(item, video_id):
                key = str(item.get("ratingKey", ""))
                if not key.isascii() or not key.isdigit():
                    raise PlexError("Plex: invalid video rating key.", permanent=True)
                found.add(key)
        return found

    def resolve_video(self, library_name, video_id):
        """Page through leaf items, rejecting missing and ambiguous matches."""
        section = self.section(library_name)
        offset = 0
        found = set()
        seen = set()
        while True:
            response = self.request(
                "GET",
                f"/library/sections/{section['key']}/all",
                params={
                    "type": 4 if section.get("type") == "show" else 1,
                    "includeMedia": 1,
                    "X-Plex-Container-Start": offset,
                    "X-Plex-Container-Size": PLEX_PAGE_SIZE,
                },
            )
            container, items = self._metadata(response)
            if not items:
                break
            keys = {str(item.get("ratingKey", "")) for item in items}
            if keys <= seen:
                raise PlexError("Plex: library pagination did not advance.")
            seen.update(keys)
            found.update(self._matching_keys(items, video_id))
            if len(found) > 1:
                raise PlexError(
                    "Plex: multiple library items match this YouTube video.",
                    permanent=True,
                )
            offset += len(items)
            try:
                total = (
                    int(container["totalSize"]) if "totalSize" in container else None
                )
            except (ValueError, TypeError):
                raise PlexError("Plex: invalid library pagination response.") from None
            if (total is not None and offset >= total) or (
                total is None and len(items) < PLEX_PAGE_SIZE
            ):
                break
        if not found:
            raise PlexError("Plex: YouTube video not found in the configured library.")
        return found.pop()

    def video(self, rating_key, video_id):
        """Verify a saved Plex ID still identifies the same YouTube file."""
        if not rating_key.isascii() or not rating_key.isdigit():
            raise PlexError("Plex: invalid video rating key.", permanent=True)
        _, items = self._metadata(
            self.request("GET", f"/library/metadata/{rating_key}"),
        )
        if len(items) != 1 or not self.matches_video(items[0], video_id):
            raise PlexError(
                "Plex: saved video no longer matches its YouTube ID.", permanent=True,
            )
        return items[0]

    def mark_watched(self, rating_key):
        """Mark the video watched for the account belonging to the API token."""
        self.request(
            "GET",
            "/:/scrobble",
            params={
                "key": rating_key,
                "identifier": "com.plexapp.plugins.library",
            },
        )
