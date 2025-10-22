import logging

import requests
from django.conf import settings
from django.core.cache import cache

from app import helpers
from app.models import MediaTypes, Sources
from app.providers import services

logger = logging.getLogger(__name__)
base_url = "https://api.themoviedb.org/3"
base_params = {
    "api_key": settings.TMDB_API,
    "language": settings.TMDB_LANG,
}


def handle_error(error):
    """Handle TMDB API errors."""
    error_resp = error.response
    status_code = error_resp.status_code

    try:
        error_json = error_resp.json()
    except requests.exceptions.JSONDecodeError as json_error:
        logger.exception("Failed to decode JSON response")
        raise services.ProviderAPIError(Sources.TMDB.value, error) from json_error

    # Handle authentication errors
    if status_code == requests.codes.unauthorized:
        details = error_json.get("status_message")
        if details:
            # Remove trailing period if present
            details = details.rstrip(".")
            raise services.ProviderAPIError(Sources.TMDB.value, error, details)

    raise services.ProviderAPIError(
        Sources.TMDB.value,
        error,
    )


def search(media_type, query, page):
    """Search for media on TMDB."""
    cache_key = f"search_{Sources.TMDB.value}_{media_type}_{query}_{page}"
    data = cache.get(cache_key)

    if data is None:
        url = f"{base_url}/search/{media_type}"

        params = {
            **base_params,
            "query": query,
            "page": page,
        }

        if settings.TMDB_NSFW:
            params["include_adult"] = "true"

        try:
            response = services.api_request(
                Sources.TMDB.value,
                "GET",
                url,
                params=params,
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        results = [
            {
                "media_id": media["id"],
                "source": Sources.TMDB.value,
                "media_type": media_type,
                "title": get_title(media),
                "image": get_image_url(media["poster_path"]),
            }
            for media in response["results"]
        ]

        total_results = response["total_results"]
        per_page = 20  # TMDB always returns 20 results per page
        data = helpers.format_search_response(
            page,
            per_page,
            total_results,
            results,
        )

        cache.set(cache_key, data)

    return data


def find(external_id, external_source):
    """Search for media on TMDB."""
    cache_key = f"find_{Sources.TMDB.value}_{external_id}_{external_source}"
    data = cache.get(cache_key)

    if data is None:
        url = f"{base_url}/find/{external_id}"

        params = {
            **base_params,
            "external_source": external_source,
        }

        try:
            response = services.api_request(
                Sources.TMDB.value,
                "GET",
                url,
                params=params,
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        cache.set(cache_key, response)
        return response

    return data


def movie(media_id):
    """Return the metadata for the selected movie from The Movie Database."""
    cache_key = f"{Sources.TMDB.value}_{MediaTypes.MOVIE.value}_{media_id}"
    data = cache.get(cache_key)

    if data is None:
        url = f"{base_url}/movie/{media_id}"
        params = {
            **base_params,
            "append_to_response": "recommendations",
        }

        try:
            response = services.api_request(
                Sources.TMDB.value,
                "GET",
                url,
                params=params,
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        data = {
            "media_id": media_id,
            "source": Sources.TMDB.value,
            "source_url": f"https://www.themoviedb.org/movie/{media_id}",
            "media_type": MediaTypes.MOVIE.value,
            "title": response["title"],
            "max_progress": 1,
            "image": get_image_url(response["poster_path"]),
            "synopsis": get_synopsis(response["overview"]),
            "genres": get_genres(response["genres"]),
            "score": get_score(response["vote_average"]),
            "score_count": response["vote_count"],
            "details": {
                "format": "Movie",
                "release_date": get_start_date(response["release_date"]),
                "status": response["status"],
                "runtime": get_readable_duration(response["runtime"]),
                "studios": get_companies(response["production_companies"]),
                "country": get_country(response["production_countries"]),
                "languages": get_languages(response["spoken_languages"]),
            },
            "related": {
                "recommendations": get_related(
                    response.get("recommendations", {}).get("results", [])[:15],
                    MediaTypes.MOVIE.value,
                ),
            },
        }

        cache.set(cache_key, data)

    return data


def tv_with_seasons(media_id, season_numbers):
    """Return the metadata for the tv show with a season appended to the response."""
    if season_numbers == []:
        return tv(media_id)

    url = f"{base_url}/tv/{media_id}"
    base_append = "recommendations,external_ids"
    tv_cache_key = f"{Sources.TMDB.value}_{MediaTypes.TV.value}_{media_id}"
    data = cache.get(tv_cache_key, {})

    uncached_seasons = []
    for season_number in season_numbers:
        season_cache_key = (
            f"{Sources.TMDB.value}_{MediaTypes.SEASON.value}_{media_id}_{season_number}"
        )
        season_data = cache.get(season_cache_key)
        if season_data:
            data[f"season/{season_number}"] = season_data
        else:
            uncached_seasons.append(season_number)

    # tmdb max remote request is 20 but we have recommendations and external_ids
    max_seasons_per_request = 18
    for i in range(0, len(uncached_seasons), max_seasons_per_request):
        season_subset = uncached_seasons[i : i + max_seasons_per_request]
        append_text = ",".join([f"season/{season}" for season in season_subset])

        params = {
            **base_params,
            "append_to_response": f"{base_append},{append_text}"
            if append_text
            else base_append,
        }

        try:
            # Debug: Log stack trace to see who's calling this
            import traceback
            import logging
            logger = logging.getLogger(__name__)
            logger.error("TMDB tv_with_seasons called for media_id=%s with seasons: %s", media_id, season_subset)
            logger.error("Stack trace:\n%s", ''.join(traceback.format_stack()))
            
            response = services.api_request(
                Sources.TMDB.value,
                "GET",
                url,
                params=params,
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        # tv show metadata is not in the response
        if "media_id" not in data:
            tv_data = process_tv(response)
            cache.set(tv_cache_key, tv_data)

            # merge tv show metadata with seasons metadata
            data = tv_data | data

        # add seasons metadata to the response
        for season_number in season_subset:
            season_key = f"season/{season_number}"
            if season_key not in response:
                msg = (
                    f"Season {season_number} not found in {Sources.TMDB.label} "
                    f"with ID {media_id}."
                )
                # Create a new response object with 404 status
                not_found_response = requests.Response()
                not_found_response.status_code = 404
                # Set the error attribute to match what ProviderAPIError expects
                not_found_error = type("Error", (), {"response": not_found_response})
                raise services.ProviderAPIError(msg, error=not_found_error, details=msg)

            season_data = process_season(response[season_key])

            # add from tv show metadata to the season metadata
            season_data["media_id"] = media_id
            season_data["source_url"] = (
                f"https://www.themoviedb.org/tv/{media_id}/season/{season_number}"
            )
            season_data["title"] = data["title"]
            season_data["tvdb_id"] = data["tvdb_id"]
            season_data["genres"] = data["genres"]
            season_data["original_language"] = data.get("original_language", "en")
            if season_data["synopsis"] == "No synopsis available.":
                season_data["synopsis"] = data["synopsis"]
            cache.set(
                f"{Sources.TMDB.value}_{MediaTypes.SEASON.value}_{media_id}_{season_number}",
                season_data,
            )
            data[season_key] = season_data

    return data


def tv(media_id):
    """Return the metadata for the selected tv show from The Movie Database."""
    cache_key = f"{Sources.TMDB.value}_{MediaTypes.TV.value}_{media_id}"
    data = cache.get(cache_key)

    if data is None:
        url = f"{base_url}/tv/{media_id}"
        params = {
            **base_params,
            "append_to_response": "recommendations,external_ids",
        }

        try:
            response = services.api_request(
                Sources.TMDB.value,
                "GET",
                url,
                params=params,
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        data = process_tv(response)
        cache.set(cache_key, data)

    return data


def process_tv(response):
    """Process the metadata for the selected tv show from The Movie Database."""
    num_episodes = response["number_of_episodes"]
    next_episode = response.get("next_episode_to_air")
    last_episode = response.get("last_episode_to_air")
    return {
        "media_id": response["id"],
        "source": Sources.TMDB.value,
        "source_url": f"https://www.themoviedb.org/tv/{response['id']}",
        "media_type": MediaTypes.TV.value,
        "title": response["name"],
        "max_progress": num_episodes,
        "image": get_image_url(response["poster_path"]),
        "synopsis": get_synopsis(response["overview"]),
        "genres": get_genres(response["genres"]),
        "score": get_score(response["vote_average"]),
        "score_count": response["vote_count"],
        "original_language": response.get("original_language", "en"),
        "details": {
            "format": "TV",
            "first_air_date": get_start_date(response["first_air_date"]),
            "last_air_date": response["last_air_date"],
            "status": response["status"],
            "seasons": response["number_of_seasons"],
            "episodes": num_episodes,
            "runtime": get_runtime_tv(response["episode_run_time"]),
            "studios": get_companies(response["production_companies"]),
            "country": get_country(response["production_countries"]),
            "languages": get_languages(response["spoken_languages"]),
        },
        "related": {
            "seasons": get_related(
                response["seasons"],
                MediaTypes.SEASON.value,
                response,
            ),
            "recommendations": get_related(
                response.get("recommendations", {}).get("results", [])[:15],
                MediaTypes.TV.value,
            ),
        },
        "tvdb_id": response.get("external_ids", {}).get("tvdb_id"),
        "last_episode_season": last_episode["season_number"] if last_episode else None,
        "next_episode_season": next_episode["season_number"] if next_episode else None,
    }


def process_season(response):
    """Process the metadata for the selected season from The Movie Database."""
    episodes = response["episodes"]
    num_episodes = len(episodes)

    runtimes = []
    total_runtime = 0
    score_count = 0

    for episode in episodes:
        if episode["runtime"] is not None:
            runtimes.append(episode["runtime"])
            total_runtime += episode["runtime"]
        score_count += episode["vote_count"]

    avg_runtime = (
        get_readable_duration(sum(runtimes) / len(runtimes)) if runtimes else None
    )
    total_runtime = get_readable_duration(total_runtime) if total_runtime else None

    return {
        "source": Sources.TMDB.value,
        "media_type": MediaTypes.SEASON.value,
        "season_title": response["name"],
        "max_progress": episodes[-1]["episode_number"] if episodes else 0,
        "image": get_image_url(response["poster_path"]),
        "season_number": response["season_number"],
        "synopsis": get_synopsis(response["overview"]),
        "score": get_score(response["vote_average"]),
        "score_count": score_count,
        "details": {
            "first_air_date": get_start_date(response["air_date"]),
            "last_air_date": get_end_date(response),
            "episodes": num_episodes,
            "runtime": avg_runtime,
            "total_runtime": total_runtime,
        },
        "episodes": response["episodes"],
    }


def get_format(media_type):
    """Return media_type capitalized."""
    if media_type == MediaTypes.TV.value:
        return "TV"
    return "Movie"


def get_image_url(path):
    """Return the image URL for the media."""
    # when no image, value from response is null
    # e.g movie: 445290
    if path:
        return f"https://image.tmdb.org/t/p/w500{path}"
    return settings.IMG_NONE


def get_title(response):
    """Return the title for the media."""
    # tv shows have name instead of title
    try:
        return response["title"]
    except KeyError:
        return response["name"]


def get_start_date(date):
    """Return the start date for the media."""
    # when unknown date, value from response is empty string
    # e.g movie: 445290
    if date == "":
        return None
    return date


def get_end_date(response):
    """Return the last air date for the season."""
    if response["episodes"]:
        return response["episodes"][-1]["air_date"]

    return None


def get_synopsis(text):
    """Return the synopsis for the media."""
    # when unknown synopsis, value from response is empty string
    # e.g movie: 445290
    if text == "":
        return "No synopsis available."
    return text


def get_readable_duration(duration):
    """Convert duration in minutes to a readable format."""
    # if unknown movie runtime, value from response is 0
    # e.g movie: 274613
    if duration:
        hours, minutes = divmod(int(duration), 60)
        return f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
    return None


def get_runtime_tv(runtime):
    """Return the runtime for the tv show."""
    # when unknown runtime, value from response is empty list
    # e.g: tv:66672
    if runtime:
        return get_readable_duration(runtime[0])
    return None


def season_scores_count(response):
    """Return the scores count for the season."""
    return sum(episode["vote_count"] for episode in response["episodes"])


def get_genres(genres):
    """Return the genres for the media."""
    # when unknown genres, value from response is empty list
    # e.g tv: 24795
    if genres:
        return [genre["name"] for genre in genres]
    return None


def get_country(countries):
    """Return the production country for the media."""
    # when unknown production country, value from response is empty list
    # e.g tv: 24795
    if countries:
        return countries[0]["name"]
    return None


def get_languages(languages):
    """Return the languages for the media."""
    # when unknown spoken languages, value from response is empty list
    # e.g tv: 24795
    if languages:
        return [language["english_name"] for language in languages]
    return None


def get_companies(companies):
    """Return the production companies for the media."""
    # when unknown production companies, value from response is empty list
    # e.g tv: 24795
    if companies:
        return [company["name"] for company in companies[:3]]
    return None


def get_score(score):
    """Return the score for the media with one decimal place."""
    # when unknown score, value from response is 0.0

    return round(score, 1)


def get_related(related_medias, media_type, parent_response=None):
    """Return list of related media for the selected media."""
    related = []
    for media in related_medias:
        data = {
            "source": Sources.TMDB.value,
            "media_type": media_type,
            "image": get_image_url(media["poster_path"]),
        }
        if media_type == MediaTypes.SEASON.value:
            data["media_id"] = parent_response["id"]
            data["title"] = parent_response["name"]
            data["season_number"] = media["season_number"]
            data["season_title"] = media["name"]
            data["first_air_date"] = get_start_date(media["air_date"])
            data["max_progress"] = media["episode_count"]
        else:
            data["media_id"] = media["id"]
            data["title"] = get_title(media)
        related.append(data)
    return related


def is_generic_episode_name(title):
    """Check if an episode title is generic (like 'Episode 1', 'Episódio 1', etc.)"""
    if not title or not title.strip():
        return True
    
    title_lower = title.lower().strip()
    
    # Common generic patterns in multiple languages
    generic_patterns = [
        r'^episode\s+\d+$',      # "Episode 1", "Episode 2"
        r'^episódio\s+\d+$',     # "Episódio 1", "Episódio 2" 
        r'^episodio\s+\d+$',     # "Episodio 1", "Episodio 2"
        r'^ep\s+\d+$',           # "Ep 1", "Ep 2"
        r'^capítulo\s+\d+$',     # "Capítulo 1", "Capítulo 2"
        r'^capitulo\s+\d+$',     # "Capitulo 1", "Capitulo 2"
        r'^\d+$',                # "1", "2", "3"
        r'^#\d+$',               # "#1", "#2", "#3"
    ]
    
    import re
    for pattern in generic_patterns:
        if re.match(pattern, title_lower):
            return True
    
    return False


def fetch_episodes_by_language(media_id, season_number, language):
    """Fetch episode data for a specific language."""
    url = f"{base_url}/tv/{media_id}/season/{season_number}"
    cache_key = f"{Sources.TMDB.value}_episodes_{language}_{media_id}_{season_number}"
    
    # Check cache first
    cached_episodes = cache.get(cache_key)
    if cached_episodes:
        return cached_episodes
    
    # Fetch from API
    params = {
        "api_key": settings.TMDB_API,
        "language": language,
    }
    
    episodes_data = {}
    try:
        response = services.api_request(
            Sources.TMDB.value,
            "GET",
            url,
            params=params,
        )
        # Store episode names by episode number
        for ep in response.get("episodes", []):
            episodes_data[ep["episode_number"]] = ep["name"]
        
        # Cache the episode names
        cache.set(cache_key, episodes_data)
    except requests.exceptions.HTTPError:
        # If request fails, cache empty dict to avoid repeated failed requests
        cache.set(cache_key, {})
    
    return episodes_data


def fetch_season_multilingual(media_id, season_number, original_language):
    """Fetch season data in multiple languages with fallback priority."""
    # Define language priority: original → english → portuguese → spanish
    languages = [original_language] if original_language else []
    
    # Add fallback languages if not already in list
    fallback_languages = ["en", "pt", "es"]
    for lang in fallback_languages:
        if lang not in languages:
            languages.append(lang)
    
    # Fetch episode data for each language
    multilingual_episodes = {}
    for language in languages:
        if language:  # Skip None/empty languages
            multilingual_episodes[language] = fetch_episodes_by_language(
                media_id, season_number, language
            )
    
    return multilingual_episodes


def process_episodes(season_metadata, episodes_in_db):
    """Process the episodes for the selected season."""
    episodes_metadata = []

    # Convert the queryset to a dictionary for efficient lookups
    tracked_episodes = {}
    for ep in episodes_in_db:
        episode_number = ep.item.episode_number
        if episode_number not in tracked_episodes:
            tracked_episodes[episode_number] = []
        tracked_episodes[episode_number].append(ep)

    # Get multilingual episode names for fallback logic
    original_language = season_metadata.get("original_language", "en")
    multilingual_episodes = {}
    
    # Always fetch multilingual data for better fallback options
    multilingual_episodes = fetch_season_multilingual(
        season_metadata["media_id"],
        season_metadata["season_number"],
        original_language
    )

    for episode in season_metadata["episodes"]:
        episode_number = episode["episode_number"]
        
        # Determine the best episode title with cascading fallback logic
        # Priority: Original language > English > Portuguese > Spanish > Default
        episode_title = episode["name"]  # Default fallback
        
        # Define language priority for fallback
        language_priority = []
        if original_language and original_language != "en":
            language_priority.append(original_language)
        language_priority.extend(["en", "pt", "es"])
        
        # Try each language in order until we find a non-generic, descriptive title
        selected_lang = None
        for lang in language_priority:
            if lang in multilingual_episodes:
                lang_title = multilingual_episodes[lang].get(episode_number, "")
                if lang_title and lang_title.strip() and not is_generic_episode_name(lang_title):
                    episode_title = lang_title
                    selected_lang = lang
                    break  # Use first available non-generic title in priority order
        
        # If no non-generic title found, fall back to any available title (including generic ones)
        if not selected_lang:
            for lang in language_priority:
                if lang in multilingual_episodes:
                    lang_title = multilingual_episodes[lang].get(episode_number, "")
                    if lang_title and lang_title.strip():
                        episode_title = lang_title
                        selected_lang = f"{lang} (generic)"
                        break
        


        episodes_metadata.append(
            {
                "media_id": season_metadata["media_id"],
                "media_type": MediaTypes.EPISODE.value,
                "source": Sources.TMDB.value,
                "season_number": season_metadata["season_number"],
                "episode_number": episode_number,
                "air_date": episode["air_date"],  # when unknown, response returns null
                "image": get_image_url(episode["still_path"]),
                "title": episode_title,
                "overview": episode["overview"],
                "history": tracked_episodes.get(episode_number, []),
                "runtime": get_readable_duration(episode["runtime"]),
            },
        )
    return episodes_metadata


def find_next_episode(episode_number, episodes_metadata):
    """Find the next episode number."""
    # Find the current episode in the sorted list
    current_episode_index = None
    for index, episode in enumerate(episodes_metadata):
        if episode["episode_number"] == episode_number:
            current_episode_index = index
            break

    # If episode not found or it's the last episode, return None
    if current_episode_index is None or current_episode_index + 1 >= len(
        episodes_metadata,
    ):
        return None

    # Return the next episode number
    return episodes_metadata[current_episode_index + 1]["episode_number"]


def episode(media_id, season_number, episode_number):
    """Return the metadata for the selected episode from The Movie Database."""
    tv_metadata = tv_with_seasons(media_id, [season_number])
    season_metadata = tv_metadata[f"season/{season_number}"]

    for episode in season_metadata["episodes"]:
        if episode["episode_number"] == int(episode_number):
            return {
                "title": season_metadata["title"],
                "season_title": season_metadata["season_title"],
                "episode_title": episode["name"],
                "image": get_image_url(episode["still_path"]),
            }

    # Episode not found - throw ProviderAPIError
    msg = (
        f"Episode {episode_number} not found in season {season_number} "
        f"for {Sources.TMDB.label} with ID {media_id}"
    )
    # Create a new response object with 404 status
    not_found_response = requests.Response()
    not_found_response.status_code = 404
    # Set the error attribute to match what ProviderAPIError expects
    not_found_error = type("Error", (), {"response": not_found_response})
    raise services.ProviderAPIError(
        Sources.TMDB.value,
        error=not_found_error,
        details=msg,
    )
