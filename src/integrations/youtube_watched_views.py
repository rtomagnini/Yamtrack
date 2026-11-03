"""Status and manual retry of outgoing watched-state deliveries."""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from integrations.models import YouTubeWatchSync
from integrations.youtube_watched import enqueue_watch


@login_required
@require_GET
def watched_status(request):
    """Show only the requesting user's outgoing watched deliveries."""
    jobs = YouTubeWatchSync.objects.filter(requested_by=request.user)
    return render(
        request,
        "integrations/youtube_watched.html",
        {
            "jobs": Paginator(jobs, 30).get_page(request.GET.get("page")),
            "enabled": settings.YOUTUBE_WATCH_SYNC_ENABLED,
        },
    )


@login_required
@require_POST
def retry_watched(request, job_id):
    """Retry a failed destination after its user fixes configuration or data."""
    job = get_object_or_404(YouTubeWatchSync, pk=job_id, requested_by=request.user)
    if not settings.YOUTUBE_WATCH_SYNC_ENABLED:
        messages.error(request, "YouTube watched synchronization is disabled.")
    elif YouTubeWatchSync.objects.filter(
        pk=job.pk, status=YouTubeWatchSync.Status.FAILED,
    ).update(
        status=YouTubeWatchSync.Status.PENDING,
        failures=0,
        last_error="",
        plex_library=settings.PLEX_YOUTUBE_LIBRARY if job.target == "plex" else "",
        plex_rating_key="",
        plex_echo_until=None,
        next_attempt_at=timezone.now(),
        locked_until=timezone.now(),
        updated_at=timezone.now(),
    ):
        enqueue_watch(job.pk)
        messages.info(request, "Watched synchronization queued again.")
    return redirect("youtube_watched_status")
