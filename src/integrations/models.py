from django.conf import settings
from django.db import models
from django.utils import timezone


class TubeArchivistDeletion(models.Model):
    """Persist deletion jobs and prevent automatic reimport of deleted videos."""

    class Status(models.TextChoices):
        """External synchronization states."""

        PENDING = "pending", "Pending"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    youtube_id = models.CharField(max_length=11, unique=True)
    title = models.CharField(max_length=255)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
    )
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.PENDING,
    )
    file_deleted = models.BooleanField(default=False)
    ta_completed_at = models.DateTimeField(null=True, blank=True)
    plex_library = models.CharField(max_length=255, blank=True, default="")
    plex_scan_requested_at = models.DateTimeField(null=True, blank=True)
    ignore_requested_at = models.DateTimeField(null=True, blank=True)
    failures = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=255, blank=True)
    next_attempt_at = models.DateTimeField(default=timezone.now, db_index=True)
    locked_until = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Display the most recent operations first."""

        ordering = ["-created_at"]

    def __str__(self):
        """Identify the external video and its synchronization state."""
        return f"{self.youtube_id}: {self.get_status_display()}"
