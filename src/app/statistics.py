def get_watch_time_distribution_pie_chart_data(user_media):
    """Aggregate total watch time (in minutes) by media type for pie chart."""
    from . import media_type_config
    from .templatetags import app_tags
    from app.models import MediaTypes
    chart_data = {
        "labels": [],
        "datasets": [
            {
                "data": [],
                "backgroundColor": [],
            },
        ],
    }
    # Include TV SHOW (tmdb/manual), YouTube, Movies, Comics, Games, and Books
    media_types = [MediaTypes.TV.value, MediaTypes.YOUTUBE.value, MediaTypes.MOVIE.value, MediaTypes.COMIC.value, MediaTypes.GAME.value, MediaTypes.BOOK.value]
    total_minutes = 0
    media_type_minutes = {}
    for media_type in media_types:
        queryset = user_media.get(media_type)
        if queryset is not None:
            if media_type == MediaTypes.MOVIE.value:
                # For movies, sum item__runtime for all watched movies
                minutes = queryset.select_related("item").aggregate(total=models.Sum("item__runtime"))["total"] or 0
            elif media_type == MediaTypes.COMIC.value:
                # For comics, sum accumulated reading_time for all comics with progress > 0
                minutes = queryset.aggregate(total=models.Sum("reading_time"))["total"] or 0
            elif media_type == MediaTypes.GAME.value:
                # For games, sum accumulated play_time for all games with progress > 0
                minutes = queryset.aggregate(total=models.Sum("play_time"))["total"] or 0
            elif media_type == MediaTypes.BOOK.value:
                # For books, sum accumulated reading_time for all books with progress > 0
                minutes = queryset.aggregate(total=models.Sum("reading_time"))["total"] or 0
            else:
                # For TV/YouTube, sum item__runtime for all watched episodes
                minutes = queryset.select_related("item").aggregate(total=models.Sum("item__runtime"))["total"] or 0
            media_type_minutes[media_type] = minutes
            total_minutes += minutes
        else:
            media_type_minutes[media_type] = 0
    legend_labels = []
    for media_type in media_types:
        minutes = media_type_minutes[media_type]
        if minutes > 0:
            label = app_tags.media_type_readable(media_type)
            hours = minutes // 60
            mins = minutes % 60
            time_str = f"{hours}h{mins:02d}m" if hours else f"{mins}m"
            # Pie chart label: name only
            chart_data["labels"].append(label)
            # Legend label: name (time only)
            legend_labels.append(f"{label} ({time_str})")
            chart_data["datasets"][0]["data"].append(minutes)
            chart_data["datasets"][0]["backgroundColor"].append(media_type_config.get_stats_color(media_type))
    chart_data["legend_labels"] = legend_labels
    return chart_data
def get_top_tv_shows(user, start_date, end_date, limit=6):
    """
    Devuelve los TV Shows con mÃ¡s episodios vistos por el usuario en el periodo filtrado.
    Agrupa por TV (Season.related_tv) donde source=manual o tmdb, suma episodios vistos.
    Retorna lista: { 'title': ..., 'count': ..., 'item': ... }
    """
    from app.models import Episode, TV
    from django.db.models import Count
    filters = {
        'related_season__related_tv__user': user,
        'item__media_type': 'episode',
        'item__source__in': ['tmdb', 'manual'],
        'end_date__isnull': False,
    }
    if start_date:
        filters['end_date__gte'] = start_date
    if end_date:
        filters['end_date__lte'] = end_date

    qs = (
        Episode.objects.filter(**filters)
        .values('related_season__related_tv')
        .annotate(count=Count('id'))
        .order_by('-count')[:limit]
    )
    tv_ids = [row['related_season__related_tv'] for row in qs]
    tvs = {tv.id: tv for tv in TV.objects.filter(id__in=tv_ids).select_related('item')}
    result = []
    for row in qs:
        tv_obj = tvs.get(row['related_season__related_tv'])
        if tv_obj:
            result.append({
                'title': tv_obj.item.title,
                'count': row['count'],
                'item': tv_obj.item,
            })
    return result

def get_top_youtube_channels(user, start_date, end_date, limit=6):
    """
    Devuelve los canales de YouTube con mÃ¡s episodios vistos por el usuario en el periodo filtrado.
    Agrupa por TV (Season.related_tv) donde source=youtube, suma episodios vistos.
    Retorna lista: { 'channel_name': ..., 'count': ..., 'item': ... }
    """
    from app.models import Episode, TV
    from django.db.models import Count
    filters = {
        'related_season__related_tv__user': user,
        'item__media_type': 'episode',
        'item__source': 'youtube',
        'end_date__isnull': False,
    }
    if start_date:
        filters['end_date__gte'] = start_date
    if end_date:
        filters['end_date__lte'] = end_date

    qs = (
        Episode.objects.filter(**filters)
        .values('related_season__related_tv')
        .annotate(count=Count('id'))
        .order_by('-count')[:limit]
    )
    tv_ids = [row['related_season__related_tv'] for row in qs]
    tvs = {tv.id: tv for tv in TV.objects.filter(id__in=tv_ids).select_related('item')}
    result = []
    for row in qs:
        tv_obj = tvs.get(row['related_season__related_tv'])
        if tv_obj:
            result.append({
                'channel_name': tv_obj.item.title,
                'count': row['count'],
                'item': tv_obj.item,
            })
    return result
def get_watch_time_timeseries(user, start_date, end_date):
    """
    Devuelve el tiempo de visionado (runtime de episodios vistos) agrupado por dÃ­a, semana o mes.
    - Hasta 30 dÃ­as: por dÃ­a
    - Entre 31 y 180 dÃ­as: por semana
    - MÃ¡s de 180 dÃ­as: por mes
    """
    from app.models import Episode, Comic, Movie, Game
    from django.db.models import Sum, F
    from django.utils import timezone
    import datetime

    # Determinar agrupamiento
    if not start_date or not end_date:
        # Si no hay fechas, usar por mes
        group = 'month'
    else:
        days = (end_date - start_date).days
        if days <= 30:
            group = 'day'
        elif days <= 180:
            group = 'week'
        else:
            group = 'month'

    # Query de episodios vistos por el usuario en el rango
    episode_filters = {
        'end_date__isnull': False,
        'item__runtime__isnull': False,
        'item__isnull': False,
        'item__media_type': 'episode',
        'related_season__user': user,
    }
    if start_date:
        episode_filters['end_date__gte'] = start_date
    if end_date:
        episode_filters['end_date__lte'] = end_date
    episodes = Episode.objects.filter(**episode_filters)

    # Query de pelÃ­culas vistas por el usuario en el rango
    movie_filters = {
        'end_date__isnull': False,
        'item__runtime__isnull': False,
        'user': user,
    }
    if start_date:
        movie_filters['end_date__gte'] = start_date
    if end_date:
        movie_filters['end_date__lte'] = end_date
    movies = Movie.objects.filter(**movie_filters).select_related('item')

    # Query de comics leÃ­dos por el usuario en el rango
    comic_filters = {
        'progress__gt': 0,
        'reading_time__gt': 0,
        'user': user,
    }
    if start_date:
        comic_filters['progressed_at__gte'] = start_date
    if end_date:
        comic_filters['progressed_at__lte'] = end_date
    comics = Comic.objects.filter(**comic_filters)

    # Query de games jugados por el usuario en el rango
    game_filters = {
        'progress__gt': 0,
        'play_time__gt': 0,
        'user': user,
    }
    if start_date:
        game_filters['progressed_at__gte'] = start_date
    if end_date:
        game_filters['progressed_at__lte'] = end_date
    games = Game.objects.filter(**game_filters)

    # Agrupar y sumar runtime
    data = {}
    for ep in episodes.select_related('item'):
        dt = ep.end_date
        if group == 'day':
            key = dt.date()
        elif group == 'week':
            key = dt.date() - datetime.timedelta(days=dt.weekday())  # lunes de la semana
        else:
            key = dt.date().replace(day=1)  # primer dÃ­a del mes
        data.setdefault(key, 0)
        data[key] += ep.item.runtime or 0

    # Agrupar y sumar runtime de pelÃ­culas
    for movie in movies:
        dt = movie.end_date
        if group == 'day':
            key = dt.date()
        elif group == 'week':
            key = dt.date() - datetime.timedelta(days=dt.weekday())  # lunes de la semana
        else:
            key = dt.date().replace(day=1)  # primer dÃ­a del mes
        data.setdefault(key, 0)
        data[key] += movie.item.runtime or 0

    # Agrupar y sumar reading_time de comics (accumulated total, not per issue)
    for comic in comics:
        dt = comic.progressed_at
        if group == 'day':
            key = dt.date()
        elif group == 'week':
            key = dt.date() - datetime.timedelta(days=dt.weekday())  # lunes de la semana
        else:
            key = dt.date().replace(day=1)  # primer dÃ­a del mes
        data.setdefault(key, 0)
        data[key] += comic.reading_time or 0

    # Agrupar y sumar play_time de games (accumulated total)
    for game in games:
        dt = game.progressed_at
        if group == 'day':
            key = dt.date()
        elif group == 'week':
            key = dt.date() - datetime.timedelta(days=dt.weekday())  # lunes de la semana
        else:
            key = dt.date().replace(day=1)  # primer dÃ­a del mes
        data.setdefault(key, 0)
        data[key] += game.play_time or 0

    # Ordenar por fecha
    sorted_keys = sorted(data.keys())
    labels = []
    values = []
    for k in sorted_keys:
        if group == 'day':
            labels.append(k.strftime('%Y-%m-%d'))
        elif group == 'week':
            labels.append(f"Semana {k.strftime('%Y-%m-%d')}")
        else:
            labels.append(k.strftime('%Y-%m'))
        values.append(data[k])

    # Formato para Chart.js
    return {
        'labels': labels,
        'datasets': [{
            'label': 'Watch Time (min)',
            'data': values,
            'fill': False,
            'borderColor': '#6366f1',
            'backgroundColor': '#6366f1',
            'tension': 0.3,
        }],
        'group': group,
    }
import calendar
import datetime
import heapq
import itertools
import logging
from collections import defaultdict

from dateutil.relativedelta import relativedelta
from django.apps import apps
from django.db import models
from django.db.models import (
    Prefetch,
    Q,
)
from django.utils import timezone

from app import media_type_config
from app.models import TV, BasicMedia, Episode, MediaManager, MediaTypes, Season, Status
from app.templatetags import app_tags

logger = logging.getLogger(__name__)


def get_user_media(user, start_date, end_date):
    """Get all media items and their counts for a user within date range."""
    def _get_model_name_for_media_type(media_type):
        # Map YouTube to TV model since they have the same structure
        if media_type == MediaTypes.YOUTUBE.value:
            return "tv"
        return media_type

    media_models = [
        apps.get_model(app_label="app", model_name=_get_model_name_for_media_type(media_type))
        for media_type in user.get_active_media_types()
    ]

    # --- Nueva lÃ³gica para separar TV Show y YouTube correctamente ---
    # 1. TV Show: Episodios con source TMDB o MANUAL
    tv_episodes = Episode.objects.filter(
        related_season__user=user,
        item__media_type=MediaTypes.EPISODE.value,
        item__source__in=["tmdb", "manual"],
    )
    # 2. YouTube: Episodios con source YOUTUBE
    youtube_episodes = Episode.objects.filter(
        related_season__user=user,
        item__media_type=MediaTypes.EPISODE.value,
        item__source="youtube",
    )
    # 3. Otros tipos (movie, comic, anime, book, game...)
    other_types = [
        MediaTypes.MOVIE.value,
        MediaTypes.ANIME.value,
        MediaTypes.COMIC.value,
        MediaTypes.BOOK.value,
        MediaTypes.GAME.value,
    ]
    other_media = {}
    for media_type in other_types:
        model = apps.get_model(app_label="app", model_name=media_type)
        if media_type == MediaTypes.COMIC.value:
            # For comics, include all with progress > 0 (issues read)
            if start_date is None and end_date is None:
                queryset = model.objects.filter(user=user, progress__gt=0)
            else:
                queryset = model.objects.filter(user=user, progress__gt=0, progressed_at__range=(start_date, end_date))
        elif media_type == MediaTypes.GAME.value:
            # For games, include all with progress > 0 (time played)
            if start_date is None and end_date is None:
                queryset = model.objects.filter(user=user, progress__gt=0)
            else:
                queryset = model.objects.filter(user=user, progress__gt=0, progressed_at__range=(start_date, end_date))
        elif media_type == MediaTypes.BOOK.value:
            # For books, include all with progress > 0 (reading progress)
            if start_date is None and end_date is None:
                queryset = model.objects.filter(user=user, progress__gt=0)
            else:
                queryset = model.objects.filter(user=user, progress__gt=0, progressed_at__range=(start_date, end_date))
        else:
            # For other media types, use end_date
            if start_date is None and end_date is None:
                queryset = model.objects.filter(user=user, end_date__isnull=False)
            else:
                queryset = model.objects.filter(user=user, end_date__isnull=False, end_date__range=(start_date, end_date))
        other_media[media_type] = queryset

    # Construir user_media y media_count
    user_media = {}
    media_count = {"total": 0}
    # Solo episodios de TV vistos (end_date no nulo y en rango)
    if start_date is None and end_date is None:
        tv_episodes_watched = tv_episodes.filter(end_date__isnull=False)
        youtube_episodes_watched = youtube_episodes.filter(end_date__isnull=False)
    else:
        tv_episodes_watched = tv_episodes.filter(end_date__isnull=False, end_date__range=(start_date, end_date))
        youtube_episodes_watched = youtube_episodes.filter(end_date__isnull=False, end_date__range=(start_date, end_date))
    user_media[MediaTypes.TV.value] = tv_episodes_watched
    media_count[MediaTypes.TV.value] = tv_episodes_watched.count()
    user_media[MediaTypes.YOUTUBE.value] = youtube_episodes_watched
    media_count[MediaTypes.YOUTUBE.value] = youtube_episodes_watched.count()
    for media_type, queryset in other_media.items():
        user_media[media_type] = queryset
        if media_type == MediaTypes.COMIC.value:
            # For comics, count total issues read (sum of progress)
            media_count[media_type] = queryset.aggregate(total=models.Sum("progress"))["total"] or 0
        elif media_type == MediaTypes.GAME.value:
            # For games, count number of games played (with progress > 0)
            media_count[media_type] = queryset.count()
        elif media_type == MediaTypes.BOOK.value:
            # For books, count number of books being read (with progress > 0)
            media_count[media_type] = queryset.count()
        else:
            media_count[media_type] = queryset.count()
    media_count["total"] = sum(media_count[mt] for mt in media_count if mt != "total")

    logger.info(
        "%s - Retrieved media %s",
        user,
        "for all time" if start_date is None else f"from {start_date} to {end_date}",
    )
    # Calculate total episodes watched and comic issues read
    if start_date is None and end_date is None:
        watched_episodes = Episode.objects.filter(related_season__user=user, end_date__isnull=False)
        watched_movies = TV.objects.none()  # placeholder
        watched_movies_qs = apps.get_model("app", "movie").objects.filter(user=user, end_date__isnull=False)
        # For comics, count issues read (sum of progress) instead of completed series
        read_comics_qs = apps.get_model("app", "comic").objects.filter(user=user, progress__gt=0)
    else:
        watched_episodes = Episode.objects.filter(related_season__user=user, end_date__isnull=False, end_date__range=(start_date, end_date))
        watched_movies = TV.objects.none()  # placeholder
        watched_movies_qs = apps.get_model("app", "movie").objects.filter(user=user, end_date__isnull=False, end_date__range=(start_date, end_date))
        # For comics with date range, we need comics that have been updated in that range
        read_comics_qs = apps.get_model("app", "comic").objects.filter(user=user, progress__gt=0, progressed_at__range=(start_date, end_date))

    episodes_watched = watched_episodes.count()
    # For comics, sum the progress (number of issues read)
    comics_read = read_comics_qs.aggregate(total=models.Sum("progress"))["total"] or 0
    # For games, count number of games played
    if start_date is None and end_date is None:
        played_games_qs = apps.get_model("app", "game").objects.filter(user=user, progress__gt=0)
    else:
        played_games_qs = apps.get_model("app", "game").objects.filter(user=user, progress__gt=0, progressed_at__range=(start_date, end_date))
    games_played = played_games_qs.count()
    items_watched = episodes_watched + comics_read + games_played

    # Sum runtime for watched episodes (from related Item)
    episode_minutes = watched_episodes.select_related("item").aggregate(
        total=models.Sum("item__runtime")
    )["total"] or 0

    # Sum runtime for watched movies
    movie_minutes = watched_movies_qs.select_related("item").aggregate(
        total=models.Sum("item__runtime")
    )["total"] or 0

    # Sum reading time for comic issues (reading_time is already accumulated per comic)
    comic_minutes = read_comics_qs.aggregate(
        total=models.Sum("reading_time")
    )["total"] or 0

    # Sum play time for games (play_time is already accumulated per game)
    game_minutes = played_games_qs.aggregate(
        total=models.Sum("play_time")
    )["total"] or 0

    total_watch_minutes = episode_minutes + movie_minutes + comic_minutes + game_minutes

    return user_media, media_count, items_watched, total_watch_minutes


def get_media_type_distribution(media_count):
    """Get data formatted for Chart.js pie chart."""
    # Define colors for each media type
    # Format for Chart.js
    chart_data = {
        "labels": [],
        "datasets": [
            {
                "data": [],
                "backgroundColor": [],
            },
        ],
    }

    # Incluir TV, YouTube, Movie, Anime, Comic, Book, Game
    allowed_types = [
        MediaTypes.TV.value,
        MediaTypes.YOUTUBE.value,
        MediaTypes.MOVIE.value,
        MediaTypes.ANIME.value,
        MediaTypes.COMIC.value,
        MediaTypes.BOOK.value,
        MediaTypes.GAME.value,
    ]
    total = sum(media_count.get(mt, 0) for mt in allowed_types)
    for media_type in allowed_types:
        count = media_count.get(media_type, 0)
        if count > 0:
            label = app_tags.media_type_readable(media_type)
            percent = (count / total * 100) if total else 0
            chart_data["labels"].append(f"{label} ({percent:.1f}%)")
            chart_data["datasets"][0]["data"].append(count)
            chart_data["datasets"][0]["backgroundColor"].append(
                media_type_config.get_stats_color(media_type),
            )
    return chart_data


def get_status_distribution(user_media):
    """Get status distribution for each media type within date range."""
    # Nuevo: mostrar total de vistos por tipo
    media_types = [
        MediaTypes.TV.value,
        MediaTypes.YOUTUBE.value,
        MediaTypes.MOVIE.value,
        MediaTypes.ANIME.value,
        MediaTypes.COMIC.value,
        MediaTypes.BOOK.value,
    ]
    data = []
    labels = []
    colors = []
    default_color = "#1976d2"  # Azul sÃ³lido (Material Design)
    for media_type in media_types:
        queryset = user_media.get(media_type)
        if queryset is None:
            count = 0
        elif media_type in [MediaTypes.TV.value, MediaTypes.YOUTUBE.value]:
            count = queryset.count()
        elif media_type == MediaTypes.COMIC.value:
            # For comics, count total issues read (sum of progress)
            count = queryset.aggregate(total=models.Sum("progress"))["total"] or 0
        else:
            model = getattr(queryset, 'model', None)
            if model and 'status' in [f.name for f in model._meta.get_fields()]:
                count = queryset.filter(status=Status.COMPLETED.value).count()
            else:
                count = queryset.count()
        labels.append(app_tags.media_type_readable(media_type))
        data.append(count)
        colors.append(default_color)
    return {
        "labels": labels,
        "datasets": [
            {
                "label": "Vistos",
                "data": data,
                "backgroundColor": colors,
            }
        ],
        "total_completed": sum(data),
    }


def get_status_pie_chart_data(status_distribution):
    """Get status distribution as a pie chart."""
    # Format for Chart.js pie chart
    chart_data = {
        "labels": [],
        "datasets": [
            {
                "data": [],
                "backgroundColor": [],
            },
        ],
    }

    # Adapted: Use the correct structure from get_status_distribution
    for idx, dataset in enumerate(status_distribution["datasets"]):
        status_label = dataset.get("label", "")
        status_count = None
        status_color = None
        # dataset["data"] is a list, usually with one value per label
        if isinstance(dataset.get("data"), list) and len(dataset["data"]) > 0:
            status_count = dataset["data"][0]
        if isinstance(dataset.get("backgroundColor"), list) and len(dataset["backgroundColor"]) > 0:
            status_color = dataset["backgroundColor"][0]
        # Only include statuses with counts > 0
        if status_count and status_count > 0:
            chart_data["labels"].append(status_label)
            chart_data["datasets"][0]["data"].append(status_count)
            chart_data["datasets"][0]["backgroundColor"].append(status_color)

    return chart_data


def get_score_distribution(user_media):
    """Get score distribution for each media type within date range."""
    distribution = {}
    total_scored = 0
    total_score_sum = 0

    top_rated = []
    top_rated_count = 14
    counter = itertools.count()  # Ensures stable sorting for equal scores
    score_range = range(11)

    for media_type, media_list in user_media.items():
        score_counts = dict.fromkeys(score_range, 0)
        # Solo filtrar por score si el modelo tiene ese campo
        model = getattr(media_list, 'model', None)
        if model and 'score' in [f.name for f in model._meta.get_fields()]:
            scored_media = media_list.exclude(score__isnull=True).select_related("item")
        else:
            scored_media = []

        for media in scored_media:
            if len(top_rated) < top_rated_count:
                heapq.heappush(
                    top_rated,
                    (float(media.score), next(counter), media),
                )
            else:
                heapq.heappushpop(
                    top_rated,
                    (float(media.score), next(counter), media),
                )

            binned_score = int(media.score)
            score_counts[binned_score] += 1
            total_scored += 1
            total_score_sum += media.score

        distribution[media_type] = score_counts

    average_score = (
        round(total_score_sum / total_scored, 2) if total_scored > 0 else None
    )

    top_rated_media = [
        media for _, _, media in sorted(top_rated, key=lambda x: (-x[0], x[1]))
    ]

    top_rated_media = _annotate_top_rated_media(top_rated_media)

    return {
        "labels": [str(score) for score in score_range],
        "datasets": [
            {
                "label": app_tags.media_type_readable(media_type),
                "data": [distribution[media_type][score] for score in score_range],
                "background_color": media_type_config.get_stats_color(media_type),
            }
            for media_type in distribution
        ],
        "average_score": average_score,
        "total_scored": total_scored,
    }, top_rated_media


def _annotate_top_rated_media(top_rated_media):
    """Apply prefetch_related and annotate max_progress for top rated media."""
    if not top_rated_media:
        return top_rated_media

    # Group by media type to batch database operations
    media_by_type = {}
    for media in top_rated_media:
        media_type = media.item.media_type
        if media_type not in media_by_type:
            media_by_type[media_type] = []
        media_by_type[media_type].append(media)

    media_manager = MediaManager()

    for media_type, media_list in media_by_type.items():
        model = apps.get_model(app_label="app", model_name=media_type)
        media_ids = [media.id for media in media_list]

        # Fetch fresh instances with proper relationships and annotations
        queryset = model.objects.filter(id__in=media_ids)
        queryset = media_manager._apply_prefetch_related(queryset, media_type)
        media_manager.annotate_max_progress(queryset, media_type)

        prefetched_media_map = {media.id: media for media in queryset}

        # Replace original instances with enhanced ones
        for i, media in enumerate(top_rated_media):
            if media.item.media_type == media_type:
                top_rated_media[i] = prefetched_media_map[media.id]

    return top_rated_media


def get_status_color(status):
    """Get the color for the status of the media."""
    colors = {
        Status.IN_PROGRESS.value: media_type_config.get_stats_color(
            MediaTypes.EPISODE.value,
        ),
        Status.COMPLETED.value: media_type_config.get_stats_color(
            MediaTypes.TV.value,
        ),
        Status.PLANNING.value: media_type_config.get_stats_color(
            MediaTypes.ANIME.value,
        ),
        Status.PAUSED.value: media_type_config.get_stats_color(
            MediaTypes.MOVIE.value,
        ),
        Status.DROPPED.value: media_type_config.get_stats_color(
            MediaTypes.MANGA.value,
        ),
    }
    return colors.get(status, "rgba(201, 203, 207)")


def get_timeline(user_media):
    """Build a timeline of media consumption organized by month-year."""
    from app.models import MediaTypes
    timeline = defaultdict(list)

    # Incluir todos los tipos, incluyendo episodios de TV
    for media_type, queryset in user_media.items():
        if media_type == MediaTypes.COMIC.value:
            # For comics, expand into individual issue entries from history
            for comic in queryset:
                # Get historical records to see progress changes
                history = comic.history.all().order_by('history_date')
                previous_progress = 0
                
                for record in history:
                    if record.progress > previous_progress:
                        # Issues were read
                        issues_read = record.progress - previous_progress
                        for i in range(issues_read):
                            issue_number = previous_progress + i + 1
                            # Create a pseudo-object for the issue
                            issue_entry = type('ComicIssue', (), {})()
                            issue_entry.item = comic.item
                            issue_entry.media_type = 'comic_issue'
                            issue_entry.issue_number = issue_number
                            issue_entry.end_date = record.history_date
                            issue_entry.progressed_at = record.history_date
                            # Estimate reading time per issue
                            if comic.progress > 0:
                                issue_entry.runtime = (comic.reading_time or 0) // comic.progress
                            else:
                                issue_entry.runtime = 0
                            issue_entry.comic_id = comic.id
                            issue_entry.user = comic.user
                            
                            local_end_date = timezone.localdate(record.history_date)
                            year = local_end_date.year
                            month = local_end_date.month
                            month_name = calendar.month_name[month]
                            month_year = f"{month_name} {year}"
                            timeline[month_year].append(issue_entry)
                        
                        previous_progress = record.progress
        elif media_type == MediaTypes.GAME.value:
            # For games, get GameSession records directly
            from app.models import GameSession
            
            for game in queryset:
                # Get GameSession records for this game
                sessions = GameSession.objects.filter(game=game).order_by('session_date')
                
                for session in sessions:
                    # Create entry for this session
                    session_entry = type('GameSessionEntry', (), {})()
                    session_entry.item = game.item
                    session_entry.media_type = 'game_session'
                    session_entry.session_number = sessions.filter(session_date__lte=session.session_date).count()
                    session_entry.end_date = session.session_date
                    session_entry.progressed_at = session.session_date
                    session_entry.runtime = session.minutes
                    session_entry.game_id = game.id
                    session_entry.user = game.user
                    
                    local_end_date = timezone.localdate(session.session_date)
                    year = local_end_date.year
                    month = local_end_date.month
                    month_name = calendar.month_name[month]
                    month_year = f"{month_name} {year}"
                    timeline[month_year].append(session_entry)
        elif media_type == MediaTypes.BOOK.value:
            # For books, expand into individual reading sessions from history
            for book in queryset:
                # Get historical records to see reading_time changes
                history = book.history.all().order_by('history_date')
                previous_reading_time = 0
                session_count = 0
                
                for record in history:
                    if hasattr(record, 'reading_time') and record.reading_time and record.reading_time > previous_reading_time:
                        # A reading session occurred
                        session_count += 1
                        session_time = record.reading_time - previous_reading_time
                        
                        # Create a pseudo-object for the session
                        session_entry = type('BookSession', (), {})()
                        session_entry.item = book.item
                        session_entry.media_type = 'book_session'
                        session_entry.session_number = session_count
                        session_entry.end_date = record.history_date
                        session_entry.progressed_at = record.history_date
                        session_entry.runtime = session_time
                        session_entry.book_id = book.id
                        session_entry.user = book.user
                        
                        local_end_date = timezone.localdate(record.history_date)
                        year = local_end_date.year
                        month = local_end_date.month
                        month_name = calendar.month_name[month]
                        month_year = f"{month_name} {year}"
                        timeline[month_year].append(session_entry)
                        
                        previous_reading_time = record.reading_time
        else:
            # For other media types, use existing logic
            for media in queryset:
                # Usar end_date como referencia principal para timeline
                end_date = getattr(media, 'end_date', None)
                if end_date is None and hasattr(media, 'item'):
                    end_date = getattr(media.item, 'end_date', None)
                if not end_date:
                    continue  # Solo mostrar consumidos (con end_date)
                # --- AÃ±adir runtime al objeto media si no existe ---
                if not hasattr(media, 'runtime') or media.runtime is None:
                    if hasattr(media, 'item') and hasattr(media.item, 'runtime'):
                        media.runtime = media.item.runtime
                    elif hasattr(media, 'reading_time'):
                        # Para comics, usar reading_time como runtime
                        media.runtime = media.reading_time
                    elif hasattr(media, 'play_time'):
                        # Para games, usar play_time como runtime
                        media.runtime = media.play_time
                local_end_date = timezone.localdate(end_date)
                year = local_end_date.year
                month = local_end_date.month
                month_name = calendar.month_name[month]
                month_year = f"{month_name} {year}"
                timeline[month_year].append(media)

    # Convert to sorted dictionary with media sorted by end_date (mÃ¡s reciente primero)
    sorted_items = []
    for month_year, media_list in timeline.items():
        month_name, year_str = month_year.split()
        year = int(year_str)
        month = list(calendar.month_name).index(month_name)
        sorted_items.append((month_year, media_list, year, month))

    # Sort by year and month in reverse chronological order
    sorted_items.sort(key=lambda x: (x[2], x[3]), reverse=True)

    # Ordenar cada lista de medios por end_date descendente
    def end_date_sort_key(media):
        end_date = getattr(media, 'end_date', None)
        if end_date is None and hasattr(media, 'item'):
            end_date = getattr(media.item, 'end_date', None)
        return end_date or timezone.now()

    result = {}
    for month_year, media_list, _, _ in sorted_items:
        result[month_year] = sorted(media_list, key=end_date_sort_key, reverse=True)
    return result


def time_line_sort_key(media):
    """Sort media items in the timeline."""
    if media.end_date is not None:
        return timezone.localdate(media.end_date)
    return timezone.localdate(media.start_date)


def get_activity_data(user, start_date, end_date):
    """Get daily activity counts for the last year."""
    if end_date is None:
        end_date = timezone.localtime()

    start_date_aligned = get_aligned_monday(start_date)

    combined_data = get_filtered_historical_data(start_date_aligned, end_date, user)

    # update start_date values from historical records if not provided
    if start_date is None:
        dates = [item["date"] for item in combined_data]
        start_date = datetime.datetime.combine(
            min(dates) if dates else timezone.localdate(),
            datetime.time.min,
        )
        start_date_aligned = get_aligned_monday(start_date)

    # Aggregate counts by date
    date_counts = {}
    for item in combined_data:
        date = item["date"]
        date_counts[date] = date_counts.get(date, 0) + item["count"]

    date_range = [
        start_date_aligned.date() + datetime.timedelta(days=x)
        for x in range((end_date.date() - start_date_aligned.date()).days + 1)
    ]

    # Calculate activity statistics
    most_active_day, day_percentage = calculate_day_of_week_stats(
        user,
        start_date,
        end_date,
    )
    current_streak, longest_streak = calculate_streaks(
        date_counts,
        end_date.date(),
    )

    # Create complete date range including padding days
    activity_data = [
        {
            "date": current_date.strftime("%Y-%m-%d"),
            "count": date_counts.get(current_date, 0),
            "level": get_level(date_counts.get(current_date, 0)),
        }
        for current_date in date_range
    ]

    # Format data into calendar weeks
    calendar_weeks = [activity_data[i : i + 7] for i in range(0, len(activity_data), 7)]

    # Generate months list with their Monday counts
    months = []
    mondays_per_month = []
    current_month = date_range[0].strftime("%b")
    monday_count = 0

    for current_date in date_range:
        if current_date.weekday() == 0:  # Monday
            month = current_date.strftime("%b")

            if current_month != month:
                if current_month is not None:
                    if monday_count > 1:
                        months.append(current_month)
                        mondays_per_month.append(monday_count)
                    else:
                        months.append("")
                        mondays_per_month.append(monday_count)
                current_month = month
                monday_count = 0

            monday_count += 1
    # For the last month
    if monday_count > 1:
        months.append(current_month)
        mondays_per_month.append(monday_count)

    return {
        "calendar_weeks": calendar_weeks,
        "months": list(zip(months, mondays_per_month, strict=False)),
        "stats": {
            "most_active_day": most_active_day,
            "most_active_day_percentage": day_percentage,
            "current_streak": current_streak,
            "longest_streak": longest_streak,
        },
    }


def get_aligned_monday(datetime_obj):
    """Get the Monday of the week containing the given date."""
    if datetime_obj is None:
        return None

    days_to_subtract = datetime_obj.weekday()  # 0=Monday, 6=Sunday
    return datetime_obj - datetime.timedelta(days=days_to_subtract)


def get_level(count):
    """Calculate intensity level (0-4) based on count."""
    thresholds = [0, 3, 6, 9]
    for i, threshold in enumerate(thresholds):
        if count <= threshold:
            return i
    return 4


def get_filtered_historical_data(start_date, end_date, user):
    """Return [{"date": datetime.date, "count": int}]."""
    historical_models = BasicMedia.objects.get_historical_models()
    local_tz = timezone.get_current_timezone()

    day_buckets = defaultdict(int)


    for model_name in historical_models:
        # Map 'historicalyoutube' to 'historicaltv' to avoid LookupError
        mapped_model_name = "historicaltv" if model_name == "historicalyoutube" else model_name
        try:
            model = apps.get_model("app", mapped_model_name)
        except LookupError:
            logger.warning(f"Model {mapped_model_name} not found in app; skipping.")
            continue

        qs = model.objects.filter(history_user_id=user)

        if start_date:
            qs = qs.filter(history_date__gte=start_date)
        if end_date:
            qs = qs.filter(history_date__lte=end_date)

        # We only need the timestamp, stream results to keep memory usage flat
        for ts in qs.values_list("history_date", flat=True).iterator(chunk_size=2_000):
            aware_ts = timezone.localtime(ts, local_tz)
            day_buckets[aware_ts.date()] += 1

    combined_data = [
        {"date": day, "count": count} for day, count in day_buckets.items()
    ]

    logger.info("%s - built historical data (%s rows)", user, len(combined_data))
    return combined_data


def calculate_day_of_week_stats(user, start_date, end_date):
    """Calculate the most active day of the week based on total watch time.

    Returns the day name and its percentage of total watch time.
    """
    from app.models import Episode, Comic, Movie, Game
    
    # Initialize counters for total minutes per day of the week
    day_minutes = defaultdict(int)
    total_minutes = 0

    # Get episodes watched in date range
    episode_filters = {
        'end_date__isnull': False,
        'item__runtime__isnull': False,
        'related_season__user': user,
    }
    if start_date:
        episode_filters['end_date__gte'] = start_date
    if end_date:
        episode_filters['end_date__lte'] = end_date
    
    episodes = Episode.objects.filter(**episode_filters).select_related('item')
    for ep in episodes:
        day_name = ep.end_date.strftime("%A")
        runtime = ep.item.runtime or 0
        day_minutes[day_name] += runtime
        total_minutes += runtime

    # Get movies watched in date range
    movie_filters = {
        'end_date__isnull': False,
        'item__runtime__isnull': False,
        'user': user,
    }
    if start_date:
        movie_filters['end_date__gte'] = start_date
    if end_date:
        movie_filters['end_date__lte'] = end_date
    
    movies = Movie.objects.filter(**movie_filters).select_related('item')
    for movie in movies:
        day_name = movie.end_date.strftime("%A")
        runtime = movie.item.runtime or 0
        day_minutes[day_name] += runtime
        total_minutes += runtime

    # Get comics read in date range
    comic_filters = {
        'progress__gt': 0,
        'reading_time__gt': 0,
        'user': user,
    }
    if start_date:
        comic_filters['progressed_at__gte'] = start_date
    if end_date:
        comic_filters['progressed_at__lte'] = end_date
    
    comics = Comic.objects.filter(**comic_filters)
    for comic in comics:
        day_name = comic.progressed_at.strftime("%A")
        reading_time = comic.reading_time or 0
        day_minutes[day_name] += reading_time
        total_minutes += reading_time

    # Get games played in date range
    game_filters = {
        'progress__gt': 0,
        'play_time__gt': 0,
        'user': user,
    }
    if start_date:
        game_filters['progressed_at__gte'] = start_date
    if end_date:
        game_filters['progressed_at__lte'] = end_date
    
    games = Game.objects.filter(**game_filters)
    for game in games:
        day_name = game.progressed_at.strftime("%A")
        play_time = game.play_time or 0
        day_minutes[day_name] += play_time
        total_minutes += play_time

    if not total_minutes:
        return None, 0

    # Find the day with most watch time
    most_active_day = max(day_minutes.items(), key=lambda x: x[1])
    percentage = (most_active_day[1] / total_minutes) * 100

    return most_active_day[0], round(percentage)


def calculate_streaks(date_counts, end_date):
    """Calculate current and longest activity streaks."""
    # Get active dates and sort them in descending order (newest first)
    active_dates = sorted(
        [date for date, count in date_counts.items() if count > 0],
        reverse=True,
    )

    if not active_dates:
        return 0, 0

    longest_streak = 1
    streak_count = 1

    # Check if the most recent active date is today/end_date
    is_current = active_dates[0] == end_date

    current_streak = 1 if is_current else 0

    for i in range(1, len(active_dates)):
        # Check if this date is consecutive with the previous one
        if (active_dates[i - 1] - active_dates[i]).days == 1:
            streak_count += 1

            if is_current:
                current_streak += 1
        else:
            longest_streak = max(longest_streak, streak_count)
            streak_count = 1

            if is_current:
                is_current = False

    # Check final streak for longest calculation
    # needed if the last date is today/end_date
    longest_streak = max(longest_streak, streak_count)

    return current_streak, longest_streak
