"""Editing personal movie and episode metadata."""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods

from app.forms import ItemMetadataForm
from app.models import Item, ItemMetadataOverride, MediaTypes, Movie, Season, Sources
from app.providers import services


@login_required
@require_http_methods(["GET", "POST"])
def edit_metadata(request, source, media_type, media_id):
    if media_type not in (MediaTypes.MOVIE.value, MediaTypes.EPISODE.value):
        raise Http404
    if source not in (Sources.TMDB.value, Sources.MANUAL.value):
        raise Http404

    season_number = episode_number = None
    if media_type == MediaTypes.EPISODE.value:
        try:
            season_number = int(request.GET.get("season_number", ""))
            episode_number = int(request.GET.get("episode_number", ""))
        except (ValueError, TypeError):
            raise Http404 from None
        if season_number < 0 or episode_number < 1:
            raise Http404

    identity = dict(source=source, media_type=media_type, media_id=media_id,
                    season_number=season_number, episode_number=episode_number)
    item = Item.objects.filter(**identity).first()
    if source == Sources.MANUAL.value:
        if media_type == MediaTypes.MOVIE.value:
            allowed = item and Movie.objects.filter(user=request.user, item=item).exists()
        else:
            allowed = item and Season.objects.filter(
                user=request.user, item__source=source, item__media_id=media_id,
                item__season_number=season_number,
            ).exists()
        if not allowed:
            raise Http404

    try:
        metadata = services.get_media_metadata(
            media_type, media_id, source,
            [season_number] if season_number is not None else None, episode_number,
        )
    except services.ProviderAPIError:
        if item is None:
            raise
        metadata = dict(title=item.title, image=item.image, runtime=item.runtime)
    if metadata is None:
        raise Http404

    original = {
        "title": metadata.get("episode_title", metadata["title"]),
        "image": metadata.get("image") or settings.IMG_NONE,
        "runtime": metadata.get("runtime"),
    }
    if original["runtime"] is None and item is not None and item.runtime is not None:
        original["runtime"] = item.runtime
    correction = ItemMetadataOverride.objects.filter(user=request.user, item=item).first() if item else None
    initial = {
        field: getattr(correction, field) if correction and getattr(correction, field) is not None else value
        for field, value in original.items()
    }

    next_url = request.POST.get("next") or request.GET.get("next") or reverse("home")
    if not url_has_allowed_host_and_scheme(next_url, {request.get_host()}, require_https=request.is_secure()):
        next_url = reverse("home")

    form = ItemMetadataForm(request.POST if request.method == "POST" else None, initial=initial)
    if request.method == "POST":
        if request.POST.get("action") == "reset":
            if correction:
                correction.delete()
            messages.success(request, "Original metadata restored.")
            return redirect(next_url)
        if form.is_valid():
            values = {
                field: value if value not in (None, "") and value != original[field] else None
                for field, value in form.cleaned_data.items()
            }
            with transaction.atomic():
                # Refresh originals too, so accepting a known provider duration
                # fills a missing catalog runtime used by the statistics.
                if item is not None or any(value is not None for value in values.values()):
                    item = Item.objects.update_or_create(**identity, defaults=original)[0]
                if any(value is not None for value in values.values()):
                    ItemMetadataOverride.objects.update_or_create(user=request.user, item=item, defaults=values)
                elif correction:
                    correction.delete()
            messages.success(request, "Metadata saved.")
            return redirect(next_url)

    return render(request, "app/edit_metadata.html", {
        "form": form, "original": original, "next_url": next_url,
        "has_correction": correction is not None,
    })
