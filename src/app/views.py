import logging

from django.apps import apps
from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db import models
from django.db.models import prefetch_related_objects
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.timezone import datetime
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django.contrib.auth.decorators import login_required

from app import helpers, history_processor
from app import statistics as stats
from app.forms import EpisodeTrackingForm, ManualItemForm, get_form_class
from app.models import TV, BasicMedia, Episode, Item, MediaTypes, Season, Sources, Status
from app.providers import manual, services, tmdb, youtube
from app.templatetags import app_tags
from users.models import HomeSortChoices, MediaSortChoices, MediaStatusChoices

logger = logging.getLogger(__name__)


@require_GET
def home(request):
    """Home page with media items in progress."""
    sort_by = request.user.update_preference("home_sort", request.GET.get("sort"))
    media_type_to_load = request.GET.get("load_media_type")
    items_limit = 14

    list_by_type = BasicMedia.objects.get_in_progress(
        request.user,
        sort_by,
        items_limit,
        media_type_to_load,
    )

    # If this is an HTMX request to load more items for a specific media type
    if request.headers.get("HX-Request") and media_type_to_load:
        context = {
            "media_list": list_by_type.get(media_type_to_load, []),
        }
        return render(request, "app/components/home_grid.html", context)

    context = {
        "list_by_type": list_by_type,
        "current_sort": sort_by,
        "sort_choices": HomeSortChoices.choices,
        "items_limit": items_limit,
    }
    # Preferred order for sections on the Home page
    context["preferred_order"] = [
        MediaTypes.TV.value,
        MediaTypes.SEASON.value,
        MediaTypes.YOUTUBE.value,
        MediaTypes.MOVIE.value,
    ]
    # Build lists of (media_type, media_list) pairs so templates don't need
    # to do dictionary lookups with bracket notation (not supported in Django
    # template variable expressions).
    # Add pending_videos for YouTube items
    for media_list in list_by_type.get(MediaTypes.YOUTUBE.value, {}).get('items', []):
        try:
            max_progress = getattr(media_list, 'max_progress', None)
            progress = getattr(media_list, 'progress', None)
            if max_progress is not None and progress is not None:
                pending = max_progress - progress
                media_list.pending_videos = pending if pending > 0 else 0
            else:
                media_list.pending_videos = 0
        except Exception:
            media_list.pending_videos = 0

    preferred_sections = []
    for mt in context["preferred_order"]:
        if mt in list_by_type:
            preferred_sections.append((mt, list_by_type[mt]))

    remaining_sections = []
    for mt, media_list in list_by_type.items():
        if mt not in context["preferred_order"]:
            remaining_sections.append((mt, media_list))

    context["preferred_sections"] = preferred_sections
    context["remaining_sections"] = remaining_sections
    return render(request, "app/home.html", context)


@require_POST
def progress_edit(request, media_type, instance_id):
    """Increase or decrease the progress of a media item from home page."""
    operation = request.POST["operation"]
    confirm_completion = request.POST.get("confirm_completion")

    media = BasicMedia.objects.get_media_prefetch(
        request.user,
        media_type,
        instance_id,
    )

    # Special handling for season increase operation
    if operation == "increase" and media_type == MediaTypes.SEASON.value:
        # Get season metadata to check if this will be the last episode
        season_metadata = services.get_media_metadata(
            MediaTypes.SEASON.value,
            media.item.media_id,
            media.item.source,
            [media.item.season_number],
        )
        episodes = season_metadata["episodes"]
        max_episodes = len(episodes)
        current_progress = media.progress
        
        # Check if incrementing will complete the season
        next_episode_number = None
        if current_progress == 0:
            next_episode_number = episodes[0]["episode_number"]
        else:
            next_episode_number = tmdb.find_next_episode(current_progress, episodes)
        
        is_last_episode = next_episode_number == max_episodes
        
        # If it's the last episode and no confirmation yet, ask for confirmation
        if is_last_episode and confirm_completion not in ["yes", "no"]:
            return JsonResponse({
                "requires_confirmation": True,
                "message": "This is the last episode of the season. Do you want to mark the season as completed?",
                "season_data": {
                    "media_type": media_type,
                    "instance_id": instance_id,
                    "operation": operation,
                }
            })
        
        # Handle the increase with auto_complete logic
        if is_last_episode:
            auto_complete = confirm_completion == "yes"
            # Manually call watch instead of increase_progress to control auto_complete
            now = timezone.now().replace(second=0, microsecond=0)
            if next_episode_number:
                media.watch(next_episode_number, now, auto_complete=auto_complete)
        else:
            media.increase_progress()
    elif operation == "increase":
        media.increase_progress()
    elif operation == "decrease":
        media.decrease_progress()

    if media_type == MediaTypes.SEASON.value:
        # clear prefetch cache to get the updated episodes
        media.refresh_from_db()
        prefetch_related_objects([media], "episodes")

    context = {
        "media": media,
    }
    return render(
        request,
        "app/components/progress_changer.html",
        context,
    )


@require_GET
def media_list(request, media_type):
    """Return the media list page."""
    layout = request.user.update_preference(
        f"{media_type}_layout",
        request.GET.get("layout"),
    )
    sort_filter = request.user.update_preference(
        f"{media_type}_sort",
        request.GET.get("sort"),
    )
    # Si es YouTube y no hay filtro, usar 'Pending' por defecto
    status_param = request.GET.get("status")
    if media_type == MediaTypes.YOUTUBE.value and not status_param:
        status_param = MediaStatusChoices.PENDING
    status_filter = request.user.update_preference(
        f"{media_type}_status",
        status_param,
    )
    search_query = request.GET.get("search", "")
    page = request.GET.get("page", 1)

    # Prepare status filter for database query
    if not status_filter:
        status_filter = MediaStatusChoices.ALL


    # Soporte para filtro 'Pending' en canales de YouTube
    if media_type == MediaTypes.YOUTUBE.value and status_filter == MediaStatusChoices.PENDING:
        # Obtener todos los canales y filtrar los que tengan episodios pendientes
        all_queryset = BasicMedia.objects.get_media_list(
            user=request.user,
            media_type=media_type,
            status_filter=MediaStatusChoices.ALL,
            sort_filter=sort_filter,
            search=search_query,
        )
        # Anotar max_progress
        BasicMedia.objects.annotate_max_progress(all_queryset, media_type)
        # Filtrar canales con episodios pendientes
        media_queryset = [media for media in all_queryset if getattr(media, 'progress', 0) < getattr(media, 'max_progress', 0)]
    else:
        # Get media list with filters applied
        media_queryset = BasicMedia.objects.get_media_list(
            user=request.user,
            media_type=media_type,
            status_filter=status_filter,
            sort_filter=sort_filter,
            search=search_query,
        )

    # Paginate results
    items_per_page = 32
    paginator = Paginator(media_queryset, items_per_page)
    media_page = paginator.get_page(page)

    BasicMedia.objects.annotate_max_progress(
        media_page.object_list,
        media_type,
    )

    context = {
        "media_type": media_type,
        "media_type_plural": app_tags.media_type_readable_plural(media_type).lower(),
        "media_list": media_page,
        "current_layout": layout,
        "layout_class": ".media-grid" if layout == "grid" else "tbody",
        "current_sort": sort_filter,
        "current_status": status_filter,
        "sort_choices": MediaSortChoices.choices,
        "status_choices": MediaStatusChoices.choices,
    }

    # Handle HTMX requests for partial updates
    if request.headers.get("HX-Request"):
        # Changing from empty list to a status with items
        if request.headers.get("HX-Target") == "empty_list":
            response = HttpResponse()
            response["HX-Redirect"] = reverse("medialist", args=[media_type])
            return response
        if layout == "grid":
            template_name = "app/components/media_grid_items.html"
        else:
            template_name = "app/components/media_table_items.html"
    else:
        template_name = "app/media_list.html"

    return render(request, template_name, context)


@require_GET
def media_search(request):
    """Return the media search page."""
    media_type = request.user.update_preference(
        "last_search_type",
        request.GET["media_type"],
    )
    query = request.GET["q"]
    page = int(request.GET.get("page", 1))
    layout = request.GET.get("layout", "grid")

    # only receives source when searching with secondary source
    source = request.GET.get("source")

    data = services.search(media_type, query, page, source)

    context = {
        "data": data,
        "source": source,
        "media_type": media_type,
        "layout": layout,
    }

    return render(request, "app/search.html", context)


@require_GET
def media_details(request, source, media_type, media_id, title):  # noqa: ARG001 title for URL
    """Return the details page for a media item."""
    media_metadata = services.get_media_metadata(media_type, media_id, source)
    user_medias = BasicMedia.objects.filter_media_prefetch(
        request.user,
        media_id,
        media_type,
        source,
    )
    current_instance = user_medias[0] if user_medias else None

    context = {
        "media": media_metadata,
        "media_type": media_type,
        "user_medias": user_medias,
        "current_instance": current_instance,
    }
    return render(request, "app/media_details.html", context)


@require_GET
def season_details(request, source, media_id, title, season_number):  # noqa: ARG001 For URL
    """Return the details page for a season."""
    tv_with_seasons_metadata = services.get_media_metadata(
        "tv_with_seasons",
        media_id,
        source,
        [season_number],
    )
    season_metadata = tv_with_seasons_metadata[f"season/{season_number}"]

    user_medias = BasicMedia.objects.filter_media_prefetch(
        request.user,
        media_id,
        MediaTypes.SEASON.value,
        source,
        season_number=season_number,
    )

    current_instance = user_medias[0] if user_medias else None
    episodes_in_db = current_instance.episodes.all() if current_instance else []

    if source in [Sources.MANUAL.value, Sources.YOUTUBE.value]:
        season_metadata["episodes"] = manual.process_episodes(
            season_metadata,
            episodes_in_db,
        )
    else:
        season_metadata["episodes"] = tmdb.process_episodes(
            season_metadata,
            episodes_in_db,
        )

    # Filter episodes by watched status if requested
    episode_filter = request.GET.get("filter", "all")
    if episode_filter == "unwatched":
        season_metadata["episodes"] = [
            episode for episode in season_metadata["episodes"]
            if not episode.get("history")
        ]
    elif episode_filter == "watched":
        season_metadata["episodes"] = [
            episode for episode in season_metadata["episodes"]
            if episode.get("history")
        ]

    # Sort episodes by episode number
    sort_order = request.GET.get("sort", "asc")
    if sort_order == "desc":
        season_metadata["episodes"] = sorted(
            season_metadata["episodes"], 
            key=lambda x: x["episode_number"], 
            reverse=True
        )
    else:  # Default to ascending
        season_metadata["episodes"] = sorted(
            season_metadata["episodes"], 
            key=lambda x: x["episode_number"]
        )

    context = {
        "media": season_metadata,
        "tv": tv_with_seasons_metadata,
        "media_type": MediaTypes.SEASON.value,
        "user_medias": user_medias,
        "current_instance": current_instance,
        "current_filter": episode_filter,
        "current_sort": sort_order,
    }
    return render(request, "app/media_details.html", context)


@require_POST
def update_media_score(request, media_type, instance_id):
    """Update the user's score for a media item."""
    media = BasicMedia.objects.get_media(
        request.user,
        media_type,
        instance_id,
    )

    score = float(request.POST.get("score"))
    media.score = score
    media.save()
    logger.info(
        "%s score updated to %s",
        media,
        score,
    )

    return JsonResponse(
        {
            "success": True,
            "score": score,
        },
    )


@require_POST
def sync_metadata(request, source, media_type, media_id, season_number=None):
    """Refresh the metadata for a media item."""
    if source in [Sources.MANUAL.value, Sources.YOUTUBE.value]:
        msg = "Manual and YouTube items cannot be synced."
        messages.error(request, msg)
        return HttpResponse(
            msg,
            status=400,
            headers={"HX-Redirect": request.POST.get("next", "/")},
        )

    cache_key = f"{source}_{media_type}_{media_id}"
    if media_type == MediaTypes.SEASON.value:
        cache_key += f"_{season_number}"

    ttl = cache.ttl(cache_key)
    logger.debug("%s - Cache TTL for: %s", cache_key, ttl)

    if ttl is not None and ttl > (settings.CACHE_TIMEOUT - 3):
        msg = "The data was recently synced, please wait a few seconds."
        messages.error(request, msg)
        logger.error(msg)
    else:
        deleted = cache.delete(cache_key)
        logger.debug("%s - Old cache deleted: %s", cache_key, deleted)

        metadata = services.get_media_metadata(
            media_type,
            media_id,
            source,
            [season_number],
        )
        item, _ = Item.objects.update_or_create(
            media_id=media_id,
            source=source,
            media_type=media_type,
            season_number=season_number,
            defaults={
                "title": metadata["title"],
                "image": metadata["image"],
            },
        )
        title = metadata["title"]
        if season_number:
            title += f" - Season {season_number}"

        if media_type == MediaTypes.SEASON.value:
            if source in [Sources.MANUAL.value, Sources.YOUTUBE.value]:
                metadata["episodes"] = manual.process_episodes(
                    metadata,
                    [],
                )
            else:
                metadata["episodes"] = tmdb.process_episodes(
                    metadata,
                    [],
                )

            # Create a dictionary of existing episodes keyed by episode number
            existing_episodes = {
                ep.episode_number: ep
                for ep in Item.objects.filter(
                    source=source,
                    media_type=MediaTypes.EPISODE.value,
                    media_id=media_id,
                    season_number=season_number,
                )
            }

            episodes_to_update = []
            episode_count = 0

            for episode_data in metadata["episodes"]:
                episode_number = episode_data["episode_number"]
                if episode_number in existing_episodes:
                    episode_item = existing_episodes[episode_number]
                    episode_item.title = metadata["title"]
                    episode_item.image = episode_data["image"]
                    episodes_to_update.append(episode_item)
                    episode_count += 1

            logger.info(
                "Found %s existing episodes to update for %s",
                episode_count,
                title,
            )

            if episodes_to_update:
                updated_count = Item.objects.bulk_update(
                    episodes_to_update,
                    ["title", "image"],
                    batch_size=100,
                )
                logger.info(
                    "Successfully updated %s episodes for %s",
                    updated_count,
                    title,
                )

        item.fetch_releases(delay=False)

        msg = f"{title} was synced to {Sources(source).label} successfully."
        messages.success(request, msg)

    if request.headers.get("HX-Request"):
        return HttpResponse(
            status=204,
            headers={
                "HX-Redirect": request.POST["next"],
            },
        )
    return helpers.redirect_back(request)


@require_GET
def track_modal(
    request,
    source,
    media_type,
    media_id,
    season_number=None,
):
    """Return the tracking form for a media item."""
    instance_id = request.GET.get("instance_id")
    if instance_id:
        media = BasicMedia.objects.get_media(
            request.user,
            media_type,
            instance_id,
        )
    elif request.GET.get("is_create"):
        media = None
    else:
        # no specific instance, try to find the first one
        user_medias = BasicMedia.objects.filter_media(
            request.user,
            media_id,
            media_type,
            source,
            season_number=season_number,
        )
        media = user_medias.first()
        if media:
            instance_id = media.id

    initial_data = {
        "media_id": media_id,
        "source": source,
        "media_type": media_type,
        "season_number": season_number,
        "instance_id": instance_id,
    }

    if media:
        title = media.item
        if media_type == MediaTypes.GAME.value:
            initial_data["progress"] = helpers.minutes_to_hhmm(media.progress)
    else:
        title = services.get_media_metadata(
            media_type,
            media_id,
            source,
            [season_number],
        )["title"]
        if media_type == MediaTypes.SEASON.value:
            title += f" S{season_number}"

    form = get_form_class(media_type)(instance=media, initial=initial_data)

    return render(
        request,
        "app/components/fill_track.html",
        {
            "title": title,
            "form": form,
            "media": media,
            "return_url": request.GET["return_url"],
        },
    )


@require_POST
def media_save(request):
    """Save or update media data to the database."""
    media_id = request.POST["media_id"]
    source = request.POST["source"]
    media_type = request.POST["media_type"]
    season_number = request.POST.get("season_number")
    instance_id = request.POST.get("instance_id")

    if instance_id:
        instance = BasicMedia.objects.get_media(
            request.user,
            media_type,
            instance_id,
        )
    else:
        metadata = services.get_media_metadata(
            media_type,
            media_id,
            source,
            [season_number],
        )
        item, _ = Item.objects.get_or_create(
            media_id=media_id,
            source=source,
            media_type=media_type,
            season_number=season_number,
            defaults={
                "title": metadata["title"],
                "image": metadata["image"],
            },
        )
        model = apps.get_model(app_label="app", model_name=media_type)
        instance = model(item=item, user=request.user)

    # Validate the form and save the instance if it's valid
    form_class = get_form_class(media_type)
    form = form_class(request.POST, instance=instance)
    if form.is_valid():
        form.save()
        logger.info("%s saved successfully.", form.instance)
    else:
        logger.error(form.errors.as_json())
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(
                    request,
                    f"{field.replace('_', ' ').title()}: {error}",
                )

    return helpers.redirect_back(request)


@require_POST
def media_delete(request):
    """Delete media data from the database."""
    instance_id = request.POST["instance_id"]
    media_type = request.POST["media_type"]

    media = BasicMedia.objects.get_media(
        request.user,
        media_type,
        instance_id,
    )
    if media:
        media.delete()
        logger.info("%s deleted successfully.", media)
    else:
        logger.warning("The %s was already deleted before.", media_type)

    return helpers.redirect_back(request)


@require_POST
def episode_save(request):
    """Handle the creation, deletion, and updating of episodes for a season."""
    from app.models import Season, Item, Episode, MediaTypes, Sources, Status
    media_id = request.POST["media_id"]
    source = request.POST["source"]
    
    # Debug logging
    logger.debug("DEBUG episode_save: media_id=%s, source=%s, Sources.YOUTUBE.value=%s", 
                media_id, source, Sources.YOUTUBE.value)
    
    # Handle season_number safely - it might be empty for some sources
    season_number_str = request.POST.get("season_number", "")
    if season_number_str:
        try:
            season_number = int(season_number_str)
        except ValueError:
            logger.error("Invalid season_number: %s", season_number_str)
            return HttpResponseBadRequest("Invalid season number")
    else:
        # For some sources like YouTube, season_number might be determined differently
        # We'll need to look it up from the episode
        season_number = None
    
    episode_number = int(request.POST["episode_number"])
    confirm_completion = request.POST.get("confirm_completion")

    form = EpisodeTrackingForm(request.POST)
    if not form.is_valid():
        logger.error("Form validation failed: %s", form.errors)
        return HttpResponseBadRequest("Invalid form data")

    # If season_number is None, try to get it from the episode
    if season_number is None:
        try:
            episode_item = Item.objects.get(
                media_id=media_id,
                source=source,
                media_type=MediaTypes.EPISODE.value,
                episode_number=episode_number,
            )
            season_number = episode_item.season_number
        except Item.DoesNotExist:
            logger.error("Episode not found: media_id=%s, source=%s, episode_number=%s", 
                        media_id, source, episode_number)
            return HttpResponseBadRequest("Episode not found")

    try:
        related_season = Season.objects.get(
            item__media_id=media_id,
            item__source=source,
            item__season_number=season_number,
            item__episode_number=None,
            user=request.user,
        )
    except Season.DoesNotExist:
        # Skip TMDB calls for YouTube sources
        if source == Sources.YOUTUBE.value:
            logger.debug("DEBUG: YouTube source detected, skipping Season creation")
            logger.error(
                "Season not found for YouTube video: media_id=%s, season_number=%s",
                media_id,
                season_number,
            )
            return HttpResponseBadRequest("Season not found for YouTube video")

        logger.debug(
            "DEBUG: Not YouTube source, proceeding with TMDB call. source=%s, expected=%s",
            source,
            Sources.YOUTUBE.value,
        )

        # Original TMDB logic for other sources
        tv_with_seasons_metadata = services.get_media_metadata(
            "tv_with_seasons",
            media_id,
            source,
            [season_number],
        )
        season_metadata = tv_with_seasons_metadata[f"season/{season_number}"]

        item, _ = Item.objects.get_or_create(
            media_id=media_id,
            source=source,  # Use the actual source, not hardcoded TMDB
            media_type=MediaTypes.SEASON.value,
            season_number=season_number,
            defaults={
                "title": tv_with_seasons_metadata["title"],
                "image": season_metadata["image"],
            },
        )
        related_season = Season.objects.create(
            item=item,
            user=request.user,
            score=None,
            status=Status.IN_PROGRESS.value,
            notes="",
        )

        logger.info("%s did not exist, it was created successfully.", related_season)
    
    # Get season metadata for existing season (skip for YouTube)
    logger.debug("DEBUG: About to check season metadata. source='%s', Sources.YOUTUBE.value='%s'", 
                source, Sources.YOUTUBE.value)
    logger.debug("DEBUG: source type=%s, Sources.YOUTUBE.value type=%s", type(source), type(Sources.YOUTUBE.value))
    logger.debug("DEBUG: source == Sources.YOUTUBE.value: %s", source == Sources.YOUTUBE.value)
    if source == Sources.YOUTUBE.value:
        logger.debug("DEBUG: YouTube source detected, skipping TMDB metadata call")
        # For YouTube, we don't need TMDB metadata, just continue with the episode tracking
        season_metadata = {"episodes": []}  # Dummy metadata to avoid errors
        max_episodes = 999  # High number so YouTube videos don't trigger completion logic
    else:
        logger.debug("DEBUG: Not YouTube source, proceeding with TMDB metadata call")
        tv_with_seasons_metadata = services.get_media_metadata(
            "tv_with_seasons",
            media_id,
            source,
            [season_number],
        )
        season_metadata = tv_with_seasons_metadata[f"season/{season_number}"]
        max_episodes = len(season_metadata["episodes"])

    # Check if this is the last episode and if completion needs confirmation
    is_last_episode = episode_number == max_episodes
    
    if is_last_episode and confirm_completion not in ["yes", "no"]:
        # Return a JSON response indicating confirmation is needed
        return JsonResponse({
            "requires_confirmation": True,
            "message": "This is the last episode of the season. Do you want to mark the season as completed?",
            "episode_data": {
                "media_id": media_id,
                "season_number": season_number,
                "episode_number": episode_number,
                "source": source,
                "end_date": form.cleaned_data["end_date"].isoformat(),
            }
        })

    # Determine if auto-completion should happen
    if is_last_episode:
        auto_complete = confirm_completion == "yes"
    else:
        auto_complete = True
    
    related_season.watch(episode_number, form.cleaned_data["end_date"], auto_complete=auto_complete)

    # If HTMX request, return only the updated card HTML
    if request.headers.get('HX-Request'):
        # Find the updated video item
        item = Item.objects.get(
            media_id=media_id,
            source=source,
            media_type=MediaTypes.EPISODE.value,
            episode_number=episode_number,
        )
        # Annotate with channel info and watched status for template
        from django.db.models import OuterRef, Exists, Subquery, CharField
        from app.models import Season
        item_qs = Item.objects.filter(pk=item.pk)
        item_qs = item_qs.annotate(
            is_watched=Exists(
                Episode.objects.filter(item=OuterRef('pk'), end_date__isnull=False)
            ),
            end_date_sub=Episode.objects.filter(item=OuterRef('pk'), end_date__isnull=False).values('end_date')[:1]
        )
        season_qs = Season.objects.filter(
            item__media_id=OuterRef('media_id'),
            item__source=OuterRef('source'),
            item__media_type=MediaTypes.SEASON.value,
            item__season_number=OuterRef('season_number'),
        )
        tv_title_qs = season_qs.filter(related_tv__isnull=False).values('related_tv__item__title')[:1]
        tv_image_qs = season_qs.filter(related_tv__isnull=False).values('related_tv__item__image')[:1]
        item_qs = item_qs.annotate(
            channel_name=Subquery(tv_title_qs, output_field=CharField()),
            channel_image=Subquery(tv_image_qs, output_field=CharField()),
        )
        video = item_qs.first()
        from django.utils import timezone
        from django.middleware.csrf import get_token
        context = {
            'video': video,
            'today': timezone.now().date(),
            'csrf_token': get_token(request),
        }
        return render(request, 'app/components/youtube_grid_items_card.html', context)
    return helpers.redirect_back(request)


def handle_youtube_video_creation(request, form):
    """Handle creation of YouTube video with automatic channel/season detection and creation."""
    from datetime import datetime
    from app.providers import youtube
    from app.models import Season, TV, Episode, Status
    
    youtube_url = form.cleaned_data.get("youtube_url")
    if not youtube_url:
        messages.error(request, "YouTube URL is required for YouTube videos.")
        return redirect("create_entry")
    
    # Extract video metadata
    video_metadata = youtube.extract_video_metadata(youtube_url)
    if not video_metadata:
        messages.error(request, "Could not extract video metadata from YouTube URL.")
        return redirect("create_entry")
    
    # Extract channel info from video metadata
    channel_id = video_metadata.get("channel_id")
    if not channel_id:
        messages.error(request, "Could not determine channel from video.")
        return redirect("create_entry")
    
    # Get channel metadata
    channel_metadata = youtube.fetch_channel_metadata(channel_id)
    if not channel_metadata:
        messages.error(request, "Could not fetch channel information.")
        return redirect("create_entry")
    
    # Get video year for season
    published_date = video_metadata.get("published_date")
    if published_date:
        try:
            video_year = datetime.strptime(published_date, "%Y-%m-%d").year
        except ValueError:
            video_year = datetime.now().year
    else:
        video_year = datetime.now().year
    
    # Find existing channel by searching TV instances with matching channel_id in notes
    # TV.notes used previously, but Item now stores youtube channel id on related TV.item.notes
    # Search TV by related item's notes or by matching item's media_id stored for channels
    existing_tv = TV.objects.filter(
        user=request.user,
        item__source=Sources.YOUTUBE.value,
        item__media_type=MediaTypes.YOUTUBE.value,
    ).filter(models.Q(notes__contains=f"YouTube Channel ID: {channel_id}") | models.Q(item__media_id=channel_id)).first()
    
    if existing_tv:
        # Channel already exists
        channel_item = existing_tv.item
        tv_instance = existing_tv
        channel_created = False
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
            user=request.user,
            item=channel_item,
            notes=f"YouTube Channel ID: {channel_id}",
            status=Status.IN_PROGRESS.value,
        )
        channel_created = True
    
    # Find or create season for the video's year
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
    
    # Create Season instance if it was just created
    if season_created:
        season_instance = Season.objects.create(
            user=request.user,
            item=season_item,
            related_tv=tv_instance,
            status=Status.IN_PROGRESS.value,
        )
    else:
        # Get existing season instance
        season_instance = Season.objects.get(
            item=season_item,
            related_tv=tv_instance,
        )
    
    # Create episode item for the video
    # Avoid creating duplicates: check if an Item for this YouTube video already exists
    video_id = video_metadata.get("video_id")
    if video_id:
        # Use the new youtube_video_id field for duplicate detection
        existing_video = Item.objects.filter(
            source=Sources.YOUTUBE.value,
            media_type=MediaTypes.EPISODE.value,
            youtube_video_id=video_id,
        ).first()
        if existing_video:
            messages.info(request, f"El video '{existing_video.title}' ya existe en {channel_item.title}.")
            return redirect("youtube_channel_details", source=channel_item.source, media_id=channel_item.media_id, title=channel_item.title)

    # Get next episode number for this season BEFORE creating the Item
    latest_episode = Item.objects.filter(
        media_id=channel_item.media_id,
        source=Sources.YOUTUBE.value,
        media_type=MediaTypes.EPISODE.value,
        season_number=video_year,
    ).order_by('-episode_number').first()

    if latest_episode:
        next_episode_number = latest_episode.episode_number + 1
    else:
        next_episode_number = 1

    episode_item = Item.objects.create(
        media_id=channel_item.media_id,
        source=Sources.YOUTUBE.value,
        media_type=MediaTypes.EPISODE.value,
        season_number=video_year,
        episode_number=next_episode_number,
        title=video_metadata.get("title", "Unknown Video"),
        image=video_metadata.get("thumbnail", ""),
        air_date=published_date,
        runtime=video_metadata.get("duration_minutes", 0),
        youtube_video_id=(video_id if video_id else None),
    )
    
    # Don't create Episode instance automatically - let user mark as watched manually
    
    # Success message
    messages.success(request, f"Successfully added video '{episode_item.title}' to {channel_item.title} ({video_year})")
    # After creating a YouTube video, redirect back to the Create Entry page
    # and preselect the YouTube Video tab so the user can add another quickly.
    return redirect(f"{reverse('create_entry')}?media_type={MediaTypes.YOUTUBE_VIDEO.value}")


@require_http_methods(["GET", "POST"])
def create_entry(request):
    """Return the form for manually adding media items."""
    if request.method == "GET":
        media_types = MediaTypes.values
        initial_media_type = request.GET.get("media_type")
        return render(request, "app/create_entry.html", {
            "media_types": media_types,
            "Status": Status,
            "initial_media_type": initial_media_type,
        })

    # Process the form submission
    form = ManualItemForm(request.POST, user=request.user)
    if not form.is_valid():
        # Handle form validation errors
        logger.error(form.errors.as_json())
        helpers.form_error_messages(form, request)
        return redirect("create_entry")

    # Special handling for YouTube Video
    if form.cleaned_data.get("media_type") == MediaTypes.YOUTUBE_VIDEO.value:
        return handle_youtube_video_creation(request, form)

    # Try to save the item
    try:
        item = form.save()
    except IntegrityError:
        # Handle duplicate item
        media_name = form.cleaned_data["title"]
        if form.cleaned_data.get("season_number"):
            media_name += f" - Season {form.cleaned_data['season_number']}"
        if form.cleaned_data.get("episode_number"):
            media_name += f" - Episode {form.cleaned_data['episode_number']}"

        logger.exception("%s already exists in the database.", media_name)
        messages.error(request, f"{media_name} already exists in the database.")
        return redirect("create_entry")

    # Episodes don't need media instances - they're just Item entries
    if item.media_type != MediaTypes.EPISODE.value:
        # Prepare and validate the media form
        updated_request = request.POST.copy()
        updated_request.update({"source": item.source, "media_id": item.media_id})
        media_form = get_form_class(item.media_type)(updated_request)

        if not media_form.is_valid():
            # Handle media form validation errors
            logger.error(media_form.errors.as_json())
            helpers.form_error_messages(media_form, request)

            # Delete the item since the media creation failed
            item.delete()
            logger.info("%s was deleted due to media form validation failure", item)
            return redirect("create_entry")

        # Save the media instance
        media_form.instance.user = request.user
        media_form.instance.item = item

        # Handle relationships based on media type
        if item.media_type == MediaTypes.SEASON.value:
            media_form.instance.related_tv = form.cleaned_data["parent_tv"]

        media_form.save()

        # Auto-create current year season for YouTube channels
        if item.media_type == MediaTypes.YOUTUBE.value:
            from datetime import datetime
            current_year = datetime.now().year
            
            # Create season item for current year
            season_item = Item.objects.create(
                media_id=item.media_id,  # Same media_id as parent channel
                source=item.source,      # Same source (YOUTUBE)
                media_type=MediaTypes.SEASON.value,
                season_number=current_year,  # Use year as season number
                title=f"{item.title} - {current_year}",
            )
            
            # Create Season instance
            from app.models import Season
            Season.objects.create(
                user=request.user,
                item=season_item,
                related_tv=media_form.instance,  # Link to the YouTube channel (TV)
            )

    # Success message
    msg = f"{item} added successfully."
    messages.success(request, msg)
    logger.info(msg)

    return redirect("create_entry")


@login_required
@require_POST
def youtube_metadata(request):
    """Extract metadata from YouTube URL via AJAX."""
    import json
    
    try:
        data = json.loads(request.body)
        youtube_url = data.get('url', '').strip()
        extract_type = data.get('type', 'video')  # 'video' or 'channel'
        
        if not youtube_url:
            return JsonResponse({'success': False, 'error': 'No URL provided'}, status=400)
        
        if extract_type == 'channel':
            # Extract channel ID from URL
            channel_id = youtube.extract_channel_id(youtube_url)
            if not channel_id:
                return JsonResponse({
                    'success': False, 
                    'error': 'Invalid YouTube channel URL format'
                }, status=400)
            
            # Fetch channel metadata from YouTube API
            metadata = youtube.fetch_channel_metadata(channel_id)
            if not metadata:
                return JsonResponse({
                    'success': False, 
                    'error': 'Could not fetch channel metadata'
                }, status=404)
            
            # metadata already contains the correct channel_id from fetch_channel_metadata()
        else:
            # Extract video ID from URL
            video_id = youtube.extract_video_id(youtube_url)
            if not video_id:
                return JsonResponse({
                    'success': False, 
                    'error': 'Invalid YouTube URL format'
                }, status=400)
            
            # Fetch metadata from YouTube API
            metadata = youtube.fetch_video_metadata(video_id)
            if not metadata:
                return JsonResponse({
                    'success': False, 
                    'error': 'Could not fetch video metadata'
                }, status=404)
        
        return JsonResponse({
            'success': True,
            'metadata': metadata
        })
        
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        logger.error(f"Error extracting YouTube metadata: {e}")
        return JsonResponse({
            'success': False, 
            'error': 'Internal server error'
        }, status=500)


@require_GET
def search_parent_tv(request):
    """Return the search results for parent TV shows."""
    query = request.GET.get("q", "").strip()

    if len(query) <= 1:
        return render(request, "app/components/search_parent_tv.html")

    logger.debug(
        "%s - Searching for TV shows with query: %s",
        request.user.username,
        query,
    )

    parent_tvs = TV.objects.filter(
        user=request.user,
        item__source=Sources.MANUAL.value,
        item__media_type=MediaTypes.TV.value,
        item__title__icontains=query,
    )[:5]

    return render(
        request,
        "app/components/search_parent_tv.html",
        {"results": parent_tvs, "query": query},
    )


@require_GET
def search_parent_season(request):
    """Return the search results for parent seasons."""
    query = request.GET.get("q", "").strip()

    if len(query) <= 1:
        return render(request, "app/components/search_parent_tv.html")

    logger.debug(
        "%s - Searching for seasons with query: %s",
        request.user.username,
        query,
    )

    parent_seasons = Season.objects.filter(
        user=request.user,
        item__source=Sources.MANUAL.value,
        item__media_type=MediaTypes.SEASON.value,
        item__title__icontains=query,
    ).order_by('item__season_number', 'item__title')[:20]

    return render(
        request,
        "app/components/search_parent_season.html",
        {"results": parent_seasons, "query": query},
    )


@require_GET
def get_next_episode_number(request):
    """Return the next episode number for a season."""
    season_id = request.GET.get("season_id")
    
    if not season_id:
        return JsonResponse({"next_episode": 1})
    
    try:
        # Get the highest episode number for this season
        highest_episode = Item.objects.filter(
            media_id__in=Season.objects.filter(id=season_id).values_list('item__media_id', flat=True),
            source=Sources.MANUAL.value,
            media_type=MediaTypes.EPISODE.value,
            season_number__in=Season.objects.filter(id=season_id).values_list('item__season_number', flat=True)
        ).aggregate(
            max_episode=models.Max('episode_number')
        )['max_episode']
        
        next_episode = (highest_episode or 0) + 1
        return JsonResponse({"next_episode": next_episode})
        
    except Exception:
        return JsonResponse({"next_episode": 1})


@require_GET
def history_modal(
    request,
    source,
    media_type,
    media_id,
    season_number=None,
    episode_number=None,
):
    """Return the history page for a media item."""
    user_medias = BasicMedia.objects.filter_media(
        request.user,
        media_id,
        media_type,
        source,
        season_number=season_number,
        episode_number=episode_number,
    )

    total_medias = user_medias.count()
    timeline_entries = []
    for index, media in enumerate(user_medias, start=1):
        if history := media.history.all():
            media_entry_number = total_medias - index + 1
            timeline_entries.extend(
                history_processor.process_history_entries(
                    history,
                    media_type,
                    media_entry_number,
                ),
            )
    return render(
        request,
        "app/components/fill_history.html",
        {
            "media_type": media_type,
            "timeline": timeline_entries,
            "total_medias": total_medias,
            "return_url": request.GET["return_url"],
        },
    )


@require_http_methods(["DELETE"])
def delete_history_record(request, media_type, history_id):
    """Delete a specific history record."""
    try:
        historical_model = apps.get_model(
            app_label="app",
            model_name=f"historical{media_type.lower()}",
        )

        historical_model.objects.get(
            history_id=history_id,
            history_user=request.user,
        ).delete()

        logger.info(
            "Deleted history record %s",
            str(history_id),
        )

        # Return empty 200 response - the element will be removed by HTMX
        return HttpResponse()

    except historical_model.DoesNotExist:
        logger.exception(
            "History record %s not found for user %s",
            str(history_id),
            str(request.user),
        )
        return HttpResponse("Record not found", status=404)


@require_GET
def statistics(request):
    """Return the statistics page."""
    # Set default date range to today
    timeformat = "%Y-%m-%d"
    today = timezone.localdate()

    # Get date parameters with defaults
    start_date_str = request.GET.get("start-date") or today.strftime(timeformat)
    end_date_str = request.GET.get("end-date") or today.strftime(timeformat)

    if start_date_str == "all" and end_date_str == "all":
        start_date = None
        end_date = None
    else:
        start_date = parse_date(start_date_str)
        end_date = parse_date(end_date_str)

        if start_date and end_date:
            # Convert to datetime with timezone awareness
            start_date = timezone.make_aware(
                datetime.combine(start_date, datetime.min.time()),
            )

            # End date should be end of day
            end_date = timezone.make_aware(
                datetime.combine(end_date, datetime.max.time()),
            )

    # Get all user media data in a single operation
    user_media, media_count, episodes_watched, total_watch_minutes = stats.get_user_media(
        request.user,
        start_date,
        end_date,
    )

    # Calculate all statistics from the retrieved data
    media_type_distribution = stats.get_media_type_distribution(
        media_count,
    )
    score_distribution, _ = stats.get_score_distribution(user_media)
    watch_time_timeseries = stats.get_watch_time_timeseries(request.user, start_date, end_date)
    status_distribution = stats.get_status_distribution(user_media)
    watch_time_distribution_pie_chart_data = stats.get_watch_time_distribution_pie_chart_data(
        user_media
    )
    timeline = stats.get_timeline(user_media)

    activity_data = stats.get_activity_data(request.user, start_date, end_date)

    def format_minutes(minutes):
        hours = minutes // 60
        mins = minutes % 60
        return f"{hours}h {mins:02d}m" if hours else f"{mins}m"

    from app.statistics import get_top_tv_shows, get_top_youtube_channels
    top_tv_shows = get_top_tv_shows(request.user, start_date, end_date, limit=6)
    top_youtube_channels = get_top_youtube_channels(request.user, start_date, end_date, limit=6)

    context = {
        "start_date": start_date,
        "end_date": end_date,
        "media_count": media_count,
        "activity_data": activity_data,
        "media_type_distribution": media_type_distribution,
        "score_distribution": score_distribution,
        "watch_time_timeseries": watch_time_timeseries,
        "status_distribution": status_distribution,
    "watch_time_distribution_pie_chart_data": watch_time_distribution_pie_chart_data,
        "timeline": timeline,
        "episodes_watched": episodes_watched,
        "total_watch_time": format_minutes(total_watch_minutes),
        "top_tv_shows": top_tv_shows,
        "top_youtube_channels": top_youtube_channels,
    }

    return render(request, "app/statistics.html", context)


@require_GET
def youtube_channel_details(request, source, media_id, title):  # noqa: ARG001 title for URL
    """Return the details page for a YouTube channel with year-based video filtering."""
    user_medias = BasicMedia.objects.filter_media_prefetch(
        request.user,
        media_id,
        "youtube",
        source,
    )
    current_instance = user_medias[0] if user_medias else None
    
    # Extract the real YouTube channel_id from the TV notes
    channel_id = None
    if current_instance and current_instance.notes:
        # Extract channel_id from notes like "YouTube Channel ID: UCTv-XvfzLX3i4IGWAm4sbmA"
        import re
        match = re.search(r'YouTube Channel ID: ([A-Za-z0-9_-]+)', current_instance.notes)
        if match:
            channel_id = match.group(1)
    
    # Get channel metadata using the real channel_id if available
    if channel_id and source == Sources.YOUTUBE.value:
        channel_metadata = services.get_media_metadata("youtube", channel_id, source)
        # Override the media_id from API with our internal media_id
        channel_metadata['media_id'] = media_id
    else:
        # Fallback to basic metadata from the database
        channel_metadata = {
            'title': current_instance.item.title if current_instance else f'Channel {media_id}',
            'image': current_instance.item.image if current_instance else '',
            'synopsis': '',
            'media_type': 'youtube',
            'source': source,
            'media_id': media_id,
            'videos': [],
        }
    
    # For YouTube channels, get episode items (not Episode records) through seasons
    episode_items = []
    if current_instance:
        # Get all episode Items from all seasons of this YouTube channel
        from app.models import Episode, Item, MediaTypes
        episode_items = Item.objects.filter(
            media_type=MediaTypes.EPISODE.value,
            source=Sources.YOUTUBE.value,
            media_id=media_id
        ).order_by('-air_date')

    # For YouTube channels, we'll work with the episode items we have in the database
    # Convert episode items to a structure that the template expects
    episodes = []
    for item in episode_items:
        # Get all Episode records (watch history) for this item
        episode_history = Episode.objects.filter(
            item=item,
            related_season__related_tv=current_instance
        ).order_by('-end_date')
        
        episode_data = {
            "title": item.title,
            "air_date": item.air_date.strftime('%Y-%m-%d') if item.air_date else None,
            "published_year": item.air_date.year if item.air_date else None,
            "episode_number": item.episode_number,
            "season_number": item.season_number,
            "image": item.image,
            "runtime": item.runtime,
            "id": item.id,
            "watched": episode_history.exists(),
            "history": list(episode_history),
            # Add required fields for template tags
            "source": item.source,
            "media_type": item.media_type,
            "media_id": item.media_id,
        }
        episodes.append(episode_data)
    
    channel_metadata["episodes"] = episodes
    
    # Filter episodes by year if requested
    year_filter = request.GET.get("year", "all")
    if year_filter != "all":
        try:
            filter_year = int(year_filter)
            episodes = [
                episode for episode in episodes
                if episode.get("published_year") == filter_year
            ]
        except ValueError:
            pass  # Invalid year, show all episodes
    
    # Filter by watched status
    # For YouTube channels default to 'unwatched' when not provided
    if source == Sources.YOUTUBE.value and "filter" not in request.GET:
        status_filter = "unwatched"
    else:
        status_filter = request.GET.get("filter", "all")
    if status_filter == "unwatched":
        episodes = [
            episode for episode in episodes
            if not episode.get("history")
        ]
    elif status_filter == "watched":
        episodes = [
            episode for episode in episodes
            if episode.get("history")
        ]
    
    # Ordenar episodios por número de episodio
    sort_order = request.GET.get("sort", "asc")  # Por defecto ascendente por número de episodio
    if sort_order == "desc":
        episodes = sorted(
            episodes,
            key=lambda x: (x.get("episode_number") or 0),
            reverse=True
        )
    else:
        episodes = sorted(
            episodes,
            key=lambda x: (x.get("episode_number") or 0)
        )
    
    # Get available years for filtering
    available_years = sorted(list(set(
        episode.get("published_year") for episode in channel_metadata.get("episodes", [])
        if episode.get("published_year")
    )), reverse=True)
    
    # Update channel metadata with filtered episodes
    channel_metadata["episodes"] = episodes
    
    context = {
        "media": channel_metadata,
        "media_type": "youtube",
        "user_medias": user_medias,
        "current_instance": current_instance,
        "current_filter": status_filter,
        "current_sort": sort_order,
        "current_year": year_filter,
        "available_years": available_years,
        "is_youtube_channel": True,  # Flag to customize template behavior
    }
    return render(request, "app/media_details.html", context)


@require_http_methods(["DELETE"])
@login_required
def delete_youtube_video(request, video_id):
    """Delete a YouTube video (Item) from the database."""
    logger.info(f"DELETE request received for video_id={video_id} by user={request.user.username}")
    
    try:
        # Get the video item (YouTube episode only)
        video_item = Item.objects.get(
            id=video_id,
            media_type=MediaTypes.EPISODE.value,
            source=Sources.YOUTUBE.value
        )

        # Delete Episode(s) for this user and item, if any
        deleted_episodes = Episode.objects.filter(
            item=video_item,
            related_season__related_tv__user=request.user
        ).delete()
        if deleted_episodes[0] > 0:
            logger.info(f"Deleted {deleted_episodes[0]} Episode(s) for user {request.user.username} and video ID {video_id}")

        video_title = video_item.title
        logger.info(f"Found video: '{video_title}' (ID: {video_id}), proceeding with deletion of Item")
        video_item.delete()
        logger.info(f"YouTube video '{video_title}' (ID: {video_id}) successfully deleted by user {request.user.username}")

        # Return empty response with 200 status to remove the element
        return HttpResponse(status=200)

    except Item.DoesNotExist:
        logger.warning(f"Attempted to delete non-existent video ID: {video_id} by user {request.user.username}")
        return HttpResponseBadRequest("Video not found")
    except Exception as e:
        logger.error(f"Error deleting video ID {video_id}: {str(e)}")
        return HttpResponseBadRequest(f"Error deleting video: {str(e)}")
