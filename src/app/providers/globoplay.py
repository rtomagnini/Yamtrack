"""Globoplay provider for episode metadata extraction."""
import requests
import logging
import re
from datetime import datetime
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


def extract_video_id(globoplay_url):
    """
    Extract video ID from Globoplay URL.
    
    Example URL: https://globoplay.globo.com/v/14122115/
    Returns: 14122115
    """
    if not globoplay_url:
        return None
    
    match = re.search(r'/v/(\d+)', globoplay_url)
    if match:
        return match.group(1)
    
    return None


def fetch_video_metadata(video_id):
    """
    Fetch video metadata from Globoplay by scraping the page.
    
    Args:
        video_id: Globoplay video ID
    
    Returns:
        dict: Metadata with keys: title, thumbnail, duration_minutes, air_date
        None: If the request fails
    """
    if not video_id:
        return None
    
    url = f"https://globoplay.globo.com/v/{video_id}/"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'pt-BR,pt;q=0.9,en;q=0.8',
        'Referer': 'https://globoplay.globo.com/',
    }
    
    try:
        session = requests.Session()
        response = session.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Extract information from meta tags
        meta_data = {}
        for meta in soup.find_all('meta'):
            prop = meta.get('property', '') or meta.get('name', '')
            content = meta.get('content', '')
            if prop and content:
                meta_data[prop] = content
        
        title = meta_data.get('og:title') or meta_data.get('twitter:title', '')
        thumbnail = meta_data.get('og:image') or meta_data.get('twitter:image', '')
        
        # Try to get HD thumbnail from video ID
        if video_id:
            thumbnail = f"https://s04.video.glbimg.com/x1080/{video_id}.jpg"
        
        # Extract duration
        duration_minutes = _find_duration(soup, response.text)
        
        # Extract date
        air_date = _find_date(soup, title)
        
        metadata = {
            'title': title,
            'thumbnail': thumbnail,
            'duration_minutes': duration_minutes,
            'air_date': air_date,
        }
        
        logger.info(f"Successfully extracted Globoplay metadata for video {video_id}")
        return metadata
        
    except requests.RequestException as e:
        logger.error(f"Error fetching Globoplay metadata for video {video_id}: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error processing Globoplay metadata: {e}")
        return None


def _find_duration(soup, html_content):
    """Find the real duration of the video from HTML."""
    
    # Strategy 1: Look for meta video:duration
    meta_duration = soup.find('meta', property='video:duration')
    if meta_duration and meta_duration.get('content'):
        try:
            duration_sec = int(meta_duration['content'])
            return round(duration_sec / 60)
        except (ValueError, TypeError):
            pass
    
    # Strategy 2: Look for ISO duration format PT2H23M48.152S (with decimals)
    iso_match = re.search(r'"duration":"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?"', html_content)
    if iso_match:
        hours = int(iso_match.group(1)) if iso_match.group(1) else 0
        minutes = int(iso_match.group(2)) if iso_match.group(2) else 0
        seconds = float(iso_match.group(3)) if iso_match.group(3) else 0
        
        total_minutes = hours * 60 + minutes + round(seconds / 60)
        if total_minutes > 0:
            return total_minutes
    
    # Strategy 3: Look for duration in seconds
    duration_match = re.search(r'"duration":\s*(\d+)', html_content)
    if duration_match:
        try:
            duration_sec = int(duration_match.group(1))
            if duration_sec < 36000:  # Less than 10 hours
                return round(duration_sec / 60)
        except (ValueError, TypeError):
            pass
    
    return None


def _find_date(soup, title):
    """Find the publication date from HTML or title."""
    
    # Try to extract from JSON-LD
    for script in soup.find_all('script'):
        if script.string:
            date_match = re.search(r'"datePublished":\s*"([^"]+)"', script.string)
            if date_match:
                try:
                    date_str = date_match.group(1)
                    # Parse ISO format
                    dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                    return dt.strftime('%Y-%m-%d')
                except (ValueError, TypeError):
                    pass
    
    # Try to extract from title (dd/mm/yyyy format)
    if title:
        date_match = re.search(r'(\d{1,2})/(\d{1,2})/(\d{4})', title)
        if date_match:
            day, month, year = date_match.groups()
            try:
                return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
            except (ValueError, TypeError):
                pass
    
    return None
