# Open Tech Quest

A CTFd plugin for team escape-room events: themed rooms, sequential challenges,
shared team progress, timed standings and a projector view. Built on CTFd 3.8.9;
CTFd handles accounts, teams, flags, hints and solves.

## Features

- Customizable rooms with ordered challenges, Markdown introductions and themes.
- Participant check-in, organizer team allocation and optional team naming.
- Shared room starts and solves, server-side clocks, pauses and closing cutoffs.
- Room and overall rankings with shared ties, plus projector and awards views.
- Organizer controls for challenges, maintainers, late arrivals and CSV exports.

New installations start in **draft** with five empty rooms: OpenShift, Ansible,
RHEL, Security and AI. Rooms can be renamed, added or removed. An optional demo
adds practice challenges without creating participants or progress; remove it
before starting a real event.

## Quick start

With Docker or Podman and a Compose provider installed, start a complete local
CTFd instance with Open Tech Quest preinstalled:

```sh
./scripts/dev.sh
```

Open `http://127.0.0.1:8000`, complete the CTFd setup wizard and visit
`/quest/admin`. The helper builds the image and creates a private local session
key in ignored `.env`. The `quest` Compose service runs CTFd and the plugin
together; no separate CTFd installation is needed. This SQLite setup is for local
development, with database, uploads and logs stored in named volumes.

Create challenges in **Organize → Rooms**. Participants register their own
accounts and check in; organizers review team allocation, finalize rosters and
start the event. See the [operations guide](docs/operations.md) for the full
workflow and [maintainer guide](docs/maintainers.md) for delegated authoring.

### Using an existing CTFd instance

Open Tech Quest uses CTFd's standard plugin loader and runs inside the same
application, sharing its database, accounts and sessions. The bundled image is
the tested deployment path, but the plugin can also be installed manually into
an existing **CTFd 3.8.9** instance. Other CTFd versions have not been validated.

A manual installation must reproduce the packaging in the [Dockerfile](Dockerfile):
copy `plugin/open_tech_quest/` into `CTFd/plugins/open_tech_quest/`, build the
frontend with `npm ci && npm run build` in `web/`, copy the contents of `web/dist/`
into the installed plugin's `assets/app/`, and copy the contents of `branding/`
into its `assets/branding/`. Restart CTFd to load the plugin and run its migrations.
Copying the Python plugin directory alone does not include the frontend.

The plugin replaces the participant challenge-board experience and manages team
allocation and event timing. It requires team mode and refuses startup if
switching from individual mode would affect existing individual solves. Back up
an existing database and validate the installation against a copy before enabling
it on an existing event. The bundled image can also be used with an existing
database and cache; deploying a new full stack is not required.

### Combining multiple plugins

For multiple plugins, build your own image from upstream CTFd and include each
plugin in it. The bundled Open Tech Quest image is a convenience for running
CTFd with this plugin preinstalled; plugins run inside CTFd, rather than as
separate containers connected to it.

For example, arrange your build directory as follows. Use a tagged release of
Open Tech Quest and the other plugin's installable Python package:

```text
my-ctfd/
├── compose.yaml
├── Dockerfile
├── open-tech-quest/   # Checkout of this repository
└── another_plugin/   # Plugin package from another repository
```

Build the Open Tech Quest frontend, then package both plugins into upstream CTFd:

```dockerfile
FROM node:24.22.2-bookworm-slim AS frontend
WORKDIR /app
COPY open-tech-quest/web/ ./
RUN npm ci --no-audit --no-fund && npm run build

FROM ctfd/ctfd:3.8.9
USER 1001
WORKDIR /opt/CTFd
COPY --chown=1001:1001 open-tech-quest/plugin/open_tech_quest/ CTFd/plugins/open_tech_quest/
COPY --chown=1001:1001 open-tech-quest/branding/ CTFd/plugins/open_tech_quest/assets/branding/
COPY --from=frontend --chown=1001:1001 /app/dist/ CTFd/plugins/open_tech_quest/assets/app/
COPY --chown=1001:1001 another_plugin/ CTFd/plugins/another_plugin/
```

In your existing Compose file, replace the `ctfd` service's `image` setting with
`build: .`, keeping its environment, database/cache connections, ports and volumes:

```yaml
services:
  ctfd:
    build: .
    # Keep the rest of your existing service configuration here.
```

Rebuild and recreate just that service with
`docker compose up -d --build --no-deps ctfd`. CTFd loads both plugins at startup.
Follow the other plugin's instructions for any additional dependencies or asset
builds. This example illustrates packaging; compatibility between plugins must
be checked, especially if they also control teams, challenge access or timing.

## Deployment

Tested, versioned images are published to
[`ghcr.io/open-tech-quest/ctfd-plugin`](https://github.com/open-tech-quest/ctfd-plugin/pkgs/container/ctfd-plugin).
The repository includes a standalone Kustomize stack with MariaDB, Redis,
persistent storage, Kubernetes Ingress and OpenShift Route overlays. It consumes
a native Secret; Argo CD and external secret managers are optional.

Follow the [deployment guide](docs/deployment.md) for installation, HTTPS,
the 400-attendee component, upgrades and existing-deployment migration. Consume
an immutable `vX.Y.Z` release tag and its published image digest. Each release
updates the manifest version and image together; `main` is a development branch.

### Sizing

Size for **active pages**, not registered accounts. With plugin 0.1.20, our
room/standings workload generated about **0.35 API requests/s per participant**;
challenge workloads ran elsewhere. Lightweight sync and shared read caches
reduced measured CTFd CPU from 5.2 to 2.8 cores against the same event fixture.

A ten-minute OpenShift test with **400 authenticated HTTP clients, 50 teams of
eight** completed 85,155 steady requests with no unrecovered errors;
endpoint p95 was at most 0.27s. The selected starting profile is:

| Component | CPU request / limit | Memory request / limit |
|---|---|---|
| CTFd, one pod with 8 workers | 4 / 6 cores | 1 / 2 GiB |
| MariaDB | 500m / 1 core | 512 MiB / 1 GiB |
| Redis | 250m / 500m | 128 / 256 MiB |

Total: **4.75 CPU requested / 7.5 limited**. All 50 simultaneous flags succeeded
(p95 8.3s), and all 400 reconnects and reloads completed. The shared flag-write
lock still queues bursts. These were HTTP clients; venue networking and browser
performance need separate validation.

See the [sizing guide](docs/sizing.md) for the method, **50–500 attendee estimates**
and a **400-attendee OpenShift example**. Only 400 attendees were measured;
repeat the test with your hardware and event history.

## Development

```sh
cd web
npm ci
npm run check
npm run format:check
npm run build
```

CI also runs backend tests against SQLite and MariaDB and a container startup
check. See [testing](tests/README.md) for backend commands and disposable browser
fixtures, and [design](docs/design.md) for event rules and architecture.

Application code is [MIT licensed](LICENSE); fonts and brand assets have
[separate notices](branding/README.md). This is an independent project, not an
official Red Hat product.
