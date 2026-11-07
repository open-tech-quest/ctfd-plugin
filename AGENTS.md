# Open Tech Quest

Public repository `open-tech-quest/ctfd-plugin`. A CTFd 3.8.9 plugin and a React
participant/admin/projector application for the Red Hat Hands-On Day.

Read `docs/design.md` and `docs/implementation.md` before changing behavior.
They define the shared API, prize rules, event lifecycle, and design direction.

## Rules

- Never commit credentials, participant exports, browser state, internal
  catalogue archives, real flags, or database dumps. Deployments consume an
  existing native Kubernetes Secret; secret-manager integration is optional
  and belongs to the operator. See `docs/deployment.md`.
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
- Every finished unit is committed and pushed. Every release runs backend
  integration tests, frontend checks/build, and container smoke tests.
- CI publishes versioned images to `ghcr.io/open-tech-quest/ctfd-plugin` and
  immutable deployment tags. Main uses `dev`; CI selects the next version from
  Git tags and stamps only the base Kustomization's image pin before testing/tagging.
  Do not manually bump deployment versions. Consumers use a `vX.Y.Z`
  tag and the matching image digest, never `main`. No secrets in build layers.
- Independent frontend/backend work and independent review are appropriate
  for this project; assign disjoint file ownership and follow review-bar.

## Layout

- `plugin/open_tech_quest/`: Python plugin, models, migrations, API and shell.
- `web/`: React/TypeScript frontend; Vite output is copied to plugin assets.
- `tests/`: integration tests, run inside the pinned CTFd image.
- `docs/`: public specification and operations documentation.
- `deploy/`: portable Kustomize base, optional database/cache, platform
  overlays and event sizing component; no external GitOps repository required.
- `Dockerfile`: pinned upstream CTFd plus built app/plugin; UID 1001.

## Deployment and verification

Use `docs/deployment.md` for Kubernetes/OpenShift installation, secrets,
configuration and migration. Keep published instructions independent of a
maintainer's cluster, local filesystem, secret manager or GitOps repository.
Deployment automation may consume releases, but is not a runtime dependency.
Never prune/recreate an existing namespace or PVCs to change manifest source.
Verify new platform claims against the actual rendered manifests and record
what was tested; historical application validation does not validate a new
deployment package automatically.
