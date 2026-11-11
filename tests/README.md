Backend verification runs the real pinned CTFd Flask app and SQLAlchemy models,
including native flags, requirements, attempts, hints and attachment downloads.
The image already contains all dependencies; these tests use `unittest`.
For hosting requirements and the Compose example, see
[deployment](../docs/deployment.md). Application tests below do not by themselves
validate Kubernetes/OpenShift networking, storage or admission policies.

From the repository root:

```sh
podman run --rm --entrypoint python \
  -v "$PWD/plugin/open_tech_quest:/opt/CTFd/CTFd/plugins/open_tech_quest:ro" \
  -v "$PWD/tests:/opt/quest-tests:ro" \
  docker.io/ctfd/ctfd:3.8.9@sha256:84aff1e50b6a6f931b5da90c6f5b7c623ba4583217a9b00e9d4c1b7c53b9739f \
  -m unittest discover -s /opt/quest-tests -p 'test_*.py' -v
```

Use Docker in place of Podman when appropriate. CI's `test-runner` image copies
both locations and uses the same command. Add `-e DATABASE_URL=...` and attach
a disposable MariaDB service/network to exercise the actual production dialect,
microsecond migrations, row locks and concurrent starts. The database name must
end in `_test`; the suite deletes its fixture data. Never point these tests at
the event database. SQLite uses an automatically removed temporary directory.

Coverage includes the safe fresh draft, five empty rooms, native team ownership,
shared solves and duplicate submissions, sequential and known-URL content guards,
signed token downloads, allocation, CSRF/admin checks, immutable topology,
partial/tied rankings, pause history, cutoff and immutable publication snapshots,
reasoned corrections/exclusions and sanitized public/export data. Fifty teams are
exercised with 50 concurrent-client standings requests (concurrency 10), reporting
p95/max latency and bounding the shared projection to 12 SQL statements.

Points regressions exercise unequal challenge weights, progress/points/time room
ordering, net-points overall ordering, zero/negative balances, concurrent shared
hint purchases, immutable purchase prices, native-route ID aliases, legacy charge
migration and versioned publication/CSV results. Disposable browser challenges
have varied point values and paid hints after each room's free first hint.

Maintainer automation regressions cover bounded metadata and native regex grading,
token/session authentication and post-lock revocation, stable keys and explicit
unmapped reattachment, owned assets/previews and solution unlocks, atomic room
cleanup, unknown dependencies, rollback/queued-upload races, idempotent migration
and archive/reset ownership preservation. Asset tests verify metadata-only removal
preserves unrelated bytes and native private cache headers.

Room-preview regressions cover staff authorization, native flag comparison,
changed-content handling and the absence of native competition writes. Browser
checks cover local unlocking, hints, tab independence and clearing progress on
leave/reload. Preview adds no database models or migrations; use disposable staff
and participant accounts rather than modifying an active event.

Archive tests cover private source collection, microsecond history, published vs
current credit, preserved authored metadata, excluded native secrets, flag-bound
fingerprints and checksums. Round-reset tests exercise both modes on disposable
data: archive/delete atomicity, stale previews, administrator/CSRF checks,
additional-plugin refusal, one retained acting administrator and the correct
content/default-room outcome, including draft and consecutive reset modes while
live/paused resets remain blocked. Frontend checks cover local JSON parsing, integrity
verification and bounded safe viewing; these reports are not restore backups.

Read-cache and sync regressions cover concurrent roster removal, old-snapshot
cache fills, transaction rollback, ORM/bulk/raw-SQL invalidation, immediate bans
and visibility changes, per-team isolation, scheduled privacy/cutoff transitions,
and cache failures. Shared cache hit/race tests run on MariaDB REPEATABLE READ;
SQLite and other isolation modes verify the direct-read fallback. Frontend tests
exercise the shipped session coordinator with controlled timers and responses,
including identity/CSRF races, attendance delivery, hidden/offline suspension and
coalesced content refreshes.

The pure rule tests also run without CTFd:

```sh
python3 -m unittest discover -s tests -p test_ranking.py -v
```

For isolated browser verification, mount or copy `demo_fixture.py` into a running
**disposable** CTFd container and execute:

```sh
QUEST_ALLOW_DEMO=1 python /opt/quest-tests/demo_fixture.py
```

This script rejects databases whose name/path lacks `demo`, `test` or `browser`,
and requires the explicit opt-in. It replaces that disposable database with 50
named teams, 100 participants and 25 native practice challenges with hints/files.
Use `--draft` to exercise allocation and topology editing before opening. All
fixture accounts use the public password `quest-demo-only`: organizer account
`organizer`; the untouched shared participant team Atlas has `atlas-one` and
`atlas-two`. Restart running workers after reseeding to clear process caches.
Use `--challenges-per-room 14` or `--challenges-per-room 30` to verify long
challenge paths and projector progress at realistic team counts. The default
remains five challenges per room. Add `--members-per-team 8` for 400 native
accounts across the same 50 teams. The fixture is excluded from the production
image and is never auto-seeded.

Organizer timing corrections are cumulative per team/room, limited to ±24 hours,
clamped at zero active elapsed time, reasoned and audited. They affect room award
times only; overall awards retain the event/team-first-room clock. Native solve
timestamps are never rewritten. Unpublish/reopen actions require reasons and
preserve history; reopening excludes the closed interval as an organizer pause.
