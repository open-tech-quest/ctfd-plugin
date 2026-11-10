# Open Tech Quest

Public repository `open-tech-quest/ctfd-plugin`. A CTFd 3.8.9 plugin and a React
participant/admin/projector application for the Red Hat Hands-On Day.

Read `docs/design.md` and `docs/implementation.md` before changing behavior.
Use `docs/rosters.md` and `docs/administration.md` for their security/lifecycle
contracts; API types and routes are defined in the source. `docs/README.md` maps
the guides by audience. Keep current guidance separate from historical evidence.

For a full manual test, exploration session or feature rehearsal, read
`docs/rehearsal.md`. Use a dedicated test instance and the operator’s setup/reset
runbook when available. Inspect the active run before changing it; preserve an
in-progress rehearsal and perform only the requested setup, check-in, synthetic
progress or reset. Reserve users 01–09 for manual tests; automation uses eligible
run-owned users 10 and higher and never changes their mixed teams.
Keep operator access, snapshots and deployment commands out of public guidance.

## Rules

- Never commit credentials, participant exports, browser state, internal
  catalogue archives, real flags, or database dumps. Runtime credentials belong
  to the operator; never commit them in hosting examples. See `docs/deployment.md`.
- Keep CTFd responsible for users, teams, challenges, flags, hints, and solves.
  Add features through the plugin; do not fork CTFd's core.
- All timing, authorization, and prize calculation is server-side. A room
  starts once per team, before challenge content is revealed. Never accept
  client-supplied timestamps or award a second solve for a teammate.
- UI and projector consume the same standings calculation. Do not count
  points as solved challenges. Preserve genuine ties.
- The event starts in draft. Startup never seeds sample event content.
  Organizer-authorized demo load/unload is an explicit admin-only draft action,
  with durable ownership tracking and preservation of real content. A loaded
  demo must be removed before competition start. It never creates participants,
  teams or progress. Bulk participant/progress fixtures remain isolated tests.
- Theme changes must preserve room IDs, memberships, starts, and solves.
- Maintainer automation uses explicit source keys and owned native content/assets;
  never adopt by name or widen native admin permissions. Preserve unmapped keys,
  preflight reattachment/whole-room cleanup, and keep asset byte cleanup separate
  from transactional metadata deletion. See `docs/automation.md`.
- Only explicit danger actions in draft, closed or published state may clear history
  (close live/paused events first):
  archive atomically first, retain the acting administrator and saved archives,
  delete every other account and teams/play; defaults also clears challenge/room
  content and restores five empty rooms. Never run either reset as an upgrade,
  QA side effect or implicit rehearsal setup. Event JSON is not a restore backup.
- Every finished unit is committed and pushed. Every release runs backend
  integration tests, frontend checks/build, and container smoke tests.
- CI publishes tested versioned images and a `latest` alias for the Compose
  example to `ghcr.io/open-tech-quest/ctfd-plugin`. Git tags identify the tested
  source commit; no generated deployment commits. Event deployments pin tested
  image versions/digests. No secrets in build layers.
- Independent frontend/backend work and independent review are appropriate
  for this project; assign disjoint file ownership and follow review-bar.

## Layout

- `plugin/open_tech_quest/`: Python plugin, models, migrations, API and shell.
- `web/`: React/TypeScript frontend; Vite output is copied to plugin assets.
- `tests/`: integration tests, run inside the pinned CTFd image.
- `docs/`: public specification and operations documentation.
- `examples/compose.yaml`: illustrative CTFd/MariaDB/Redis hosting setup.
- Root `compose.yaml`: disposable SQLite development setup.
- `Dockerfile`: pinned upstream CTFd plus built app/plugin; UID 1001.

## Deployment and verification

Use `docs/deployment.md` for runtime configuration, hosting and upgrades. Keep
public guidance independent of a maintainer's filesystem, secret manager, cluster
or GitOps repository. Production Kubernetes/OpenShift manifests belong to the
operator; do not introduce a versioned deployment package or release stamping.
Verify the Compose example and runtime in CI. Preserve measured sizing evidence;
application tests do not qualify another cluster's networking, storage or capacity.
