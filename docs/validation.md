# Validation and evidence

Use [tests/README.md](../tests/README.md) for runnable backend commands and safe
fixtures, and [`web/package.json`](../web/package.json) for frontend commands.
The [image workflow](../.github/workflows/image-ctfd-plugin.yml) calls the
[current CI definition](../.github/workflows/ci.yml); those files define the gates,
not a copied test count from a past release.

## Automated gates

| Check | Scope |
|---|---|
| Backend suites on SQLite and MariaDB | Real CTFd models/routes, migrations, timing, native grading/guards, roster/identity races, moderation, recovery and read-cache invalidation |
| Frontend check and build | TypeScript, Node tests for session/polling/path behavior, and Vite production assets |
| Runtime smoke | Actual image with disposable SQLite: shell/assets, empty draft/five rooms and anonymous private-API denial |
| Compose example | Actual image with fresh MariaDB/Redis, healthy dependencies and native CTFd setup page; owned containers/volumes cleaned afterwards |
| Publication test target | Container test stage before publishing the numbered image and `latest` alias |

Frontend formatting can be checked with `npm run format:check` in `web`; it is
not currently a separate CI gate. Compose startup does not by itself prove the
complete administrator/participant browser journey or event-scale capacity.

## Isolated acceptance for behavior changes

For a complete manual run with fresh accounts, editable challenges and reset,
follow the [rehearsal walkthrough](rehearsal.md). The browser fixture preassigns
teams, including in draft mode, and serves targeted UI verification instead.

Use disposable accounts/data and real native sessions for affected journeys:

- Shared teammate starts/solves; future challenge, hint and known-file-URL denial.
- Staff preview landing/room flow, local sequential unlocking, hint reveals,
  changed-content notices and tab isolation. Leaving/reloading clears local
  progress. Compare all database tables and public read generations before/after
  preview; native security tracking may still change.
- Registration closure, attendance prompts, reviewed grouping, optional naming,
  finalization and controlled late admission, including stale requests.
- Role revocation, account ID reuse, safe deletion/captain repair, bans, legitimate
  re-solves, unban deduplication and immutable published results.
- Hidden/Visible/Locked authoring and access; supported/unsupported flag logic;
  demo ownership/removal; preserving real content and intentionally empty rooms.
- Projector at 50 teams, long chains/names, 390px participant layouts, keyboard,
  reduced motion and accessibility scans plus manual device checks.
- Upgrade/restart persistence of accounts, settings, memberships, original/recovery
  submissions, room starts and timing values.
- Private archive download/local viewing, checksum failure and safe rendering;
  both end-of-event reset modes with the retained acting administrator, archived
  source/results, deleted other accounts, content-preservation/default differences,
  stale-preview/role/CSRF rejection, dependent-plugin refusal and atomic rollback.
  Include draft resets and consecutive another-round/defaults actions; live/paused
  events must be closed first.
  Run destructive checks only in disposable fixtures, never the active event.

Automated accessibility scans and development screenshots do not establish venue
readability or assistive-technology usability. Production verification remains
read-only; never seed or mutate real event records for QA.

## Recorded evidence and limits

- [0.1.14 release](https://github.com/open-tech-quest/ctfd-plugin/releases/tag/v0.1.14)
  records the account-moderation, contribution-recovery and late-admission work.
- [0.1.15 release](https://github.com/open-tech-quest/ctfd-plugin/releases/tag/v0.1.15)
  records attendance-first grouping and optional naming.
- [0.1.17 CI](https://github.com/open-tech-quest/ctfd-plugin/actions/runs/42240613877)
  covers the native visibility/access regression suite.
- [0.1.24 CI](https://github.com/open-tech-quest/ctfd-plugin/actions/runs/42404867106)
  includes the current Compose startup path and image publication.
- [Sizing](sizing.md) preserves the **0.1.20** OpenShift qualification: 400 native
  HTTP clients, including its fixture, measured resources, bursts and limitations.
  These were protocol clients, not 400 rendered browsers. Other attendee counts
  are estimates and later releases/clusters need their own qualification.

Historical feature proposals and detailed rollout narratives are retained in Git
history. Installation-specific credentials, backups and operational evidence
belong to the operator. Application CI does not qualify a cluster's ingress/TLS,
volume permissions, networking, venue Wi-Fi or disaster recovery.
