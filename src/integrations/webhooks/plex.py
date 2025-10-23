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

        # Handle library.new event for YouTube videos
        if event_type == "library.new":
            logger.info("Processing library.new event")
            try:
                handled = self._create_youtube_video_from_plex(payload, user)
                if handled:
                    logger.info("Successfully created YouTube video from library.new event")
                    return
                logger.debug("library.new event was not a YouTube video or failed to create")
            except Exception:
                logger.exception("Error creating YouTube video from library.new event")
            # If not handled as YouTube, fall through to normal processing
            # (though library.new for TV/Movie usually won't have watch status)

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

    def _create_youtube_video_from_plex(self, payload, user):
        """
        Create a YouTube video in Yamtrack when detected from Plex library.new event.
        
        Returns True if successfully created, False otherwise.
        """
        import app
        from datetime import datetime
        from django.utils import timezone
        from app.providers import youtube
        from app.models import Season, TV, Item, Status, Sources, MediaTypes
        
        metadata = payload.get("Metadata", {})
        guids = metadata.get("Guid", [])
        
        # Try to extract YouTube video ID from GUIDs
        video_id = self._extract_youtube_id_from_guids(guids)
        
        if not video_id:
            logger.debug("No YouTube video ID found in Plex payload GUIDs")
            return False
        
        logger.info("Detected YouTube video ID from Plex: %s", video_id)
        
        # Check if video already exists in Yamtrack
        existing_video = Item.objects.filter(
            source=Sources.YOUTUBE.value,
            media_type=MediaTypes.EPISODE.value,
            youtube_video_id=video_id,
        ).first()
        
        if existing_video:
            logger.info("YouTube video %s already exists in Yamtrack: %s", video_id, existing_video.title)
            return True  # Already exists, consider it handled
        
        # Fetch video metadata from YouTube
        try:
            video_metadata = youtube.fetch_video_metadata(video_id)
            if not video_metadata:
                logger.warning("Could not fetch YouTube metadata for video ID: %s", video_id)
                return False
        except Exception:
            logger.exception("Error fetching YouTube video metadata for %s", video_id)
            return False
        
        # Extract channel info
        channel_id = video_metadata.get("channel_id")
        if not channel_id:
            logger.warning("No channel ID in YouTube metadata for video %s", video_id)
            return False
        
        # Fetch channel metadata
        try:
            channel_metadata = youtube.fetch_channel_metadata(channel_id)
            if not channel_metadata:
                logger.warning("Could not fetch YouTube channel metadata for %s", channel_id)
                return False
        except Exception:
            logger.exception("Error fetching YouTube channel metadata for %s", channel_id)
            return False
        
        # Determine video year for season
        published_date = video_metadata.get("published_date")
        if published_date:
            try:
                video_year = datetime.strptime(published_date, "%Y-%m-%d").year
            except ValueError:
                video_year = datetime.now().year
        else:
            video_year = datetime.now().year
        
        # Find or create channel (TV instance)
        from django.db import models as django_models
        existing_tv = TV.objects.filter(
            user=user,
            item__source=Sources.YOUTUBE.value,
            item__media_type=MediaTypes.YOUTUBE.value,
        ).filter(
            django_models.Q(notes__contains=f"YouTube Channel ID: {channel_id}") |
            django_models.Q(item__media_id=channel_id)
        ).first()
        
        if existing_tv:
            channel_item = existing_tv.item
            tv_instance = existing_tv
            logger.info("Using existing YouTube channel: %s", channel_item.title)
        else:
            # Create new channel
            channel_item = Item.objects.create(
                media_id=Item.generate_next_id(Sources.YOUTUBE.value, MediaTypes.YOUTUBE.value),
                source=Sources.YOUTUBE.value,
                media_type=MediaTypes.YOUTUBE.value,
                title=channel_metadata.get("title", "Unknown Channel"),
                image=channel_metadata.get("thumbnail", ""),
            )
            
            tv_instance = TV.objects.create(
                user=user,
                item=channel_item,
                notes=f"YouTube Channel ID: {channel_id}",
                status=Status.IN_PROGRESS.value,
            )
            logger.info("Created new YouTube channel: %s", channel_item.title)
        
        # Find or create season for video year
        season_item, season_created = Item.objects.get_or_create(
            media_id=channel_item.media_id,
            source=Sources.YOUTUBE.value,
            media_type=MediaTypes.SEASON.value,
            season_number=video_year,
            defaults={
                "title": f"{channel_item.title} - {video_year}",
                "image": channel_item.image,
            }
        )
        
        if season_created:
            season_instance = Season.objects.create(
                user=user,
                item=season_item,
                related_tv=tv_instance,
                status=Status.IN_PROGRESS.value,
            )
            logger.info("Created new season: %s", season_item.title)
        else:
            season_instance = Season.objects.get(
                item=season_item,
                related_tv=tv_instance,
            )
        
        # Get next episode number for this season
        latest_episode = Item.objects.filter(
            media_id=channel_item.media_id,
            source=Sources.YOUTUBE.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=video_year,
        ).order_by('-episode_number').first()
        
        if latest_episode:
            episode_number = latest_episode.episode_number + 1
        else:
            episode_number = 1
        
        # Create episode item for the video
        episode_item = Item.objects.create(
            media_id=channel_item.media_id,
            source=Sources.YOUTUBE.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=video_year,
            episode_number=episode_number,
            title=video_metadata.get("title", "Unknown Video"),
            image=video_metadata.get("thumbnail", ""),
            air_date=published_date,
            runtime=video_metadata.get("duration_minutes", 0),
            youtube_video_id=video_id,
        )
        
        logger.info(
            "Successfully created YouTube video in Yamtrack: %s (video_id=%s, channel=%s, year=%s, ep=%d)",
            episode_item.title,
            video_id,
            channel_item.title,
            video_year,
            episode_number
        )
        
        return True

    def _attempt_handle_youtube(self, payload, user):
        """Attempt to find a YouTube video in the local DB and mark it as watched.

        Returns True if handled (marked as watched), False otherwise.
        """
        import app
        from django.utils import timezone

        metadata = payload.get("Metadata", {})
        guids = metadata.get("Guid", [])

        # Try to get a video id from GUIDs
        video_id = self._extract_youtube_id_from_guids(guids)

        # If not found, try to use ratingKey to query Plex for more metadata
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
                        # Search guids in returned XML
                        for guid_elem in root.findall('.//Guid'):
                            gid = guid_elem.get("id", "")
                            candidate = self._extract_youtube_id_from_guids([{"id": gid}])
                            if candidate:
                                video_id = candidate
                                break
            except Exception:
                logger.exception("Error querying Plex API for item metadata")

        # If still no video_id, try to match by title
        title = metadata.get("title")
        if not video_id and title:
            # Best-effort: exact title match among YouTube episode Items
            try:
                candidate = app.models.Item.objects.filter(
                    source=app.models.Sources.YOUTUBE.value,
                    media_type=app.models.MediaTypes.EPISODE.value,
                    title__iexact=title,
                ).first()
                if candidate:
                    video_id = candidate.youtube_video_id
            except Exception:
                logger.exception("Error searching for YouTube item by title")

        if not video_id:
            logger.info("No YouTube id or title match found in payload")
            return False

        # Find the matching Item by youtube_video_id
        try:
            episode_item = app.models.Item.objects.filter(
                source=app.models.Sources.YOUTUBE.value,
                media_type=app.models.MediaTypes.EPISODE.value,
                youtube_video_id=video_id,
            ).first()
        except Exception:
            logger.exception("DB error while looking up YouTube item")
            return False

        if not episode_item:
            logger.info("No local Item found with youtube_video_id=%s", video_id)
            return False

        # Find season instance for this user
        try:
            season_instance = app.models.Season.objects.filter(
                item__media_id=episode_item.media_id,
                item__season_number=episode_item.season_number,
                user=user,
            ).first()
        except Exception:
            logger.exception("DB error while looking up Season instance")
            return False

        if not season_instance:
            logger.info("User %s does not track the season for item %s (channel=%s, year=%s)", user, episode_item.media_id, episode_item.media_id, episode_item.season_number)
            return False

        # Check for recent duplicate episode records (same logic as TV handler)
        now = timezone.now().replace(second=0, microsecond=0)
        latest_episode = (
            app.models.Episode.objects.filter(
                item=episode_item,
                related_season=season_instance,
            )
            .order_by("-end_date")
            .first()
        )

        should_create = True
        if latest_episode and latest_episode.end_date:
            time_diff = abs((now - latest_episode.end_date).total_seconds())
            threshold = 5
            if time_diff < threshold:
                should_create = False

        if should_create:
            season_instance.watch(episode_item.episode_number, now, auto_complete=False)
            logger.info("Marked YouTube video as played: %s (video_id=%s) for user %s", episode_item.title, video_id, user)
            return True

        logger.debug("Skipping duplicate YouTube episode record for %s (video_id=%s)", episode_item.title, video_id)
        return True

    def _is_supported_event(self, event_type):
        return event_type in ("media.scrobble", "media.play", "library.new")

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
