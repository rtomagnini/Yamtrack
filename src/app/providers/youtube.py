"""YouTube provider for episode metadata extraction."""

import re
import requests
import logging
from datetime import datetime
from django.conf import settings
from app.providers import services
from app.models import Sources

logger = logging.getLogger(__name__)


def extract_video_id(youtube_url):
    """
    Extract video ID from various YouTube URL formats.
    
    Supported formats:
    - https://www.youtube.com/watch?v=dQw4w9WgXcQ
    - https://youtu.be/dQw4w9WgXcQ
    - https://m.youtube.com/watch?v=dQw4w9WgXcQ
    - https://www.youtube.com/embed/dQw4w9WgXcQ
    """
    if not youtube_url:
        return None
    
    # YouTube URL patterns
    patterns = [
        r'(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/)([^&\n?#]+)',
        r'youtube\.com\/watch\?.*v=([^&\n?#]+)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, youtube_url)
        if match:
            return match.group(1)
    
    return None


def parse_duration(duration_str):
    """
    Parse YouTube duration from ISO 8601 format (PT4M13S) to minutes.
    
    Args:
        duration_str: ISO 8601 duration string (e.g., "PT4M13S")
    
    Returns:
        int: Duration in minutes, or None if parsing fails
    """
    if not duration_str:
        return None
    
    # Parse ISO 8601 duration format: PT4M13S = 4 minutes 13 seconds
    pattern = r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?'
    match = re.match(pattern, duration_str)
    
    if not match:
        return None
    
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    
    # Convert everything to minutes (rounded up if seconds > 0)
    total_minutes = hours * 60 + minutes
    if seconds > 0:
        total_minutes += 1
    
    return total_minutes if total_minutes > 0 else None


def parse_published_date(published_at):
    """
    Parse YouTube publishedAt date to Django date format.
    
    Args:
        published_at: ISO 8601 datetime string from YouTube API
    
    Returns:
        str: Date in YYYY-MM-DD format, or None if parsing fails
    """
    if not published_at:
        return None
    
    try:
        # Parse ISO 8601 datetime: 2023-05-15T14:30:00Z
        dt = datetime.fromisoformat(published_at.replace('Z', '+00:00'))
        return dt.strftime('%Y-%m-%d')
    except (ValueError, AttributeError):
        return None


def get_best_thumbnail(thumbnails):
    """
    Get the best quality thumbnail URL from YouTube thumbnails object.
    
    Args:
        thumbnails: YouTube API thumbnails object
    
    Returns:
        str: Best thumbnail URL, or None if no thumbnails available
    """
    if not thumbnails:
        return None
    
    # Priority order: maxres > high > medium > default
    for quality in ['maxres', 'high', 'medium', 'default']:
        if quality in thumbnails:
            return thumbnails[quality].get('url')
    
    return None


def fetch_video_metadata(video_id):
    """
    Fetch video metadata from YouTube Data API v3.
    
    Args:
        video_id: YouTube video ID
    
    Returns:
        dict: Video metadata with title, air_date, runtime, thumbnail
        None: If API call fails or video not found
    """
    if not video_id:
        return None
    
    # Check if YouTube API is configured
    api_key = getattr(settings, 'YOUTUBE_API_KEY', None)
    if not api_key:
        logger.warning("YouTube API key not configured")
        return None
    
    url = "https://www.googleapis.com/youtube/v3/videos"
    params = {
        'part': 'snippet,contentDetails',
        'id': video_id,
        'key': api_key,
    }
    
    try:
        # Make direct request to YouTube API
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        response = response.json()
        
        if not response.get('items'):
            logger.warning(f"YouTube video not found: {video_id}")
            return None
        
        video_data = response['items'][0]
        snippet = video_data.get('snippet', {})
        content_details = video_data.get('contentDetails', {})
        

        
        # Extract metadata
        title = snippet.get('title', '').strip()
        published_at = snippet.get('publishedAt')
        duration = content_details.get('duration')
        thumbnails = snippet.get('thumbnails', {})
        
        # Parse the data
        parsed_date = parse_published_date(published_at)
        parsed_duration = parse_duration(duration)
        best_thumbnail = get_best_thumbnail(thumbnails)
        
        return {
            'title': title if title else None,
            'published_date': parsed_date,
            'duration_minutes': parsed_duration,
            'thumbnail': best_thumbnail,
            'video_id': video_id,
        }
        
    except requests.exceptions.RequestException as e:
        logger.error(f"YouTube API request failed for video {video_id}: {e}")
        return None
    except Exception as e:
        logger.error(f"Error processing YouTube video {video_id}: {e}")
        return None


def extract_video_metadata(youtube_url):
    """
    Main function to extract episode metadata from YouTube URL.
    
    Args:
        youtube_url: Full YouTube URL
    
    Returns:
        dict: Episode metadata or None if extraction fails
    """
    video_id = extract_video_id(youtube_url)
    if not video_id:
        logger.warning(f"Could not extract video ID from URL: {youtube_url}")
        return None
    
    return fetch_video_metadata(video_id)