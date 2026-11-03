# TubeArchivist Delete + Ignore

Supported API: TubeArchivist **v0.6.0**. Any authenticated Yamtrack user can
delete a YouTube video from the shared TubeArchivist archive. An optional final
step scans the Plex YouTube library. Watched-state synchronization uses a
separate durable queue, described below.

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
reported as completion. The deletion worker never calls the watched API. TA deletes the media
file and metadata, not just a Yamtrack tracking entry.

The durable record also blocks later Tautulli automatic imports, regardless of
the requesting user or synchronization status. Keep completed records to retain
this protection. This does not block deliberate manual additions in Yamtrack.

Open **Settings → Integrations → TubeArchivist and Plex → View deletion status** to see all shared jobs and
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

## Optional Plex scan

Plex reads the same folder as TA. Enable scans for new deletion jobs with these
additional values in the deployment `.env`:

```dotenv
PLEX_SERVER_URL=http://192.168.68.142:32400
PLEX_TOKEN=replace-with-your-owner-token
PLEX_YOUTUBE_LIBRARY=Youtube
```

The default compose file passes all three variables through. Configure them on
separate workers too. Leaving `PLEX_YOUTUBE_LIBRARY` empty disables this step.
Apply migration `integrations.0002` before starting the new web/worker version.
Old jobs keep their original behavior; enabling Plex does not rescan previous
deletions. Each new job records its target library name.

After TA confirms the video is deleted and ignored, Yamtrack persists that
checkpoint and looks up the named Plex library with `GET /library/sections`.
There must be exactly one match (case insensitive). Yamtrack requests a file
scan with `POST /library/sections/SECTION_ID/refresh`, without forcing a metadata
refresh, scanning other libraries, or issuing a delete/empty-trash command.

Plex's **Empty trash automatically after every scan** setting must be enabled
to remove the missing video's entry after the scan. The status page says
**Scan requested** once Plex accepts the request; it does not claim the
asynchronous scan has finished or that the entry has already disappeared.

If Plex is unavailable, the same persistent job retries the Plex step. The
completed TA operation is not repeated, including after restarts or manual
retries. Invalid credentials or missing/duplicate libraries show a failure on
the status page. Correct the configuration and use **Retry**. A Plex-only retry
does not require TA to be online or its token to remain configured. Tautulli is
not required for this step.

## Watched synchronization from Yamtrack

An optional third destination sends new watched **and deleted** YouTube videos
to your Invidious account's history. See [Invidious setup](INVIDIOUS_INTEGRATION.md)
for the URL/token configuration and required permission. This destination is
independent of TA/Plex and can run after local/TA deletion. No old records are
exported automatically.

Marking a YouTube video as watched in Yamtrack also queues its watched state
for each configured destination. This applies to the individual video watched
buttons in the channel page and the YouTubes video list. It uses the video's
`youtube_video_id`, never its local episode number or title. Existing history
is not exported retroactively. Unmarking or deleting a local viewing does not
clear the watched flag in the external services.

No additional credentials are required:

- TubeArchivist uses `TUBEARCHIVIST_URL` and `TUBEARCHIVIST_TOKEN`.
- Plex uses `PLEX_SERVER_URL`, `PLEX_TOKEN` and `PLEX_YOUTUBE_LIBRARY`.
- Set `YOUTUBE_WATCH_SYNC_ENABLED=false` on web and workers to disable only
  outgoing watched synchronization, keeping deletion behavior unchanged.

The watched state belongs to the external accounts authenticated by those
tokens. There is no mapping to individual external accounts for multiple
Yamtrack users. TubeArchivist may also apply its configured automatic deletion
policy to watched videos.

Yamtrack saves the local viewing and independent durable delivery records in
one transaction. It contacts neither external server while handling the click.
After commit, Celery sends and verifies each destination independently:

1. TubeArchivist: read `GET /api/video/VIDEO_ID/`, then, if needed, send
   `POST /api/watched/` with `{"id":"VIDEO_ID","is_watched":true}` and verify.
2. Plex: resolve exactly one configured library, page through its leaf videos,
   match the complete YouTube ID in the media filename, and resolve its
   `ratingKey`. Missing videos are retried; ambiguous matches require manual
   intervention. It reads the video's state before sending
   `GET /:/scrobble?key=RATING_KEY&identifier=com.plexapp.plugins.library`,
   then reads again to confirm. Already watched items are not scrobbled again.

When an outgoing Plex scrobble causes a webhook, a short-lived, one-use guard
matches its user, YouTube ID and rating key and prevents a duplicate viewing
when the callback carries no real player/session information.
Incoming webhooks never enqueue outgoing watched jobs. Later genuine playback
can still add another viewing.

Open **Settings → Integrations → TubeArchivist and Plex → View watched status**
(`/youtube-watched/`) for your pending, completed and failed deliveries and
manual **Retry**. A successful destination is not repeated when the other
fails. Jobs retry after connection/server errors (up to eight failures) and
survive worker restarts or broker outages; beat recovers them every 30 seconds.
Authentication and missing TA video errors require manual retry after fixing
the cause. Invalid or missing YouTube IDs are only marked locally. A saved
deletion record prevents a queued watched delivery from acting on that video.

This feature adds migration `integrations.0003_youtubewatchsync`. Deploy the
image, allow startup migrations to run and restart the web, worker and beat
processes. Test with an existing video in both external services and inspect
both destination rows. Automated tests mock HTTP and never change live watch
state.

## References

- [API authentication](https://docs.tubearchivist.com/api/introduction/)
- [v0.6.0 video API](https://github.com/tubearchivist/tubearchivist/blob/v0.6.0/backend/video/views.py)
- [v0.6.0 download API](https://github.com/tubearchivist/tubearchivist/blob/v0.6.0/backend/download/views.py)
- [v0.6.0 watched API](https://github.com/tubearchivist/tubearchivist/blob/v0.6.0/backend/common/views.py)
- [PlexAPI watched requests](https://github.com/pkkid/python-plexapi/blob/master/plexapi/mixins/played_unplayed.py)
- [Plex library API](https://developer.plex.tv/pms/#tag/Library)
- [Plex library trash](https://support.plex.tv/articles/200289326-emptying-library-trash/)
