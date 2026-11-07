# Release verification — 2026-11-03

Open Tech Quest **0.1.2**, source `2373077`, was published by
[CI run 41633395583](https://github.com/open-tech-quest/ctfd-plugin/actions/runs/41633395583).
The public GHCR image was pulled anonymously before deployment. Its index digest:

```text
sha256:d2a5251b0a0c743d07736c3ea732060db9191d51b8a3b17af1e7eaa90dc1ab13
```

## Automated and isolated verification

- MariaDB: all 24 tests passed, including production-dialect migrations,
  microsecond timing, concurrent team starts and lifecycle serialization.
- SQLite: 22 passed, with the two MariaDB-specific concurrency cases skipped.
- Frontend TypeScript checks, formatting and production build passed.
- The actual runtime image started successfully with five empty draft rooms;
  no demo fixture is included in that image.
- Two independent browser sessions shared a team's solves and room start.
  Correct answers unlocked the next step on both devices; future content,
  hints and downloads remained protected through native API paths.
- Organizer allocation, room configuration, pause controls, hint preview and
  projector controls were exercised in a disposable event.
- Fifty named teams fit the projector overview at 1920×1080 without scrolling.
  A 390px participant viewport had no horizontal overflow or broken images.
- Reduced-motion preference disabled the navigation underline transition.
- Fifty-team standings tests bounded the common projection to 12 SQL queries.
  On the local disposable MariaDB setup, 60 summary requests at concurrency
  20 all succeeded, with p95 latency 193ms. This is a development measurement,
  not an event-network latency guarantee.

## Live rollout verification

The historical installation deployed matching initialization/runtime image
references after manifest validation. Rollout, database/cache health and all
three persistent volumes were verified. This installation-specific evidence
predates the standalone manifests; it is not platform qualification for them.

The live HTTPS root redirected to `/quest`; compiled frontend/branding assets,
registration, native authoring and organizer pages loaded. Existing organizer
credentials authenticated successfully. Session cookies were Secure and
HttpOnly, and anonymous organizer API access was denied. A read-only organizer
check confirmed draft status, all five rooms, and zero event challenges,
teams or participants. No event was started and no demo data was inserted.

The live lobby's automated axe WCAG A/AA scan reported zero violations. One
contrast item for a decorative non-text dot required manual review. These
checks do not replace keyboard/assistive-technology testing with participants
or readability checks on the actual venue projector.

## Organizer preparation

Follow [operations](operations.md) to author the real challenges, choose final
room assets, configure timing/roster release, allocate teams and rehearse the
event. Default room timing starts explicitly when a team enters; overall
timing uses the common event start. Pauses are excluded, partial rooms rank by
last qualifying milestone, and exact ties remain shared.

A private SQL/uploads backup was taken before migration. Scheduled off-cluster
backups and SMTP still need event-environment configuration before relying on
recovery or password-reset mail. No credentials or participant data are stored
in this repository.

## Account moderation and controlled late admission — 2026-11-05

Release **0.1.14**, source
[`28aac6c`](https://github.com/open-tech-quest/ctfd-plugin/commit/28aac6cc855ade7f7bf0b61436f8277f24abc7b9),
passed [release CI](https://github.com/open-tech-quest/ctfd-plugin/actions/runs/42051966151):
143 cases on each of SQLite and MariaDB, with three skips per dialect;
frontend check/build; empty-install runtime smoke; and the publication test target.
The first full run exposed one older fixture that attempted Start without an
attended assigned team. The fixture now supplies real participant attendance;
the runtime guard remains intact. Independent review found no outstanding issues.

The anonymous GHCR pull and OCI labels verified the exact tested revision.
Both initialization and runtime matched the historical published image:

```text
Historical 0.1.14 image digest: sha256:352c1f8918052b33cf4735bd54887f8c8fc1d682c7a0c7f31ba8f48dea32d123
```

Actual disposable browser upgrade journeys covered identity-bound role changes
and deletion, captain repair, stale previews and reused account IDs; banning,
native evidence retention, wrong/correct teammate recovery, original/recovered
award timing and unbanning; and post-start self-registration/check-in followed
by reasoned admission into an existing or additional named team. Existing team
standings and clocks remained unchanged during admission. Restart preserved
native submissions, recovery evidence, attendance and memberships. Users and
Teams accessibility scans reported zero violations or incomplete checks; Users
fit a 390px viewport without document-wide overflow. Details are recorded in
[user lifecycle](user-lifecycle.md), [contribution moderation](contribution-moderation.md)
and [late admission](late-admission.md).

The historical rollout retained matching initialization/runtime images,
running MariaDB/Redis and all three bound persistent volumes.

Read-only authenticated production checks verified the new capabilities,
private/no-store user responses and byte-identical built frontend assets.
The saved baseline remained unchanged: draft/check-in phase, public registration,
attendance closed at generation zero, five rooms, 25 demo challenges, one native
administrator and no participants, teams or readiness records. No production
user was deleted, demoted, banned or admitted for verification. A private
SQL/uploads backup preceded this upgrade.
Restore matching plugin code with the database: older code cannot interpret the
new native `quest_recovery` submission subtype.

## Attendance-first team formation — 2026-11-05

Release **0.1.15**, source
[`6592c67`](https://github.com/open-tech-quest/ctfd-plugin/commit/6592c67a8aca06980fedbd82fe7edd380d4d57e9),
passed [release CI](https://github.com/open-tech-quest/ctfd-plugin/actions/runs/42070881719):
150 cases on each database, with three skips per dialect; frontend check/build;
runtime empty-install smoke; and publication tests. The first full run caught
one older fixture inserting duplicate team-name metadata after an organizer
correction. It now reuses the actual row and asserts the administrator's
provenance; runtime behavior was unchanged. Independent review has no outstanding
findings. See [attendance-first acceptance](attendance-first.md) for actual
registration/attendance/grouping, both naming modes, event starts, native shared
solve, restart, accessibility and mobile verification.

The anonymous GHCR pull and OCI labels verified the exact tested revision.
Initialization and runtime matched the historical published image:

```text
Historical 0.1.15 image digest: sha256:7327e4279f8989129ba3c375911ec8c60d45b291791620e09cf56caf9d4605e2
```

The historical rollout passed manifest checks and retained the database,
cache and all three persistent volumes.

Read-only authenticated checks confirmed `allow_team_naming:false`, unchanged
event/content/account/attendance/team data and byte-identical built frontend
assets. An administrator explicitly closed registration before the rollout;
the audit timestamp established this and that setting was preserved. Production
remains draft/check-in with five rooms, 25 demo challenges, attendance closed at
generation zero, one native administrator and no participants, teams or readiness
records. No production grouping or naming actions were used for QA.
A private SQL/uploads backup preceded this upgrade.

## Organization migration — 2026-11-05

Release **0.1.16** completes the move to `open-tech-quest/ctfd-plugin` and its
public organization GHCR package. Pre-transfer and organization CI passed,
anonymous full-image pull succeeded, and the historical installation deployed
the tested digest. Read-only checks preserved event data/settings. See the
[migration record](org-migration.md#verified-completion--2026-11-05) for source,
image and preserved-history evidence, and [deployment](deployment.md) for current
installation and migration instructions.

## Challenge visibility — 2026-11-06

Release **0.1.17**, source `4f57c436aa747c565bb45ec67fe2e38e2dd8740c`, fixes
Hidden/Visible/Locked authoring, participant paths, native access and unpublished
award projections. [Release CI](https://github.com/open-tech-quest/ctfd-plugin/actions/runs/42240613877)
passed 161 cases per SQLite/MariaDB (three dialect-specific skips each), frontend
checks/build, empty-install runtime smoke and the publication image test stage.
Eleven new real-native integration cases also passed independently on both
databases. They cover hidden leading/middle steps, native detail/hints/attempts,
Locked gates and retained credit, exact withdrawal/restoration prize times,
recovery grading, staff previews, enum round-trips, unstartable/idempotent clocks,
startup backfill and failed-native-edit atomicity. Independent review found and
re-tested the native prerequisite gap before publication; no findings remained.

Chromium checks using the actual React components and isolated synthetic API
responses verified all three editor states, removal of hidden/locked selections,
404 content clearing, retained content through transient 500 failures, and
disabled starts for unavailable/empty paths. No production participation was
created for verification.

Anonymous full-image pull and OCI source/revision/version checks passed for
`ghcr.io/open-tech-quest/ctfd-plugin:0.1.17@sha256:56c7e3bd1ae3c6f8fcbf6e13ba5afeb1544c503085f5cd67fe5b6af781eeac8c`.
The historical installation pinned matching initialization/runtime images,
passed manifest validation and retained all three bound persistent volumes.

Read-only authenticated before/after checks matched event, onboarding,
registration, accounts, teams, challenge catalog and room configuration; live
frontend assets matched the built bytes. At verification the event was draft
with five rooms, one visible challenge, one administrator, one maintainer and no
participants/teams/readiness; registration was open and attendance closed.
Startup may repair derived native Requirements/next links, preserving mapping,
submission and timing history. No schema change was required. A private
SQL/uploads backup preceded rollout.
The previous 0.1.16 image remains available for rollback. Refresh existing Quest
tabs once to load the new frontend behavior.
