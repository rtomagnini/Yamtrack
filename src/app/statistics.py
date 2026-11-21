def get_watch_time_distribution_pie_chart_data(user_media, user=None, start_date=None, end_date=None):
    """Aggregate total watch time (in minutes) by media type for pie chart."""
    from . import media_type_config
    from .templatetags import app_tags
    from app.models import MediaTypes, ComicSession, GameSession, BookSession
    chart_data = {
        "labels": [],
        "datasets": [
            {
                "data": [],
                "backgroundColor": [],
            },
        ],
    }
    media_types = [MediaTypes.TV.value, MediaTypes.YOUTUBE.value, MediaTypes.MOVIE.value, MediaTypes.COMIC.value, MediaTypes.MANGA.value, MediaTypes.GAME.value, MediaTypes.BOOK.value]
    total_minutes = 0
    media_type_minutes = {}
    for media_type in media_types:
        queryset = user_media.get(media_type)
        if queryset is not None:
            if media_type == MediaTypes.MOVIE.value:
                # For movies, sum item__runtime for all watched movies
                minutes = queryset.select_related("item").aggregate(total=models.Sum(runtime_expression(user)))["total"] or 0
            elif media_type == MediaTypes.COMIC.value:
                # For comics, sum minutes from ComicSession filtered by date
                if user:
                    session_filters = {'comic__user': user}
                    if start_date is not None and end_date is not None:
                        session_filters['session_date__range'] = (start_date, end_date)
                    minutes = ComicSession.objects.filter(**session_filters).aggregate(total=models.Sum("minutes"))["total"] or 0
                else:
                    minutes = queryset.aggregate(total=models.Sum("reading_time"))["total"] or 0
            elif media_type == MediaTypes.MANGA.value:
                minutes = (
                    manga_sessions(user, start_date, end_date).aggregate(total=models.Sum("minutes"))["total"]
                    if user else queryset.aggregate(total=models.Sum("reading_time"))["total"]
                ) or 0
            elif media_type == MediaTypes.GAME.value:
                # For games, sum minutes from GameSession filtered by date
                if user:
                    session_filters = {'game__user': user}
                    if start_date is not None and end_date is not None:
                        session_filters['session_date__range'] = (start_date, end_date)
                    minutes = GameSession.objects.filter(**session_filters).aggregate(total=models.Sum("minutes"))["total"] or 0
                else:
                    minutes = queryset.aggregate(total=models.Sum("play_time"))["total"] or 0
            elif media_type == MediaTypes.BOOK.value:
                # For books, sum minutes from BookSession filtered by date
                if user:
                    session_filters = {'book__user': user}
                    if start_date is not None and end_date is not None:
                        session_filters['session_date__range'] = (start_date, end_date)
                    minutes = BookSession.objects.filter(**session_filters).aggregate(total=models.Sum("minutes"))["total"] or 0
                else:
                    minutes = queryset.aggregate(total=models.Sum("reading_time"))["total"] or 0
            else:
                # For TV/YouTube, sum item__runtime for all watched episodes
                minutes = queryset.select_related("item").aggregate(total=models.Sum(runtime_expression(user)))["total"] or 0
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
    Devuelve el tiempo de visionado (runtime de episodios vistos) agrupado por día, semana o mes.
    - Hasta 30 días: por día
    - Entre 31 y 180 días: por semana
    - Más de 180 días: por mes
    """
    from app.models import Episode, Movie, ComicSession, GameSession, BookSession
    from django.db.models import Sum, F
    from django.utils import timezone
    import datetime

    # Extend short selections to a 30-day daily chart.
    chart_start_date = start_date
    selected_days = None
    if start_date and end_date:
        selected_days = (end_date.date() - start_date.date()).days + 1
        if selected_days <= 30:
            chart_start_date = min(
                start_date,
                end_date - datetime.timedelta(days=29),
            )

    # Determinar agrupamiento
    if not start_date or not end_date:
        # Si no hay fechas, usar por mes
        group = 'month'
    else:
        days = (end_date - start_date).days
        if selected_days is not None and selected_days <= 30:
            group = 'day'
        elif days <= 30:
            group = 'day'
        elif days <= 180:
            group = 'week'
        else:
            group = 'month'

    # Query de episodios vistos por el usuario en el rango
    episode_filters = {
        'end_date__isnull': False,
        'item__isnull': False,
        'item__media_type': 'episode',
        'related_season__user': user,
    }
    if chart_start_date:
        episode_filters['end_date__gte'] = chart_start_date
    if end_date:
        episode_filters['end_date__lte'] = end_date
    episodes = Episode.objects.filter(**episode_filters).annotate(metadata_runtime=runtime_expression(user)).filter(metadata_runtime__isnull=False)

    # Query de películas vistas por el usuario en el rango
    movie_filters = {
        'end_date__isnull': False,
        'user': user,
    }
    if chart_start_date:
        movie_filters['end_date__gte'] = chart_start_date
    if end_date:
        movie_filters['end_date__lte'] = end_date
    movies = Movie.objects.filter(**movie_filters).annotate(metadata_runtime=runtime_expression(user)).filter(metadata_runtime__isnull=False).select_related('item')

    # Query de ComicSession por usuario en el rango
    comic_session_filters = {'comic__user': user}
    if chart_start_date:
        comic_session_filters['session_date__gte'] = chart_start_date
    if end_date:
        comic_session_filters['session_date__lte'] = end_date
    comic_sessions = ComicSession.objects.filter(**comic_session_filters)

    # Query de GameSession por usuario en el rango
    game_session_filters = {'game__user': user}
    if chart_start_date:
        game_session_filters['session_date__gte'] = chart_start_date
    if end_date:
        game_session_filters['session_date__lte'] = end_date
    game_sessions = GameSession.objects.filter(**game_session_filters)

    # Query de BookSession por usuario en el rango
    book_session_filters = {'book__user': user}
    if chart_start_date:
        book_session_filters['session_date__gte'] = chart_start_date
    if end_date:
        book_session_filters['session_date__lte'] = end_date
    book_sessions = BookSession.objects.filter(**book_session_filters)

    # Agrupar y sumar runtime
    data = {}
    from django.utils import timezone

    for ep in episodes.select_related('item'):
        dt = timezone.localtime(ep.end_date)
        if group == 'day':
            key = dt.date()
        elif group == 'week':
            key = dt.date() - datetime.timedelta(days=dt.weekday())  # lunes de la semana
        else:
            key = dt.date().replace(day=1)  # primer día del mes
        data.setdefault(key, 0)
        data[key] += ep.metadata_runtime or 0

    # Agrupar y sumar runtime de películas
    for movie in movies:
        dt = timezone.localtime(movie.end_date)
        if group == 'day':
            key = dt.date()
        elif group == 'week':
            key = dt.date() - datetime.timedelta(days=dt.weekday())  # lunes de la semana
        else:
            key = dt.date().replace(day=1)  # primer día del mes
        data.setdefault(key, 0)
        data[key] += movie.metadata_runtime or 0

    for session in itertools.chain(comic_sessions, manga_sessions(user, chart_start_date, end_date)):
        dt = timezone.localtime(session.session_date)
        if group == 'day':
            key = dt.date()
        elif group == 'week':
            key = dt.date() - datetime.timedelta(days=dt.weekday())  # lunes de la semana
        else:
            key = dt.date().replace(day=1)  # primer día del mes
        data.setdefault(key, 0)
        data[key] += session.minutes or 0

    # Agrupar y sumar minutes de GameSession
    for session in game_sessions:
        dt = timezone.localtime(session.session_date)
        if group == 'day':
            key = dt.date()
        elif group == 'week':
            key = dt.date() - datetime.timedelta(days=dt.weekday())  # lunes de la semana
        else:
            key = dt.date().replace(day=1)  # primer día del mes
        data.setdefault(key, 0)
        data[key] += session.minutes or 0

    # Agrupar y sumar minutes de BookSession
    for session in book_sessions:
        dt = timezone.localtime(session.session_date)
        if group == 'day':
            key = dt.date()
        elif group == 'week':
            key = dt.date() - datetime.timedelta(days=dt.weekday())  # lunes de la semana
        else:
            key = dt.date().replace(day=1)  # primer día del mes
        data.setdefault(key, 0)
        data[key] += session.minutes or 0

    # Ordenar por fecha
    sorted_keys = sorted(data.keys())
    labels = []
    values = []
    if selected_days is not None and selected_days <= 30:
        total_chart_days = (end_date.date() - chart_start_date.date()).days + 1
        for offset in range(total_chart_days):
            day = chart_start_date.date() + datetime.timedelta(days=offset)
            labels.append(day.strftime('%Y-%m-%d'))
            values.append(data.get(day, 0))
    else:
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
from types import SimpleNamespace

from dateutil.relativedelta import relativedelta
from django.apps import apps
from django.db import models
from django.db.models import (
    Prefetch,
    Q,
)
from django.db.models.functions import TruncDate
from django.utils import timezone

from app import media_type_config
from app.metadata import display_metadata, runtime_expression
from app.models import (
    TV,
    BookSession,
    ComicSession,
    Episode,
    GameSession,
    MangaSession,
    MediaManager,
    MediaTypes,
    Movie,
    Season,
    Status,
)
from app.templatetags import app_tags

logger = logging.getLogger(__name__)


def manga_sessions(user, start_date=None, end_date=None):
    """Get a user's chapter reads within any provided date bounds."""
    sessions = MangaSession.objects.filter(manga__user=user)
    if start_date is not None:
        sessions = sessions.filter(session_date__gte=start_date)
    if end_date is not None:
        sessions = sessions.filter(session_date__lte=end_date)
    return sessions


def get_consumption_querysets(user):
    """Return the querysets and date fields counted as consumed items."""
    return (
        (Episode.objects.filter(related_season__user=user, end_date__isnull=False), "end_date"),
        (Movie.objects.filter(user=user, end_date__isnull=False), "end_date"),
        (ComicSession.objects.filter(comic__user=user), "session_date"),
        (GameSession.objects.filter(game__user=user), "session_date"),
        (BookSession.objects.filter(book__user=user), "session_date"),
        (manga_sessions(user), "session_date"),
    )


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
        MediaTypes.MANGA.value,
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
        elif media_type == MediaTypes.MANGA.value:
            queryset = model.objects.filter(
                pk__in=manga_sessions(user, start_date, end_date).values("manga_id"),
            )
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
    
    # Import session models for counting
    from app.models import ComicSession, GameSession, BookSession
    
    for media_type, queryset in other_media.items():
        user_media[media_type] = queryset
        if media_type == MediaTypes.MANGA.value:
            media_count[media_type] = manga_sessions(user, start_date, end_date).count()
        elif media_type == MediaTypes.COMIC.value:
            # For comics, count sessions in date range
            if start_date is None and end_date is None:
                media_count[media_type] = ComicSession.objects.filter(comic__user=user).count()
            else:
                media_count[media_type] = ComicSession.objects.filter(comic__user=user, session_date__range=(start_date, end_date)).count()
        elif media_type == MediaTypes.GAME.value:
            # For games, count sessions in date range
            if start_date is None and end_date is None:
                media_count[media_type] = GameSession.objects.filter(game__user=user).count()
            else:
                media_count[media_type] = GameSession.objects.filter(game__user=user, session_date__range=(start_date, end_date)).count()
        elif media_type == MediaTypes.BOOK.value:
            # For books, count sessions in date range
            if start_date is None and end_date is None:
                media_count[media_type] = BookSession.objects.filter(book__user=user).count()
            else:
                media_count[media_type] = BookSession.objects.filter(book__user=user, session_date__range=(start_date, end_date)).count()
        else:
            media_count[media_type] = queryset.count()
    media_count["total"] = sum(media_count[mt] for mt in media_count if mt != "total")

    logger.info(
        "%s - Retrieved media %s",
        user,
        "for all time" if start_date is None else f"from {start_date} to {end_date}",
    )
    consumption = get_consumption_querysets(user)
    if start_date is not None or end_date is not None:
        querysets = tuple(
            queryset.filter(**{f"{field}__range": (start_date, end_date)})
            for queryset, field in consumption
        )
    else:
        querysets = tuple(queryset for queryset, _ in consumption)
    watched_episodes, watched_movies_qs, *session_querysets = querysets
    items_watched = sum(queryset.count() for queryset in querysets)

    episode_minutes = watched_episodes.select_related("item").aggregate(total=models.Sum(runtime_expression(user)))["total"] or 0
    movie_minutes = watched_movies_qs.select_related("item").aggregate(total=models.Sum(runtime_expression(user)))["total"] or 0
    session_minutes = sum(qs.aggregate(total=models.Sum("minutes"))["total"] or 0 for qs in session_querysets)
    total_watch_minutes = episode_minutes + movie_minutes + session_minutes

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

    allowed_types = [
        MediaTypes.TV.value,
        MediaTypes.YOUTUBE.value,
        MediaTypes.MOVIE.value,
        MediaTypes.ANIME.value,
        MediaTypes.COMIC.value,
        MediaTypes.MANGA.value,
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


def get_status_distribution(user_media, user=None, start_date=None, end_date=None):
    """Get status distribution for each media type within date range."""
    from app.models import ComicSession, GameSession, BookSession
    # Nuevo: mostrar total de vistos por tipo
    media_types = [
        MediaTypes.TV.value,
        MediaTypes.YOUTUBE.value,
        MediaTypes.MOVIE.value,
        MediaTypes.ANIME.value,
        MediaTypes.COMIC.value,
        MediaTypes.MANGA.value,
        MediaTypes.BOOK.value,
        MediaTypes.GAME.value,
    ]
    data = []
    labels = []
    colors = []
    default_color = "#1976d2"  # Azul sólido (Material Design)
    for media_type in media_types:
        queryset = user_media.get(media_type)
        if queryset is None:
            count = 0
        elif media_type in [MediaTypes.TV.value, MediaTypes.YOUTUBE.value]:
            count = queryset.count()
        elif media_type == MediaTypes.MANGA.value:
            count = manga_sessions(user, start_date, end_date).count() if user else queryset.aggregate(
                total=models.Count("sessions"),
            )["total"]
        elif media_type == MediaTypes.COMIC.value:
            # For comics, count sessions in date range
            if user:
                session_filters = {'comic__user': user}
                if start_date is not None and end_date is not None:
                    session_filters['session_date__range'] = (start_date, end_date)
                count = ComicSession.objects.filter(**session_filters).count()
            else:
                count = queryset.aggregate(total=models.Sum("progress"))["total"] or 0
        elif media_type == MediaTypes.GAME.value:
            # For games, count sessions in date range
            if user:
                session_filters = {'game__user': user}
                if start_date is not None and end_date is not None:
                    session_filters['session_date__range'] = (start_date, end_date)
                count = GameSession.objects.filter(**session_filters).count()
            else:
                count = queryset.count()
        elif media_type == MediaTypes.BOOK.value:
            # For books, count sessions in date range
            if user:
                session_filters = {'book__user': user}
                if start_date is not None and end_date is not None:
                    session_filters['session_date__range'] = (start_date, end_date)
                count = BookSession.objects.filter(**session_filters).count()
            else:
                count = queryset.count()
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


def get_timeline(user_media, start_date=None, end_date=None, user=None):
    """Build a timeline of media consumption organized by month-year."""
    from app.models import MediaTypes, ComicSession, GameSession, BookSession
    timeline = defaultdict(list)
    
    # Get user from any queryset
    for media_type, queryset in user_media.items():
        if user is not None:
            break
        if queryset.exists():
            first_item = queryset.first()
            if hasattr(first_item, 'user'):
                user = first_item.user
                break
            if hasattr(first_item, 'related_season'):
                user = first_item.related_season.user
                break

    # Incluir todos los tipos, incluyendo episodios de TV
    for media_type, queryset in user_media.items():
        if media_type == MediaTypes.MANGA.value:
            if user:
                for session in manga_sessions(user, start_date, end_date).select_related("manga__item"):
                    day = timezone.localdate(session.session_date)
                    month = f"{calendar.month_name[day.month]} {day.year}"
                    timeline[month].append(SimpleNamespace(
                        item=display_metadata(session.manga.item, user),
                        media_type="manga_chapter",
                        chapter_number=session.chapter_number,
                        end_date=session.session_date,
                        runtime=session.minutes,
                    ))
        elif media_type == MediaTypes.COMIC.value:
            # For comics, get ComicSession records directly filtered by date
            if user:
                session_filters = {'comic__user': user}
                if start_date is not None and end_date is not None:
                    session_filters['session_date__range'] = (start_date, end_date)
                sessions = ComicSession.objects.filter(**session_filters).select_related('comic', 'comic__item').order_by('session_date')
                
                for idx, session in enumerate(sessions, 1):
                    # Create entry for this session
                    issue_entry = type('ComicIssue', (), {})()
                    issue_entry.item = session.comic.item
                    issue_entry.media_type = 'comic_issue'
                    issue_entry.issue_number = session.issues_read
                    issue_entry.end_date = session.session_date
                    issue_entry.progressed_at = session.session_date
                    issue_entry.runtime = session.minutes
                    issue_entry.comic_id = session.comic.id
                    issue_entry.user = session.comic.user
                    
                    local_end_date = timezone.localdate(session.session_date)
                    year = local_end_date.year
                    month = local_end_date.month
                    month_name = calendar.month_name[month]
                    month_year = f"{month_name} {year}"
                    timeline[month_year].append(issue_entry)
        elif media_type == MediaTypes.GAME.value:
            # For games, get GameSession records directly filtered by date
            if user:
                session_filters = {'game__user': user}
                if start_date is not None and end_date is not None:
                    session_filters['session_date__range'] = (start_date, end_date)
                sessions = GameSession.objects.filter(**session_filters).select_related('game', 'game__item').order_by('session_date')
                
                for idx, session in enumerate(sessions, 1):
                    # Create entry for this session
                    session_entry = type('GameSessionEntry', (), {})()
                    session_entry.item = session.game.item
                    session_entry.media_type = 'game_session'
                    session_entry.session_number = idx
                    session_entry.end_date = session.session_date
                    session_entry.progressed_at = session.session_date
                    session_entry.runtime = session.minutes
                    session_entry.game_id = session.game.id
                    session_entry.user = session.game.user
                    
                    local_end_date = timezone.localdate(session.session_date)
                    year = local_end_date.year
                    month = local_end_date.month
                    month_name = calendar.month_name[month]
                    month_year = f"{month_name} {year}"
                    timeline[month_year].append(session_entry)
        elif media_type == MediaTypes.BOOK.value:
            # For books, get BookSession records directly filtered by date
            if user:
                session_filters = {'book__user': user}
                if start_date is not None and end_date is not None:
                    session_filters['session_date__range'] = (start_date, end_date)
                sessions = BookSession.objects.filter(**session_filters).select_related('book', 'book__item').order_by('session_date')
                
                for idx, session in enumerate(sessions, 1):
                    # Create entry for this session
                    session_entry = type('BookSessionEntry', (), {})()
                    session_entry.item = session.book.item
                    session_entry.media_type = 'book_session'
                    session_entry.session_number = idx
                    session_entry.end_date = session.session_date
                    session_entry.progressed_at = session.session_date
                    session_entry.runtime = session.minutes
                    session_entry.book_id = session.book.id
                    session_entry.user = session.book.user
                    
                    local_end_date = timezone.localdate(session.session_date)
                    year = local_end_date.year
                    month = local_end_date.month
                    month_name = calendar.month_name[month]
                    month_year = f"{month_name} {year}"
                    timeline[month_year].append(session_entry)
        else:
            # For other media types, use existing logic
            for media in queryset:
                media.item = display_metadata(media.item, user)
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


def get_daily_consumption(user):
    """Count watched episodes and movies and reading and play sessions by local date."""
    counts = defaultdict(int)
    for queryset, field in get_consumption_querysets(user):
        days = queryset.order_by().values(day=TruncDate(field)).annotate(count=models.Count("pk"))
        for day in days:
            counts[day["day"]] += day["count"]
    return counts


def get_activity_data(user, start_date, end_date):
    """Build a weekly calendar and streaks from daily consumption."""
    today = timezone.localdate()
    end_date = end_date or timezone.localtime()
    end_day = end_date.date()
    date_counts = get_daily_consumption(user)

    if start_date is None:
        first_day = min((day for day in date_counts if day <= min(today, end_day)), default=end_day)
    else:
        first_day = min(start_date.date(), end_day - datetime.timedelta(days=89))

    monday = first_day - datetime.timedelta(days=first_day.weekday())
    sunday = end_day + datetime.timedelta(days=6 - end_day.weekday())
    days = [monday + datetime.timedelta(days=i) for i in range((sunday - monday).days + 1)]

    positive_counts = sorted({date_counts.get(day, 0) for day in days if day <= today} - {0})
    # Spread distinct totals across the color scale so equal counts always match.
    levels = {
        count: 1 + index * 3 // max(len(positive_counts) - 1, 1)
        for index, count in enumerate(positive_counts)
    }

    calendar_days = []
    for day in days:
        count = date_counts.get(day, 0)
        calendar_days.append({
            "date": day.isoformat(),
            "count": count,
            "level": levels.get(count, 0),
            "future": day > today,
        })
    calendar_weeks = [calendar_days[i : i + 7] for i in range(0, len(calendar_days), 7)]
    months = [
        (month, sum(1 for _ in weeks))
        for month, weeks in itertools.groupby(days[::7], key=lambda day: day.strftime("%b"))
    ]

    active_day_start = (
        min(start_date, end_date - datetime.timedelta(days=27)) if start_date else None
    )
    most_active_day, day_percentage = calculate_day_of_week_stats(user, active_day_start, end_date)
    current_streak, longest_streak = calculate_streaks(
        {day: count for day, count in date_counts.items() if day <= today},
        today,
    )

    return {
        "calendar_weeks": calendar_weeks,
        "months": months,
        "max_items": positive_counts[-1] if positive_counts else 0,
        "legend_levels": sorted(set(levels.values())),
        "stats": {
            "most_active_day": most_active_day,
            "most_active_day_percentage": day_percentage,
            "current_streak": current_streak,
            "longest_streak": longest_streak,
        },
    }


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
        'related_season__user': user,
    }
    if start_date:
        episode_filters['end_date__gte'] = start_date
    if end_date:
        episode_filters['end_date__lte'] = end_date
    
    episodes = Episode.objects.filter(**episode_filters).annotate(metadata_runtime=runtime_expression(user)).filter(metadata_runtime__isnull=False).select_related('item')
    for ep in episodes:
        day_name = ep.end_date.strftime("%A")
        runtime = ep.metadata_runtime or 0
        day_minutes[day_name] += runtime
        total_minutes += runtime

    # Get movies watched in date range
    movie_filters = {
        'end_date__isnull': False,
        'user': user,
    }
    if start_date:
        movie_filters['end_date__gte'] = start_date
    if end_date:
        movie_filters['end_date__lte'] = end_date
    
    movies = Movie.objects.filter(**movie_filters).annotate(metadata_runtime=runtime_expression(user)).filter(metadata_runtime__isnull=False).select_related('item')
    for movie in movies:
        day_name = movie.end_date.strftime("%A")
        runtime = movie.metadata_runtime or 0
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

    for session in manga_sessions(user, start_date, end_date):
        day_name = timezone.localtime(session.session_date).strftime("%A")
        day_minutes[day_name] += session.minutes
        total_minutes += session.minutes

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
