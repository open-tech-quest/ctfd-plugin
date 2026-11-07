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

With Docker or Podman and a Compose provider installed:

```sh
./scripts/dev.sh
```

Open `http://127.0.0.1:8000`, complete the CTFd setup wizard and visit
`/quest/admin`. The helper builds the image and creates a private local session
key in ignored `.env`. This SQLite setup is for local development.

Create challenges in **Organize → Rooms**. Participants register their own
accounts and check in; organizers review team allocation, finalize rosters and
start the event. See the [operations guide](docs/operations.md) for the full
workflow and [maintainer guide](docs/maintainers.md) for delegated authoring.

## Deployment

Tested, versioned images are published to
[`ghcr.io/open-tech-quest/ctfd-plugin`](https://github.com/open-tech-quest/ctfd-plugin/pkgs/container/ctfd-plugin).
Pin a release tag and digest for your deployment.

For an event, provide MariaDB, Redis, persistent uploads, HTTPS and runtime
secrets (`SECRET_KEY`, `DATABASE_URL`, `REDIS_URL`). Keep the session key stable
across restarts and back up the database and uploads before upgrades. See
[deployment and rollback](docs/operations.md#deployment-and-rollback).

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
