import logging

from datetime import datetime
from hashlib import blake2s

from django.apps import apps
from django.conf import settings
from django.core.validators import (
    DecimalValidator,
    MaxValueValidator,
    MinValueValidator,
)
from django.db import models
from django.db.models import (
    CheckConstraint,
    Count,
    F,
    IntegerField,
    Max,
    OuterRef,
    Prefetch,
    Q,
    UniqueConstraint,
    Window,
)
from django.db.models.functions import Cast, RowNumber
from django.utils import timezone
from model_utils import FieldTracker
from model_utils.fields import MonitorField
from simple_history.models import HistoricalRecords
from simple_history.utils import bulk_create_with_history, bulk_update_with_history

import app
import events
import users
from app import providers
from app.mixins import CalendarTriggerMixin

logger = logging.getLogger(__name__)


class Sources(models.TextChoices):
    """Choices for the source of the item."""

    TMDB = "tmdb", "The Movie Database"
    MAL = "mal", "MyAnimeList"
    MANGAUPDATES = "mangaupdates", "MangaUpdates"
    IGDB = "igdb", "Internet Game Database"
    OPENLIBRARY = "openlibrary", "Open Library"
    HARDCOVER = "hardcover", "Hardcover"
    COMICVINE = "comicvine", "Comic Vine"
    YOUTUBE = "youtube", "YouTube"
    MANUAL = "manual", "Manual"


class MediaTypes(models.TextChoices):
    """Choices for the media type of the item."""

    TV = "tv", "TV Show"
    SEASON = "season", "TV Season"
    EPISODE = "episode", "Episode"
    MOVIE = "movie", "Movie"
    ANIME = "anime", "Anime"
    MANGA = "manga", "Manga"
    GAME = "game", "Game"
    BOOK = "book", "Book"
    COMIC = "comic", "Comic"
    YOUTUBE = "youtube", "YouTube"
    YOUTUBE_VIDEO = "youtube_video", "YouTube Video"


class ExternalIdMapping(models.Model):
    """Mapping from external fake IDs (like Plex fake TMDB IDs) to valid TMDB IDs."""
    
    tmdb_id_plex = models.CharField(max_length=50, help_text="Fake TMDB ID from external source")
    external_source = models.CharField(max_length=20, default="plex", help_text="Source of fake ID")
    real_tmdb_id = models.CharField(max_length=20, help_text="Valid TMDB ID that actually exists")
    media_type = models.CharField(
        max_length=15,
        choices=MediaTypes.choices,
        default=MediaTypes.TV.value,
        help_text="Type of media being mapped"
    )
    title = models.CharField(max_length=255, help_text="Series/movie title for reference")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        """Meta options for the ExternalIdMapping model."""
        unique_together = ['tmdb_id_plex', 'external_source', 'media_type']
        verbose_name = "External ID Mapping"
        verbose_name_plural = "External ID Mappings"
        
    def __str__(self):
        return f"{self.external_source} {self.tmdb_id_plex} â†’ TMDB {self.real_tmdb_id} ({self.title})"


class Item(CalendarTriggerMixin, models.Model):
    """Model to store basic information about media items."""

    media_id = models.CharField(max_length=20)
    source = models.CharField(
        max_length=20,
        choices=Sources.choices,
    )
    media_type = models.CharField(
        max_length=15,
        choices=MediaTypes.choices,
        default=MediaTypes.MOVIE.value,
    )
    title = models.CharField(max_length=255)
    image = models.URLField()  # if add default, custom media entry will show the value
    season_number = models.PositiveIntegerField(null=True, blank=True, validators=[MaxValueValidator(9999)])
    episode_number = models.PositiveIntegerField(null=True, blank=True)
    air_date = models.DateField(null=True, blank=True)
    runtime = models.PositiveIntegerField(null=True, blank=True)  # Duration in minutes
    # Optional field to store YouTube video id for Episode items coming from YouTube
    youtube_video_id = models.CharField(max_length=50, null=True, blank=True)
    # Optional field to store TMDB ID for MANUAL TV Shows (for Plex integration)
    tmdb_id = models.CharField(
        max_length=20,
        null=True,
        blank=True,
        help_text="TMDB ID for manual TV shows (used for Plex webhook integration)"
    )

    @staticmethod
    def episode_metadata_fields(episode):
        """Return the catalog fields that can be copied onto an episode item."""
        still_path = episode.get("still_path")
        image = (
            f"https://image.tmdb.org/t/p/original{still_path}"
            if still_path else episode.get("image", settings.IMG_NONE)
        )
        return {
            "air_date": episode.get("air_date") or None,
            "runtime": episode.get("runtime"),
            "image": image,
        }

    class Meta:
        """Meta options for the model."""

        constraints = [
            # Ensures items without season/episode numbers are unique
            UniqueConstraint(
                fields=["media_id", "source", "media_type"],
                condition=Q(season_number__isnull=True, episode_number__isnull=True),
                name="unique_item_without_season_episode",
            ),
            # Ensures seasons are unique within a show
            UniqueConstraint(
                fields=["media_id", "source", "media_type", "season_number"],
                condition=Q(season_number__isnull=False, episode_number__isnull=True),
                name="unique_item_with_season",
            ),
            # Ensures episodes are unique within a season
            UniqueConstraint(
                fields=[
                    "media_id",
                    "source",
                    "media_type",
                    "season_number",
                    "episode_number",
                ],
                condition=Q(season_number__isnull=False, episode_number__isnull=False),
                name="unique_item_with_season_episode",
            ),
            # Enforces that season items must have a season number but no episode number
            CheckConstraint(
                condition=Q(
                    media_type=MediaTypes.SEASON.value,
                    season_number__isnull=False,
                    episode_number__isnull=True,
                )
                | ~Q(media_type=MediaTypes.SEASON.value),
                name="season_number_required_for_season",
            ),
            # Enforces that episode items must have both season and episode numbers
            CheckConstraint(
                condition=Q(
                    media_type=MediaTypes.EPISODE.value,
                    season_number__isnull=False,
                    episode_number__isnull=False,
                )
                | ~Q(media_type=MediaTypes.EPISODE.value),
                name="season_and_episode_required_for_episode",
            ),
            # Prevents season/episode numbers from being set on non-TV media types
            CheckConstraint(
                condition=Q(
                    ~Q(
                        media_type__in=[
                            MediaTypes.SEASON.value,
                            MediaTypes.EPISODE.value,
                        ],
                    ),
                    season_number__isnull=True,
                    episode_number__isnull=True,
                )
                | Q(media_type__in=[MediaTypes.SEASON.value, MediaTypes.EPISODE.value]),
                name="no_season_episode_for_other_types",
            ),
            # Validate source choices
            CheckConstraint(
                condition=Q(source__in=Sources.values),
                name="%(app_label)s_%(class)s_source_valid",
            ),
            # Validate media_type choices
            CheckConstraint(
                condition=Q(media_type__in=MediaTypes.values),
                name="%(app_label)s_%(class)s_media_type_valid",
            ),
            # Unique constraint to prevent duplicate YouTube video items when youtube_video_id is present
            UniqueConstraint(
                fields=["youtube_video_id"],
                condition=Q(source=Sources.YOUTUBE.value, media_type=MediaTypes.EPISODE.value, youtube_video_id__isnull=False),
                name="unique_youtube_video_id_for_youtube_episodes",
            ),
        ]
        ordering = ["media_id"]

    def __str__(self):
        """Return the name of the item."""
        name = self.title
        if self.season_number is not None:
            name += f" S{self.season_number}"
            if self.episode_number is not None:
                name += f"E{self.episode_number}"
        return name

    @classmethod
    def generate_manual_id(cls, media_type):
        """Generate a new ID for manual items."""
        return cls.generate_next_id(Sources.MANUAL.value, media_type)

    @classmethod
    def generate_next_id(cls, source, media_type):
        """Generate a new ID for items of a specific source and media_type."""
        latest_item = (
            cls.objects.filter(source=source, media_type=media_type)
            .annotate(
                media_id_int=Cast("media_id", IntegerField()),
            )
            .order_by("-media_id_int")
            .first()
        )

        if latest_item is None:
            return "1"

        return str(int(latest_item.media_id) + 1)

    def fetch_releases(self, delay):
        """Fetch releases for the item."""
        if self._disable_calendar_triggers:
            return

        # Skip release fetching for YouTube items as they don't exist in TMDB
        if self.source == Sources.YOUTUBE.value:
            return

        if self.media_type == MediaTypes.SEASON.value:
            # Get or create the TV item for this season
            try:
                tv_item = Item.objects.get(
                    media_id=self.media_id,
                    source=self.source,
                    media_type=MediaTypes.TV.value,
                )
            except Item.DoesNotExist:
                # Get metadata for the TV show
                tv_metadata = providers.services.get_media_metadata(
                    MediaTypes.TV.value,
                    self.media_id,
                    self.source,
                )
                tv_item = Item.objects.create(
                    media_id=self.media_id,
                    source=self.source,
                    media_type=MediaTypes.TV.value,
                    title=tv_metadata["title"],
                    image=tv_metadata["image"],
                )
                logger.info("Created TV item %s for season %s", tv_item, self)

            # Process the TV item instead of the season
            items_to_process = [tv_item]
        else:
            items_to_process = [self]

        if delay:
            events.tasks.reload_calendar.delay(items_to_process=items_to_process)
        else:
            events.tasks.reload_calendar(items_to_process=items_to_process)


class ItemMetadataOverride(models.Model):
    """Optional metadata corrections belonging to one user and catalog item."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="metadata_overrides")
    title = models.CharField(max_length=255, null=True, blank=True)
    image = models.URLField(null=True, blank=True)
    runtime = models.PositiveIntegerField(null=True, blank=True, validators=[MinValueValidator(1)])
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "item"], name="unique_user_item_metadata_override"),
            models.CheckConstraint(
                condition=Q(runtime__isnull=True) | Q(runtime__gt=0),
                name="metadata_override_runtime_positive",
            ),
        ]


class MediaManager(models.Manager):
    """Custom manager for media models."""

    def get_historical_models(self):
        """Return list of historical model names."""
        return [f"historical{media_type}" for media_type in MediaTypes.values]

    def _get_model_name_for_media_type(self, media_type):
        """Map media types to their corresponding Django model names."""
        # Map YouTube to TV model since they have the same structure
        if media_type == MediaTypes.YOUTUBE.value:
            return "tv"
        # For all other media types, the model name matches the media type
        return media_type

    def get_media_list(self, user, media_type, status_filter, sort_filter, search=None):
        """Get media list based on filters, sorting and search."""
        # Map media types to their corresponding models
        model_name = self._get_model_name_for_media_type(media_type)
        model = apps.get_model(app_label="app", model_name=model_name)
        queryset = model.objects.filter(user=user.id)
        
        # Filter by media_type to distinguish between different types using the same model
        # Exclude YouTube from TV and Season queries to keep them separate
        if media_type == MediaTypes.TV.value:
            queryset = queryset.filter(item__media_type=media_type).exclude(item__source=Sources.YOUTUBE.value)
        elif media_type == MediaTypes.SEASON.value:
            queryset = queryset.filter(item__media_type=media_type).exclude(item__source=Sources.YOUTUBE.value)
        else:
            queryset = queryset.filter(item__media_type=media_type)

        if media_type == MediaTypes.MOVIE.value:
            correction = ItemMetadataOverride.objects.filter(user=user, item_id=OuterRef("item_id"))
            queryset = queryset.annotate(metadata_title=models.functions.Coalesce(
                models.Subquery(correction.values("title")[:1]), F("item__title"),
            ))

        if status_filter != users.models.MediaStatusChoices.ALL:
            queryset = queryset.filter(status=status_filter)

        if search:
            title_field = "metadata_title" if media_type == MediaTypes.MOVIE.value else "item__title"
            queryset = queryset.filter(**{f"{title_field}__icontains": search})

        queryset = queryset.annotate(
            repeats=Window(
                expression=Count("id"),
                partition_by=[F("item")],
            ),
            row_number=Window(
                expression=RowNumber(),
                partition_by=[F("item")],
                order_by=F("created_at").desc(),
            ),
        ).filter(row_number=1)

        queryset = queryset.select_related("item")
        queryset = self._apply_prefetch_related(queryset, media_type)

        if sort_filter:
            return self._sort_media_list(queryset, sort_filter, media_type)
        return queryset

    def _apply_prefetch_related(self, queryset, media_type):
        """Apply appropriate prefetch_related based on media type."""
        # Apply media-specific prefetches
        if media_type in [MediaTypes.TV.value, MediaTypes.YOUTUBE.value]:
            return queryset.prefetch_related(
                Prefetch(
                    "seasons",
                    queryset=Season.objects.select_related("item"),
                ),
                Prefetch(
                    "seasons__episodes",
                    queryset=Episode.objects.select_related("item"),
                ),
            )

        base_queryset = queryset.prefetch_related(
            Prefetch(
                "item__event_set",
                queryset=events.models.Event.objects.all(),
                to_attr="prefetched_events",
            ),
        )

        if media_type == MediaTypes.SEASON.value:
            return base_queryset.prefetch_related(
                Prefetch(
                    "episodes",
                    queryset=Episode.objects.select_related("item"),
                ),
                "skipped_episodes",
            )

        return base_queryset

    def _sort_media_list(self, queryset, sort_filter, media_type=None):
        """Sort media list using SQL sorting with annotations for calculated fields."""
        if media_type in [MediaTypes.TV.value, MediaTypes.YOUTUBE.value]:
            return self._sort_tv_media_list(queryset, sort_filter)
        if media_type == MediaTypes.SEASON.value:
            return self._sort_season_media_list(queryset, sort_filter)

        return self._sort_generic_media_list(queryset, sort_filter)

    def _sort_tv_media_list(self, queryset, sort_filter):
        """Sort TV media list based on the sort criteria."""
        if sort_filter == "start_date":
            # Annotate with the minimum start_date from related seasons/episodes
            queryset = queryset.annotate(
                calculated_start_date=models.Min(
                    "seasons__episodes__end_date",
                    filter=models.Q(seasons__item__season_number__gt=0),
                ),
            )
            return queryset.order_by(
                models.F("calculated_start_date").asc(nulls_last=True),
                models.functions.Lower("item__title"),
            )

        if sort_filter == "end_date":
            # Annotate with the maximum end_date from related seasons/episodes
            queryset = queryset.annotate(
                calculated_end_date=models.Max(
                    "seasons__episodes__end_date",
                    filter=models.Q(seasons__item__season_number__gt=0),
                ),
            )
            return queryset.order_by(
                models.F("calculated_end_date").desc(nulls_last=True),
                models.functions.Lower("item__title"),
            )

        if sort_filter == "progress":
            # Annotate with the sum of episodes watched (excluding season 0)
            queryset = queryset.annotate(
                # Count episodes in regular seasons (season_number > 0)
                calculated_progress=models.Count(
                    "seasons__episodes",
                    filter=models.Q(seasons__item__season_number__gt=0),
                ),
            )
            return queryset.order_by(
                "-calculated_progress",
                models.functions.Lower("item__title"),
            )

        # Default to generic sorting
        return self._sort_generic_media_list(queryset, sort_filter)

    def _sort_season_media_list(self, queryset, sort_filter):
        """Sort Season media list based on the sort criteria."""
        if sort_filter == "start_date":
            # Annotate with the minimum end_date from related episodes
            queryset = queryset.annotate(
                calculated_start_date=models.Min("episodes__end_date"),
            )
            return queryset.order_by(
                models.F("calculated_start_date").asc(nulls_last=True),
                models.functions.Lower("item__title"),
            )

        if sort_filter == "end_date":
            # Annotate with the maximum end_date from related episodes
            queryset = queryset.annotate(
                calculated_end_date=models.Max("episodes__end_date"),
            )
            return queryset.order_by(
                models.F("calculated_end_date").desc(nulls_last=True),
                models.functions.Lower("item__title"),
            )

        if sort_filter == "progress":
            # Annotate with the maximum episode number
            queryset = queryset.annotate(
                calculated_progress=models.Max("episodes__item__episode_number"),
            )
            return queryset.order_by(
                "-calculated_progress",
                models.functions.Lower("item__title"),
            )

        # Default to generic sorting
        return self._sort_generic_media_list(queryset, sort_filter)

    def _sort_generic_media_list(self, queryset, sort_filter):
        """Apply generic sorting logic for all media types."""
        # Handle sorting by date fields with special null handling
        if sort_filter in ("start_date", "end_date"):
            # For start_date, sort ascending (earliest first)
            if sort_filter == "start_date":
                return queryset.order_by(
                    models.F(sort_filter).asc(nulls_last=True),
                    models.functions.Lower("item__title"),
                )
            # For other date fields, sort descending (latest first)
            return queryset.order_by(
                models.F(sort_filter).desc(nulls_last=True),
                models.functions.Lower("item__title"),
            )

        # Handle sorting by Item fields
        item_fields = [f.name for f in Item._meta.fields]
        if sort_filter in item_fields:
            if sort_filter == "title":
                # Case-insensitive title sorting
                title_field = "metadata_title" if "metadata_title" in queryset.query.annotations else "item__title"
                return queryset.order_by(models.functions.Lower(title_field))
            # Default sorting for other Item fields
            return queryset.order_by(
                f"-item__{sort_filter}",
                models.functions.Lower("item__title"),
            )

        # Default sorting by media field
        return queryset.order_by(
            models.F(sort_filter).desc(nulls_last=True),
            models.functions.Lower("item__title"),
        )

    def get_in_progress(
        self,
        user,
        sort_by,
        items_limit,
        specific_media_type=None,
        *,
        for_section=False,
        section_limits=None,
        offset=None,
    ):
        """Get a media list of in progress media by type."""
        list_by_type = {}
        media_types = self._get_media_types_to_process(user, specific_media_type)

        for media_type in media_types:
            media_list = self.get_media_list(
                user=user,
                media_type=media_type,
                status_filter=Status.IN_PROGRESS.value,
                sort_filter=None,
            )

            if not media_list:
                continue

            # Annotate with max_progress and next_event
            self.annotate_max_progress(media_list, media_type, local_seasons=True)
            self._annotate_next_event(media_list)

            if media_type == MediaTypes.SEASON.value:
                media_list = [
                    media
                    for media in media_list
                    if media.pending_episode_numbers or media.max_progress is None
                ]
            elif media_type == MediaTypes.YOUTUBE.value:
                media_list = [media for media in media_list if media.pending_videos]

            if not media_list:
                continue

            # Sort the media list
            sorted_list = self._sort_in_progress_media(media_list, sort_by)

            total_count = len(sorted_list)
            if offset is not None:
                start, stop = offset, offset + items_limit
            elif specific_media_type and not for_section:
                start, stop = items_limit, None
            else:
                start = 0
                stop = (section_limits or {}).get(media_type, items_limit)

            paginated_list = sorted_list[start:stop]
            shown = min(start + len(paginated_list), total_count)

            list_by_type[media_type] = {
                "items": paginated_list,
                "total": total_count,
                "shown": shown,
                "signature": self._home_page_signature(sorted_list[:shown]),
                "previous_signature": self._home_page_signature(sorted_list[:start]),
            }

        return list_by_type

    @staticmethod
    def _home_page_signature(ordered_media):
        """Identify the ordered cards that should already be in the browser."""
        ids = ",".join(str(media.pk) for media in ordered_media)
        return blake2s(ids.encode(), digest_size=8).hexdigest()

    def _get_media_types_to_process(self, user, specific_media_type):
        """Determine which media types to process based on user settings."""
        if specific_media_type:
            return [specific_media_type]

        # The Home tracks seasons and channels instead of series or individual videos.
        return [
            media_type
            for media_type in user.get_active_media_types()
            if media_type not in {MediaTypes.TV.value, MediaTypes.YOUTUBE_VIDEO.value}
        ]

    def _annotate_next_event(self, media_list):
        """Annotate next_event for media items."""
        current_time = timezone.now()

        for media in media_list:
            skipped = media.skipped_episode_numbers if media.item.media_type == MediaTypes.SEASON.value else set()
            # Get future events sorted by datetime
            future_events = sorted(
                [
                    event
                    for event in getattr(media.item, "prefetched_events", [])
                    if event.datetime > current_time and event.content_number not in skipped
                ],
                key=lambda e: e.datetime,
            )

            media.next_event = future_events[0] if future_events else None

    def _sort_in_progress_media(self, media_list, sort_by):
        """Sort in-progress media based on the sort criteria."""
        def episodes_left(media):
            if hasattr(media, "pending_videos"):
                return media.pending_videos
            return media.max_progress - media.progress if media.max_progress else 0

        # Define primary sort functions based on sort_by
        primary_sort_functions = {
            users.models.HomeSortChoices.UPCOMING: lambda x: (
                x.next_event is None,
                x.next_event.datetime if x.next_event else None,
            ),
            users.models.HomeSortChoices.RECENT: lambda x: -timezone.datetime.timestamp(
                x.progressed_at if x.progressed_at is not None else x.created_at,
            ),
            users.models.HomeSortChoices.COMPLETION: lambda x: (
                x.max_progress is None,
                -(
                    getattr(x, "watched_videos", x.progress) / x.max_progress * 100
                    if x.max_progress and x.max_progress > 0
                    else 0
                ),
            ),
            users.models.HomeSortChoices.EPISODES_LEFT: lambda x: (
                x.max_progress is None,
                episodes_left(x),
            ),
            users.models.HomeSortChoices.TITLE: lambda x: x.item.title.lower(),
        }

        primary_sort_function = primary_sort_functions[sort_by]

        return sorted(
            media_list,
            key=lambda x: (
                primary_sort_function(x),
                -timezone.datetime.timestamp(
                    x.progressed_at if x.progressed_at is not None else x.created_at,
                ),
                x.item.title.lower(),
                x.pk,
            ),
        )

    def annotate_max_progress(self, media_list, media_type, *, local_seasons=False):
        """Annotate max_progress for all media items."""
        current_datetime = timezone.now()

        if media_type == MediaTypes.MOVIE.value:
            for media in media_list:
                media.max_progress = 1
            return

        if media_type in [MediaTypes.TV.value, MediaTypes.YOUTUBE.value]:
            self._annotate_tv_released_episodes(media_list, current_datetime)
            return

        if media_type == MediaTypes.SEASON.value:
            self._annotate_season_pending_episodes(
                media_list, current_datetime, local_only=local_seasons,
            )
            return

        # For other media types, calculate max_progress from events
        # Create a dictionary mapping item_id to max content_number
        max_progress_dict = {}

        item_ids = [media.item.id for media in media_list]

        # Fetch all relevant events in a single query
        events_data = events.models.Event.objects.filter(
            item_id__in=item_ids,
            datetime__lte=current_datetime,
        ).values("item_id", "content_number")

        # Process events to find max content number per item
        for event in events_data:
            item_id = event["item_id"]
            content_number = event["content_number"]
            if content_number is not None:
                current_max = max_progress_dict.get(item_id, 0)
                max_progress_dict[item_id] = max(current_max, content_number)

        for media in media_list:
            media.max_progress = max_progress_dict.get(media.item.id)

    def _annotate_tv_released_episodes(self, tv_list, current_datetime):
        """Annotate TV shows with the number of released episodes."""
        # Handle YouTube sources differently
        if tv_list and tv_list[0].item.source == Sources.YOUTUBE.value:
            totals = dict(
                Item.objects.filter(
                    media_id__in=[tv.item.media_id for tv in tv_list],
                    source=Sources.YOUTUBE.value,
                    media_type=MediaTypes.EPISODE.value,
                )
                .values("media_id")
                .annotate(total=Count("pk"))
                .values_list("media_id", "total"),
            )
            for tv in tv_list:
                watched_ids = {
                    episode.item_id
                    for season in tv.seasons.all()
                    for episode in season.episodes.all()
                    if episode.end_date is not None
                    and episode.item is not None
                    and episode.item.source == Sources.YOUTUBE.value
                    and episode.item.media_type == MediaTypes.EPISODE.value
                    and episode.item.media_id == tv.item.media_id
                }
                tv.max_progress = totals.get(tv.item.media_id, 0)
                tv.watched_videos = len(watched_ids)
                tv.pending_videos = max(tv.max_progress - tv.watched_videos, 0)
            return
        
        # Original logic for TMDB sources
        # Prefetch all relevant events in one query
        released_events = events.models.Event.objects.filter(
            item__media_id__in=[tv.item.media_id for tv in tv_list],
            item__source=tv_list[0].item.source if tv_list else None,
            item__media_type=MediaTypes.SEASON.value,
            item__season_number__gt=0,
            datetime__lte=current_datetime,
            content_number__isnull=False,
        ).select_related("item")

        # Create a dictionary to store max episode numbers per season per show
        released_episodes = {}

        for event in released_events:
            media_id = event.item.media_id
            season_number = event.item.season_number
            episode_number = event.content_number

            if media_id not in released_episodes:
                released_episodes[media_id] = {}

            if (
                season_number not in released_episodes[media_id]
                or episode_number > released_episodes[media_id][season_number]
            ):
                released_episodes[media_id][season_number] = episode_number

        # Calculate total released episodes per TV show
        for tv in tv_list:
            tv_episodes = released_episodes.get(tv.item.media_id, {})
            tv.max_progress = sum(tv_episodes.values()) if tv_episodes else 0

    def _annotate_season_pending_episodes(
        self, season_list, current_datetime, *, local_only=False,
    ):
        """Count watched and available episodes and identify pending ones."""
        local_releases = (
            self._get_local_season_releases(season_list, current_datetime)
            if local_only else {}
        )
        for season in season_list:
            if season.status != Status.IN_PROGRESS.value:
                season.max_progress = 0
                season.pending_episode_numbers = []
                continue

            watched = {
                episode.item.episode_number
                for episode in season.episodes.all()
                if episode.end_date is not None and episode.item_id is not None
            }
            if local_only:
                available, catalog = local_releases[season.pk]
            elif season.item.source == Sources.TMDB.value:
                available, catalog = self._get_tmdb_available_episodes(
                    season, current_datetime,
                )
            else:
                available, catalog = self._get_local_available_episodes(
                    season, current_datetime,
                )

            skipped = season.skipped_episode_numbers - watched
            season.pending_episode_numbers = sorted(available - watched - skipped)
            # Keep already registered watches in the total if release data changes.
            season.max_progress = (
                None if local_only and catalog is None else len((available | watched) - skipped)
            )
            season._custom_progress = len(watched)
            season.completes_on_next_episode = (
                len(season.pending_episode_numbers) == 1
                and catalog is not None
                and catalog <= (watched | skipped | set(season.pending_episode_numbers))
            )

    def _get_local_season_releases(self, seasons, current_datetime):
        """Read calendar releases and fall back to locally stored episode items."""
        local_now = timezone.localtime(current_datetime)
        releases = {}
        missing = {}
        for season in seasons:
            if season.status != Status.IN_PROGRESS.value:
                continue

            item = season.item
            key = (item.source, item.media_id, item.season_number)
            calendar_episodes = {
                event.content_number: event
                for event in item.prefetched_events
                if event.content_number is not None
            }
            if item.source == Sources.TMDB.value and calendar_episodes:
                available = {
                    number
                    for number, event in calendar_episodes.items()
                    if self._is_home_release_available(
                        event, season.broadcast_time, local_now,
                    )
                }
                releases[season.pk] = available, set(calendar_episodes)
            else:
                missing[season.pk] = key, season.broadcast_time

        if not missing:
            return releases

        keys = {key for key, _ in missing.values()}
        fields = Item.objects.filter(
            media_type=MediaTypes.EPISODE.value,
            source__in={key[0] for key in keys},
            media_id__in={key[1] for key in keys},
            season_number__in={key[2] for key in keys},
        ).values_list("source", "media_id", "season_number", "episode_number", "air_date")
        local_episodes = {key: {} for key in keys}
        for source, media_id, season_number, number, air_date in fields:
            key = (source, media_id, season_number)
            if key in local_episodes:
                local_episodes[key][number] = air_date

        for season_pk, (key, broadcast_time) in missing.items():
            is_tmdb = key[0] == Sources.TMDB.value
            episode_dates = local_episodes[key]
            latest_available_date = local_now.date()
            if is_tmdb and broadcast_time and local_now.time() < broadcast_time:
                latest_available_date -= timezone.timedelta(days=1)
            available = {
                number
                for number, air_date in episode_dates.items()
                if air_date is None or air_date <= latest_available_date
            }
            catalog = None if is_tmdb else set(episode_dates)
            releases[season_pk] = available, catalog
        return releases

    @staticmethod
    def _is_home_release_available(event, broadcast_time, local_now):
        if event.datetime.year == datetime.min.year:
            # The calendar stores missing dates as the minimum datetime.
            return True
        if event.is_max_datetime:
            return False
        if event.is_sentinel_time:
            air_date = event.datetime.date()
        elif broadcast_time is not None:
            air_date = timezone.localtime(event.datetime).date()
        else:
            return event.datetime <= local_now

        return air_date < local_now.date() or (
            air_date == local_now.date()
            and (broadcast_time is None or broadcast_time <= local_now.time())
        )

    def _get_local_available_episodes(self, season, current_datetime):
        """Get available episodes from local database (for MANUAL sources)."""
        episode_items = Item.objects.filter(
            media_type=MediaTypes.EPISODE.value,
            media_id=season.item.media_id,
            source=season.item.source,
            season_number=season.item.season_number
        )
        
        today = timezone.localtime(current_datetime).date()
        available = set()
        catalog = set()
        for number, air_date in episode_items.values_list("episode_number", "air_date"):
            catalog.add(number)
            if air_date is None or air_date <= today:
                available.add(number)
        return available, catalog
    
    def _get_tmdb_available_episodes(self, season, current_datetime):
        """Get the TMDB episode catalog and filter by the local broadcast time."""
        try:
            from app.providers import tmdb

            current_local = timezone.localtime(current_datetime)

            # Get season metadata from TMDB using the correct function
            tv_data = tmdb.tv_with_seasons(
                season.item.media_id,
                [season.item.season_number],
            )

            season_key = f"season/{season.item.season_number}"
            if season_key not in tv_data:
                available, _ = self._get_local_available_episodes(
                    season, current_datetime,
                )
                return available, None

            season_metadata = tv_data[season_key]

            # Filter episodes by air date and broadcast_time
            available_episode_numbers = set()
            catalog = set()
            for episode in season_metadata.get("episodes", []):
                episode_number = episode.get("episode_number")
                air_date_str = episode.get("air_date")

                if episode_number:
                    catalog.add(episode_number)
                    # If no air_date, always available
                    if not air_date_str:
                        available_episode_numbers.add(episode_number)
                    else:
                        try:
                            air_date = datetime.fromisoformat(air_date_str).replace(
                                tzinfo=current_local.tzinfo,
                            )
                            # If broadcast_time is set, combine with air_date
                            if season.broadcast_time:
                                # Use the season's broadcast_time (as time object)
                                air_datetime = air_date.replace(
                                    hour=season.broadcast_time.hour,
                                    minute=season.broadcast_time.minute,
                                    second=season.broadcast_time.second or 0,
                                    microsecond=0,
                                )
                            else:
                                air_datetime = air_date
                            if air_datetime <= current_local:
                                available_episode_numbers.add(episode_number)
                        except (ValueError, TypeError, AttributeError):
                            # If air_date parsing fails, include the episode
                            available_episode_numbers.add(episode_number)

        except Exception:
            # If TMDB API fails, fallback to local database
            available, _ = self._get_local_available_episodes(season, current_datetime)
            return available, None
        else:
            return available_episode_numbers, catalog

    def get_media(
        self,
        user,
        media_type,
        instance_id,
    ):
        """Get user media object given the media type and item."""
        model = apps.get_model(app_label="app", model_name=self._get_model_name_for_media_type(media_type))
        params = self._get_media_params(
            user,
            media_type,
            instance_id,
        )

        try:
            return model.objects.get(**params)
        except model.DoesNotExist:
            return None

    def get_media_prefetch(
        self,
        user,
        media_type,
        instance_id,
        *,
        lock=False,
        local_seasons=False,
    ):
        """Get user media object with prefetch_related applied."""
        model = apps.get_model(app_label="app", model_name=self._get_model_name_for_media_type(media_type))
        params = self._get_media_params(
            user,
            media_type,
            instance_id,
        )

        queryset = model.objects.filter(**params).select_related("item")
        if lock:
            queryset = queryset.select_for_update(of=("self",))

        queryset = self._apply_prefetch_related(queryset, media_type)
        self.annotate_max_progress(queryset, media_type, local_seasons=local_seasons)

        return queryset[0]

    def _get_media_params(
        self,
        user,
        media_type,
        instance_id,
    ):
        """Get the common filter parameters for media queries."""
        params = {"id": instance_id}
        if media_type == MediaTypes.YOUTUBE.value:
            params["item__media_type"] = media_type

        if media_type == MediaTypes.EPISODE.value:
            params["related_season__user"] = user
        else:
            params["user"] = user

        return params

    def filter_media(
        self,
        user,
        media_id,
        media_type,
        source,
        season_number=None,
        episode_number=None,
    ):
        """Filter media objects based on parameters."""
        model_name = self._get_model_name_for_media_type(media_type)
        model = apps.get_model(app_label="app", model_name=model_name)
        params = self._filter_media_params(
            media_type,
            media_id,
            source,
            user,
            season_number,
            episode_number,
        )

        return model.objects.filter(**params)

    def filter_media_prefetch(
        self,
        user,
        media_id,
        media_type,
        source,
        season_number=None,
        episode_number=None,
    ):
        """Filter user media object with prefetch_related applied."""
        queryset = self.filter_media(
            user,
            media_id,
            media_type,
            source,
            season_number,
            episode_number,
        )
        queryset = self._apply_prefetch_related(queryset, media_type)
        self.annotate_max_progress(queryset, media_type)

        return queryset

    def _filter_media_params(
        self,
        media_type,
        media_id,
        source,
        user,
        season_number=None,
        episode_number=None,
    ):
        """Get the common filter parameters for media queries."""
        params = {
            "item__media_type": media_type,
            "item__source": source,
            "item__media_id": media_id,
        }

        if media_type == MediaTypes.SEASON.value:
            params["item__season_number"] = season_number
            params["user"] = user
        elif media_type == MediaTypes.EPISODE.value:
            params["item__season_number"] = season_number
            params["item__episode_number"] = episode_number
            params["related_season__user"] = user
        else:
            params["user"] = user

        return params


class Status(models.TextChoices):
    """Choices for item status."""

    COMPLETED = "Completed", "Completed"
    IN_PROGRESS = "In progress", "In Progress"
    PLANNING = "Planning", "Planning"
    PAUSED = "Paused", "Paused"
    DROPPED = "Dropped", "Dropped"


class Media(models.Model):
    """Abstract model for all media types."""

    history = HistoricalRecords(
        cascade_delete_history=True,
        inherit=True,
        excluded_fields=[
            "item",
            "user",
            "related_tv",
            "created_at",
            "home_completion_pending",
        ],
    )

    created_at = models.DateTimeField(auto_now_add=True)
    item = models.ForeignKey(Item, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    score = models.DecimalField(
        null=True,
        blank=True,
        max_digits=3,
        decimal_places=1,
        validators=[
            DecimalValidator(3, 1),
            MinValueValidator(0),
            MaxValueValidator(10),
        ],
    )
    progress = models.PositiveIntegerField(default=0)
    progressed_at = MonitorField(monitor="progress")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.COMPLETED.value,
    )
    start_date = models.DateTimeField(null=True, blank=True)
    end_date = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        """Meta options for the model."""

        abstract = True
        ordering = ["user", "item", "-created_at"]

    def __str__(self):
        """Return the title of the media."""
        return self.item.__str__()

    def save(self, *args, **kwargs):
        """Save the media instance."""
        if self.tracker.has_changed("progress"):
            self.process_progress()

        if self.tracker.has_changed("status"):
            self.process_status()

        super().save(*args, **kwargs)

    def process_progress(self):
        """Update fields depending on the progress of the media."""
        if self.progress < 0:
            self.progress = 0
        else:
            metadata = providers.services.get_media_metadata(
                self.item.media_type,
                self.item.media_id,
                self.item.source,
            )
            max_progress = metadata["max_progress"]

            if max_progress:
                self.progress = min(self.progress, max_progress)

                if self.progress == max_progress:
                    self.status = Status.COMPLETED.value

                    now = timezone.now().replace(second=0, microsecond=0)
                    self.end_date = now
                    
                    # Update Item runtime if missing (for movies)
                    if self.item.media_type == MediaTypes.MOVIE.value and not self.item.runtime:
                        runtime = metadata.get("runtime")
                        if runtime:
                            self.item.runtime = runtime
                            self.item.save(update_fields=["runtime"])

    def process_status(self):
        """Update fields depending on the status of the media."""
        if self.status == Status.COMPLETED.value:
            metadata = providers.services.get_media_metadata(
                self.item.media_type,
                self.item.media_id,
                self.item.source,
            )
            max_progress = metadata["max_progress"]

            if max_progress:
                self.progress = max_progress
            
            # Update Item runtime if missing (for movies)
            if self.item.media_type == MediaTypes.MOVIE.value and not self.item.runtime:
                runtime = metadata.get("runtime")
                if runtime:
                    self.item.runtime = runtime
                    self.item.save(update_fields=["runtime"])

        self.item.fetch_releases(delay=True)

    @property
    def formatted_score(self):
        """Return as int if score is 10.0 or 0.0, otherwise show decimal."""
        if self.score is not None:
            max_score = 10
            min_score = 0
            if self.score in (max_score, min_score):
                return int(self.score)
            return self.score
        return None

    @property
    def formatted_progress(self):
        """Return the progress of the media in a formatted string."""
        return str(self.progress)

    def increase_progress(self):
        """Increase the progress of the media by one."""
        self.progress += 1
        self.save()
        logger.info("Incresed progress of %s to %s", self, self.progress)

    def decrease_progress(self):
        """Decrease the progress of the media by one."""
        self.progress -= 1
        self.save()
        logger.info("Decreased progress of %s to %s", self, self.progress)


class BasicMedia(Media):
    """Model for basic media types."""

    objects = MediaManager()


class TV(Media):
    """Model for TV shows."""

    tracker = FieldTracker()

    class Meta:
        """Meta options for the model."""

        ordering = ["user", "item"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "item"],
                name="%(app_label)s_%(class)s_unique_item_user",
            ),
        ]

    @tracker  # postpone field reset until after the save
    def save(self, *args, **kwargs):
        """Save the media instance."""
        super(Media, self).save(*args, **kwargs)

        if self.tracker.has_changed("status"):
            if self.status == Status.COMPLETED.value:
                self._completed()

            elif self.status == Status.DROPPED.value:
                self._mark_in_progress_seasons_as_dropped()

            elif (
                self.status == Status.IN_PROGRESS.value
                and not self.seasons.filter(status=Status.IN_PROGRESS.value).exists()
            ):
                self._start_next_available_season()

            self.item.fetch_releases(delay=True)

    @property
    def progress(self):
        """Return the total episodes watched for the TV show."""
        return sum(
            season.progress
            for season in self.seasons.all()
            if (
                self.item.source == Sources.YOUTUBE.value
                or season.item.season_number != 0
            )
        )

    @property
    def last_watched(self):
        """Return the latest watched episode in SxxExx format."""
        watched_episodes = [
            {
                "season": season.item.season_number,
                "episode": episode.item.episode_number,
                "end_date": episode.end_date,
            }
            for season in self.seasons.all()
            if hasattr(season, "episodes")
            and (
                self.item.source == Sources.YOUTUBE.value
                or season.item.season_number != 0
            )
            for episode in season.episodes.all()
            if episode.end_date is not None
        ]

        if not watched_episodes:
            return ""

        latest_episode = max(
            watched_episodes,
            key=lambda x: (x["end_date"], x["season"], x["episode"]),
        )

        if (
            self.item.source == Sources.YOUTUBE.value
            and latest_episode["season"] == 0
        ):
            return f"Unknown year E{latest_episode['episode']:02d}"

        return f"S{latest_episode['season']:02d}E{latest_episode['episode']:02d}"

    @property
    def progressed_at(self):
        """Return the date when the last episode was watched."""
        dates = [
            season.progressed_at
            for season in self.seasons.all()
            if season.progressed_at
            and (
                self.item.source == Sources.YOUTUBE.value
                or season.item.season_number != 0
            )
        ]
        return max(dates) if dates else None

    @property
    def start_date(self):
        """Return the date of the first episode watched."""
        dates = [
            season.start_date
            for season in self.seasons.all()
            if season.start_date
            and (
                self.item.source == Sources.YOUTUBE.value
                or season.item.season_number != 0
            )
        ]
        return min(dates) if dates else None

    @property
    def end_date(self):
        """Return the date of the last episode watched."""
        dates = [
            season.end_date
            for season in self.seasons.all()
            if season.end_date
            and (
                self.item.source == Sources.YOUTUBE.value
                or season.item.season_number != 0
            )
        ]
        return max(dates) if dates else None

    def _completed(self):
        """Create remaining seasons and episodes for a TV show."""
        # Skip completion logic for YouTube and Manual sources
        if self.item.source in [Sources.YOUTUBE.value, Sources.MANUAL.value]:
            return
            
        tv_metadata = providers.services.get_media_metadata(
            self.item.media_type,
            self.item.media_id,
            self.item.source,
        )
        max_progress = tv_metadata["max_progress"]

        if not max_progress or self.progress > max_progress:
            return

        seasons_to_create = []
        seasons_to_update = []
        episodes_to_create = []

        season_numbers = [
            season["season_number"]
            for season in tv_metadata["related"]["seasons"]
            if season["season_number"] != 0
        ]
        tv_with_seasons_metadata = providers.services.get_media_metadata(
            "tv_with_seasons",
            self.item.media_id,
            self.item.source,
            season_numbers,
        )
        for season_number in season_numbers:
            season_metadata = tv_with_seasons_metadata[f"season/{season_number}"]

            item, _ = Item.objects.get_or_create(
                media_id=self.item.media_id,
                source=self.item.source,
                media_type=MediaTypes.SEASON.value,
                season_number=season_number,
                defaults={
                    "title": self.item.title,
                    "image": season_metadata["image"],
                },
            )
            try:
                season_instance = Season.objects.get(
                    item=item,
                    user=self.user,
                )

                if season_instance.status != Status.COMPLETED.value:
                    season_instance.status = Status.COMPLETED.value
                    seasons_to_update.append(season_instance)

            except Season.DoesNotExist:
                seasons_to_create.append(
                    Season(
                        item=item,
                        score=None,
                        status=Status.COMPLETED.value,
                        notes="",
                        related_tv=self,
                        user=self.user,
                    ),
                )

        bulk_create_with_history(seasons_to_create, Season)
        bulk_update_with_history(seasons_to_update, Season, ["status"])

        for season_instance in seasons_to_create + seasons_to_update:
            season_metadata = tv_with_seasons_metadata[
                f"season/{season_instance.item.season_number}"
            ]
            episodes_to_create.extend(
                season_instance.get_remaining_eps(season_metadata),
            )
        bulk_create_with_history(episodes_to_create, Episode)

    def _mark_in_progress_seasons_as_dropped(self):
        """Mark all in-progress seasons as dropped."""
        in_progress_seasons = list(
            self.seasons.filter(status=Status.IN_PROGRESS.value),
        )

        for season in in_progress_seasons:
            season.status = Status.DROPPED.value

        if in_progress_seasons:
            bulk_update_with_history(
                in_progress_seasons,
                Season,
                fields=["status"],
            )

    def _start_next_available_season(self):
        """Find the next available season to watch and set it to in-progress."""
        all_seasons = self.seasons.filter(
            item__season_number__gt=0,
        ).order_by("item__season_number")

        next_unwatched_season = all_seasons.exclude(
            status__in=[Status.COMPLETED.value],
        ).first()

        if not next_unwatched_season:
            # For YouTube channels, don't try to auto-create seasons since they don't have traditional seasons
            if self.item.source == Sources.YOUTUBE.value:
                return
                
            # If all existing seasons are watched, get the next available season
            tv_metadata = providers.services.get_media_metadata(
                self.item.media_type,
                self.item.media_id,
                self.item.source,
            )

            existing_season_numbers = set(
                all_seasons.values_list("item__season_number", flat=True),
            )

            for season_data in tv_metadata["related"]["seasons"]:
                season_number = season_data["season_number"]
                if season_number > 0 and season_number not in existing_season_numbers:
                    item, _ = Item.objects.get_or_create(
                        media_id=self.item.media_id,
                        source=self.item.source,
                        media_type=MediaTypes.SEASON.value,
                        season_number=season_data["season_number"],
                        defaults={
                            "title": self.item.title,
                            "image": season_data["image"],
                        },
                    )

                    next_unwatched_season = Season(
                        item=item,
                        user=self.user,
                        related_tv=self,
                        status=Status.IN_PROGRESS.value,
                    )
                    bulk_create_with_history([next_unwatched_season], Season)
                    break

        elif next_unwatched_season.status != Status.IN_PROGRESS.value:
            next_unwatched_season.status = Status.IN_PROGRESS.value
            bulk_update_with_history(
                [next_unwatched_season],
                Season,
                fields=["status"],
            )


class Season(Media):
    """Model for seasons of TV shows."""

    related_tv = models.ForeignKey(
        TV,
        on_delete=models.CASCADE,
        related_name="seasons",
    )

    tracker = FieldTracker()
    home_completion_pending = models.BooleanField(default=False)

    broadcast_time = models.TimeField(
        null=True,
        blank=True,
        help_text="Hora de emisiÃ³n local (opcional, solo para filtrar pendientes en Home)."
    )

    class Meta:
        """Limit the uniqueness of seasons.

        Only one season per media can have the same season number.
        """

        constraints = [
            models.UniqueConstraint(
                fields=["related_tv", "item"],
                name="%(app_label)s_season_unique_tv_item",
            ),
        ]

    def __str__(self):
        """Return the title of the media and season number."""
        return f"{self.item.title} S{self.item.season_number}"

    @tracker  # postpone field reset until after the save
    def save(self, *args, **kwargs):
        """Save the media instance."""
        # if related_tv is not set
        if self.related_tv_id is None:
            self.related_tv = self.get_tv()

        update_fields = kwargs.get("update_fields")
        status_changed = self.tracker.has_changed("status") and (
            update_fields is None or "status" in update_fields
        )
        if status_changed and self.home_completion_pending:
            # An explicit status change supersedes the deferred Home decision.
            self.home_completion_pending = False
            if update_fields is not None:
                kwargs["update_fields"] = {*update_fields, "home_completion_pending"}

        super(Media, self).save(*args, **kwargs)

        if not status_changed:
            return

        if (
            self.status == Status.DROPPED.value
            and self.related_tv.status != Status.DROPPED.value
        ) or (
            self.status == Status.IN_PROGRESS.value
            and self.related_tv.status != Status.IN_PROGRESS.value
        ):
            self.related_tv.status = self.status
            bulk_update_with_history([self.related_tv], TV, fields=["status"])

        if self.status != Status.COMPLETED.value or self.item.source not in {
            Sources.YOUTUBE.value, Sources.MANUAL.value,
        }:
            self.item.fetch_releases(delay=True)

    @property
    def progress(self):
        """Return the count of watched episodes for display purposes."""
        # If we have a custom progress count (from home page filtering), use it
        if hasattr(self, '_custom_progress'):
            return self._custom_progress
            
        episodes = self.episodes.all()
        if not episodes:
            return 0

        # For YouTube sources, count unique watched episodes
        if self.item.source == Sources.YOUTUBE.value:
            watched_episode_numbers = set(ep.item.episode_number for ep in episodes)
            return len(watched_episode_numbers)
        
        # For other sources, return the highest episode number that has been watched
        # This shows the furthest progress regardless of duplicates/repeats
        watched_episode_numbers = [ep.item.episode_number for ep in episodes]
        return max(watched_episode_numbers)

    @property
    def progressed_at(self):
        """Return the date when the last episode was watched."""
        dates = [
            episode.end_date
            for episode in self.episodes.all()
            if episode.end_date is not None
        ]
        return max(dates) if dates else None

    @property
    def start_date(self):
        """Return the date of the first episode watched."""
        dates = [
            episode.end_date
            for episode in self.episodes.all()
            if episode.end_date is not None
        ]
        return min(dates) if dates else None

    @property
    def end_date(self):
        """Return the date of the last episode watched."""
        dates = [
            episode.end_date
            for episode in self.episodes.all()
            if episode.end_date is not None
        ]
        return max(dates) if dates else None

    @property
    def skipped_episode_numbers(self):
        return {episode.episode_number for episode in self.skipped_episodes.all()}

    def increase_progress(self):
        """Watch the next episode of the season."""
        season_metadata = providers.services.get_media_metadata(
            MediaTypes.SEASON.value,
            self.item.media_id,
            self.item.source,
            [self.item.season_number],
        )
        episodes = season_metadata["episodes"]

        # Find the next unwatched episode instead of using progress (which counts repeats)
        watched_episode_numbers = set(
            episode.item.episode_number for episode in self.episodes.all()
        ) | self.skipped_episode_numbers
        
        # Find first episode that hasn't been watched yet
        next_episode_number = None
        for episode in episodes:
            ep_num = episode["episode_number"]
            if ep_num not in watched_episode_numbers:
                next_episode_number = ep_num
                break

        now = timezone.now().replace(second=0, microsecond=0)

        if next_episode_number:
            self.watch(next_episode_number, now)
            logger.info("Watched next unwatched episode: %d", next_episode_number)
        else:
            logger.info("No more episodes to watch - all episodes have been seen.")

    def watch(
        self,
        episode_number,
        end_date,
        auto_complete=True,  # noqa: FBT002
        *,
        complete_season=False,
        from_home=False,
    ):
        """Create or add a repeat to an episode of the season."""
        item = self.get_episode_item(
            episode_number, {"episodes": []} if from_home else None,
        )

        episode = Episode(
            related_season=self,
            item=item,
            end_date=end_date,
        )
        
        episode.save(
            auto_complete=auto_complete,
            complete_season=complete_season,
            from_home=from_home,
        )
        
        logger.info(
            "%s created successfully.",
            episode,
        )

    def decrease_progress(self):
        """Unwatch the current episode of the season."""
        self.unwatch(self.progress)

    def unwatch(self, episode_number):
        """Unwatch the episode instance."""
        item = self.get_episode_item(episode_number)

        episodes = Episode.objects.filter(
            related_season=self,
            item=item,
        ).order_by("-end_date")

        episode = episodes.first()

        if episode is None:
            logger.warning(
                "Episode %s does not exist.",
                self.item,
            )
            return

        # Get count before deletion for logging
        remaining_count = episodes.count() - 1

        episode.delete()
        logger.info(
            "Deleted %s S%02dE%02d (%d remaining instances)",
            self.item.title,
            self.item.season_number,
            episode_number,
            remaining_count,
        )

    def get_tv(self):
        """Get related TV instance for a season and create it if it doesn't exist."""
        try:
            tv = TV.objects.get(
                item__media_id=self.item.media_id,
                item__media_type=MediaTypes.TV.value,
                item__season_number=None,
                item__source=self.item.source,
                user=self.user,
            )
        except TV.DoesNotExist:
            tv_metadata = providers.services.get_media_metadata(
                MediaTypes.TV.value,
                self.item.media_id,
                self.item.source,
            )

            # creating tv with multiple seasons from a completed season
            if (
                self.status == Status.COMPLETED.value
                and tv_metadata["details"]["seasons"] > 1
            ):
                status = Status.IN_PROGRESS.value
            else:
                status = self.status

            item, _ = Item.objects.get_or_create(
                media_id=self.item.media_id,
                source=Sources.TMDB.value,
                media_type=MediaTypes.TV.value,
                defaults={
                    "title": tv_metadata["title"],
                    "image": tv_metadata["image"],
                },
            )

            tv = TV(
                item=item,
                score=None,
                status=status,
                notes="",
                user=self.user,
            )

            # save_base to avoid custom save method
            TV.save_base(tv)

            logger.info("%s did not exist, it was created successfully.", tv)

        return tv

    def get_remaining_eps(self, season_metadata):
        """Build watch records for the whole-series completion flow."""
        latest_watched_ep_num = Episode.objects.filter(related_season=self).aggregate(
            latest_watched_ep_num=Max("item__episode_number"),
        )["latest_watched_ep_num"]

        if latest_watched_ep_num is None:
            latest_watched_ep_num = 0

        episodes_to_create = []
        now = timezone.now().replace(second=0, microsecond=0)

        # Create Episode objects for the remaining episodes
        for episode in reversed(season_metadata["episodes"]):
            if episode["episode_number"] <= latest_watched_ep_num:
                break

            item = self.get_episode_item(episode["episode_number"], season_metadata)

            episode_db = Episode(
                related_season=self,
                item=item,
                end_date=now,
            )
            episodes_to_create.append(episode_db)

        return episodes_to_create

    def get_episode_item(self, episode_number, season_metadata=None):
        """Get the episode item instance, create it if it doesn't exist."""
        if not season_metadata:
            # Skip TMDB calls for YouTube and Manual sources
            if self.item.source in [Sources.YOUTUBE.value, Sources.MANUAL.value]:
                season_metadata = {"episodes": []}  # Empty metadata for non-TMDB sources
            else:
                season_metadata = providers.services.get_media_metadata(
                    MediaTypes.SEASON.value,
                    self.item.media_id,
                    self.item.source,
                    [self.item.season_number],
                )

        metadata = next(
            (
                episode for episode in season_metadata["episodes"]
                if episode["episode_number"] == int(episode_number)
            ),
            {},
        )
        fields = Item.episode_metadata_fields(metadata)

        item, _ = Item.objects.get_or_create(
            media_id=self.item.media_id,
            source=self.item.source,
            media_type=MediaTypes.EPISODE.value,
            season_number=self.item.season_number,
            episode_number=episode_number,
            defaults={
                "title": self.item.title,
                **fields,
            },
        )

        # If the Item already existed but missing air_date/runtime, update it
        updated_fields = []
        for field in ("air_date", "runtime"):
            if getattr(item, field) is None and fields[field] is not None:
                setattr(item, field, fields[field])
                updated_fields.append(field)

        if updated_fields:
            item.save(update_fields=updated_fields)

        return item


class SkippedEpisode(models.Model):
    """An episode excluded from a user's tracked season."""

    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="skipped_episodes")
    episode_number = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["season", "episode_number"], name="unique_skipped_season_episode"),
        ]


class Episode(models.Model):
    """Model for episodes of a season."""

    history = HistoricalRecords(
        cascade_delete_history=True,
        excluded_fields=["item", "related_season", "created_at"],
    )

    created_at = models.DateTimeField(auto_now_add=True)
    item = models.ForeignKey(Item, on_delete=models.CASCADE, null=True)
    related_season = models.ForeignKey(
        Season,
        on_delete=models.CASCADE,
        related_name="episodes",
    )
    end_date = models.DateTimeField(null=True, blank=True)

    class Meta:
        """Meta options for the model."""

        ordering = [
            "related_season",
            "item__episode_number",
            "-end_date",
            "-created_at",
        ]

    def __str__(self):
        """Return the season and episode number."""
        return self.item.__str__()

    def save(self, *args, **kwargs):
        """Save the episode instance."""
        # Extract the auto_complete parameter from kwargs
        auto_complete = kwargs.pop('auto_complete', True)
        complete_season = kwargs.pop("complete_season", False)
        from_home = kwargs.pop("from_home", False)
        
        super().save(*args, **kwargs)

        if self.end_date is not None and self.item_id is not None:
            SkippedEpisode.objects.filter(
                season_id=self.related_season_id, episode_number=self.item.episode_number,
            ).delete()

        # Skip TMDB metadata fetching for YouTube and Manual episodes
        if self.item.source in [Sources.YOUTUBE.value, Sources.MANUAL.value]:
            if complete_season and self.item.source == Sources.MANUAL.value:
                self.related_season.status = Status.COMPLETED.value
                self.related_season.save(update_fields=["status"])
            return

        season_number = self.item.season_number
        if from_home:
            self._update_tracking_status(
                completed=complete_season,
                defer_tv_completion=complete_season,
            )
            return

        tv_with_seasons_metadata = providers.services.get_media_metadata(
            "tv_with_seasons",
            self.item.media_id,
            self.item.source,
            [season_number],
        )
        season_metadata = tv_with_seasons_metadata[f"season/{season_number}"]
        max_progress = len(season_metadata["episodes"])

        # clear prefetch cache to get the updated episodes
        self.related_season.refresh_from_db()

        completed = complete_season or (
            auto_complete and self.item.episode_number == max_progress
        )
        last_season = (
            tv_with_seasons_metadata["related"]["seasons"][-1]["season_number"]
            if completed else None
        )
        self._update_tracking_status(completed=completed, last_season=last_season)

    def _update_tracking_status(
        self, *, completed, last_season=None, defer_tv_completion=False,
    ):
        season = self.related_season
        tv = season.related_tv
        if completed:
            season.status = Status.COMPLETED.value
            season.home_completion_pending = defer_tv_completion
            bulk_update_with_history(
                [season],
                Season,
                fields=["status", "home_completion_pending"],
            )
            if last_season == self.item.season_number:
                tv.status = Status.COMPLETED.value
                bulk_update_with_history([tv], TV, fields=["status"])
            return

        if season.status != Status.IN_PROGRESS.value:
            season.status = Status.IN_PROGRESS.value
            bulk_update_with_history(
                [season],
                Season,
                fields=["status"],
            )

        if tv.status != Status.IN_PROGRESS.value:
            tv.status = Status.IN_PROGRESS.value
            bulk_update_with_history([tv], TV, fields=["status"])


class Manga(Media):
    """Model for manga."""

    reading_time = models.PositiveIntegerField(default=0)
    tracker = FieldTracker()


class Anime(Media):
    """Model for anime."""

    tracker = FieldTracker()


class Movie(Media):
    """Model for movies."""

    tracker = FieldTracker()


class Game(Media):
    """Model for games."""

    play_time = models.PositiveIntegerField(
        default=0,
        help_text="Total accumulated play time in minutes for all gaming sessions",
    )
    percentage_progress = models.PositiveSmallIntegerField(
        default=0,
        validators=[MaxValueValidator(100)],
        help_text="Optional percentage progress (0-100) for tracking game completion",
    )
    
    tracker = FieldTracker()

    def process_progress(self):
        """Clamp accumulated minutes without looking up provider metadata."""
        self.progress = max(0, self.progress)

    @property
    def formatted_progress(self):
        """Return progress in hours:minutes format."""
        return app.helpers.minutes_to_hhmm(self.progress)

    def increase_progress(self):
        """Increase the progress of the media by the play time from session.
        
        Note: Progress is now tracked via play_time sessions from the modal.
        This method is called by views.py after play_time is accumulated.
        """
        # Progress is handled by play_time accumulation in views.py
        # We just log the current formatted progress
        logger.info("Play session recorded for %s, total playtime: %s", self, self.formatted_progress)

    def decrease_progress(self):
        """Decrease the progress of the media by 10 minutes."""
        self.progress -= 10
        self.save()
        logger.info("Changed playtime of %s to %s", self, self.formatted_progress)


class Book(Media):
    """Model for books."""

    reading_time = models.PositiveIntegerField(
        default=0,
        help_text="Total accumulated reading time in minutes for all reading sessions",
    )
    
    tracker = FieldTracker()
    
    @property
    def formatted_progress(self):
        """Return progress as percentage."""
        return f"{self.progress}%"
    
    def increase_progress(self):
        """Increase the progress of the book.
        
        Note: Progress is now tracked as percentage (0-100) via reading_time sessions from the modal.
        This method is called by views.py after reading_time is accumulated.
        """
        # Progress is handled by percentage and reading_time accumulation in views.py
        logger.info("Reading session recorded for %s, progress: %s%%", self, self.progress)
    
    def decrease_progress(self):
        """Decrease the progress of the book by 5%."""
        self.progress = max(0, self.progress - 5)
        self.save()
        logger.info("Changed progress of %s to %s%%", self, self.progress)


class Comic(Media):
    """Model for comics."""

    reading_time = models.PositiveIntegerField(
        default=0,
        help_text="Total accumulated reading time in minutes for all issues read",
    )
    
    tracker = FieldTracker()


class YouTubeChannelFilter(models.Model):
    """Model for filtering/blocking YouTube channels from auto-creation via webhooks."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="youtube_channel_filters",
    )
    channel_id = models.CharField(
        max_length=100,
        help_text="YouTube Channel ID (e.g., UCuAXFkgsw1L7xaCfnd5JJOw)",
    )
    channel_name = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional channel name for easier identification",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Meta options for the model."""

        verbose_name = "YouTube Channel Filter"
        verbose_name_plural = "YouTube Channel Filters"
        ordering = ["channel_name", "channel_id"]
        constraints = [
            UniqueConstraint(
                fields=["user", "channel_id"],
                name="unique_user_channel_filter",
            ),
        ]

    def __str__(self):
        """Return string representation."""
        if self.channel_name:
            return f"{self.channel_name} ({self.channel_id})"
        return self.channel_id






class GameSession(models.Model):
    """Individual play session for a game."""

    class SessionSource(models.TextChoices):
        MANUAL = "manual", "Manual"
        IMPORT = "import", "Import"
        HISTORY = "history", "Migrated from History"

    game = models.ForeignKey(
        Game,
        on_delete=models.CASCADE,
        related_name="sessions",
    )
    minutes = models.PositiveIntegerField(help_text="Playtime in minutes for this session")
    percentage_progress = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MaxValueValidator(100)],
        help_text="Optional completion percentage captured during the session",
    )
    session_date = models.DateTimeField(
        default=timezone.now,
        help_text="Timestamp when the session occurred",
    )
    source = models.CharField(
        max_length=20,
        choices=SessionSource.choices,
        default=SessionSource.MANUAL,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-session_date", "-id"]

    def __str__(self):
        return f"{self.game} session ({self.minutes}m on {self.session_date:%Y-%m-%d %H:%M})"


class ComicSession(models.Model):
    """Individual reading session for a comic."""

    class SessionSource(models.TextChoices):
        MANUAL = "manual", "Manual"
        IMPORT = "import", "Import"
        HISTORY = "history", "Migrated from History"

    comic = models.ForeignKey(
        Comic,
        on_delete=models.CASCADE,
        related_name="sessions",
    )
    minutes = models.PositiveIntegerField(help_text="Reading time in minutes for this session")
    issues_read = models.PositiveIntegerField(
        default=1,
        help_text="Number of issues read in this session",
    )
    session_date = models.DateTimeField(
        default=timezone.now,
        help_text="Timestamp when the session occurred",
    )
    source = models.CharField(
        max_length=20,
        choices=SessionSource.choices,
        default=SessionSource.MANUAL,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-session_date", "-id"]

    def __str__(self):
        return f"{self.comic} session ({self.issues_read} issues, {self.minutes}m on {self.session_date:%Y-%m-%d %H:%M})"


class MangaSession(models.Model):
    """A chapter read on a given date, with optional reading time."""

    manga = models.ForeignKey(Manga, on_delete=models.CASCADE, related_name="sessions")
    chapter_number = models.PositiveIntegerField()
    minutes = models.PositiveIntegerField(default=0)
    session_date = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-session_date", "-id"]


class BookSession(models.Model):
    """Individual reading session for a book."""

    class SessionSource(models.TextChoices):
        MANUAL = "manual", "Manual"
        IMPORT = "import", "Import"
        HISTORY = "history", "Migrated from History"

    book = models.ForeignKey(
        Book,
        on_delete=models.CASCADE,
        related_name="sessions",
    )
    minutes = models.PositiveIntegerField(help_text="Reading time in minutes for this session")
    percentage_progress = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MaxValueValidator(100)],
        help_text="Optional reading percentage captured during the session",
    )
    session_date = models.DateTimeField(
        default=timezone.now,
        help_text="Timestamp when the session occurred",
    )
    source = models.CharField(
        max_length=20,
        choices=SessionSource.choices,
        default=SessionSource.MANUAL,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-session_date", "-id"]

    def __str__(self):
        return f"{self.book} session ({self.minutes}m on {self.session_date:%Y-%m-%d %H:%M})"
