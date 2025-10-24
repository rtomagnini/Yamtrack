from django import forms
from django.conf import settings

from app import media_type_config
from app.models import (
    TV,
    Anime,
    Book,
    Comic,
    Episode,
    Game,
    Item,
    Manga,
    MediaTypes,
    Movie,
    Season,
    Sources,
    Status,
)


def get_form_class(media_type):
    """Return the form class for the media type."""
    class_name = media_type.capitalize() + "Form"
    return globals().get(class_name, None)


class CustomDurationField(forms.CharField):
    """Custom form field for duration input that accepts multiple time formats."""

    def _parse_hours_minutes(self, value):
        """Parse hours and minutes from various time formats.

        Supported formats:
        - Plain number (hours only): "5"
        - HH:MM: "5:30"
        - Nh Nmin: "5h 30min"
        - NhNmin: "5h30min"
        - Nmin: "30min"
        - Nh: "5h"
        """
        if value.isdigit():  # hours only
            return int(value), 0

        if ":" in value:  # hh:mm format
            hours, minutes = value.split(":")
            return int(hours), int(minutes)

        if " " in value:  # [n]h [n]min format
            hours, minutes = value.split(" ")
            return int(hours.strip("h")), int(minutes.strip("min"))

        if "h" in value and "min" in value:  # [n]h[n]min format
            hours, minutes = value.split("h")
            return int(hours), int(minutes.strip("min"))

        if "min" in value:  # [n]min format
            return 0, int(value.strip("min"))

        if "h" in value:  # [n]h format
            return int(value.strip("h")), 0
        msg = "Invalid time format"
        raise ValueError(msg)

    def _validate_minutes(self, minutes):
        """Validate that minutes are within acceptable range."""
        max_min = 59
        if not (0 <= minutes <= max_min):
            msg = f"Minutes must be between 0 and {max_min}."
            raise forms.ValidationError(msg)

    def clean(self, value):
        """Validate and convert the time string to total minutes."""
        cleaned_value = super().clean(value)
        if not cleaned_value:
            return 0

        try:
            hours, minutes = self._parse_hours_minutes(cleaned_value)
            self._validate_minutes(minutes)
            return hours * 60 + minutes
        except ValueError as e:
            msg = "Invalid time played format. Please use hh:mm, [n]h [n]min or [n]h[n]min format."  # noqa: E501
            raise forms.ValidationError(msg) from e


class ManualItemForm(forms.ModelForm):
    """Form for adding items to the database."""

    parent_tv = forms.ModelChoiceField(
        required=False,
        queryset=TV.objects.none(),
        empty_label="Select",
        label="Parent TV Show",
    )

    parent_season = forms.ModelChoiceField(
        required=False,
        queryset=Season.objects.none(),
        empty_label="Select",
        label="Parent Season",
    )

    youtube_url = forms.URLField(
        required=False,
        label="YouTube URL",
        help_text="Paste a YouTube URL to automatically extract episode information",
        widget=forms.URLInput(attrs={
            "placeholder": "https://www.youtube.com/watch?v=...",
            "class": "youtube-url-input",
        }),
    )

    channel_url = forms.URLField(
        required=False,
        label="YouTube Channel URL",
        help_text="Paste a YouTube channel URL to automatically extract channel information",
        widget=forms.URLInput(attrs={
            "placeholder": "https://www.youtube.com/@channelname",
            "class": "youtube-channel-input",
        }),
    )

    class Meta:
        """Bind form to model."""

        model = Item
        fields = [
            "media_type",
            "title",
            "image",
            "season_number",
            "episode_number",
            "air_date",
            "runtime",
        ]
        widgets = {
            "air_date": forms.DateInput(attrs={"type": "date"}),
            "runtime": forms.NumberInput(attrs={"placeholder": "Duration in minutes"}),
        }

    def __init__(self, *args, **kwargs):
        """Initialize the form."""
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        if self.user:
            self.fields["parent_tv"].queryset = TV.objects.filter(
                user=self.user,
                item__source=Sources.MANUAL.value,
                item__media_type=MediaTypes.TV.value,
            )
            self.fields["parent_season"].queryset = Season.objects.filter(
                user=self.user,
                item__source=Sources.MANUAL.value,
                item__media_type=MediaTypes.SEASON.value,
            ).order_by('item__season_number', 'item__title')
        self.fields["image"].required = False
        self.fields["title"].required = False

    def clean(self):
        """Validate the form."""
        cleaned_data = super().clean()
        image = cleaned_data.get("image")
        media_type = cleaned_data.get("media_type")

        if not image:
            cleaned_data["image"] = settings.IMG_NONE

        # Title not required for season/episode
        if media_type in [MediaTypes.SEASON.value, MediaTypes.EPISODE.value]:
            if media_type == MediaTypes.SEASON.value:
                parent = cleaned_data.get("parent_tv")
                if not parent:
                    self.add_error(
                        "parent_tv",
                        "Parent TV show is required for seasons",
                    )
                    return cleaned_data
                cleaned_data["title"] = parent.item.title
                cleaned_data["episode_number"] = None
            else:  # episode
                parent = cleaned_data.get("parent_season")
                if not parent:
                    self.add_error(
                        "parent_season",
                        "Parent season is required for episodes",
                    )
                    return cleaned_data
                # Keep the episode title as provided by user, don't override with parent title
                if not cleaned_data.get("title"):
                    self.add_error("title", "Episode title is required")
                cleaned_data["season_number"] = parent.item.season_number
        elif media_type == MediaTypes.YOUTUBE.value:
            # For YouTube channels, title is required (will be auto-filled from channel URL)
            if not cleaned_data.get("title"):
                self.add_error("title", "Channel title is required")
            cleaned_data["season_number"] = None
            cleaned_data["episode_number"] = None
        else:
            # For standalone media, title is required
            if not cleaned_data.get("title"):
                self.add_error("title", "Title is required for this media type")
            cleaned_data["season_number"] = None
            cleaned_data["episode_number"] = None

        return cleaned_data

    def save(self, commit=True):  # noqa: FBT002
        """Save the form and handle manual media ID generation."""
        # Handle YouTube URL if provided for episodes
        youtube_url = self.cleaned_data.get("youtube_url")
        if youtube_url and self.cleaned_data.get("media_type") == MediaTypes.EPISODE.value:
            # Set the source to Manual for episodes with YouTube URLs (custom episodes)
            self.cleaned_data["source"] = Sources.MANUAL.value
        
        # Handle YouTube Channel URL if provided for YouTube media type
        channel_url = self.cleaned_data.get("channel_url")
        if channel_url and self.cleaned_data.get("media_type") == MediaTypes.YOUTUBE.value:
            # Set the source to YouTube for channels
            self.cleaned_data["source"] = Sources.YOUTUBE.value
        
        # Remove non-model fields from cleaned_data
        if "youtube_url" in self.cleaned_data:
            del self.cleaned_data["youtube_url"]
        if "channel_url" in self.cleaned_data:
            del self.cleaned_data["channel_url"]
        
        instance = super().save(commit=False)
        instance.source = self.cleaned_data.get("source", Sources.MANUAL.value)

        if instance.media_type == MediaTypes.SEASON.value:
            parent_tv = self.cleaned_data["parent_tv"]
            instance.media_id = parent_tv.item.media_id
        elif instance.media_type == MediaTypes.EPISODE.value:
            parent_season = self.cleaned_data["parent_season"]
            instance.media_id = parent_season.item.media_id
            instance.season_number = parent_season.item.season_number
        elif instance.media_type == MediaTypes.YOUTUBE.value:
            # For YouTube channels, use auto-generated media_id specific to YouTube source
            instance.media_id = Item.generate_next_id(Sources.YOUTUBE.value, instance.media_type)
        elif instance.media_type == MediaTypes.YOUTUBE_VIDEO.value:
            # YouTube videos will be handled by special logic in views.py
            # This is just a placeholder - the real logic is in create_entry view
            instance.media_id = Item.generate_next_id(Sources.YOUTUBE.value, MediaTypes.EPISODE.value)
        else:
            instance.media_id = Item.generate_manual_id(instance.media_type)

        if commit:
            instance.save()
        return instance


class MediaForm(forms.ModelForm):
    """Base form for all media types."""

    instance_id = forms.CharField(widget=forms.HiddenInput(), required=False)
    media_type = forms.CharField(widget=forms.HiddenInput(), required=True)
    source = forms.CharField(widget=forms.HiddenInput(), required=True)
    media_id = forms.CharField(widget=forms.HiddenInput(), required=True)

    class Meta:
        """Define fields and input types."""

        fields = [
            "score",
            "progress",
            "status",
            "start_date",
            "end_date",
            "notes",
        ]
        widgets = {
            "score": forms.NumberInput(
                attrs={"min": 0, "max": 10, "step": 0.1, "placeholder": "0-10"},
            ),
            "progress": forms.NumberInput(attrs={"min": 0}),
            "start_date": forms.DateTimeInput(attrs={"type": "datetime-local"})
            if settings.TRACK_TIME
            else forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateTimeInput(attrs={"type": "datetime-local"})
            if settings.TRACK_TIME
            else forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(
                attrs={"placeholder": "Add any notes or comments...", "rows": "5"},
            ),
        }


class MangaForm(MediaForm):
    """Form for manga."""

    class Meta(MediaForm.Meta):
        """Bind form to model."""

        model = Manga
        labels = {
            "progress": (
                f"Progress "
                f"({media_type_config.get_unit(MediaTypes.MANGA.value, short=False)}s)"
            ),
        }


class AnimeForm(MediaForm):
    """Form for anime."""

    class Meta(MediaForm.Meta):
        """Bind form to model."""

        model = Anime


class MovieForm(MediaForm):
    """Form for movies."""

    class Meta(MediaForm.Meta):
        """Bind form to model."""

        model = Movie
        fields = [
            "score",
            "status",
            "start_date",
            "end_date",
            "notes",
        ]


class GameForm(MediaForm):
    """Form for games."""

    progress = CustomDurationField(
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "hh:mm"}),
        label="Progress (Time Played)",
    )

    class Meta(MediaForm.Meta):
        """Bind form to model."""

        model = Game


class BookForm(MediaForm):
    """Form for books."""

    class Meta(MediaForm.Meta):
        """Bind form to model."""

        model = Book
        labels = {
            "progress": (
                f"Progress "
                f"({media_type_config.get_unit(MediaTypes.BOOK.value, short=False)}s)"
            ),
        }


class ComicForm(MediaForm):
    """Form for comics."""

    class Meta(MediaForm.Meta):
        """Bind form to model."""

        model = Comic
        labels = {
            "progress": (
                f"Progress "
                f"({media_type_config.get_unit(MediaTypes.COMIC.value, short=False)}s)"
            ),
        }


class TvForm(MediaForm):
    """Form for TV shows."""

    class Meta(MediaForm.Meta):
        """Bind form to model."""

        model = TV
        fields = ["score", "status", "notes"]


class SeasonForm(MediaForm):
    """Form for seasons."""

    season_number = forms.IntegerField(widget=forms.HiddenInput(), required=False, min_value=1, max_value=9999)

    class Meta(MediaForm.Meta):
        """Bind form to model."""

        model = Season
        fields = [
            "score",
            "status",
            "notes",
        ]

    def save(self, commit=True):
        """Save season with intelligent status assignment."""
        season = super().save(commit=False)
        
        # If this is a new season (no pk yet) and status is PLANNING,
        # check if parent TV series is completed
        if not season.pk and season.status == Status.PLANNING.value:
            try:
                # Try to find existing TV instance for this series
                tv_instance = TV.objects.get(
                    item__media_id=season.item.media_id,
                    item__source=season.item.source,
                    item__media_type=MediaTypes.TV.value,
                    user=season.user,
                )
                
                # If TV series is completed, set new season to IN_PROGRESS
                if tv_instance.status == Status.COMPLETED.value:
                    season.status = Status.IN_PROGRESS.value
                    
            except TV.DoesNotExist:
                # No TV instance exists yet, keep original status
                pass
        
        if commit:
            season.save()
        return season


class EpisodeForm(forms.Form):
    """Form for episodes - episodes don't have status, they're just created."""
    
    pass  # Episodes don't need additional fields beyond the base item creation


class EpisodeTrackingForm(forms.ModelForm):
    """Form for tracking episodes (marking as watched)."""

    class Meta:
        """Bind form to model."""

        model = Episode
        fields = ("end_date",)
        widgets = {
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        """Initialize the form."""
        super().__init__(*args, **kwargs)

        if settings.TRACK_TIME:
            self.fields["end_date"].widget = forms.DateTimeInput(
                attrs={"type": "datetime-local"},
            )
        else:
            self.fields["end_date"].widget = forms.DateInput(
                attrs={"type": "date"},
            )


class YoutubeForm(MediaForm):
    """Form for YouTube channels."""

    class Meta(MediaForm.Meta):
        """Bind form to model."""

        model = TV
        fields = ["score", "status", "notes"]
