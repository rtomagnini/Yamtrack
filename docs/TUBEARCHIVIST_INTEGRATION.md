# TubeArchivist Delete + Ignore

Supported API: TubeArchivist **v0.6.0**. Any authenticated Yamtrack user can
delete a YouTube video from the shared TubeArchivist archive. Plex updates and
watched-state synchronization are outside this integration.

## Configuration

Add these variables to the Yamtrack service's environment (and to separate
Celery worker/beat services if your deployment uses them):

```yaml
environment:
  TUBEARCHIVIST_URL: http://192.168.68.106:8770
  TUBEARCHIVIST_TOKEN: ${TUBEARCHIVIST_TOKEN}
```

The default `docker-compose.yml` already passes both variables through. For it,
set both values in a private, untracked deployment `.env` file:

```dotenv
TUBEARCHIVIST_URL=http://192.168.68.106:8770
TUBEARCHIVIST_TOKEN=replace-with-your-token
```

Obtain the token in TA under **Settings → Application → Integrations → API Token**,
using an administrator account. Alternatively use `TUBEARCHIVIST_TOKEN_FILE`
with a Docker secret, following Yamtrack's existing secret-file convention.
The URL must be reachable from the Yamtrack worker container.

Apply migrations using `python manage.py migrate` from `/yamtrack` (or your
source deployment's `src` directory), then restart the web, worker and beat
processes. Deploy an image built from this feature branch; the current master
image does not contain this feature. Celery **worker and beat** must be running.

Both variables empty disables external synchronization and preserves the
existing local delete behavior. A partial configuration blocks new deletes.
No token is rendered in templates or stored in job records/log messages.

## Behavior

The existing YouTube Delete buttons save a persistent deletion record in the
same transaction as the local deletion. After commit, Celery sends:

1. `DELETE /api/video/VIDEO_ID/` (404 is accepted as already deleted).
2. `GET /api/download/VIDEO_ID/` to check the ignore record.
3. If needed, `POST /api/download/VIDEO_ID/` with `{"status":"ignore-force"}`.
4. Poll until `status=ignore`, then verify `GET /api/video/VIDEO_ID/` is 404.

TA creates the ignore entry asynchronously. A successful POST alone is not
reported as completion. No watched-state API is called. TA deletes the media
file and metadata, not just a Yamtrack tracking entry.

The durable record also blocks later Tautulli automatic imports, regardless of
the requesting user or synchronization status. Keep completed records to retain
this protection. This does not block deliberate manual additions in Yamtrack.

Open **YouTube → TubeArchivist deletion status** to see all shared jobs and
retry failures. The URL is `/tubearchivist/` when installed at the
root. Connection/server failures retry automatically with exponential backoff
(up to eight failures). Authentication/configuration errors require correction
and a manual retry. Ignore confirmation times out after ten minutes; check TA's
worker and retry from the status page. Pending jobs survive restarts and broker
outages; beat checks for due jobs every 30 seconds. A three-minute lease prevents
parallel workers from processing the same job.

If TA deletes the file but cannot create the ignore record, the job remains
pending or failed until corrected. During that interval a TA subscription scan
could queue the video again. Check failed jobs; Yamtrack cannot make these two
TA operations atomic. Do not delete files directly or remove TA's ignored queue
entry, as that would defeat the ignore protection.

TA's `ignore-force` task may need to fetch metadata from YouTube. If a removed or
private YouTube video cannot be added to the ignore list, the operation will
remain failed rather than claiming success. Resolve the ignore entry in TA and
retry to confirm it.

## References

- [API authentication](https://docs.tubearchivist.com/api/introduction/)
- [v0.6.0 video API](https://github.com/tubearchivist/tubearchivist/blob/v0.6.0/backend/video/views.py)
- [v0.6.0 download API](https://github.com/tubearchivist/tubearchivist/blob/v0.6.0/backend/download/views.py)
