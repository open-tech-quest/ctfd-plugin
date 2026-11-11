# Hosting Open Tech Quest

The published image bundles **CTFd 3.8.9**, the plugin and its frontend:
`ghcr.io/open-tech-quest/ctfd-plugin`. It currently runs on **Linux amd64**.

This repository provides an illustrative Compose setup and application guidance.
Keep production manifests, secrets integration and cluster configuration in your
own deployment repository. See [sizing](sizing.md) for measured event resources
and [operations](operations.md) for organizer workflows and backups.

## Try the published image

[examples/compose.yaml](../examples/compose.yaml) runs CTFd, MariaDB and Redis.
It binds CTFd to localhost; the database and cache have no published ports.
Docker needs an amd64 host or amd64 emulation. From a checkout of this repository:

```sh
cd examples
python3 - <<'PY'
import os, secrets
os.umask(0o077)
with open('.env', 'x') as output:
    for key in ('SECRET_KEY', 'DB_PASSWORD', 'DB_ROOT_PASSWORD'):
        output.write(key + '=' + secrets.token_hex(32) + '\n')
PY
docker compose up -d
```

The command creates private credentials and refuses to overwrite an existing
`.env`. Keep that file and the named volumes across restarts. Never commit it.
Use hex database passwords in this example because they are embedded in a URL.

Open **http://localhost:8000/setup**, create the administrator and select **Teams**
mode. Then open `/quest` or `/quest/admin`. A fresh event starts in draft with
five empty rooms; it does not create demo participants, challenges or solves.

The example defaults to `latest` for convenience. Set `OTQ_IMAGE` in `.env` to a
specific `ghcr.io/open-tech-quest/ctfd-plugin:X.Y.Z@sha256:...` reference when you
want a reproducible version. Versioned releases publish the image digest in their
release notes; the footer displays the plugin version loaded by the browser.
`docker compose pull && docker compose up -d` updates the example. `down` retains
its data; `down --volumes` deletes it.

The root `compose.yaml` and `scripts/dev.sh` remain a separate source-build setup
with SQLite for local development.

## Install into an existing CTFd

Open Tech Quest uses CTFd's standard plugin loader and runs inside the same
application, sharing its database, accounts and sessions. The bundled image is
the tested deployment path, but the plugin can also be installed manually into
an existing **CTFd 3.8.9** instance. Other CTFd versions have not been validated.

A manual installation must reproduce the packaging in the [Dockerfile](../Dockerfile):
copy `plugin/open_tech_quest/` into `CTFd/plugins/open_tech_quest/`, build the
frontend with `npm ci && npm run build` in `web/`, copy the contents of `web/dist/`
into the installed plugin's `assets/app/`, and copy the contents of `branding/`
into its `assets/branding/`. Restart CTFd to load the plugin and run its migrations.
Copying the Python plugin directory alone does not include the frontend.
Set `VITE_PLUGIN_VERSION` when building a labelled manual frontend; otherwise
the footer identifies it as a development build.

The plugin replaces the participant challenge-board experience and manages team
allocation and event timing. It requires team mode and refuses startup if
switching from individual mode would affect existing individual solves. Back up
an existing database and validate the installation against a copy before enabling
it on an existing event. The bundled image can also be used with an existing
database and cache; deploying a new full stack is not required.

## Combine multiple plugins

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
FROM node:24-bookworm-slim AS frontend
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

For reproducible event builds, pin the base images by digest.

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

## Configuration and storage

CTFd owns accounts, authentication, challenges, flags and solves. The plugin
uses the same database for event configuration, timing, progress and audit data.
MariaDB/MySQL and Redis are recommended for hosting an event. SQLite and CTFd's
filesystem cache are suitable for small local trials, not our measured profile.

| Setting | Purpose |
|---|---|
| `SECRET_KEY` | Stable random session/signing key; preserve across upgrades |
| `DATABASE_URL` | SQLAlchemy URL, e.g. `mysql+pymysql://USER:PASSWORD@HOST/ctfd` |
| `REDIS_URL` | Cache URL; include authentication/TLS for your Redis service |
| `WORKERS` | Gunicorn worker processes; see the sizing guide |
| `SQLALCHEMY_MAX_OVERFLOW` | Set to `0` for the measured eight-worker profile |
| `UPLOAD_FOLDER` | Persistent uploads, `/var/uploads` in the example |
| `LOG_FOLDER` | Logs, `/var/log/CTFd` in the example |
| `REVERSE_PROXY` | CTFd proxy handling; match your actual trusted proxy chain |

Persist and back up the database and uploads. Redis is a cache, not the source
of truth for solves or room clocks. Retain logs according to your operational
needs. The image runs as UID **1001**; mounted directories must be writable by
that user or an appropriate supplemental group.

For public hosting, finish setup before exposing the instance. Configure HTTPS,
CTFd's secure session cookies and proxy handling, SMTP, backups and monitoring.
Restrict database/cache access to CTFd. The local example's plain HTTP and private,
unauthenticated Redis are not an event deployment configuration.

## Kubernetes and OpenShift

Maintain the Deployment, Services, persistent volumes, ingress/Route, network
policies and Secret provider in your own infrastructure repository. Supply the
settings above through your platform's configuration and secret mechanisms.
Use a tested **image version and digest**, including any init/migration container;
there is no upstream manifest tag to pair with the image.

Keep a single CTFd replica with its worker processes unless you have separately
validated shared storage, initialization and multi-replica behavior. With
ReadWriteOnce uploads, a Recreate rollout avoids competing volume attachments.
Allow startup time for CTFd migrations. On OpenShift, configure an SCC/service
account that permits the image's non-root UID and writable storage; do not assume
the namespace's default UID range includes 1001.

## Recover a changed event

A backup represents its capture time. Different content or software in the live
instance is expected; choose the recovery scope before replacing anything.
These are operator procedures. **Organize → Archives** exports event reports;
it does not browse or restore infrastructure backups.

| Problem | Recovery action |
|---|---|
| Wrong instructions, answers, points or hints | Extract the old backup privately and copy selected content through the current editor. Review current impact and retain current native IDs, solves, attempts and purchase receipts. |
| A withdrawn challenge should return | Review visibility on the existing challenge. This restores retained eligibility, not an older content version or elapsed time. |
| An unused challenge was deleted | Recreate eligible content through supported authoring/catalog tooling. Do not force old IDs into current tables; recreating content does not reconstruct lost history. |
| An attachment is missing | Recover verified bytes only after checking that the current file record, ownership and intended asset match. Preserve newer files at reused paths. |
| A release is broken but event data is healthy | Qualify a compatible image rollback against the current schema/data. Changing the image does not roll back a migration. |
| Database corruption, accidental reset or lost deployment | Test a complete snapshot in isolation, then explicitly restore/cut over the whole database, uploads and configuration together. Everything after that recovery point is lost. |
| Backup images/schema or target identity differ | Restore first with the snapshot's recorded images in isolation, qualify the intended upgrade/target, and reconcile deployment pins before cutover. Never merge old SQL into a newer live database. |

Content recovery is an ordinary reviewed edit, not an automatic backup merge.
Compare the selected fields with the current challenge and ownership, rather
than trusting only a name or numeric ID. Preserve purchased hint IDs and original
costs. If another edit changed the current revision, refresh/review again. Secret
flags and file bytes require a real backup or their maintained source; the
[event JSON archive](archives.md) intentionally cannot restore them.

A full restore requires a fresh safety backup of the current target, verified
snapshot checksums, matching database/upload/configuration material and stopped
writers/traffic. Clear dedicated cache/session state, require fresh login and
verify data, files and application behavior before reopening. A rollback also
restores the snapshot's accounts, roles and credential state; review later
revocations before cutover. Recreate cluster/PVC/access identities on replacement
infrastructure rather than copying old identities or tokens. Pausing ordinary
maintenance remains manual; a full restore cannot run over active writers.

## Upgrades and existing deployments

Back up SQL and uploads, retain the previous image reference, and test the next
image against a disposable copy before an event. Preserve session keys, database
credentials, resource identities and storage. Image rollback does not undo a
schema migration; check compatibility or restore a matching backup.

The points upgrade links existing native hint purchases to their recorded
charges. An ambiguous or missing charge stops startup with the unlock ID;
restore the previous image and review that purchase against your backup before
retrying. It never guesses historical prices or recalculates published results.

Releases **v0.1.22–v0.1.23** included a reusable `deploy/` package. For deployments
consuming it, copy those manifests and any bootstrap configuration from your
existing pinned tag into your infrastructure repository. Compare rendered
resources before changing the source, preserving namespaces, PVCs, selectors,
secrets and application ownership. Do not point the old remote paths at newer
releases. Keep the same Argo Application if using Argo CD; never let two Applications
manage the same resources. Back up first, preserve session/database credentials
and bootstrap behavior, and verify storage, authentication and event state after
the source change. A source move is not a reason to seed demo data or reinstall.
