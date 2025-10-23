import json
import logging
import re
import os
from datetime import datetime

from django.db import models as django_models
from django.utils import timezone

from app.models import MediaTypes, Sources, Status, Item, TV, Season, Episode
from app.providers import youtube
from .base import BaseWebhookProcessor

logger = logging.getLogger(__name__)


class TautulliWebhookProcessor(BaseWebhookProcessor):
    """Processor for Tautulli webhook events."""

    def process_payload(self, payload, user):
        """
        Process the incoming Tautulli webhook payload.
        
        Tautulli sends webhook data as JSON with notification parameters.
        For "Recently Added" events, we check if the added media is a YouTube video
        and create it in Yamtrack.
        """
        logger.debug("Received Tautulli webhook payload: %s", json.dumps(payload, indent=2))
        
        # Extract action (event type) from payload
        # Tautulli typically sends this in the 'action' field or can be inferred
        action = payload.get("action", "")
        media_type = payload.get("media_type", "")
        
        logger.info("Processing Tautulli webhook: action=%s, media_type=%s", action, media_type)
        
        # We only care about "created" or "recently_added" events
        # Check various possible action names Tautulli might send
        if action not in ("created", "recently_added", "on_created"):
            logger.debug("Ignoring Tautulli webhook action: %s (not a Recently Added event)", action)
            return
        
        # Extract file path from payload
        file_path = payload.get("file") or payload.get("filename")
        
        if not file_path:
            logger.debug("No file path in Tautulli payload, skipping")
            return
        
        logger.info("Tautulli Recently Added: file=%s", file_path)
        
        # Check if this looks like a YouTube video
        if not self._looks_like_youtube_video(file_path):
            logger.debug("File path does not appear to be a YouTube video: %s", file_path)
            return
        
        # Attempt to create YouTube video
        try:
            handled = self._create_youtube_video_from_tautulli(payload, user)
            if handled:
                logger.info("Successfully created YouTube video from Tautulli Recently Added event")
            else:
                logger.warning("Failed to create YouTube video from Tautulli event")
        except Exception:
            logger.exception("Error creating YouTube video from Tautulli event")

    def _looks_like_youtube_video(self, file_path):
        """
        Quick check to see if a file path looks like a YouTube video.
        
        Checks:
        - File path contains YouTube video ID pattern (11 chars)
        - File path contains YouTube channel ID pattern (UC...)
        
        Returns True if any indicator found, False otherwise.
        """
        if not file_path:
            return False
        
        # Get filename without extension
        filename = os.path.splitext(os.path.basename(file_path))[0]
        
        # YouTube video ID: 11 characters [A-Za-z0-9_-]{11}
        if re.match(r'^[A-Za-z0-9_-]{11}$', filename):
            logger.debug("File path matches YouTube video ID pattern: %s", filename)
            return True
        
        # YouTube channel ID in path: UC followed by 22 characters
        if re.search(r'/UC[A-Za-z0-9_-]{22}/', file_path):
            logger.debug("File path contains YouTube channel ID pattern")
            return True
        
        return False

    def _extract_youtube_id_from_file_path(self, file_path):
        """
        Extract YouTube video ID from file path.
        
        Common patterns for TubeArchivist and similar tools:
        - /path/to/CHANNEL_ID/VIDEO_ID.mp4
        - /path/to/VIDEO_ID.mp4
        
        YouTube video IDs are typically 11 characters: [A-Za-z0-9_-]{11}
        """
        if not file_path:
            return None
        
        # Get the filename without extension
        filename = os.path.splitext(os.path.basename(file_path))[0]
        
        # YouTube video ID pattern: 11 characters, alphanumeric plus _ and -
        video_id_pattern = r'^[A-Za-z0-9_-]{11}$'
        
        if re.match(video_id_pattern, filename):
            logger.debug("Extracted YouTube video ID from filename: %s", filename)
            return filename
        
        return None

    def _extract_youtube_channel_id_from_file_path(self, file_path):
        """
        Extract YouTube channel ID from file path.
        
        YouTube channel IDs typically start with UC and are 24 characters.
        Common in TubeArchivist: /path/CHANNEL_ID/VIDEO_ID.mp4
        """
        if not file_path:
            return None
        
        # Get parent directory name
        parent_dir = os.path.basename(os.path.dirname(file_path))
        
        # YouTube channel ID pattern: starts with UC, typically 24 chars
        channel_id_pattern = r'^UC[A-Za-z0-9_-]{22}$'
        
        if re.match(channel_id_pattern, parent_dir):
            logger.debug("Extracted YouTube channel ID from path: %s", parent_dir)
            return parent_dir
        
        return None

    def _create_youtube_video_from_tautulli(self, payload, user):
        """
        Create a YouTube video in Yamtrack when detected from Tautulli Recently Added event.
        
        Returns True if successfully created, False otherwise.
        """
        # Extract file path
        file_path = payload.get("file") or payload.get("filename")
        
        if not file_path:
            logger.debug("No file path in Tautulli payload")
            return False
        
        # Extract YouTube video ID from file path
        video_id = self._extract_youtube_id_from_file_path(file_path)
        channel_id_from_path = self._extract_youtube_channel_id_from_file_path(file_path)
        
        if not video_id:
            logger.debug("No YouTube video ID found in file path: %s", file_path)
            return False
        
        logger.info("Detected YouTube video ID from Tautulli: %s", video_id)
        
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
        
        # Fallback: use channel ID from file path if YouTube API didn't provide one
        if not channel_id and channel_id_from_path:
            logger.info("Using channel ID from file path as fallback: %s", channel_id_from_path)
            channel_id = channel_id_from_path
        
        if not channel_id:
            logger.warning("No channel ID in YouTube metadata or file path for video %s", video_id)
            return False
        
        # Check if this channel is blocked/filtered for this user
        from app.models import YouTubeChannelFilter
        
        is_filtered = YouTubeChannelFilter.objects.filter(
            user=user,
            channel_id=channel_id,
        ).exists()
        
        if is_filtered:
            logger.info(
                "YouTube channel %s is filtered for user %s. Skipping video creation.",
                channel_id,
                user.username,
            )
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
            "Created YouTube video item from Tautulli: %s S%sE%s - %s",
            channel_item.title,
            video_year,
            episode_number,
            episode_item.title,
        )
        
        # Don't create Episode instance automatically - only create the Item
        # This follows the same logic as manual YouTube video creation
        # Users can mark it as watched later if needed
        
        return True
