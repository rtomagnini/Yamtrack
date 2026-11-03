"""Outbox for watched-state delivery to TubeArchivist and Plex."""

# Short redacted API failures are deliberately safe to show on the status page.
# ruff: noqa: EM101, TRY003

import logging
from datetime import timedelta
from functools import partial

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from app.models import MediaTypes, Sources
from integrations.models import TubeArchivistDeletion, YouTubeWatchSync
from integrations.plex_library import PlexError, PlexLibraryClient
from integrations.tubearchivist import (
    TubeArchivistClient,
    TubeArchivistError,
    validate_video_id,
)

logger = logging.getLogger(__name__)
MAX_FAILURES = 8


def enqueue_watch(job_id):
    """Submit immediately; let beat recover broker outages from the outbox."""
    from integrations.tasks import sync_youtube_watched  # noqa: PLC0415

    try:
        sync_youtube_watched.apply_async(args=[job_id], retry=False)
    except Exception:  # noqa: BLE001
        logger.warning("Watched sync %s saved; waiting for periodic delivery.", job_id)


def queue_watched(item, user):
    """Queue only explicit local watched actions, inside their transaction."""
    if (
        not settings.YOUTUBE_WATCH_SYNC_ENABLED
        or item.source != Sources.YOUTUBE.value
        or item.media_type != MediaTypes.EPISODE.value
    ):
        return
    try:
        validate_video_id(item.youtube_video_id)
    except TubeArchivistError:
        logger.warning(
            "YouTube item %s has no valid external video ID; watched locally only.",
            item.pk,
        )
        return
    targets = []
    if settings.TUBEARCHIVIST_URL or settings.TUBEARCHIVIST_TOKEN:
        targets.append(YouTubeWatchSync.Target.TUBEARCHIVIST)
    if settings.PLEX_YOUTUBE_LIBRARY:
        targets.append(YouTubeWatchSync.Target.PLEX)
    for target in targets:
        job, _ = YouTubeWatchSync.objects.get_or_create(
            requested_by=user,
            youtube_id=item.youtube_video_id,
            target=target,
            defaults={
                "title": item.title,
                "plex_library": settings.PLEX_YOUTUBE_LIBRARY
                if target == "plex"
                else "",
            },
        )
        if job.status == YouTubeWatchSync.Status.COMPLETED:
            YouTubeWatchSync.objects.filter(
                pk=job.pk,
                status=YouTubeWatchSync.Status.COMPLETED,
            ).update(
                status=YouTubeWatchSync.Status.PENDING,
                failures=0,
                last_error="",
                plex_library=settings.PLEX_YOUTUBE_LIBRARY if target == "plex" else "",
                plex_rating_key="",
                plex_echo_until=None,
                next_attempt_at=timezone.now(),
                locked_until=timezone.now(),
                updated_at=timezone.now(),
            )
            job.status = YouTubeWatchSync.Status.PENDING
        if job.status == YouTubeWatchSync.Status.PENDING:
            transaction.on_commit(partial(enqueue_watch, job.pk))


def due_watches():
    """Recover pending deliveries whose retry time or worker lease has expired."""
    if not settings.YOUTUBE_WATCH_SYNC_ENABLED:
        return []
    now = timezone.now()
    return (
        YouTubeWatchSync.objects.filter(
            status=YouTubeWatchSync.Status.PENDING,
            next_attempt_at__lte=now,
            locked_until__lte=now,
        )
        .order_by("next_attempt_at")
        .values_list("pk", flat=True)[:100]
    )


def _ensure_not_deleted(video_id):
    if TubeArchivistDeletion.objects.filter(youtube_id=video_id).exists():
        raise TubeArchivistError(
            "Video has a deletion record; watched sync cancelled.",
            permanent=True,
        )


def process_watched(job_id):
    """Claim one destination without blocking or repeating another destination."""
    if not settings.YOUTUBE_WATCH_SYNC_ENABLED:
        return
    now = timezone.now()
    if not YouTubeWatchSync.objects.filter(
        pk=job_id,
        status=YouTubeWatchSync.Status.PENDING,
        next_attempt_at__lte=now,
        locked_until__lte=now,
    ).update(locked_until=now + timedelta(minutes=5)):
        return
    job = YouTubeWatchSync.objects.get(pk=job_id)
    try:
        validate_video_id(job.youtube_id)
        _ensure_not_deleted(job.youtube_id)
        if job.target == YouTubeWatchSync.Target.TUBEARCHIVIST:
            TubeArchivistClient().mark_watched(job.youtube_id)
        else:
            _sync_plex(job)
        job.status = YouTubeWatchSync.Status.COMPLETED
        job.last_error = ""
    except (TubeArchivistError, PlexError) as error:
        job.failures += 1
        job.last_error = str(error)
        if error.permanent or job.failures >= MAX_FAILURES:
            job.status = YouTubeWatchSync.Status.FAILED
        else:
            job.next_attempt_at = now + timedelta(
                seconds=min(30 * 2**job.failures, 3600),
            )
        logger.warning(
            "Watched sync %s (%s): %s",
            job.youtube_id,
            job.target,
            job.last_error,
        )
    except Exception:  # noqa: BLE001
        job.failures += 1
        job.last_error = (
            "Unexpected watched synchronization error. Retry this operation."
        )
        job.status = YouTubeWatchSync.Status.FAILED
        logger.error(  # noqa: TRY400 Credentials can be present in exception text.
            "Unexpected error in watched sync %s (%s).",
            job.youtube_id,
            job.target,
        )
    finally:
        # Do not overwrite the echo guard: the webhook can consume it concurrently.
        job.locked_until = timezone.now()
        job.save(
            update_fields=[
                "status",
                "failures",
                "last_error",
                "next_attempt_at",
                "locked_until",
                "updated_at",
            ],
        )


def _sync_plex(job):
    client = PlexLibraryClient()
    if not job.plex_rating_key:
        job.plex_rating_key = client.resolve_video(job.plex_library, job.youtube_id)
        job.save(update_fields=["plex_rating_key", "updated_at"])
    video = client.video(job.plex_rating_key, job.youtube_id)
    if int(video.get("viewCount") or 0) > 0:
        return
    # Persist before the request, since Plex can call the webhook before returning.
    YouTubeWatchSync.objects.filter(pk=job.pk).update(
        plex_echo_until=timezone.now() + timedelta(minutes=5),
    )
    client.mark_watched(job.plex_rating_key)
    video = client.video(job.plex_rating_key, job.youtube_id)
    if int(video.get("viewCount") or 0) < 1:
        raise PlexError("Plex has not confirmed the watched status.")


def consume_plex_echo(video_id, user, rating_key):
    """Suppress one outbound mark's callback, preserving later real rewatches."""
    return bool(
        YouTubeWatchSync.objects.filter(
            youtube_id=video_id,
            requested_by=user,
            target=YouTubeWatchSync.Target.PLEX,
            plex_rating_key=str(rating_key),
            plex_echo_until__gt=timezone.now(),
        ).update(plex_echo_until=None),
    )
