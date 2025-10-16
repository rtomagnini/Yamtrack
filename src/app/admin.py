import contextlib

from django.apps import apps
from django.contrib import admin
from django.contrib.admin.sites import AlreadyRegistered

from app.models import (
    Episode,
    ExternalIdMapping,
    Item,
)


# Custom ModelAdmin classes with search functionality
class ItemAdmin(admin.ModelAdmin):
    """Custom admin for Item model with search and filter options."""

    search_fields = ["title", "media_id", "source"]
    list_display = [
        "title",
        "media_id",
        "season_number",
        "episode_number",
        "media_type",
        "source",
    ]
    list_filter = ["media_type", "source"]


class EpisodeAdmin(admin.ModelAdmin):
    """Custom admin for Episode model with search and filter options."""

    search_fields = ["item__title", "related_season__item__title"]
    list_display = ["__str__", "end_date"]


class ExternalIdMappingAdmin(admin.ModelAdmin):
    """Custom admin for ExternalIdMapping model."""

    search_fields = ["title", "tmdb_id_plex", "real_tmdb_id"]
    list_display = ["title", "tmdb_id_plex", "real_tmdb_id", "external_source", "media_type", "created_at"]
    list_filter = ["external_source", "media_type", "created_at"]
    readonly_fields = ["created_at", "updated_at"]


class MediaAdmin(admin.ModelAdmin):
    """Custom admin for regular media model with search and filter options."""

    search_fields = ["item__title", "user__username", "notes"]
    list_display = ["__str__", "status", "score", "user"]
    list_filter = ["status"]


# Register models with custom admin classes
admin.site.register(Item, ItemAdmin)
admin.site.register(Episode, EpisodeAdmin)
admin.site.register(ExternalIdMapping, ExternalIdMappingAdmin)


# Auto-register remaining models
app_models = apps.get_app_config("app").get_models()
SpecialModels = ["Item", "Episode", "BasicMedia", "ExternalIdMapping"]
for model in app_models:
    if (
        not model.__name__.startswith("Historical")
        and model.__name__ not in SpecialModels
    ):
        with contextlib.suppress(AlreadyRegistered):
            admin.site.register(model, MediaAdmin)
