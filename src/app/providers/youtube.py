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
        channel_id = snippet.get('channelId')
        channel_title = snippet.get('channelTitle')
        
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
            'channel_id': channel_id,
            'channel_title': channel_title,
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


def extract_channel_id(youtube_url):
    """
    Extract channel ID or handle from various YouTube channel URL formats.
    
    Supported formats:
    - https://www.youtube.com/@channelname
    - https://www.youtube.com/c/channelname  
    - https://www.youtube.com/channel/UC...
    - https://www.youtube.com/user/username
    - https://youtube.com/victorabadg (legacy format)
    """
    if not youtube_url:
        return None
    
    # Clean the URL
    youtube_url = youtube_url.strip()
    
    # YouTube channel URL patterns
    patterns = [
        r'youtube\.com/@([^/\?&\s]+)',  # @handle format
        r'youtube\.com/c/([^/\?&\s]+)',  # /c/ format
        r'youtube\.com/channel/([^/\?&\s]+)',  # /channel/ format  
        r'youtube\.com/user/([^/\?&\s]+)',  # /user/ format
        r'youtube\.com/([^/\?&\s]+)$',  # Legacy direct username format
    ]
    
    for pattern in patterns:
        match = re.search(pattern, youtube_url)
        if match:
            extracted = match.group(1)
            # Skip common paths that aren't usernames
            if extracted.lower() not in ['watch', 'playlist', 'results', 'feed', 'trending']:
                return extracted
    
    return None


def fetch_channel_metadata(channel_identifier):
    """
    Fetch channel metadata from YouTube API.
    
    Args:
        channel_identifier: Channel ID, handle, or username
    
    Returns:
        dict: Channel metadata or None if fetch fails
    """
    api_key = settings.YOUTUBE_API_KEY
    if not api_key:
        logger.error("YouTube API key not configured")
        return None
    
    try:
        # Try different ways to get channel info
        # First try as channel ID
        if channel_identifier.startswith('UC') and len(channel_identifier) == 24:
            channel_id = channel_identifier
        else:
            # Try to resolve handle/username to channel ID
            if channel_identifier.startswith('@'):
                # Handle format (@channelname)
                handle = channel_identifier[1:]  # Remove @
                channel_id = resolve_handle_to_channel_id(handle, api_key)
            else:
                # Legacy username or custom URL
                channel_id = resolve_username_to_channel_id(channel_identifier, api_key)
            
            if not channel_id:
                logger.warning(f"Could not resolve channel identifier: {channel_identifier}")
                return None
        
        # Fetch channel details
        url = "https://www.googleapis.com/youtube/v3/channels"
        params = {
            'key': api_key,
            'id': channel_id,
            'part': 'snippet,statistics'
        }
        
        response = requests.get(url, params=params, timeout=10).json()
        
        if not response.get('items'):
            logger.warning(f"No channel found for ID: {channel_id}")
            return None
        
        channel_data = response['items'][0]
        snippet = channel_data.get('snippet', {})
        statistics = channel_data.get('statistics', {})
        
        # Extract metadata
        title = snippet.get('title', '').strip()
        description = snippet.get('description', '').strip()
        thumbnails = snippet.get('thumbnails', {})
        subscriber_count = statistics.get('subscriberCount', '0')
        
        best_thumbnail = get_best_thumbnail(thumbnails)
        
        return {
            'title': title if title else None,
            'description': description if description else None,
            'thumbnail': best_thumbnail,
            'channel_id': channel_id,
            'subscriber_count': subscriber_count,
        }
        
    except requests.exceptions.RequestException as e:
        logger.error(f"YouTube API request failed for channel {channel_identifier}: {e}")
        return None
    except Exception as e:
        logger.error(f"Error processing YouTube channel {channel_identifier}: {e}")
        return None


def resolve_handle_to_channel_id(handle, api_key):
    """Resolve @handle to channel ID using YouTube API search."""
    try:
        url = "https://www.googleapis.com/youtube/v3/search"
        params = {
            'key': api_key,
            'q': f"@{handle}",
            'type': 'channel',
            'part': 'snippet',
            'maxResults': 1
        }
        
        response = requests.get(url, params=params, timeout=10).json()
        
        if response.get('items'):
            return response['items'][0]['snippet']['channelId']
            
    except Exception as e:
        logger.error(f"Error resolving handle {handle}: {e}")
    
    return None


def resolve_username_to_channel_id(username, api_key):
    """Resolve legacy username to channel ID using YouTube API."""
    try:
        # First try the forUsername parameter
        url = "https://www.googleapis.com/youtube/v3/channels"
        params = {
            'key': api_key,
            'forUsername': username,
            'part': 'id'
        }
        
        response = requests.get(url, params=params, timeout=10).json()
        
        if response.get('items'):
            return response['items'][0]['id']
        
        # If forUsername fails, try searching for the username
        search_url = "https://www.googleapis.com/youtube/v3/search"
        search_params = {
            'key': api_key,
            'q': username,
            'type': 'channel',
            'part': 'snippet',
            'maxResults': 5
        }
        
        search_response = requests.get(search_url, params=search_params, timeout=10).json()
        
        if search_response.get('items'):
            # Look for exact match or close match
            for item in search_response['items']:
                channel_title = item['snippet']['title'].lower()
                custom_url = item['snippet'].get('customUrl', '').lower()
                
                # Check if title or customUrl matches
                if (username.lower() in channel_title or 
                    custom_url == username.lower() or
                    channel_title == username.lower()):
                    return item['snippet']['channelId']
            
            # If no exact match, return the first result
            return search_response['items'][0]['snippet']['channelId']
            
    except Exception as e:
        logger.error(f"Error resolving username {username}: {e}")
    
    return None


def search(query, page=1):
    """
    Search for YouTube channels based on a query.
    
    Args:
        query: Search query string
        page: Page number (default: 1)
    
    Returns:
        dict: Search results in the expected format
    """
    try:
        # Check if YouTube API key is configured
        api_key = getattr(settings, 'YOUTUBE_API_KEY', None)
        if not api_key:
            logger.warning("YouTube API key not configured, returning empty results")
            return {
                "results": [],
                "total_pages": 0,
                "total_results": 0,
                "page": page
            }
        
        # YouTube API search parameters
        max_results = 20  # Number of results per page
        params = {
            'part': 'snippet',
            'q': query,
            'type': 'channel',  # Search only for channels
            'maxResults': max_results,
            'key': api_key
        }
        
        # Handle pagination
        if page > 1:
            # For simplicity, we'll use offset-based pagination
            # YouTube API uses pageToken, but this is a basic implementation
            pass
        
        response = requests.get(
            'https://www.googleapis.com/youtube/v3/search',
            params=params,
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        
        # Process the results
        results = []
        for item in data.get('items', []):
            snippet = item.get('snippet', {})
            channel_id = snippet.get('channelId')
            
            if not channel_id:
                continue
                
            # Get additional channel metadata
            channel_data = fetch_channel_metadata(channel_id)
            
            result_item = {
                'media_id': channel_id,
                'title': snippet.get('title', ''),
                'image': snippet.get('thumbnails', {}).get('high', {}).get('url', ''),
                'synopsis': snippet.get('description', ''),
                'source': 'youtube',
                'media_type': 'youtube',
            }
            
            # Add additional metadata if available
            if channel_data:
                result_item.update({
                    'subscriber_count': channel_data.get('subscriber_count'),
                    'video_count': channel_data.get('video_count'),
                    'view_count': channel_data.get('view_count'),
                })
            
            results.append(result_item)
        
        # Calculate total pages (YouTube API doesn't provide exact totals)
        total_results = data.get('pageInfo', {}).get('totalResults', len(results))
        total_pages = max(1, (total_results + max_results - 1) // max_results)
        
        return {
            "results": results,
            "total_pages": min(total_pages, 50),  # Limit to reasonable number
            "total_results": total_results,
            "page": page
        }
        
    except requests.exceptions.RequestException as e:
        logger.error(f"YouTube search API request failed: {e}")
        return {
            "results": [],
            "total_pages": 0,
            "total_results": 0,
            "page": page,
            "error": "YouTube search temporarily unavailable"
        }
    except Exception as e:
        logger.error(f"Error in YouTube search: {e}")
        return {
            "results": [],
            "total_pages": 0,
            "total_results": 0,
            "page": page,
            "error": "Search error occurred"
        }


def channel(channel_id):
    """
    Get comprehensive metadata for a YouTube channel.
    
    Args:
        channel_id: YouTube channel ID
    
    Returns:
        dict: Channel metadata in the expected format for Yamtrack
    """
    try:
        # Get channel metadata
        channel_data = fetch_channel_metadata(channel_id)
        
        if not channel_data:
            logger.warning(f"No metadata found for channel {channel_id}")
            return {
                'title': f'Channel {channel_id}',
                'image': '',
                'synopsis': '',
                'media_type': 'youtube',
                'source': 'youtube',
                'media_id': channel_id,
                'videos': [],
            }
        
        # Format the response in the expected Yamtrack structure
        return {
            'title': channel_data.get('title', f'Channel {channel_id}'),
            'image': channel_data.get('thumbnail', ''),
            'synopsis': channel_data.get('description', ''),
            'media_type': 'youtube',
            'source': 'youtube',
            'media_id': channel_id,
            'subscriber_count': channel_data.get('subscriber_count'),
            'video_count': channel_data.get('video_count'),
            'view_count': channel_data.get('view_count'),
            'channel_url': channel_data.get('custom_url', ''),
            'published_at': channel_data.get('published_at'),
            'videos': [],  # Videos will be loaded separately when viewing the channel
            'genres': [],  # YouTube channels don't have genres like other media
        }
        
    except Exception as e:
        logger.error(f"Error getting channel metadata for {channel_id}: {e}")
        return {
            'title': f'Channel {channel_id}',
            'image': '',
            'synopsis': f'Error loading channel: {str(e)}',
            'media_type': 'youtube',
            'source': 'youtube',
            'media_id': channel_id,
            'videos': [],
        }