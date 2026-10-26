"""Resolve personal metadata without changing catalog data or provider caches."""

from copy import copy, deepcopy

from django.db.models import F, OuterRef, Subquery
from django.db.models.functions import Coalesce

from app.models import ItemMetadataOverride


def runtime_expression(user):
    """SQL runtime for a Movie/Episode queryset, including personal corrections."""
    if user is None:
        return F("item__runtime")
    corrections = ItemMetadataOverride.objects.filter(user=user, item_id=OuterRef("item_id"))
    return Coalesce(Subquery(corrections.values("runtime")[:1]), F("item__runtime"))


def user_overrides(user):
    """Load corrections once per request's user object, shared by cards and views."""
    if not user or not user.is_authenticated:
        return {}
    if not hasattr(user, "_item_metadata_overrides"):
        user._item_metadata_overrides = {
            (row.item.source, row.item.media_type, row.item.media_id,
             row.item.season_number, row.item.episode_number): row
            for row in ItemMetadataOverride.objects.filter(user=user).select_related("item")
        }
    return user._item_metadata_overrides


def override_for(item, user):
    def value(name):
        return item.get(name) if isinstance(item, dict) else getattr(item, name, None)

    def number(name):
        raw = value(name)
        return int(raw) if raw not in (None, "") else None

    key = (value("source"), value("media_type"), str(value("media_id")),
           number("season_number"), number("episode_number"))
    return user_overrides(user).get(key)


def display_metadata(item, user):
    """Return a copy with corrected fields; never mutate a shared provider result."""
    correction = override_for(item, user)
    if correction is None:
        return item
    result = deepcopy(item) if isinstance(item, dict) else copy(item)
    for field in ("title", "image", "runtime"):
        value = getattr(correction, field)
        if value is not None:
            if isinstance(result, dict):
                result[field] = value
            else:
                setattr(result, field, value)
    if isinstance(result, dict) and correction.runtime is not None and "details" in result:
        result["details"]["runtime"] = f"{correction.runtime} min"
    return result
