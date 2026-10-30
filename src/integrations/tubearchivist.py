"""TubeArchivist v0.6.0 API client and durable deletion processing."""

# API errors deliberately carry short, redacted user-facing messages.
# ruff: noqa: EM101, EM102, TRY003

import logging
import re
from datetime import timedelta
from http import HTTPStatus
from urllib.parse import urlsplit

import requests
from django.conf import settings
from django.utils import timezone

from integrations.models import TubeArchivistDeletion
from integrations.plex_library import PlexError, PlexLibraryClient

logger = logging.getLogger(__name__)
MAX_FAILURES = 8


class TubeArchivistError(Exception):
    """An API failure safe to display without exposing credentials."""

    def __init__(self, message, *, permanent=False):
        """Attach whether a failure needs configuration changes or manual retry."""
        super().__init__(message)
        self.permanent = permanent


def is_enabled():
    """Return whether both connection settings have been supplied."""
    return bool(settings.TUBEARCHIVIST_URL and settings.TUBEARCHIVIST_TOKEN)


class TubeArchivistClient:
    """Send authenticated requests only to the configured TA server."""

    def __init__(self):
        """Validate connection settings without making an API request."""
        url = settings.TUBEARCHIVIST_URL.strip().rstrip("/")
        parsed = urlsplit(url)
        if (
            not is_enabled()
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
        ):
            raise TubeArchivistError(
                "Configure TUBEARCHIVIST_URL and TUBEARCHIVIST_TOKEN.",
                permanent=True,
            )
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise TubeArchivistError("Invalid TubeArchivist URL.", permanent=True)
        self.url = url

    def request(self, method, path, *, data=None, allow_missing=False):
        """Validate API responses and redact server bodies and transport errors."""
        try:
            response = requests.request(
                method,
                f"{self.url}/api/{path}",
                headers={"Authorization": f"Token {settings.TUBEARCHIVIST_TOKEN}"},
                json=data,
                timeout=(5, 15),
                allow_redirects=False,
            )
        except requests.RequestException:
            raise TubeArchivistError("Cannot connect to TubeArchivist.") from None
        if response.status_code == HTTPStatus.NOT_FOUND and allow_missing:
            return None
        if response.status_code not in {200, 201, 202, 204}:
            raise TubeArchivistError(
                f"TubeArchivist returned HTTP {response.status_code}.",
                permanent=response.status_code in {400, 401, 403, 405}
                or HTTPStatus.MULTIPLE_CHOICES
                <= response.status_code
                < HTTPStatus.BAD_REQUEST,
            )
        if method == "GET":
            try:
                result = response.json()
            except ValueError:
                raise TubeArchivistError(
                    "Invalid TubeArchivist JSON response.",
                ) from None
            if not isinstance(result, dict):
                raise TubeArchivistError("Unexpected TubeArchivist response.")
            return result
        return True


def validate_video_id(video_id):
    """Reject local IDs and malformed external identifiers before deleting."""
    if not video_id or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise TubeArchivistError(
            "This video has no valid YouTube video ID.",
            permanent=True,
        )


def enqueue_deletion(job_id):
    """Attempt immediate delivery; the periodic sweep recovers broker outages."""
    from integrations.tasks import sync_tubearchivist_deletion  # noqa: PLC0415

    try:
        sync_tubearchivist_deletion.apply_async(args=[job_id], retry=False)
    except Exception:  # noqa: BLE001
        logger.warning("TA deletion %s saved; waiting for periodic delivery.", job_id)


def process_deletion(job_id):
    """Claim a job with a lease and resume its delete-and-ignore operation."""
    now = timezone.now()
    claimed = TubeArchivistDeletion.objects.filter(
        pk=job_id,
        status=TubeArchivistDeletion.Status.PENDING,
        next_attempt_at__lte=now,
        locked_until__lte=now,
    ).update(locked_until=now + timedelta(minutes=3))
    if not claimed:
        return
    job = TubeArchivistDeletion.objects.get(pk=job_id)
    try:
        _sync(job)
    except (TubeArchivistError, PlexError) as error:
        job.failures += 1
        job.last_error = str(error)
        if error.permanent or job.failures >= MAX_FAILURES:
            job.status = TubeArchivistDeletion.Status.FAILED
        else:
            job.next_attempt_at = now + timedelta(
                seconds=min(30 * 2**job.failures, 3600),
            )
        logger.warning("TA deletion %s: %s", job.youtube_id, job.last_error)
    except Exception:  # noqa: BLE001
        # Release the lease without storing exception text that may contain secrets.
        job.failures += 1
        job.last_error = "Unexpected synchronization error. Retry this operation."
        job.status = TubeArchivistDeletion.Status.FAILED
        logger.error("Unexpected error in TA deletion %s.", job.youtube_id)  # noqa: TRY400
    finally:
        job.locked_until = timezone.now()
        job.save()


def _sync(job):
    """Follow TA's Delete and Ignore flow, verifying the asynchronous ignore."""
    if job.ta_completed_at:
        _sync_plex(job)
        return
    validate_video_id(job.youtube_id)
    client = TubeArchivistClient()
    video_path = f"video/{job.youtube_id}/"
    queue_path = f"download/{job.youtube_id}/"
    # DELETE is repeatable: a missing video is already in the desired state.
    client.request("DELETE", video_path, allow_missing=True)
    job.file_deleted = True
    queued = client.request("GET", queue_path, allow_missing=True)
    if queued and queued.get("status") == "ignore":
        if client.request("GET", video_path, allow_missing=True) is not None:
            raise TubeArchivistError("Video still exists in TubeArchivist.")
        job.ta_completed_at = timezone.now()
        job.failures = 0
        job.last_error = ""
        # Commit the TA checkpoint before contacting Plex, so retries and crashes
        # cannot repeat a completed deletion even when Plex is unavailable.
        job.save(
            update_fields=[
                "ta_completed_at",
                "file_deleted",
                "failures",
                "last_error",
                "updated_at",
            ],
        )
        _sync_plex(job)
        return
    now = timezone.now()
    if job.ignore_requested_at is None:
        client.request("POST", queue_path, data={"status": "ignore-force"})
        job.ignore_requested_at = now
    elif now - job.ignore_requested_at > timedelta(minutes=10):
        raise TubeArchivistError(
            "TubeArchivist did not confirm the ignored status within 10 minutes.",
            permanent=True,
        )
    job.last_error = "Waiting for TubeArchivist to confirm ignored status."
    job.next_attempt_at = now + timedelta(seconds=30)


def _sync_plex(job):
    """Finish the optional Plex step independently of TA's credentials or state."""
    if job.plex_library and not job.plex_scan_requested_at:
        PlexLibraryClient().scan(job.plex_library)
        job.plex_scan_requested_at = timezone.now()
    job.status = TubeArchivistDeletion.Status.COMPLETED
    job.last_error = ""


def due_deletions():
    """Return pending jobs whose delivery or worker lease has expired."""
    now = timezone.now()
    return (
        TubeArchivistDeletion.objects.filter(
            next_attempt_at__lte=now,
            status=TubeArchivistDeletion.Status.PENDING,
            locked_until__lte=now,
        )
        .order_by("next_attempt_at")
        .values_list("pk", flat=True)[:100]
    )
