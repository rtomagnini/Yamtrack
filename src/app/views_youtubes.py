from django.shortcuts import render
from django.db.models import Q, OuterRef, Exists, Subquery, Value, CharField
from app.models import Item, TV, Season, Episode, Sources, MediaTypes
from django.views.decorators.http import require_GET

@require_GET
def youtubes_view(request):
    search = request.GET.get('search', '').strip()
    filter_status = request.GET.get('status', 'unwatched')
    sort = request.GET.get('sort', 'air_date')

    # Base queryset: Items that are YouTube videos
    qs = Item.objects.filter(source=Sources.YOUTUBE.value, media_type=MediaTypes.EPISODE.value)

    # Annotate with whether watched (has Episode with end_date)
    qs = qs.annotate(
        is_watched=Exists(
            Episode.objects.filter(item=OuterRef('pk'), end_date__isnull=False)
        ),
        end_date_sub=Episode.objects.filter(item=OuterRef('pk'), end_date__isnull=False).values('end_date')[:1]
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


    # Annotate with channel (TV) name and image (logo) by joining through Season
    season_qs = Season.objects.filter(
        item__media_id=OuterRef('media_id'),
        item__source=OuterRef('source'),
        item__media_type=MediaTypes.SEASON.value,
        item__season_number=OuterRef('season_number'),
    )
    tv_title_qs = season_qs.filter(related_tv__isnull=False).values('related_tv__item__title')[:1]
    tv_image_qs = season_qs.filter(related_tv__isnull=False).values('related_tv__item__image')[:1]
    qs = qs.annotate(
        channel_name=Subquery(tv_title_qs, output_field=CharField()),
        channel_image=Subquery(tv_image_qs, output_field=CharField()),
    )

    layout = request.GET.get('layout', 'grid')
    from django.utils import timezone
    from django.middleware.csrf import get_token
    context = {
        'videos': qs,
        'search': search,
        'current_status': filter_status,
        'current_sort': sort,
        'current_layout': layout,
        'today': timezone.now().date(),
        'csrf_token': get_token(request),
    }
    if request.headers.get('HX-Request'):
        # Render only the content area (grid or table) for htmx
        return render(request, 'app/components/youtube_content.html', context)
    return render(request, 'app/youtubes_list.html', context)
