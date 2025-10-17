import json
import logging
import requests
from urllib.parse import quote

from django.conf import settings
from app.models import MediaTypes

from .base import BaseWebhookProcessor

logger = logging.getLogger(__name__)


class PlexWebhookProcessor(BaseWebhookProcessor):
    """Processor for Plex webhook events."""

    def process_payload(self, payload, user):
        """Process the incoming Plex webhook payload."""
        logger.debug("Received Plex webhook payload: %s", json.dumps(payload, indent=2))
        
        # Log essential info only
        metadata = payload.get("Metadata", {})
        logger.info("Processing Plex webhook: %s S%sE%s (Rating Key: %s)", 
                   metadata.get("grandparentTitle", "Unknown"),
                   metadata.get("parentIndex", "?"),
                   metadata.get("index", "?"),
                   metadata.get("ratingKey", "?"))

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
    
    def _get_series_tmdb_from_plex_api(self, episode_tmdb_id, payload):
        """Get series TMDB ID by querying Plex API using grandparentRatingKey."""
        
        # Try to get Plex configuration from settings
        plex_url = getattr(settings, 'PLEX_SERVER_URL', None)
        plex_token = getattr(settings, 'PLEX_TOKEN', None)
        
        if not plex_url or not plex_token:
            logger.warning("Plex API credentials not configured in settings")
            return None
        
        # Get grandparentRatingKey directly from payload
        metadata = payload.get("Metadata", {})
        grandparent_rating_key = metadata.get("grandparentRatingKey")
        
        if not grandparent_rating_key:
            logger.warning("No grandparentRatingKey found in payload")
            return None
        
        logger.info("Using grandparentRatingKey from payload: %s", grandparent_rating_key)
        
        try:
            # Query directly for the series metadata using grandparentRatingKey
            series_tmdb_id = self._get_series_metadata_from_plex(
                grandparent_rating_key, plex_url, plex_token
            )
            if series_tmdb_id:
                return series_tmdb_id
            
            logger.warning("No TMDB ID found in series metadata")
            return None
            
        except Exception as e:
            logger.error("Error querying Plex API: %s", str(e))
            return None
    
    def _get_series_metadata_from_plex(self, rating_key, plex_url, plex_token):
        """Get series metadata from Plex using rating key."""
        
        try:
            api_url = f"{plex_url}/library/metadata/{rating_key}"
            params = {'X-Plex-Token': plex_token}
            
            logger.info("Getting series metadata from Plex API (rating key: %s)", rating_key)
            
            response = requests.get(api_url, params=params, timeout=10)
            response.raise_for_status()
            
            # Parse XML response
            import xml.etree.ElementTree as ET
            root = ET.fromstring(response.content)
            
            # Find TMDB GUID in series metadata - try multiple paths
            # Method 1: Look in Directory elements
            for directory in root.findall('.//Directory'):
                for guid_elem in directory.findall('Guid'):
                    guid_id = guid_elem.get('id', '')
                    if guid_id.startswith('tmdb://'):
                        tmdb_id = guid_id.replace('tmdb://', '')
                        logger.info("Found series TMDB ID from Plex API: %s", tmdb_id)
                        return tmdb_id
            
            # Method 2: Look directly in root Guid elements
            for guid_elem in root.findall('.//Guid'):
                guid_id = guid_elem.get('id', '')
                if guid_id.startswith('tmdb://'):
                    tmdb_id = guid_id.replace('tmdb://', '')
                    logger.info("Found series TMDB ID from Plex API: %s", tmdb_id)
                    return tmdb_id
            
            logger.warning("No TMDB GUID found in Plex series metadata")
            return None
            
        except Exception as e:
            logger.error("Error getting series metadata from Plex: %s", str(e))
            return None
    
    def _extract_series_tmdb_id(self, payload):
        """Extract TMDB ID for the series (grandparent) from a TV episode payload."""
        
        logger.info("Extracting series TMDB ID...")
        
        # Method 0: Use Plex API to get series TMDB ID from episode TMDB ID
        episode_guids = payload["Metadata"].get("Guid", [])
        episode_tmdb_id = self._get_id_from_guids(episode_guids, "tmdb")
        
        if episode_tmdb_id:
            logger.info("Found episode TMDB ID: %s, querying Plex API for series", episode_tmdb_id)
            series_tmdb_id = self._get_series_tmdb_from_plex_api(episode_tmdb_id, payload)
            if series_tmdb_id:
                logger.info("Successfully got series TMDB ID from Plex API: %s", series_tmdb_id)
                return series_tmdb_id
            else:
                logger.warning("Plex API method failed, trying payload analysis...")
        
        # Method 1: Check if there's a grandparent section with GUIDs
        grandparent = payload["Metadata"].get("Grandparent", {})
        if grandparent and "Guid" in grandparent:
            tmdb_id = self._get_id_from_guids(grandparent["Guid"], "tmdb")
            if tmdb_id:
                logger.info("Found series TMDB ID from grandparent: %s", tmdb_id)
                return tmdb_id
        
        # Method 2: Check parent section with GUIDs
        parent = payload["Metadata"].get("Parent", {})
        if parent and "Guid" in parent:
            tmdb_id = self._get_id_from_guids(parent["Guid"], "tmdb")
            if tmdb_id:
                logger.info("Found series TMDB ID from parent: %s", tmdb_id)
                return tmdb_id
        
        # Method 3: Fallback to regular TMDB ID extraction
        guids = payload["Metadata"].get("Guid", [])
        tmdb_id = self._get_id_from_guids(guids, "tmdb")
        if tmdb_id:
            logger.warning("Using fallback TMDB ID (might be episode-level): %s", tmdb_id)
            return tmdb_id
        
        logger.warning("No TMDB ID found for series")
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
