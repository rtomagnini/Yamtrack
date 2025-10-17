import json
import logging

from app.models import MediaTypes

from .base import BaseWebhookProcessor

logger = logging.getLogger(__name__)


class PlexWebhookProcessor(BaseWebhookProcessor):
    """Processor for Plex webhook events."""

    def process_payload(self, payload, user):
        """Process the incoming Plex webhook payload."""
        logger.debug("Received Plex webhook payload: %s", json.dumps(payload, indent=2))
        
        # Log detailed payload structure for debugging
        logger.info("=== PLEX PAYLOAD ANALYSIS ===")
        logger.info("Full payload: %s", json.dumps(payload, indent=2))
        
        # Log metadata structure specifically
        metadata = payload.get("Metadata", {})
        logger.info("Metadata section: %s", json.dumps(metadata, indent=2))
        
        # Log parent information if available
        if "Parent" in metadata:
            logger.info("Parent section: %s", json.dumps(metadata["Parent"], indent=2))
        
        # Log grandparent information if available
        if "Grandparent" in metadata:
            logger.info("Grandparent section: %s", json.dumps(metadata["Grandparent"], indent=2))
        
        # Log all GUIDs for analysis
        guids = metadata.get("Guid", [])
        logger.info("Episode GUIDs: %s", json.dumps(guids, indent=2))
        
        # Check for parent GUIDs
        parent_guids = metadata.get("Parent", {}).get("Guid", [])
        if parent_guids:
            logger.info("Parent GUIDs: %s", json.dumps(parent_guids, indent=2))
        
        # Check for grandparent GUIDs
        grandparent_guids = metadata.get("Grandparent", {}).get("Guid", [])
        if grandparent_guids:
            logger.info("Grandparent GUIDs: %s", json.dumps(grandparent_guids, indent=2))
        
        logger.info("=== END PAYLOAD ANALYSIS ===")

        event_type = payload.get("event")
        if not self._is_supported_event(payload.get("event")):
            logger.debug("Ignoring Plex webhook event type: %s", event_type)
            return

        payload_user = payload["Account"]["title"].strip().lower()
        if not self._is_valid_user(payload_user, user):
            logger.debug(
                "Ignoring Plex webhook event for user %s: not a valid user",
                payload_user,
            )
            return

        ids = self._extract_external_ids(payload)
        logger.info("Extracted IDs from payload: %s", ids)

        if not any(ids.values()):
            logger.warning("Ignoring Plex webhook call because no ID was found.")
            return

        self._process_media(payload, user, ids)

    def _is_supported_event(self, event_type):
        return event_type in ("media.scrobble", "media.play")

    def _is_valid_user(self, payload_user, user):
        stored_usernames = [
            u.strip().lower()
            for u in (user.plex_usernames or "").split(",")
            if u.strip()
        ]
        logger.debug(
            "Checking if payload user '%s' is in stored usernames: %s",
            payload_user,
            stored_usernames,
        )
        return payload_user in stored_usernames

    def _is_played(self, payload):
        return payload["event"] == "media.scrobble"

    def _get_media_type(self, payload):
        media_type = payload["Metadata"].get("type")
        if not media_type:
            return None

        return self.MEDIA_TYPE_MAPPING.get(media_type.title())

    def _get_media_title(self, payload):
        """Get media title from payload."""
        title = None

        if self._get_media_type(payload) == MediaTypes.TV.value:
            series_name = payload["Metadata"].get("grandparentTitle")
            season_number = payload["Metadata"].get("parentIndex")
            episode_number = payload["Metadata"].get("index")
            title = f"{series_name} S{season_number:02d}E{episode_number:02d}"

        elif self._get_media_type(payload) == MediaTypes.MOVIE.value:
            title = payload["Metadata"].get("title")

        return title

    def _extract_external_ids(self, payload):
        """Extract external IDs from Plex payload."""
        
        # For TV episodes, we need the series TMDB ID, not the episode TMDB ID
        # Plex structure: Episode -> Season (parent) -> Series (grandparent)
        media_type = self._get_media_type(payload)
        
        if media_type == MediaTypes.TV.value:
            # For TV episodes, try to get the series TMDB ID from grandparent
            tmdb_id = self._extract_series_tmdb_id(payload)
        else:
            # For movies, use the direct TMDB ID
            guids = payload["Metadata"].get("Guid", [])
            tmdb_id = self._get_id_from_guids(guids, "tmdb")
        
        # Extract other IDs from episode level (these are usually correct)
        guids = payload["Metadata"].get("Guid", [])
        
        return {
            "tmdb_id": tmdb_id,
            "imdb_id": self._get_id_from_guids(guids, "imdb"),
            "tvdb_id": self._get_id_from_guids(guids, "tvdb"),
        }
    
    def _extract_series_tmdb_id(self, payload):
        """Extract TMDB ID for the series (grandparent) from a TV episode payload."""
        
        logger.info("=== EXTRACTING SERIES TMDB ID ===")
        
        # Method 1: Check if there's a grandparent section with GUIDs
        grandparent = payload["Metadata"].get("Grandparent", {})
        logger.info("Grandparent section found: %s", bool(grandparent))
        if grandparent and "Guid" in grandparent:
            logger.info("Grandparent GUIDs: %s", json.dumps(grandparent["Guid"], indent=2))
            tmdb_id = self._get_id_from_guids(grandparent["Guid"], "tmdb")
            if tmdb_id:
                logger.info("Found series TMDB ID from grandparent: %s", tmdb_id)
                return tmdb_id
        
        # Method 2: Check parent section with GUIDs
        parent = payload["Metadata"].get("Parent", {})
        logger.info("Parent section found: %s", bool(parent))
        if parent and "Guid" in parent:
            logger.info("Parent GUIDs: %s", json.dumps(parent["Guid"], indent=2))
            tmdb_id = self._get_id_from_guids(parent["Guid"], "tmdb")
            if tmdb_id:
                logger.info("Found series TMDB ID from parent: %s", tmdb_id)
                return tmdb_id
        
        # Method 3: Look for series-level TMDB in main Guid array
        # Sometimes Plex includes multiple levels of GUIDs
        guids = payload["Metadata"].get("Guid", [])
        logger.info("Episode GUIDs: %s", json.dumps(guids, indent=2))
        for guid in guids:
            guid_id = guid.get("id", "")
            logger.info("Checking GUID: %s", guid_id)
            # Look for patterns that indicate this is a series TMDB ID
            if guid_id.startswith("tmdb://") and "show" in str(guid).lower():
                tmdb_id = guid_id.replace("tmdb://", "")
                logger.info("Found series TMDB ID from show context: %s", tmdb_id)
                return tmdb_id
        
        # Method 4: Fallback to regular TMDB ID extraction
        # This might be the episode TMDB ID, but better than nothing
        tmdb_id = self._get_id_from_guids(guids, "tmdb")
        if tmdb_id:
            logger.warning("Using fallback TMDB ID (might be episode-level): %s", tmdb_id)
            return tmdb_id
        
        logger.warning("No TMDB ID found for series")
        logger.info("=== END SERIES TMDB ID EXTRACTION ===")
        return None
    
    def _get_id_from_guids(self, guids, prefix):
        """Extract ID with given prefix from GUID array."""
        if not guids:
            return None
            
        return next(
            (
                guid["id"].replace(f"{prefix}://", "")
                for guid in guids
                if guid.get("id", "").startswith(f"{prefix}://")
            ),
            None,
        )
