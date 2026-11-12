# Open Tech Quest

A CTFd plugin for team escape-room events: themed rooms, sequential challenges,
shared team progress, points and time rankings, and a projector view. Built on CTFd 3.8.9;
CTFd handles accounts, teams, flags, hints and solves.

## Features

- Customizable rooms with ordered challenges, Markdown introductions and themes.
- Participant check-in, organizer team allocation and optional team naming.
- Shared room starts and solves, server-side clocks, pauses and closing cutoffs.
- Room progress/points/time rankings and overall net-points rankings, with shared
  ties, team-paid hints, projector and awards views.
- Organizer authoring, staff controls, CSV exports, JSON archives and round resets.
- [Full backups and reviewed restore](docs/backups.md) with an external deployment worker.
- [Maintainer automation](docs/automation.md) for owned challenges, metadata and assets.
- [Staff room preview](docs/preview.md) with local progress and the participant interface.

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
start the event. See [event operations](docs/operations.md) for preparation, staffing, attendance
and play, or the [documentation index](docs/README.md) for developer contracts.

For an end-to-end practice run, use the [rehearsal guide](docs/rehearsal.md):
50 test accounts, editable challenges, team formation, play and a complete reset.

The bundled image includes CTFd and the plugin. To use an existing CTFd instance
or combine multiple plugins, follow the [installation instructions](docs/deployment.md#install-into-an-existing-ctfd).

## Deployment

Tested, versioned images are published to
[`ghcr.io/open-tech-quest/ctfd-plugin`](https://github.com/open-tech-quest/ctfd-plugin/pkgs/container/ctfd-plugin).
Use the [Compose example](examples/compose.yaml) to try the published image with
MariaDB and Redis. It uses `latest` for convenience; real event deployments should
pin a tested image version and digest.

The [hosting guide](docs/deployment.md) explains configuration, storage, platform
requirements and upgrades. Maintain production Kubernetes/OpenShift manifests
and secret-manager integration in your own infrastructure repository.

### Sizing

With plugin **0.1.20 / CTFd 3.8.9**, a ten-minute OpenShift test of **400
simultaneously active HTTP clients in 50 teams** sustained about 142 requests/s
with zero unrecovered errors; CTFd averaged 2.77 CPU cores. The selected starting
profile is **4 / 6 CPU cores for CTFd**, or **4.75 / 7.5 cores including MariaDB
and Redis**. Challenge workloads ran elsewhere.

The [sizing guide](docs/sizing.md) contains the resource breakdown, burst results,
limits and 50–500 attendee estimates. Only 400 clients were measured; this does
not qualify rendered browsers or venue networking.

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
