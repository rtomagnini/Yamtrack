# Invidious watched history

Only **new** YouTube watched and delete actions in Yamtrack are exported.
Existing viewing history and deletion records are not scanned or backfilled.

## Generate the token

Log into your Invidious account and keep watch history enabled. For the current
deployment, open this link in that same browser and authorize the token:

<http://192.168.68.106:3001/authorize_token?scopes=POST%3Ahistory%2F%2A>

The only requested permission is `POST:history/*`. No callback or password is
sent to Yamtrack. Copy the **entire JSON token** shown after authorization,
including its `session`, `scopes`, `signature` and any `expire` field. The
session ID alone is not an API token. Invidious also supports revoking this
token from `/token_manager`.

Keep the token in a private deployment `.env` file, outside Git:

```dotenv
INVIDIOUS_URL=http://192.168.68.106:3001
INVIDIOUS_TOKEN='{"session":"v1:REPLACE","scopes":["POST:history/*"],"signature":"REPLACE"}'
```

The example values are placeholders, not valid credentials. Pass them to the
Yamtrack web, Celery worker and beat services:

```yaml
environment:
  INVIDIOUS_URL: ${INVIDIOUS_URL:-}
  INVIDIOUS_TOKEN: ${INVIDIOUS_TOKEN:-}
```

Alternatively set `INVIDIOUS_TOKEN_FILE` to a Docker-secret file containing the
complete token. The client accepts plain JSON, multiline JSON secret files or
URL-encoded JSON. It sends a normalized URL-encoded token in the bearer header,
which is the format accepted by Invidious's authentication middleware. The
token is never stored in delivery jobs, templates, URLs or error messages.

When both settings are empty, this destination is disabled. With a partial
configuration, new delivery records remain available as failures to retry after
fixing configuration; local watched and delete actions still work. The existing
TubeArchivist partial-configuration validation for deletion still applies.
`YOUTUBE_WATCH_SYNC_ENABLED=false` disables outgoing watched deliveries to all
destinations, including Invidious updates from local deletions.

## Behavior

- A new watched action queues Invidious alongside configured TubeArchivist and
  Plex destinations, independently.
- A new local deletion captures the YouTube ID before deleting the shared
  catalog item, and queues **only Invidious** as a watched destination. The
  existing TubeArchivist Delete + Ignore / Plex scan jobs are unchanged.
- Invidious delivery survives deletion of the local item and is deliberately
  allowed even when a TubeArchivist deletion record exists. Pending TA/Plex
  watched jobs remain blocked by that deletion record.
- There is no watched API call when a viewing is undone or removed, no
  automatic export of old records and no deletion of Invidious history.
- All deliveries use the account represented by the configured token; there
  is no automatic per-user Invidious account mapping.

The client sends `POST /api/v1/auth/history/VIDEO_ID` with the bearer header.
The endpoint updates the authenticated user's history and returns `204`. It
does not fetch YouTube metadata or require a media file in TubeArchivist.
Replaying a successful request keeps one entry per ID: Invidious removes an
existing occurrence before appending the ID at the end of its watched array.
This can move the video to the most recent position, but does not duplicate it.

Open **Settings → Integrations → TubeArchivist, Plex and Invidious → View
watched status** (`/youtube-watched/`) to review deliveries or manually retry a
failed destination. Worker/beat retries connection failures, `408`, `429` and
server errors independently, up to eight failures. A completed destination is
not repeated just because another destination fails.

`401`/`403` requires a valid token with history permission; `409` requires
enabling watch history in that account's preferences; `404` requires checking
the URL and installed API version. Proxy redirects and unexpected success
statuses are treated as configuration errors rather than followed with the
token. Correct the cause and click **Retry**.

## Deployment and first test

The existing watched queue requires migration `integrations.0003`; adding the
destination also requires `integrations.0004_alter_youtubewatchsync_target`.
The normal container entrypoint applies migrations. Restart web, worker and
beat with the environment variables above.

Choose a video for a **new** watched action in Yamtrack, then inspect its
Invidious delivery in `/youtube-watched/` and the account's Invidious history.
For a deletion test choose a video you intend to delete: the original shared
deletion behavior still applies. Tests in the repository use mocked APIs and
never modify live history.

## References

- [Authenticated API and history endpoint](https://docs.invidious.io/api/authenticated-endpoints/)
- [Token authorization and display](https://github.com/iv-org/invidious/blob/master/src/invidious/routes/account.cr)
- [Token serialization and validation](https://github.com/iv-org/invidious/blob/master/src/invidious/helpers/tokens.cr)
- [History endpoint implementation](https://github.com/iv-org/invidious/blob/master/src/invidious/routes/api/v1/authenticated.cr)
