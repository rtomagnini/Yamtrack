from django.conf import settings
from django.db.models import CharField, Exists, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from app.models import Episode, Item, ItemMetadataOverride, MediaTypes, Season, Sources
from integrations.invidious import InvidiousError, get_video_comments
from integrations.tubearchivist import TubeArchivistError


def with_video_details(videos, user):
    """Add watch dates and channel details for the video cards."""
    watches = Episode.objects.filter(
        item=OuterRef("pk"), end_date__isnull=False,
    )
    channel_seasons = Season.objects.filter(
        item__media_id=OuterRef("media_id"),
        item__source=OuterRef("source"),
        item__media_type=MediaTypes.SEASON.value,
        item__season_number=OuterRef("season_number"),
        related_tv__isnull=False,
    )
    personal_logo_qs = ItemMetadataOverride.objects.filter(
        user=user,
        item__source=OuterRef("source"),
        item__media_type=MediaTypes.YOUTUBE.value,
        item__media_id=OuterRef("media_id"),
    )
    return videos.annotate(
        is_watched=Exists(watches),
        end_date_sub=Subquery(watches.values("end_date")[:1]),
        channel_name=Subquery(channel_seasons.values("related_tv__item__title")[:1]),
        channel_image=Coalesce(
            Subquery(personal_logo_qs.values("image")[:1]),
            Subquery(channel_seasons.values("related_tv__item__image")[:1]),
            output_field=CharField(),
        ),
    )


@require_GET
def youtubes_view(request):
    search = request.GET.get('search', '').strip()
    filter_status = request.GET.get('status', 'unwatched')
    sort = request.GET.get('sort', 'air_date')

    # Base queryset: Items that are YouTube videos
    qs = with_video_details(
        Item.objects.filter(source=Sources.YOUTUBE.value, media_type=MediaTypes.EPISODE.value),
        request.user,
    )

    # Search by title
    if search:
        qs = qs.filter(title__icontains=search)

    # Filter by watched/unwatched
    if filter_status == 'watched':
        qs = qs.filter(is_watched=True)
    elif filter_status == 'unwatched':
        qs = qs.filter(is_watched=False)
    # else 'all': no filter

    # Sorting with secondary order by ID (descending) for consistent ordering
    if sort == 'title':
        qs = qs.order_by('title', '-id')
    elif sort == 'runtime':
        qs = qs.order_by('runtime', '-id')
    elif sort == 'end_date':
        qs = qs.order_by('end_date_sub', '-id')
    else:  # default to air_date
        qs = qs.order_by('-air_date', '-id')

    layout = request.GET.get('layout', 'grid')
    context = {
        'videos': qs,
        'search': search,
        'current_status': filter_status,
        'current_sort': sort,
        'current_layout': layout,
        'csrf_token': get_token(request),
    }
    if request.headers.get('HX-Request'):
        # Render only the content area (grid or table) for htmx
        return render(request, 'app/components/youtube_content.html', context)
    return render(request, 'app/youtubes_list.html', context)


@require_GET
def youtube_comments(request, video_id):
    """Render a page of comments for a video from the Yamtrack catalog."""
    video = get_object_or_404(
        Item, pk=video_id, source=Sources.YOUTUBE.value, media_type=MediaTypes.EPISODE.value,
    )
    continuation = request.GET.get("continuation", "")
    if len(continuation) > 4096:
        return HttpResponseBadRequest()
    if not settings.INVIDIOUS_URL.strip():
        return HttpResponse(status=503)
    try:
        page = get_video_comments(video.youtube_video_id, continuation)
    except TubeArchivistError as error:
        raise Http404 from error
    except InvidiousError:
        return HttpResponse(status=502)

    return render(request, "app/components/youtube_comments_page.html", {
        "page": page,
        "video_id": video.pk,
        "replies": request.GET.get("replies") == "1",
    })
