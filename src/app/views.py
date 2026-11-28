import logging
from collections import deque

from copy import deepcopy
from urllib.parse import urlencode

from django.apps import apps
from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db import models
from django.db.models.functions import Greatest
from django.http import Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.timezone import datetime
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django.views.decorators.vary import vary_on_headers
from django.contrib.auth.decorators import login_required

from app import helpers, history_processor
from app import statistics as stats
from app.metadata import display_metadata, override_for, save_poster_override
from app.forms import EpisodeTrackingForm, ItemMetadataForm, ManualItemForm, get_form_class
from app.models import TV, BasicMedia, Episode, Item, ItemMetadataOverride, MangaSession, MediaTypes, Season, Sources, Status, GameSession, Game
from app.models import SkippedEpisode
from app.providers import manual, services, tmdb, youtube
from app.templatetags import app_tags
from app.views_youtubes import with_video_details
from integrations.youtube_watched import queue_watched
from users.models import HomeSortChoices, MediaSortChoices, MediaStatusChoices

logger = logging.getLogger(__name__)
HOME_ITEMS_LIMIT = 14
HOME_MORE_LIMIT = 8
STALE_PROGRESS_MESSAGE = "Progress changed. Refresh the page and try again."


@require_GET
@vary_on_headers("HX-Request", "HX-Target")
def home(request):
    """Home page with media items in progress."""
    is_htmx = request.headers.get("HX-Request") == "true"
    media_type_to_load = request.GET.get("load_media_type")
    if is_htmx and media_type_to_load:
        return _home_load_more(request, media_type_to_load)

    sort_by = request.user.update_preference("home_sort", request.GET.get("sort"))
    sort_fragment = is_htmx and request.headers.get("HX-Target") == "home-feed"
    section_limits = {
        media_type: _home_count(request.GET.get(f"shown_{media_type}"))
        for media_type in request.user.get_active_media_types()
    } if sort_fragment else None
    list_by_type = BasicMedia.objects.get_in_progress(
        request.user,
        sort_by,
        HOME_ITEMS_LIMIT,
        section_limits=section_limits,
    )
    context = {
        "list_by_type": list_by_type,
        "current_sort": sort_by,
        "sort_choices": HomeSortChoices.choices,
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
    if sort_fragment:
        response = render(request, "app/components/home_sorted.html", context)
        response.headers["X-Home-Fragment"] = "sort"
        response.headers["HX-Replace-Url"] = (
            f"{reverse('home')}?{urlencode({'sort': sort_by})}"
        )
        return response
    return render(request, "app/home.html", context)


def _home_count(value):
    try:
        return max(HOME_ITEMS_LIMIT, int(value))
    except (TypeError, ValueError):
        return HOME_ITEMS_LIMIT


def _home_load_more(request, media_type):
    if (
        media_type not in request.user.get_active_media_types()
        or media_type in {MediaTypes.TV.value, MediaTypes.YOUTUBE_VIDEO.value}
    ):
        raise Http404

    sort = request.GET.get("sort")
    if sort not in HomeSortChoices.values:
        sort = request.user.home_sort
    offset = _home_count(request.GET.get("offset"))
    media = BasicMedia.objects.get_in_progress(
        request.user, sort, HOME_MORE_LIMIT, media_type, offset=offset,
    ).get(media_type)
    signature = request.GET.get("signature")
    context = {"media_type": media_type, "current_sort": sort, "media_list": media}
    if signature and (not media or signature != media["previous_signature"]):
        # The ordered prefix changed in another tab or in a background update.
        context["media_list"] = BasicMedia.objects.get_in_progress(
            request.user, sort, offset + HOME_MORE_LIMIT, media_type, for_section=True,
        ).get(media_type)
        response = render(request, "app/components/home_section.html", context)
        response.headers["HX-Retarget"] = f"#home-section-{media_type}"
    else:
        response = render(request, "app/components/home_more.html", context)
    response.headers["X-Home-Fragment"] = "more"
    return response


def _update_season_progress(season, request, operation):
    if operation == "decrease":
        latest_watch = (
            season.episodes.filter(end_date__isnull=False, item__isnull=False)
            .order_by("-end_date", "-pk")
            .first()
        )
        if latest_watch:
            latest_watch.delete()
        return None

    if not season.pending_episode_numbers:
        return None

    next_episode = season.pending_episode_numbers[0]
    expected = request.POST.get("episode_number")
    if expected and expected != str(next_episode):
        return JsonResponse({"message": STALE_PROGRESS_MESSAGE}, status=409)

    answer = request.POST.get("confirm_completion")
    if season.completes_on_next_episode and answer not in {"yes", "no"}:
        return JsonResponse({"requires_confirmation": True})

    season.watch(
        next_episode,
        timezone.now().replace(second=0, microsecond=0),
        auto_complete=False,
        complete_season=season.completes_on_next_episode and answer == "yes",
        from_home=True,
    )
    return None


def _update_youtube_progress(channel, operation):
    watches = Episode.objects.filter(
        related_season__related_tv=channel,
        item__media_id=channel.item.media_id,
        item__source=Sources.YOUTUBE.value,
        item__media_type=MediaTypes.EPISODE.value,
        end_date__isnull=False,
    )
    if operation == "decrease":
        latest_watch = (
            watches.select_related("item").order_by("-end_date", "-pk").first()
        )
        if latest_watch:
            item = latest_watch.item
            latest_watch.delete()
            if not watches.filter(item=item).exists():
                queue_watched(item, channel.user, deleted=True)
        return

    video = (
        Item.objects.filter(
            media_id=channel.item.media_id,
            source=Sources.YOUTUBE.value,
            media_type=MediaTypes.EPISODE.value,
        )
        .exclude(pk__in=watches.values("item_id"))
        .order_by(
            models.F("air_date").asc(nulls_last=True),
            "season_number",
            "episode_number",
            "pk",
        )
        .first()
    )
    if video:
        season_item, _ = Item.objects.get_or_create(
            media_id=video.media_id,
            source=video.source,
            media_type=MediaTypes.SEASON.value,
            season_number=video.season_number,
            defaults={"title": channel.item.title, "image": channel.item.image},
        )
        season, _ = Season.objects.get_or_create(
            related_tv=channel,
            item=season_item,
            defaults={"user": channel.user, "status": Status.IN_PROGRESS.value},
        )
        Episode.objects.create(
            related_season=season, item=video, end_date=timezone.now(),
        )
        queue_watched(video, channel.user)


def _update_home_episodic_progress(request, media_type, instance_id, operation):
    section = request.POST.get("home_section")
    with transaction.atomic():
        try:
            media = BasicMedia.objects.get_media_prefetch(
                request.user, media_type, instance_id, lock=True,
                local_seasons=True,
            )
        except IndexError as error:
            raise Http404 from error

        if media_type == MediaTypes.SEASON.value:
            response = _update_season_progress(media, request, operation)
            if response is not None:
                return response
        else:
            expected = request.POST.get("pending_videos")
            if expected and expected != str(media.pending_videos):
                return JsonResponse({"message": STALE_PROGRESS_MESSAGE}, status=409)
            _update_youtube_progress(media, operation)

    if section in {"initial", "all"}:
        sort = request.POST.get("sort")
        if sort not in HomeSortChoices.values:
            sort = request.user.home_sort
        media_list = BasicMedia.objects.get_in_progress(
            request.user,
            sort,
            _home_count(request.POST.get("shown")),
            media_type,
            for_section=True,
        )
        return render(request, "app/components/home_section.html", {
            "media_type": media_type,
            "media_list": media_list.get(media_type),
            "current_sort": sort,
        })

    media = BasicMedia.objects.get_media_prefetch(
        request.user, media_type, instance_id, local_seasons=True,
    )
    return render(request, "app/components/progress_changer.html", {"media": media})


@transaction.atomic
def _record_manga_chapter(request, instance_id):
    try:
        minutes = int(request.POST.get("reading_time") or 0)
    except ValueError:
        return HttpResponseBadRequest("Enter a whole number of minutes.")
    if not 0 <= minutes <= 2_147_483_647:
        return HttpResponseBadRequest("Reading time is out of range.")

    try:
        manga = BasicMedia.objects.get_media_prefetch(
            request.user, MediaTypes.MANGA.value, instance_id, lock=True,
        )
    except IndexError as error:
        raise Http404 from error
    if manga.max_progress and manga.progress >= manga.max_progress:
        return HttpResponseBadRequest("All chapters are already tracked.")
    if manga.reading_time + minutes > 2_147_483_647:
        return HttpResponseBadRequest("Total reading time is out of range.")

    manga.progressed_at = timezone.now()
    manga.reading_time += minutes
    previous_progress = manga.progress
    manga.increase_progress()
    if manga.progress <= previous_progress:
        transaction.set_rollback(True)
        return HttpResponseBadRequest("All chapters are already tracked.")
    MangaSession.objects.create(
        manga=manga,
        chapter_number=manga.progress,
        minutes=minutes,
        session_date=manga.progressed_at,
    )
    return render(request, "app/components/progress_changer.html", {"media": manga})


@transaction.atomic
def _record_game_session(request, instance_id):
    try:
        minutes = int(request.POST.get("play_time", ""))
        percentage = request.POST.get("percentage_progress")
        percentage = int(percentage) if percentage else None
    except ValueError:
        return HttpResponseBadRequest("Enter whole numbers for minutes and progress.")

    max_minutes = 2_147_483_647
    if not 0 < minutes <= max_minutes:
        return HttpResponseBadRequest("Enter a valid number of minutes greater than zero.")
    if percentage is not None and not 0 <= percentage <= 100:
        return HttpResponseBadRequest("Progress must be between 0 and 100%.")

    try:
        game = BasicMedia.objects.get_media_prefetch(
            request.user, MediaTypes.GAME.value, instance_id, lock=True,
        )
    except IndexError as error:
        raise Http404 from error
    if max(game.play_time, game.progress) + minutes > max_minutes:
        return HttpResponseBadRequest("Total play time is out of range.")

    game.play_time += minutes
    game.progress += minutes
    game.progressed_at = timezone.now()
    if percentage is not None:
        game.percentage_progress = percentage
    game.save(update_fields=["play_time", "progress", "percentage_progress", "progressed_at"])
    GameSession.objects.create(
        game=game,
        minutes=minutes,
        percentage_progress=game.percentage_progress,
        session_date=game.progressed_at,
    )
    return render(request, "app/components/progress_changer.html", {"media": game})


@require_POST
def progress_edit(request, media_type, instance_id):
    """Increase or decrease the progress of a media item from home page."""
    operation = request.POST.get("operation")
    if operation not in {"increase", "decrease"}:
        return HttpResponseBadRequest("Unknown progress operation.")
    if media_type in {MediaTypes.SEASON.value, MediaTypes.YOUTUBE.value}:
        return _update_home_episodic_progress(
            request, media_type, instance_id, operation,
        )

    if media_type == MediaTypes.MANGA.value and operation == "increase":
        return _record_manga_chapter(request, instance_id)
    if media_type == MediaTypes.GAME.value and operation == "increase":
        return _record_game_session(request, instance_id)

    reading_time = request.POST.get("reading_time", 0)
    percentage_progress = request.POST.get("percentage_progress")

    media = BasicMedia.objects.get_media_prefetch(
        request.user,
        media_type,
        instance_id,
    )

    if operation == "increase":
        # For comics, always increment progress (issues read)
        if media_type == MediaTypes.COMIC.value:
            try:
                from app.models import ComicSession
                
                # Increment progress (number of issues read)
                media.progress = (media.progress or 0) + 1
                now = timezone.now()
                media.progressed_at = now
                
                # If reading_time is provided, accumulate it and create session
                if reading_time:
                    time_to_add = int(reading_time)
                    media.reading_time = (media.reading_time or 0) + time_to_add
                    media.save(update_fields=['reading_time', 'progress', 'progressed_at'])
                    
                    # Create ComicSession record
                    ComicSession.objects.create(
                        comic=media,
                        minutes=time_to_add,
                        issues_read=1,
                        session_date=now,
                        source=ComicSession.SessionSource.MANUAL,
                    )
                else:
                    # No reading_time provided (clicked + from home), just save progress
                    media.save(update_fields=['progress', 'progressed_at'])
            except (ValueError, TypeError):
                pass
        # For books, accumulate reading_time and update progress percentage
        elif media_type == MediaTypes.BOOK.value:
            try:
                from app.models import BookSession
                
                # reading_time is optional from the modal
                time_to_add = int(reading_time) if reading_time else 0
                if time_to_add > 0:
                    media.reading_time = (media.reading_time or 0) + time_to_add
                
                # Update percentage progress if provided
                percentage_for_session = None
                if percentage_progress:
                    try:
                        new_percentage = int(percentage_progress)
                        # Validate range 0-100
                        if 0 <= new_percentage <= 100:
                            media.progress = new_percentage
                            percentage_for_session = new_percentage
                    except (ValueError, TypeError):
                        pass
                
                now = timezone.now()
                media.progressed_at = now
                fields_to_update = ['reading_time', 'progress', 'progressed_at']
                media.save(update_fields=fields_to_update)
                
                # Create BookSession record if time was added
                if time_to_add > 0:
                    BookSession.objects.create(
                        book=media,
                        minutes=time_to_add,
                        percentage_progress=percentage_for_session,
                        session_date=now,
                        source=BookSession.SessionSource.MANUAL,
                    )
            except (ValueError, TypeError):
                pass
        else:
            media.increase_progress()
    elif operation == "decrease":
        media.decrease_progress()

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
    if media_type in (MediaTypes.MOVIE.value, MediaTypes.TV.value):
        media_metadata = display_metadata(media_metadata, request.user)
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
    if media_type == MediaTypes.GAME.value and current_instance:
        context["play_sessions"], context["play_sessions_total"] = _game_sessions_page(
            current_instance, request.GET.get("sessions"),
        )
    return render(request, "app/media_details.html", context)


@require_GET
@vary_on_headers("X-Season-Fragment")
def season_details(request, source, media_id, title, season_number):  # noqa: ARG001 For URL
    """Return the details page for a season."""
    if source == Sources.YOUTUBE.value:
        return redirect(reverse(
            "youtube_channel_details",
            kwargs={"source": source, "media_id": media_id, "title": title},
            query={"year": season_number if 1000 <= season_number <= 9999 else "all", "filter": "all"},
        ))
    tv_with_seasons_metadata = services.get_media_metadata(
        "tv_with_seasons",
        media_id,
        source,
        [season_number],
    )
    season_metadata = deepcopy(tv_with_seasons_metadata[f"season/{season_number}"])
    season_metadata = display_metadata(season_metadata, request.user)

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

    # Keep personal corrections outside the shared provider cache.
    season_metadata["episodes"] = [
        display_metadata(episode, request.user)
        for episode in season_metadata["episodes"]
    ]
    watched_numbers = {
        episode.item.episode_number
        for episode in episodes_in_db
        if episode.end_date is not None and episode.item_id is not None
    }
    listed_numbers = {episode["episode_number"] for episode in season_metadata["episodes"]}

    skipped_numbers = current_instance.skipped_episode_numbers - watched_numbers if current_instance else set()
    for episode in season_metadata["episodes"]:
        episode["skipped"] = episode["episode_number"] in skipped_numbers

    episode_filter = request.GET.get("filter", "all")
    if episode_filter == "unwatched":
        season_metadata["episodes"] = [
            episode for episode in season_metadata["episodes"]
            if not episode.get("history") and not episode["skipped"]
        ]
    elif episode_filter == "watched":
        season_metadata["episodes"] = [
            episode for episode in season_metadata["episodes"]
            if episode.get("history")
        ]
    elif episode_filter == "skipped":
        season_metadata["episodes"] = [episode for episode in season_metadata["episodes"] if episode["skipped"]]
    else:
        episode_filter = "all"
        season_metadata["episodes"] = [episode for episode in season_metadata["episodes"] if not episode["skipped"]]

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

    episodes_page = Paginator(season_metadata["episodes"], 30).get_page(
        request.GET.get("page"),
    )

    context = {
        "media": season_metadata,
        "tv": tv_with_seasons_metadata,
        "media_type": MediaTypes.SEASON.value,
        "user_medias": user_medias,
        "current_instance": current_instance,
        "watched_episode_count": len(watched_numbers),
        "season_episode_count": len(watched_numbers | listed_numbers),
        "current_filter": episode_filter,
        "current_sort": sort_order,
        "episodes_page": episodes_page,
    }
    if request.headers.get("X-Season-Fragment") == "1":
        response = render(request, "app/components/season_watch_fragments.html", context)
        response.headers["X-Season-Fragment"] = "1"
        return response
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
        catalog_defaults = {"title": metadata["title"], "image": metadata["image"]}
        if media_type == MediaTypes.MOVIE.value and metadata.get("runtime") is not None:
            catalog_defaults["runtime"] = metadata["runtime"]
        item, _ = Item.objects.update_or_create(
            media_id=media_id,
            source=source,
            media_type=media_type,
            season_number=season_number,
            defaults=catalog_defaults,
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
        title = display_metadata(media.item, request.user)
        if media_type == MediaTypes.GAME.value:
            initial_data["progress"] = helpers.minutes_to_hhmm(media.progress)
    else:
        metadata = services.get_media_metadata(
            media_type,
            media_id,
            source,
            [season_number],
        )
        title = display_metadata(metadata, request.user)["title"]
        if media_type == MediaTypes.SEASON.value:
            title += f" S{season_number}"

    form = get_form_class(media_type)(instance=media, initial=initial_data)
    form.order_fields(["status", "score"])
    original_poster = None
    if "poster_url" in form.fields:
        poster_item = media.item if media else metadata
        correction = override_for(poster_item, request.user)
        form.initial["poster_url"] = correction.image if correction and correction.image else ""
        original_poster = media.item.image if media else metadata.get("image")

    metadata_form = None
    if media_type == MediaTypes.MOVIE.value and source in {Sources.TMDB.value, Sources.MANUAL.value}:
        catalog = media.item if media else metadata
        correction = override_for(catalog, request.user)
        metadata_form = ItemMetadataForm(
            prefix="metadata",
            initial={field: getattr(correction, field, None) for field in ItemMetadataForm.base_fields},
        )
        for field, form_field in metadata_form.fields.items():
            original = catalog.get(field) if isinstance(catalog, dict) else getattr(catalog, field)
            if original is not None:
                form_field.widget.attrs["placeholder"] = original

    return render(
        request,
        "app/components/fill_track.html",
        {
            "title": title,
            "form": form,
            "media": media,
            "return_url": request.GET["return_url"],
            "original_poster": original_poster or settings.IMG_NONE,
            "metadata_form": metadata_form,
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
        if instance is None:
            raise Http404
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
                **({"runtime": metadata.get("runtime")} if media_type == MediaTypes.MOVIE.value else {}),
            },
        )
        model_name = "tv" if media_type == MediaTypes.YOUTUBE.value else media_type
        model = apps.get_model(app_label="app", model_name=model_name)
        instance = model(item=item, user=request.user)

    # Validate the form and save the instance if it's valid
    form_class = get_form_class(media_type)
    form = form_class(request.POST, instance=instance)
    forms = [form]
    metadata_form = None
    if (
        instance.item.media_type == MediaTypes.MOVIE.value
        and instance.item.source in {Sources.TMDB.value, Sources.MANUAL.value}
        and all(f"metadata-{field}" in request.POST for field in ItemMetadataForm.base_fields)
    ):
        metadata_form = ItemMetadataForm(request.POST, prefix="metadata")
        forms.append(metadata_form)

    if all(bound_form.is_valid() for bound_form in forms):
        with transaction.atomic():
            form.save()
            if (
                "tmdb_id" in request.POST
                and "tmdb_id" in form.cleaned_data
                and form.instance.item.source == Sources.MANUAL.value
            ):
                tmdb_id = form.cleaned_data["tmdb_id"] or None
                if tmdb_id != form.instance.item.tmdb_id:
                    Item.objects.filter(pk=form.instance.item_id).update(tmdb_id=tmdb_id)
            if "poster_url" in form.cleaned_data and "poster_url" in request.POST:
                save_poster_override(form.instance.item, request.user, form.cleaned_data["poster_url"])
            if metadata_form is not None:
                item = form.instance.item
                corrections = {
                    field: value if value not in (None, "", getattr(item, field)) else None
                    for field, value in metadata_form.cleaned_data.items()
                }
                overrides = ItemMetadataOverride.objects.filter(user=request.user, item=item)
                if any(value is not None for value in corrections.values()):
                    overrides.update_or_create(user=request.user, item=item, defaults=corrections)
                else:
                    overrides.delete()
        logger.info("%s saved successfully.", form.instance)
    else:
        for bound_form in forms:
            if bound_form.errors:
                logger.error(bound_form.errors.as_json())
            for field, errors in bound_form.errors.items():
                for error in errors:
                    messages.error(request, f"{field.replace('_', ' ').title()}: {error}")

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


@login_required
@require_POST
@transaction.atomic
def episode_skip(request, season_id):
    """Set or clear a skipped episode for this user's tracked season."""
    season = get_object_or_404(
        Season.objects.select_for_update(of=("self",)).select_related("item"),
        pk=season_id,
        user=request.user,
        item__source__in=(Sources.TMDB.value, Sources.MANUAL.value),
    )
    try:
        number = int(request.POST.get("episode_number", ""))
    except ValueError:
        return HttpResponseBadRequest("Invalid episode number.")
    if not 0 < number <= 2_147_483_647:
        return HttpResponseBadRequest("Invalid episode number.")

    if request.POST.get("skip_action") == "restore":
        season.skipped_episodes.filter(episode_number=number).delete()
    elif request.POST.get("skip_action") == "skip":
        if season.episodes.filter(item__episode_number=number, end_date__isnull=False).exists():
            return HttpResponseBadRequest("This episode has already been watched.")
        item = season.item
        if not Item.objects.filter(
            source=item.source, media_type=MediaTypes.EPISODE.value, media_id=item.media_id,
            season_number=item.season_number, episode_number=number,
        ).exists():
            metadata = services.get_media_metadata(
                "tv_with_seasons", item.media_id, item.source, [item.season_number],
            )
            episodes = metadata[f"season/{item.season_number}"]["episodes"]
            if not any(episode["episode_number"] == number for episode in episodes):
                raise Http404
        SkippedEpisode.objects.get_or_create(season=season, episode_number=number)
    else:
        return HttpResponseBadRequest("Unknown episode action.")
    return JsonResponse({"saved": True})


@require_POST
def episode_save(request):
    """Handle the creation, deletion, and updating of episodes for a season."""
    from app.models import TV, BasicMedia, Episode, Item, MediaTypes, Season, Sources, Status, GameSession, Game
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
        # For YouTube sources, end_date might come as 'Y-m-d H:i' but form expects only date.
        # In that case, fall back to timezone.now() instead of rejecting the request.
        if source == Sources.YOUTUBE.value:
            logger.debug("DEBUG episode_save: YouTube form invalid (%s), using timezone.now() as end_date", form.errors)
            end_date = timezone.now()
        else:
            logger.error("Form validation failed: %s", form.errors)
            return HttpResponseBadRequest("Invalid form data")
    else:
        end_date = form.cleaned_data["end_date"] or timezone.now()

    # If season_number is None, try to get it from the episode
    if season_number is None:
        episode_items = Item.objects.filter(
            media_id=media_id,
            source=source,
            media_type=MediaTypes.EPISODE.value,
            episode_number=episode_number,
        ).order_by("season_number")
        if (
            source == Sources.YOUTUBE.value
            and episode_items.values_list("season_number", flat=True).distinct().count() > 1
        ):
            return HttpResponseBadRequest("Season number is required for this YouTube video")
        episode_item = episode_items.first()
        if episode_item is None:
            logger.error("Episode not found: media_id=%s, source=%s, episode_number=%s", 
                        media_id, source, episode_number)
            return HttpResponseBadRequest("Episode not found")
        season_number = episode_item.season_number

    try:
        related_season = Season.objects.get(
            item__media_id=media_id,
            item__source=source,
            item__season_number=season_number,
            item__episode_number=None,
            user=request.user,
        )
    except Season.DoesNotExist:
        # For YouTube sources, create Season automatically if it doesn't exist
        if source == Sources.YOUTUBE.value:
            parent_tv = TV.objects.select_related("item").filter(
                item__media_id=media_id,
                item__source=source,
                item__media_type=MediaTypes.YOUTUBE.value,
                user=request.user,
            ).first()
            if parent_tv is None:
                return HttpResponseBadRequest("YouTube channel is not tracked by this user")
            parent_item = parent_tv.item

            season_label = "Unknown year" if season_number == 0 else str(season_number)
            season_item, _ = Item.objects.get_or_create(
                media_id=media_id,
                source=source,
                media_type=MediaTypes.SEASON.value,
                season_number=season_number,
                episode_number=None,
                defaults={
                    "title": f"{parent_item.title} - {season_label}",
                    "image": parent_item.image or settings.IMG_NONE,
                },
            )
            related_season, _ = Season.objects.get_or_create(
                item=season_item,
                user=request.user,
                defaults={
                    "related_tv": parent_tv,
                    "status": Status.IN_PROGRESS.value,
                },
            )
            logger.info("Created YouTube season %s for %s", season_label, parent_item.title)
        else:
            tv_with_seasons_metadata = services.get_media_metadata(
                "tv_with_seasons",
                media_id,
                source,
                [season_number],
            )
            season_metadata = tv_with_seasons_metadata[f"season/{season_number}"]

            item, _ = Item.objects.get_or_create(
                media_id=media_id,
                source=source,
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

    with transaction.atomic():
        skipped = set()
        if source != Sources.YOUTUBE.value:
            related_season = Season.objects.select_for_update().get(pk=related_season.pk)
            skipped = related_season.skipped_episode_numbers
        if skipped:
            catalog = {episode["episode_number"] for episode in season_metadata["episodes"]}
            watched = set(
                related_season.episodes.filter(end_date__isnull=False)
                .values_list("item__episode_number", flat=True),
            )
            is_last_episode = catalog - watched - skipped == {episode_number}
        else:
            is_last_episode = episode_number == max_episodes

        if is_last_episode and confirm_completion not in {"yes", "no"}:
            return JsonResponse({
                "requires_confirmation": True,
                "message": "Mark the season as completed after saving this watch?",
                "episode_data": {
                    "media_id": media_id,
                    "season_number": season_number,
                    "episode_number": episode_number,
                    "source": source,
                    "end_date": end_date.isoformat() if hasattr(end_date, "isoformat") else str(end_date),
                },
            })

        related_season.watch(
            episode_number, end_date,
            auto_complete=not skipped and (not is_last_episode or confirm_completion == "yes"),
            complete_season=bool(skipped and is_last_episode and confirm_completion == "yes"),
        )
        if source == Sources.YOUTUBE.value:
            from integrations.youtube_watched import queue_watched

            watched_item = Item.objects.filter(
                media_id=media_id, source=source, media_type=MediaTypes.EPISODE.value,
                season_number=season_number, episode_number=episode_number,
            ).first()
            if watched_item:
                queue_watched(watched_item, request.user)

    if request.headers.get("X-Season-Watch") == "1":
        return JsonResponse({"saved": True})

    # If HTMX request, return only the updated card HTML
    if request.headers.get('HX-Request'):
        videos = Item.objects.filter(
            media_id=media_id,
            source=source,
            media_type=MediaTypes.EPISODE.value,
            episode_number=episode_number,
            season_number=season_number,
        )
        video = with_video_details(videos, request.user).first()
        from django.middleware.csrf import get_token
        context = {
            'video': video,
            'csrf_token': get_token(request),
        }
        return render(request, 'app/components/youtube_grid_items_card.html', context)
    return helpers.redirect_back(request)


def handle_youtube_video_creation(request, form):
    """Handle creation of YouTube video with automatic channel/season detection and creation."""
    from app.providers import youtube
    from app.models import TV, BasicMedia, Episode, Item, MediaTypes, Season, Sources, Status, GameSession, Game
    
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
    video_year = youtube.get_published_year(published_date)
    if video_year is None:
        published_date = None
        video_year = 0
    
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
            "title": (
                f"{channel_item.title} - Unknown year"
                if video_year == 0
                else f"{channel_item.title} - {video_year}"
            ),
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
    season_label = "Unknown year" if video_year == 0 else str(video_year)
    messages.success(request, f"Successfully added video '{episode_item.title}' to {channel_item.title} ({season_label})")
    # After creating a YouTube video, redirect back to the Create Entry page
    # and preselect the YouTube Video tab so the user can add another quickly.
    return redirect(f"{reverse('create_entry')}?media_type={MediaTypes.YOUTUBE_VIDEO.value}")


@require_http_methods(["GET", "POST"])
def create_entry(request):
    """Return the form for manually adding media items."""
    from app.models import MediaTypes, Status
    if request.method == "GET":
        media_types = MediaTypes.values
        initial_media_type = request.GET.get("media_type")
        return render(request, "app/create_entry.html", {
            "media_types": media_types,
            "Status": Status,
            "initial_media_type": initial_media_type,
        })

    return_url = reverse("create_entry")
    if request.POST.get("media_type") == MediaTypes.EPISODE.value:
        return_url += "?media_type=episode"

    # Process the form submission
    form = ManualItemForm(request.POST, user=request.user)
    if not form.is_valid():
        # Handle form validation errors
        logger.error(form.errors.as_json())
        helpers.form_error_messages(form, request)
        return redirect(return_url)

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
        return redirect(return_url)

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
            return redirect(return_url)

        # Save the media instance
        media_form.instance.user = request.user
        media_form.instance.item = item

        # Handle relationships based on media type
        if item.media_type == MediaTypes.SEASON.value:
            media_form.instance.related_tv = form.cleaned_data["parent_tv"]

        media_form.save()

    # Success message
    msg = f"{item} added successfully."
    messages.success(request, msg)
    logger.info(msg)

    return redirect(return_url)


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


@require_POST
def atresplayer_metadata(request):
    """Extract metadata from Atresplayer URL via AJAX."""
    import json
    from app.providers import atresplayer
    
    try:
        data = json.loads(request.body)
        atresplayer_url = data.get('url', '').strip()
        
        if not atresplayer_url:
            return JsonResponse({'success': False, 'error': 'No URL provided'}, status=400)
        
        # Extract episode ID from URL
        episode_id = atresplayer.extract_episode_id(atresplayer_url)
        if not episode_id:
            return JsonResponse({
                'success': False, 
                'error': 'Invalid Atresplayer URL format'
            }, status=400)
        
        # Fetch metadata from Atresplayer API
        metadata = atresplayer.fetch_video_metadata(episode_id)
        if not metadata:
            return JsonResponse({
                'success': False, 
                'error': 'Could not fetch video metadata from Atresplayer'
            }, status=404)
        
        return JsonResponse({
            'success': True,
            'metadata': metadata
        })
        
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        logger.error(f"Error extracting Atresplayer metadata: {e}")
        return JsonResponse({
            'success': False, 
            'error': 'Internal server error'
        }, status=500)


@require_POST
def extract_url_metadata(request):
    """
    Extract metadata from a URL by auto-detecting the service.
    Supports: YouTube, Atresplayer, Globoplay.
    """
    import json
    from app.providers import atresplayer, globoplay
    
    try:
        data = json.loads(request.body)
        url = data.get('url', '').strip()
        
        if not url:
            return JsonResponse({'success': False, 'error': 'No URL provided'}, status=400)
        
        # Detect service and extract metadata
        service = None
        metadata = None
        
        # YouTube detection
        if 'youtube.com' in url or 'youtu.be' in url:
            service = 'youtube'
            # Extract video info using existing YouTube logic
            from app.providers import youtube
            video_id = youtube.extract_video_id(url)
            if video_id:
                video_info = youtube.fetch_video_metadata(video_id)
                if video_info:
                    metadata = {
                        'title': video_info.get('title'),
                        'thumbnail': video_info.get('thumbnail'),
                        'duration_minutes': video_info.get('duration_minutes'),
                        'air_date': video_info.get('published_date'),
                        'youtube_video_id': video_id,
                        'channel_info': {
                            'id': video_info.get('channel_id'),
                            'title': video_info.get('channel_title'),
                        },
                    }
        
        # Atresplayer detection
        elif 'atresplayer.com' in url:
            service = 'atresplayer'
            episode_id = atresplayer.extract_episode_id(url)
            if episode_id:
                metadata = atresplayer.fetch_video_metadata(episode_id)
        
        # Globoplay detection
        elif 'globoplay.globo.com' in url or 'globo.com/v/' in url:
            service = 'globoplay'
            video_id = globoplay.extract_video_id(url)
            if video_id:
                metadata = globoplay.fetch_video_metadata(video_id)
        
        else:
            return JsonResponse({
                'success': False, 
                'error': 'URL not recognized. Supported services: YouTube, Atresplayer, Globoplay'
            }, status=400)
        
        if not metadata:
            return JsonResponse({
                'success': False, 
                'error': f'Could not fetch metadata from {service}'
            }, status=404)
        
        return JsonResponse({
            'success': True,
            'service': service,
            'metadata': metadata
        })
        
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        logger.error(f"Error extracting URL metadata: {e}")
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
    ).order_by('-item__season_number', 'item__title')[:20]

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
        else:
            start_date = end_date = None

    # Get all user media data in a single operation
    user_media, media_count, items_watched, total_watch_minutes = stats.get_user_media(
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
    status_distribution = stats.get_status_distribution(user_media, request.user, start_date, end_date)
    watch_time_distribution_pie_chart_data = stats.get_watch_time_distribution_pie_chart_data(
        user_media, request.user, start_date, end_date
    )
    timeline = stats.get_timeline(user_media, start_date, end_date, user=request.user)

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
        "items_watched": items_watched,
        "total_watch_time": format_minutes(total_watch_minutes),
        "top_tv_shows": top_tv_shows,
        "top_youtube_channels": top_youtube_channels,
    }

    return render(request, "app/statistics.html", context)


@require_GET
def youtube_channel_details(request, source, media_id, title):  # noqa: ARG001 title for URL
    """Return the details page for a YouTube channel with year-based video filtering."""
    from app.models import BasicMedia, Sources
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
        channel_metadata = deepcopy(services.get_media_metadata("youtube", channel_id, source))
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
    
    channel_metadata = display_metadata(channel_metadata, request.user)

    # For YouTube channels, get episode items (not Episode records) through seasons
    episode_items = []
    if current_instance:
        # Get all episode Items from all seasons of this YouTube channel
        from app.models import TV, BasicMedia, Episode, Item, MediaTypes, Season, Sources, Status, GameSession, Game
        episode_items = list(Item.objects.filter(
            media_type=MediaTypes.EPISODE.value,
            source=Sources.YOUTUBE.value,
            media_id=media_id
        ).order_by('-air_date'))

    # Load all watch histories in one query instead of two queries per video.
    episode_history_by_item = {}
    if episode_items:
        histories = Episode.objects.filter(
            item__in=episode_items,
            related_season__related_tv=current_instance,
        ).order_by('-end_date')
        for history in histories:
            episode_history_by_item.setdefault(history.item_id, []).append(history)

    # For YouTube channels, we'll work with the episode items we have in the database
    # Convert episode items to a structure that the template expects
    episodes = []
    for item in episode_items:
        episode_history = episode_history_by_item.get(item.id, [])
        
        episode_data = {
            "title": item.title,
            "air_date": item.air_date.strftime('%Y-%m-%d') if item.air_date else None,
            "published_year": item.air_date.year if item.air_date else None,
            "episode_number": item.episode_number,
            "season_number": item.season_number,
            "image": item.image,
            "runtime": item.runtime,
            "id": item.id,
            "watched": bool(episode_history),
            "history": episode_history,
            "youtube_video_id": item.youtube_video_id,
            # Add required fields for template tags
            "source": item.source,
            "media_type": item.media_type,
            "media_id": item.media_id,
        }
        episodes.append(episode_data)
    
    channel_metadata["episodes"] = episodes

    all_channel_episodes = channel_metadata.get("episodes", [])
    available_year_values = sorted(
        {
            episode["published_year"]
            for episode in all_channel_episodes
            if episode.get("published_year") is not None
        },
        reverse=True,
    )
    available_years = [
        {"value": str(year), "label": str(year)}
        for year in available_year_values
    ]
    has_unknown_year = any(
        episode.get("published_year") is None
        for episode in all_channel_episodes
    )
    if has_unknown_year:
        available_years.append({"value": "unknown", "label": "Unknown year"})

    # Default each channel to its newest publication year unless the user
    # explicitly chose a year or All years.
    year_filter = request.GET.get("year")
    if year_filter is None:
        if available_year_values:
            year_filter = str(available_year_values[0])
        elif has_unknown_year:
            year_filter = "unknown"
        else:
            year_filter = "all"

    # Filter episodes by year if requested
    if year_filter == "unknown":
        episodes = [episode for episode in episodes if episode["published_year"] is None]
    elif year_filter != "all":
        try:
            filter_year = int(year_filter)
            episodes = [
                episode for episode in episodes
                if episode.get("published_year") == filter_year
            ]
        except ValueError:
            year_filter = "all"
    
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
    
    # Sort by publication year, then by episode number within each year.
    sort_order = request.GET.get("sort", "asc")
    descending = sort_order == "desc"
    known_year_episodes = [
        episode for episode in episodes if episode.get("published_year") is not None
    ]
    unknown_year_episodes = [
        episode for episode in episodes if episode.get("published_year") is None
    ]
    known_year_episodes.sort(
        key=lambda episode: (
            episode["published_year"],
            episode.get("air_date") or "",
            episode.get("episode_number") or 0,
        ),
        reverse=descending,
    )
    unknown_year_episodes.sort(
        key=lambda episode: (
            episode.get("season_number") or 0,
            episode.get("episode_number") or 0,
        ),
        reverse=descending,
    )
    episodes = known_year_episodes + unknown_year_episodes

    paginator = Paginator(episodes, 30)
    page = request.GET.get("page")
    video_id = request.GET.get("video")
    if page is None and video_id:
        for index, episode in enumerate(episodes):
            if str(episode["id"]) == video_id:
                page = index // paginator.per_page + 1
                break
    episodes_page = paginator.get_page(page)
    
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
        "episodes_page": episodes_page,
        "is_youtube_channel": True,  # Flag to customize template behavior
    }
    return render(request, "app/media_details.html", context)


@require_http_methods(["DELETE"])
@login_required
def delete_youtube_video(request, video_id):
    """Delete a YouTube video and persist its optional TA synchronization."""
    from integrations.models import TubeArchivistDeletion
    from integrations.tubearchivist import (
        TubeArchivistClient,
        TubeArchivistError,
        enqueue_deletion,
        validate_video_id,
    )
    from integrations.youtube_watched import queue_watched

    try:
        configured = bool(settings.TUBEARCHIVIST_URL or settings.TUBEARCHIVIST_TOKEN)
        if configured:
            TubeArchivistClient()  # Reject partial configuration before local deletion.
        with transaction.atomic():
            video_item = Item.objects.select_for_update().get(
                id=video_id,
                media_type=MediaTypes.EPISODE.value,
                source=Sources.YOUTUBE.value,
            )
            if configured:
                validate_video_id(video_item.youtube_video_id)
                job, created = TubeArchivistDeletion.objects.get_or_create(
                    youtube_id=video_item.youtube_video_id,
                    defaults={
                        "title": video_item.title,
                        "requested_by": request.user,
                        "plex_library": settings.PLEX_YOUTUBE_LIBRARY,
                    },
                )
                if not created and job.status != TubeArchivistDeletion.Status.PENDING:
                    job.status = TubeArchivistDeletion.Status.PENDING
                    job.failures = 0
                    job.last_error = ""
                    job.file_deleted = False
                    job.ta_completed_at = None
                    job.plex_library = settings.PLEX_YOUTUBE_LIBRARY
                    job.plex_scan_requested_at = None
                    job.ignore_requested_at = None
                    job.next_attempt_at = timezone.now()
                    job.locked_until = timezone.now()
                    job.save()
                transaction.on_commit(lambda: enqueue_deletion(job.pk))
            # Capture the external ID before the shared Item and history cascade.
            queue_watched(video_item, request.user, deleted=True)
            # The Item is shared; cascading deletion removes its tracking records.
            video_item.delete()
        if configured:
            messages.info(request, "Video deleted. TubeArchivist Delete + Ignore is queued.")
        return HttpResponse(status=200)
    except Item.DoesNotExist:
        return HttpResponse(status=200)  # Repeated deletes are harmless.
    except TubeArchivistError as error:
        return HttpResponseBadRequest(str(error))


def _game_session_records(game):
    """Combine saved sessions and any additional play time from older history."""
    recorded = [
        {
            "id": session.pk,
            "kind": "recorded",
            "date": session.session_date,
            "play_time": session.minutes,
            "percentage_progress": session.percentage_progress,
            "history_minutes": 0,
        }
        for session in game.sessions.order_by("session_date", "id")
    ]
    pending = deque(recorded)
    sessions = list(recorded)
    previous_time = 0

    history = game.history.order_by("history_date", "history_id").only(
        "history_id", "history_date", "play_time", "percentage_progress",
    )
    for snapshot in history:
        remaining = max(0, (snapshot.play_time or 0) - previous_time)
        previous_time = snapshot.play_time or 0
        # A saved session can already be included in this cumulative snapshot.
        while pending and pending[0]["date"] <= snapshot.history_date:
            session = pending.popleft()
            covered = min(session["play_time"], remaining)
            session.update(history=snapshot, history_minutes=covered)
            remaining -= covered

        if remaining:
            sessions.append({
                "id": snapshot.history_id,
                "kind": "history",
                "date": snapshot.history_date,
                "play_time": remaining,
                "percentage_progress": snapshot.percentage_progress,
                "history": snapshot,
                "history_minutes": remaining,
            })

    return sorted(sessions, key=lambda session: session["date"])


def _game_sessions_page(game, page_number):
    sessions = _game_session_records(game)
    total = 0
    for number, session in enumerate(sessions, start=1):
        total += session["play_time"]
        session.update(
            number=number,
            play_time_formatted=helpers.minutes_to_hhmm(session["play_time"]),
            total_formatted=helpers.minutes_to_hhmm(total),
        )
    sessions.reverse()
    return Paginator(sessions, 10).get_page(page_number), helpers.minutes_to_hhmm(total)


def _game_sessions_url(game, page=None):
    url = app_tags.media_url(game.item)
    if page:
        url += "?" + urlencode({"sessions": page})
    return url + "#play-sessions"


@login_required
@require_GET
def game_sessions(request, game_id):
    """Redirect old session bookmarks to the game's session table."""
    game = get_object_or_404(Game.objects.select_related("item"), pk=game_id, user=request.user)
    return redirect(_game_sessions_url(game, request.GET.get("sessions")))


@login_required
@require_POST
@transaction.atomic
def delete_game_session(request, game_id, session_kind, session_id):
    """Remove one session and its contribution to cumulative play time."""
    game = get_object_or_404(
        Game.objects.select_related("item").select_for_update(),
        pk=game_id,
        user=request.user,
    )
    sessions = _game_session_records(game)
    session = next(
        (row for row in sessions if (row["kind"], row["id"]) == (session_kind, session_id)),
        None,
    )
    if session is None:
        raise Http404

    if session_kind == "recorded":
        game.sessions.filter(pk=session_id).delete()

    if minutes := session["history_minutes"]:
        snapshot = session["history"]
        game.history.filter(
            models.Q(history_date__gt=snapshot.history_date)
            | models.Q(history_date=snapshot.history_date, history_id__gte=snapshot.history_id),
        ).update(
            play_time=Greatest(models.F("play_time") - minutes, 0),
            progress=Greatest(models.F("progress") - minutes, 0),
        )

    game.play_time = max(0, game.play_time - session["play_time"])
    game.progress = max(0, game.progress - session["play_time"])
    if session is sessions[-1] and game.percentage_progress == session["percentage_progress"]:
        game.percentage_progress = next(
            (row["percentage_progress"] for row in reversed(sessions[:-1])
             if row["percentage_progress"] is not None),
            0,
        )
    game.save(update_fields=["play_time", "progress", "percentage_progress"])

    destination = _game_sessions_url(game, request.POST.get("sessions"))
    if request.headers.get("HX-Request") == "true":
        return HttpResponse(status=200, headers={"HX-Redirect": destination})
    return redirect(destination)
