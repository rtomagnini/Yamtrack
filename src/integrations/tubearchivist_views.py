"""Authenticated status and retry controls for shared TA deletions."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from integrations.models import TubeArchivistDeletion
from integrations.tubearchivist import enqueue_deletion, is_enabled


@login_required
@require_GET
def deletion_status(request):
    """Show the shared archive's pending, completed and failed deletions."""
    jobs = Paginator(TubeArchivistDeletion.objects.all(), 30)
    return render(
        request,
        "integrations/tubearchivist.html",
        {
            "jobs": jobs.get_page(request.GET.get("page")),
            "enabled": is_enabled(),
        },
    )


@login_required
@require_POST
def retry_deletion(request, job_id):
    """Allow any authenticated user to retry a failed archive operation."""
    job = get_object_or_404(TubeArchivistDeletion, pk=job_id)
    if not job.ta_completed_at and not is_enabled():
        messages.error(request, "Configure the TubeArchivist URL and API token first.")
    elif TubeArchivistDeletion.objects.filter(
        pk=job.pk,
        status=TubeArchivistDeletion.Status.FAILED,
    ).update(
        status=TubeArchivistDeletion.Status.PENDING,
        failures=0,
        last_error="",
        ignore_requested_at=None,
        next_attempt_at=timezone.now(),
        locked_until=timezone.now(),
        updated_at=timezone.now(),
    ):
        enqueue_deletion(job.pk)
        messages.info(request, "Deletion synchronization queued again.")
    return redirect("tubearchivist_status")
