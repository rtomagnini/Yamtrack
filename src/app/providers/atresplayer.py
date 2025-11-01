"""Atresplayer provider for episode metadata extraction."""
import requests
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def extract_episode_id(atresplayer_url):
    """
    Extract episode ID from Atresplayer URL.
    
    Example URL: https://www.atresplayer.com/antena3/programas/el-hormiguero/temporada-1/capitulo-123_123456789/
    Returns: 123456789
    """
    if not atresplayer_url:
        return None
    
    # Extract the ID after the last underscore
    if "_" in atresplayer_url:
        episode_id = atresplayer_url.rstrip("/.").split("_")[-1]
        return episode_id
    
    return None


def fetch_video_metadata(episode_id):
    """
    Fetch video metadata from Atresplayer API.
    
    Args:
        episode_id: Atresplayer episode ID
    
    Returns:
        dict: Metadata with keys: title, thumbnail, duration_minutes, air_date
        None: If the request fails
    """
    if not episode_id:
        return None
    
    try:
        api_url = f"https://api.atresplayer.com/client/v1/page/episode/{episode_id}"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        
        response = requests.get(api_url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        # Extract title
        title = data.get("title", "")
        
        # Extract thumbnail
        imagen_path = (
            data.get("image", {}).get("pathHorizontal")
            or data.get("image", {}).get("images", {}).get("HORIZONTAL", {}).get("path")
        )
        thumbnail = f"{imagen_path}1920x1080.jpg" if imagen_path else None
        
        # Extract duration (convert from seconds to minutes)
        duration_seconds = int(data.get("duration", 0))
        duration_minutes = round(duration_seconds / 60) if duration_seconds else None
        
        # Extract publication date
        publication_timestamp = data.get("publicationDate")
        air_date = None
        if publication_timestamp:
            try:
                dt = datetime.fromtimestamp(publication_timestamp / 1000)
                air_date = dt.strftime('%Y-%m-%d')
            except (ValueError, OSError):
                logger.warning(f"Could not parse Atresplayer publication date: {publication_timestamp}")
        
        metadata = {
            'title': title,
            'thumbnail': thumbnail,
            'duration_minutes': duration_minutes,
            'air_date': air_date,
        }
        
        logger.info(f"Successfully extracted Atresplayer metadata for episode {episode_id}")
        return metadata
        
    except requests.RequestException as e:
        logger.error(f"Error fetching Atresplayer metadata for episode {episode_id}: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error processing Atresplayer metadata: {e}")
        return None
