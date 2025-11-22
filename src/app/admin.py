import contextlib

from django.apps import apps
from django.contrib import admin
from django.contrib.admin.sites import AlreadyRegistered

from app.models import (
    Episode,
    ExternalIdMapping,
    Item,
    YouTubeChannelFilter,
    Game,
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


class YouTubeChannelFilterAdmin(admin.ModelAdmin):
    """Custom admin for YouTube Channel Filter model."""

    search_fields = ["channel_name", "channel_id", "user__username"]
    list_display = ["channel_name", "channel_id", "user", "created_at"]
    list_filter = ["user", "created_at"]
    readonly_fields = ["created_at"]
    autocomplete_fields = ["user"]



# Register models with custom admin classes
admin.site.register(Item, ItemAdmin)
admin.site.register(Episode, EpisodeAdmin)
admin.site.register(ExternalIdMapping, ExternalIdMappingAdmin)
admin.site.register(YouTubeChannelFilter, YouTubeChannelFilterAdmin)

# Custom admin for Season to expose broadcast_time
from app.models import Season
class SeasonAdmin(MediaAdmin):
    fieldsets = (
        (None, {
            'fields': ('item', 'related_tv', 'status', 'score', 'notes', 'broadcast_time')
        }),
    )
    list_display = MediaAdmin.list_display + ["broadcast_time"]

try:
    admin.site.unregister(Season)
except Exception:
    pass
admin.site.register(Season, SeasonAdmin)


# Custom admin for HistoricalGame to manage game sessions
class HistoricalGameAdmin(admin.ModelAdmin):
    list_display = ["history_id", "get_game_title", "get_username", "play_time", "progress", "history_date", "history_type"]
    list_filter = ["history_type", "history_date", "history_user"]
    search_fields = ["history_id"]
    ordering = ["-history_date"]
    list_select_related = False
    actions = ['delete_selected']
    
    def get_actions(self, request):
        """Ensure delete action is available."""
        actions = super().get_actions(request)
        return actions
    
    def get_game_title(self, obj):
        """Get title from the Game id."""
        try:
            if obj.id:
                game = Game.objects.filter(id=obj.id).select_related('item').first()
                return game.item.title if game else f"Game ID: {obj.id} (deleted)"
            return "N/A"
        except Exception as e:
            return f"Error: {str(e)}"
    get_game_title.short_description = "Game Title"
    
    def get_username(self, obj):
        """Get username from history_user."""
        try:
            if obj.history_user:
                return obj.history_user.username
            return "N/A"
        except Exception as e:
            return f"Error: {str(e)}"
    get_username.short_description = "User"
    
    def has_add_permission(self, request):
        """Prevent adding historical records manually."""
        return False
    
    def has_change_permission(self, request, obj=None):
        """Allow viewing but not changing - required for checkboxes to show."""
        if obj is None:
            # Allow list view with checkboxes
            return True
        # But don't allow editing individual objects
        return False
    
    def has_delete_permission(self, request, obj=None):
        """Allow deleting historical records."""
        return True
    
    def change_view(self, request, object_id, form_url='', extra_context=None):
        """Redirect change view to list view to prevent editing."""
        from django.http import HttpResponseRedirect
        from django.urls import reverse
        return HttpResponseRedirect(reverse('admin:app_historicalgame_changelist'))

# Register HistoricalGame
admin.site.register(Game.history.model, HistoricalGameAdmin)


# Auto-register remaining models
app_models = apps.get_app_config("app").get_models()
SpecialModels = ["Item", "Episode", "BasicMedia", "ExternalIdMapping", "YouTubeChannelFilter"]
for model in app_models:
    if (
        not model.__name__.startswith("Historical")
        and model.__name__ not in SpecialModels
    ):
        with contextlib.suppress(AlreadyRegistered):
            admin.site.register(model, MediaAdmin)
