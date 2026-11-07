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

## Upgrades and existing deployments

Back up SQL and uploads, retain the previous image reference, and test the next
image against a disposable copy before an event. Preserve session keys, database
credentials, resource identities and storage. Image rollback does not undo a
schema migration; check compatibility or restore a matching backup.

Releases **v0.1.22–v0.1.23** included a reusable `deploy/` package. For deployments
consuming it, copy those manifests and any bootstrap configuration from your
existing pinned tag into your infrastructure repository. Compare rendered
resources before changing the source, preserving namespaces, PVCs, selectors,
secrets and application ownership. Do not point the old remote paths at newer
releases. See [source migration](org-migration.md) for the migration checklist.
