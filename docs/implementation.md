# Implementation guide

Read the [product contract](design.md) before changing behavior. Organizer steps,
hosting and sizing have [separate guides](README.md). The current backend routes
and frontend types are authoritative for payload details; avoid copying full type
schemas into feature documents.

## Source map

| Area | Source |
|---|---|
| Plugin initialization, migrations and native hooks | [`__init__.py`](../plugin/open_tech_quest/__init__.py), [`migrations/`](../plugin/open_tech_quest/migrations), [`schema.py`](../plugin/open_tech_quest/schema.py) |
| Quest API and response serialization | [`api.py`](../plugin/open_tech_quest/api.py) |
| Projection, timing and rankings | [`service.py`](../plugin/open_tech_quest/service.py), [`ranking.py`](../plugin/open_tech_quest/ranking.py) |
| Registration, attendance and grouping | [`registration.py`](../plugin/open_tech_quest/registration.py), [`onboarding.py`](../plugin/open_tech_quest/onboarding.py), [`onboarding_math.py`](../plugin/open_tech_quest/onboarding_math.py) |
| Roles, account safety and recovery | [`roles.py`](../plugin/open_tech_quest/roles.py), [`lifecycle.py`](../plugin/open_tech_quest/lifecycle.py), [`recovery.py`](../plugin/open_tech_quest/recovery.py) |
| Native access and room topology | [`guards.py`](../plugin/open_tech_quest/guards.py), [`room_topology.py`](../plugin/open_tech_quest/room_topology.py), [`authoring.py`](../plugin/open_tech_quest/authoring.py) |
| Owned native assets and automation contract | [`assets.py`](../plugin/open_tech_quest/assets.py), [automation guide](automation.md) |
| Shared read inputs and revisions | [`read_cache.py`](../plugin/open_tech_quest/read_cache.py) |
| Event archives and deliberate round cleanup | [`archive.py`](../plugin/open_tech_quest/archive.py), [`rounds.py`](../plugin/open_tech_quest/rounds.py), [`defaults.py`](../plugin/open_tech_quest/defaults.py) |
| Browser API types and transport | [`types.ts`](../web/src/types.ts), [`api.ts`](../web/src/api.ts) |
| Shared session/polling coordinator | [`quest-session.ts`](../web/src/quest-session.ts), [`polling.ts`](../web/src/polling.ts) |
| Room paths and public display | [`challenge-path.tsx`](../web/src/challenge-path.tsx), [`standings.tsx`](../web/src/standings.tsx), [`stage.tsx`](../web/src/stage.tsx), [`stage-progress.ts`](../web/src/stage-progress.ts) |
| Local archive validation and viewer | [`archive-format.ts`](../web/src/archive-format.ts), [`event-archive.tsx`](../web/src/event-archive.tsx) |

## Native CTFd integration

The plugin runs inside pinned CTFd 3.8.9 rather than a separate service or core
fork. Native login, accounts, teams, challenge grading, hints and submissions
remain in CTFd. Guards extend native detail/attempt/unlock/file and configuration
paths; protecting only Quest endpoints would leave bypasses.

MariaDB/MySQL uses the plugin migration chain. CTFd's SQLite plugin runner calls
`create_all` rather than the Alembic path; `schema.py` applies the required
introspective attendance upgrades under `BEGIN IMMEDIATE`. Migrations preserve
existing content, accounts, clocks and policy; startup must not reset a populated
installation or reintroduce deliberately removed rooms.

Writes serialize on the event transaction. Fresh authority, account identity,
phase, membership and cutoff checks happen after lock waits, not only before them.
Native operations can close/commit sessions; recovery must reacquire the lock and
validate again. See [rosters](rosters.md) and [administration](administration.md)
for the constraints that must survive changes.

## Reads and browser state

API paths use `/api/v1/quest` and CTFd's `success/data` envelope, session cookies
and CSRF protection. Native challenge content/attempt APIs remain native. Errors
use HTTP status plus a human-readable message; a native `success:true` field does
not make a non-2xx response successful.

CTFd's native access-token hook also authenticates Quest management routes when
`Content-Type: application/json` is present, including GET requests. Tokens do not
grant native administrator privileges to Maintainers. See [automation](automation.md).

Automation version 1 keeps standard content in native records. Migration
`0011_catalog` adds durable room-scoped source keys and file-kind ownership
metadata; SQLite applies the same idempotent upgrade. New-round reset retains
them; defaults clears them after archiving. Their mutations invalidate content
revisions. Scoped JSON base64 uploads use native storage without upload helpers'
internal commits. Metadata-only deletion preserves rollback and leaves provider
garbage collection to the operator. See the canonical
[payload, ownership and reconciliation contract](automation.md).

`/bootstrap` supplies full initial metadata. `/sync` supplies live event/viewer,
registration, attendance, CSRF and opaque content/roster/progress revisions without
resending rooms. The shared coordinator accepts viewer and CSRF together, merges
only current responses and refreshes full data when relevant revisions change.
Preserve hidden/offline suspension, focus/reconnect refresh and stale-response
protection; separate component polling would multiply load and race identity.

Shared caches store primitive read inputs, not write authorization. Durable
generation markers change with the writer's transaction, including ORM/bulk/raw
SQL invalidation paths. Cross-request sharing requires verified MySQL/MariaDB
REPEATABLE READ; SQLite and other isolation modes use direct reads. Cache errors
fall back to the database. Scheduled privacy/cutoff boundaries also affect visible
revisions. Use supported APIs during events; out-of-band SQL edits require cache
clearing/restarting readers.

Challenge values and immutable hint receipts feed the same batched projection
as solves. `scoring.py` handles event hint purchases through the guarded native
unlock route, using fresh Quest net balance and native unlock/award records.
Migration `0010_points` links legacy native purchases to their recorded charges;
ambiguous/missing charges stop migration with the unlock ID for operator review,
rather than guessing a price. Receipts retain their original cost. Published
snapshots and CSV exports carry the scoring version; legacy published results
continue to display their original count-based rules.

## Frontend and assets

The React/Vite app is copied into the plugin's `assets/app/`; the Jinja shell
serves `/quest` and its routes. Only safe viewer/CSRF data belongs in
`window.QUEST_INIT`. Native login, registration, settings and admin remain usable.

Use the existing shared session resource and sanitized Markdown renderer. Keep
unsaved editor state on polling refreshes. Room and projector progress derive
from server counts, with bounded, horizontally scrollable participant paths and
adaptive long-chain marks. Projector settings live in the existing event JSON;
`projector.py` normalizes old settings without rewriting them. Rotation uses a
server anchor and durable pause timestamp, while frontend `projector-rotation.ts`
resolves views and fair room/tie paging without extra HTTP polling. The existing
shared session supplies viewer identity for personal progress; comparisons use the
same public standings projection, not a new ranking or access policy.
The bundled Dockerfile passes `PLUGIN_VERSION` to Vite and the runtime archive producer; direct
frontend builds can set `VITE_PLUGIN_VERSION`. Unset versions show **Development
build** in the footer.

Respect keyboard focus, contrast and reduced motion. Artwork provenance, alpha
transparency and separate licenses are documented in [branding](../branding/README.md).

## Development and release

Use `scripts/dev.sh` for the local source-build setup. Frontend commands live in
[`web/package.json`](../web/package.json); backend and disposable fixture commands
are in [tests/README.md](../tests/README.md). See [validation](validation.md) for
which checks are automated and which require an isolated browser/event.

CI derives the next version from Git tags, tests/builds the image and tags the
**tested source commit**. It publishes the numbered image plus a `latest` alias,
matching Git tag, OCI source/revision/version labels and digest in release notes.
There are no generated manifest commits or upstream Kubernetes deployment API.
Ensure the GHCR package permits anonymous pulls when first publishing; real event
operators pin an image version/digest in their infrastructure repository.

Commit finished units and review risky paths independently. Keep production
verification read-only; user/flag/roster mutations belong in isolated fixtures.
Never commit real flags, participant exports, session state or credentials.
