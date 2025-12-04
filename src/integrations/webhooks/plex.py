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

        # If payload doesn't clearly map to TV/Movie, try a YouTube fallback
        media_type = self._get_media_type(payload)
        if media_type not in (MediaTypes.TV.value, MediaTypes.MOVIE.value):
            logger.info("Payload not TV/Movie (media_type=%s), attempting YouTube fallback", media_type)
            try:
                handled = self._attempt_handle_youtube(payload, user)
                if handled:
                    return
            except Exception:
                logger.exception("Error while attempting YouTube fallback")

        if not any(ids.values()):
            logger.warning("No TMDB/IMDB/TVDB ID found in payload, trying YouTube fallback")
            try:
                handled = self._attempt_handle_youtube(payload, user)
                if handled:
                    return
            except Exception:
                logger.exception("Error while attempting YouTube fallback")

            logger.warning("Ignoring Plex webhook call because no ID was found.")
            return

        self._process_media(payload, user, ids)

    def _extract_youtube_id_from_guids(self, guids):
        """Try to extract a YouTube video id from Plex GUID entries.

        Plex GUIDs are free-form; attempt a few heuristics to find a YouTube id.
        """
        if not guids:
            return None

        for guid in guids:
            gid = guid.get("id", "") or ""
            lower = gid.lower()
            # Common patterns
            if "youtube" in lower or "youtu.be" in lower or "youtu" in lower:
                # Try to extract a video id after last slash or 'v=' param
                if "v=" in gid:
                    # e.g. https://www.youtube.com/watch?v=VIDEOID
                    parts = gid.split("v=")
                    vid = parts[-1].split("&")[0]
                    if vid:
                        return vid
                if "/" in gid:
                    vid = gid.rstrip("/").split("/")[-1]
                    if vid:
                        return vid
                # fallback: the whole guid may be an id-like string
                candidate = gid.replace("youtube://", "").replace("yt://", "")
                if candidate:
                    return candidate

        return None

    def _attempt_handle_youtube(self, payload, user):
        """Attempt to find a YouTube video in the local DB and mark it as watched.

        Returns True if handled (marked as watched), False otherwise.
        """
        import app
        import re
        import os
        from django.utils import timezone

        # Only process scrobble events for YouTube (when video is finished)
        # Ignore media.play to avoid duplicate entries
        event_type = payload.get("event")
        if event_type != "media.scrobble":
            logger.debug("Ignoring YouTube event type %s (only scrobble marks as watched)", event_type)
            return False

        metadata = payload.get("Metadata", {})
        
        # First, try to extract video ID from file path (like Tautulli does)
        # This is more reliable than GUIDs for TubeArchivist files
        video_id = None
        
        # Try to get file path from Media/Part structure
        media_list = metadata.get("Media", [])
        if media_list and isinstance(media_list, list):
            for media in media_list:
                parts = media.get("Part", [])
                if parts and isinstance(parts, list):
                    for part in parts:
                        file_path = part.get("file")
                        if file_path:
                            # Extract YouTube video ID from filename (11 characters)
                            filename = os.path.basename(file_path)
                            # YouTube video IDs are 11 characters: letters, numbers, -, _
                            match = re.search(r'([A-Za-z0-9_-]{11})\.(mp4|mkv|webm)', filename)
                            if match:
                                video_id = match.group(1)
                                logger.debug("Extracted YouTube video ID from file path: %s", video_id)
                                break
                if video_id:
                    break

        # If not found in file path, try GUIDs
        if not video_id:
            guids = metadata.get("Guid", [])
            video_id = self._extract_youtube_id_from_guids(guids)

        # If still not found, try querying Plex API for more metadata
        if not video_id and metadata.get("ratingKey"):
            try:
                rating_key = metadata.get("ratingKey")
                plex_url = getattr(settings, "PLEX_SERVER_URL", None)
                plex_token = getattr(settings, "PLEX_TOKEN", None)
                if plex_url and plex_token:
                    api_url = f"{plex_url}/library/metadata/{rating_key}"
                    params = {"X-Plex-Token": plex_token}
                    r = requests.get(api_url, params=params, timeout=10)
                    if r.ok:
                        import xml.etree.ElementTree as ET
                        root = ET.fromstring(r.content)
                        
                        # First, try to extract video ID from file path in XML
                        for part_elem in root.findall('.//Part'):
                            file_path = part_elem.get("file")
                            if file_path:
                                filename = os.path.basename(file_path)
                                # YouTube video IDs are 11 characters: letters, numbers, -, _
                                match = re.search(r'([A-Za-z0-9_-]{11})\.(mp4|mkv|webm)', filename)
                                if match:
                                    video_id = match.group(1)
                                    logger.debug("Extracted YouTube video ID from Plex API file path: %s", video_id)
                                    break
                        
                        # If not found in file path, search guids in returned XML
                        if not video_id:
                            for guid_elem in root.findall('.//Guid'):
                                gid = guid_elem.get("id", "")
                                candidate = self._extract_youtube_id_from_guids([{"id": gid}])
                                if candidate:
                                    video_id = candidate
                                    break
            except Exception:
                logger.exception("Error querying Plex API for item metadata")

        if not video_id:
            logger.info("No YouTube video ID found in payload (file path, GUIDs, or Plex API)")
            return False


        # Try to find a matching Item by youtube_video_id (YouTube source first, then manual TV Show)
        episode_item = None
        try:
            # Try YouTube source first
            episode_item = app.models.Item.objects.filter(
                source=app.models.Sources.YOUTUBE.value,
                media_type=app.models.MediaTypes.EPISODE.value,
                youtube_video_id=video_id,
            ).first()
            if not episode_item:
                # Fallback: try manual TV Show with youtube_video_id
                episode_item = app.models.Item.objects.filter(
                    source=app.models.Sources.MANUAL.value,
                    media_type=app.models.MediaTypes.EPISODE.value,
                    youtube_video_id=video_id,
                ).first()
        except Exception:
            logger.exception("DB error while looking up YouTube or manual TV Show item")
            return False

        if not episode_item:
            logger.info("No local Item found with youtube_video_id=%s (YouTube or manual TV Show)", video_id)
            return False

        # Find season instance for this user, using the same source as the episode item
        try:
            season_instance = app.models.Season.objects.filter(
                item__media_id=episode_item.media_id,
                item__season_number=episode_item.season_number,
                item__source=episode_item.source,
                user=user,
            ).first()
        except Exception:
            logger.exception("DB error while looking up Season instance")
            return False

        if not season_instance:
            logger.info("User %s does not track the season for item %s (source=%s, year=%s)", user, episode_item.media_id, episode_item.source, episode_item.season_number)
            return False

        # Check if the related TV show has DROPPED or PAUSED status - skip if so
        if season_instance.related_tv and season_instance.related_tv.status in (
            app.models.Status.DROPPED.value,
            app.models.Status.PAUSED.value,
        ):
            logger.info(
                "Skipping YouTube episode update for '%s' - TV status is %s",
                episode_item.title,
                season_instance.related_tv.status,
            )
            return False

        # Create Episode directly with the specific Item we found
        # Don't use season_instance.watch() which searches by episode_number
        # and could match the wrong video
        now = timezone.now().replace(second=0, microsecond=0)
        episode = app.models.Episode.objects.create(
            related_season=season_instance,
            item=episode_item,
            end_date=now,
        )
        episode.save(auto_complete=False)
        logger.info("Marked YouTube video as played: %s (video_id=%s) for user %s", episode_item.title, video_id, user)
        return True

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
        
        # Method 0: Try to extract TMDB ID from file path (e.g., {tmdb-6809} in folder name)
        tmdb_id_from_path = self._extract_tmdb_id_from_file_path(payload)
        if tmdb_id_from_path:
            logger.info("Found series TMDB ID from file path: %s", tmdb_id_from_path)
            return tmdb_id_from_path
        
        # Method 1: Use Plex API to get series TMDB ID from grandparentRatingKey
        episode_guids = payload["Metadata"].get("Guid", [])
        episode_tmdb_id = self._get_id_from_guids(episode_guids, "tmdb")
        
        # Always try Plex API if we have grandparentRatingKey, even without episode TMDB ID
        series_tmdb_id = self._get_series_tmdb_from_plex_api(episode_tmdb_id, payload)
        if series_tmdb_id:
            logger.info("Successfully got series TMDB ID from Plex API: %s", series_tmdb_id)
            return series_tmdb_id
        
        if episode_tmdb_id:
            logger.warning("Plex API method failed, trying payload analysis...")
        
        # Method 2: Check if there's a grandparent section with GUIDs
        grandparent = payload["Metadata"].get("Grandparent", {})
        if grandparent and "Guid" in grandparent:
            tmdb_id = self._get_id_from_guids(grandparent["Guid"], "tmdb")
            if tmdb_id:
                logger.info("Found series TMDB ID from grandparent: %s", tmdb_id)
                return tmdb_id
        
        # Method 3: Check parent section with GUIDs
        parent = payload["Metadata"].get("Parent", {})
        if parent and "Guid" in parent:
            tmdb_id = self._get_id_from_guids(parent["Guid"], "tmdb")
            if tmdb_id:
                logger.info("Found series TMDB ID from parent: %s", tmdb_id)
                return tmdb_id
        
        # Method 4: Fallback to regular TMDB ID extraction from episode
        guids = payload["Metadata"].get("Guid", [])
        tmdb_id = self._get_id_from_guids(guids, "tmdb")
        if tmdb_id:
            logger.warning("Using fallback TMDB ID (might be episode-level): %s", tmdb_id)
            return tmdb_id
        
        logger.warning("No TMDB ID found for series")
        return None
    
    def _extract_tmdb_id_from_file_path(self, payload):
        """Extract TMDB ID from file path folder name (e.g., {tmdb-6809})."""
        import re
        
        metadata = payload.get("Metadata", {})
        media_list = metadata.get("Media", [])
        
        # First, try to get file path from payload
        file_path = None
        if media_list and isinstance(media_list, list):
            for media in media_list:
                parts = media.get("Part", [])
                if parts and isinstance(parts, list):
                    for part in parts:
                        file_path = part.get("file")
                        if file_path:
                            break
                if file_path:
                    break
        
        # If no file path in payload, query Plex API
        if not file_path:
            rating_key = metadata.get("ratingKey")
            if rating_key:
                file_path = self._get_file_path_from_plex_api(rating_key)
        
        if file_path:
            # Look for {tmdb-XXXX} pattern in the file path
            match = re.search(r'\{tmdb-(\d+)\}', file_path, re.IGNORECASE)
            if match:
                tmdb_id = match.group(1)
                logger.info("Extracted TMDB ID %s from file path: %s", tmdb_id, file_path)
                return tmdb_id
        
        return None
    
    def _get_file_path_from_plex_api(self, rating_key):
        """Get file path from Plex API using rating key."""
        import xml.etree.ElementTree as ET
        
        plex_url = getattr(settings, 'PLEX_SERVER_URL', None)
        plex_token = getattr(settings, 'PLEX_TOKEN', None)
        
        if not plex_url or not plex_token:
            logger.debug("Plex API credentials not configured, cannot get file path")
            return None
        
        try:
            api_url = f"{plex_url}/library/metadata/{rating_key}"
            params = {'X-Plex-Token': plex_token}
            
            response = requests.get(api_url, params=params, timeout=10)
            response.raise_for_status()
            
            root = ET.fromstring(response.content)
            
            # Find file path in Part element
            for part_elem in root.findall('.//Part'):
                file_path = part_elem.get('file')
                if file_path:
                    logger.debug("Got file path from Plex API: %s", file_path)
                    return file_path
            
            return None
            
        except Exception as e:
            logger.debug("Error getting file path from Plex API: %s", str(e))
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
