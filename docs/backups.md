# Backup and restore

**Organize → Backup & Restore** lets a signed-in administrator create, download,
upload, review and fully restore event backups. Maintainers and API tokens cannot
use these controls. Hosting must first configure the external backup worker;
otherwise the page explains that backups are unavailable.

An [event JSON archive](archives.md) is a shareable report. A `.otqbackup` file is
private recovery material containing **all accounts and password hashes, roles,
tokens, flags, hints, native/plugin tables, timing/history, saved archives and
uploaded files**, plus the runtime configuration needed by an operator. Protect
it like the database: downloads are authenticated but are not encrypted files.
The worker's Restic repository is encrypted and deduplicated. Keep its password,
signing keys and a separate data copy outside the application cluster.

## Organizer workflow

1. Check the target installation, scheduled-backup health and snapshot time.
   **Create backup** uses the same capture engine and repository as the schedule.
2. Download a snapshot for safekeeping, or upload an unmodified backup signed by
   a worker key trusted by this installation. Uploading stores/verifies it; it
   does not restore anything. Event reports and arbitrary ZIP/SQL files are refused.
3. **Review full restore** compares account/team/challenge/solve counts and shows
   the producer versions, target and consequences. An incompatible snapshot stays
   downloadable for operator inspection with a specific refusal reason.
4. Supply a reason, acknowledge full replacement and the safety backup, and type
   the displayed target name. The review expires after ten minutes and is bound to
   the current installation, software, schema, round and administrator authority.
5. Keep the progress screen open. It follows the external worker while CTFd is
   offline. After success everyone must sign in using credentials from the backup.

This replaces the whole event; it does **not** retain the executing account or
merge later work. Accounts, edits, solves, purchases and archives created after
the selected snapshot disappear. Old passwords, roles and native tokens return;
review any later credential revocations. Recorded timestamps remain original,
and the real-world clock keeps advancing. Restoring a live event resumes its saved
phase with elapsed wall time, not a replay clock.

The worker stops traffic and writers, takes a separate safety backup, replaces
SQL and uploads together, verifies bytes/data, clears dedicated Redis sessions,
restarts CTFd and checks it before reopening. This maintenance fence is independent
of the event's sporting Pause control. A failure keeps the application closed for
operator recovery; it does not attempt an unreviewed rollback. If the browser loses
the initial reply, it checks the existing operation UUID rather than submitting a
second restore. After a tab reload or expired status ticket, ask the operator to
check the durable job journal before starting another operation.

## Compatibility

| Backup/target | Automatic restore |
|---|---|
| Same exact CTFd/plugin image and schema, same installation and runtime configuration | Supported after this installation's recovery drill |
| Different plugin image, unchanged schema | Only an explicitly tested exact source→target qualification configured by the operator |
| Different Quest or native schema, database/cache image, credentials, database settings or storage identity | Operator recovery/qualification required |
| Older unsigned Restic capture | Inspection/extraction only; never silently promoted to an automatic restore |
| Unknown signature/key, altered bytes or unsupported backup format | Refused before importing SQL |

The backup container format is `open-tech-quest/backup`, version **1**. Exact
producer versions, native/Quest revisions and image digest travel in the signed
manifest. Matching schema labels alone do not prove compatibility: the complete
table structure, database character settings, data-service pins and runtime
credentials must match too. Future schema changes must keep old formats readable
or explicitly document an operator migration. Never add a version range to the
qualification list without testing the real recovery path.

For one bad instruction, flag, hint or attachment, an operator extracts a private
copy and deliberately reapplies selected content through the current guarded
editor. This preserves current IDs, solves and purchase receipts. There is no
granular SQL merge in Organizer. See [recovery choices](deployment.md#recover-a-changed-event).

## Hosting the worker

The worker is a deployment component, separate from the CTFd web process. It needs
Python 3.9+, Podman, `oc`, durable private storage and the four stdlib modules
`backup_contract.py`, `backup_worker.py`, `backup_openshift.py`, `backup_operator.py`
from the **tested plugin tag**. Copy them together to one worker directory; run
`python backup_worker.py --config /private/worker.json serve` under a supervised
service. Keep its configuration, keys, jobs and recovery journal outside restored
SQL/uploads. Use the [example configuration](../examples/backup-worker.json) as a
guide; it is not a deployable infrastructure package.

Each profile pins an API server, application Route, installation ID and PVC UIDs.
Give each installation a separate high-entropy API token and Restic repository;
retain old signing keys when rotating. The profile token selects its fixed target:
neither the browser nor a restored database supplies a kubeconfig or target.
Use TLS at the worker ingress, restrict the listener to the trusted network and
allow only each application's exact origin for read-only operation status.

Set these CTFd environment variables:

| Variable | Value |
|---|---|
| `OTQ_BACKUP_WORKER_URL` | Worker HTTP(S) origin reachable from CTFd, without a path |
| `OTQ_BACKUP_WORKER_TOKEN` | That installation's private profile token, from a separate Secret |
| `OTQ_BACKUP_STATUS_ORIGIN` | External HTTPS worker origin reachable by the browser |
| `OTQ_BACKUP_MAINTENANCE_FILE` | Default `/var/quest-backup/maintenance.json` on persistent shared control storage |
| `OTQ_BACKUP_MAX_UPLOAD_BYTES` | Optional proxy upload ceiling; default 8 GiB |

Use a separate control directory on the application data claim, mounted at
`/var/quest-backup` in CTFd and the recovery helper. Its maintenance marker survives
web restarts and must **not** be part of restored uploads. Use `/healthcheck` for
startup/readiness; during maintenance all other requests return 503 before native
authentication or database writes. Skip hosting bootstrap/setup mutations while
the marker exists. The supplied adapter supports one healthy web replica and one
predeclared zero-replica `ctfd-backup-helper` Deployment, using the same pinned image,
database/session environment and uploads/control claim mounts. It runs a sleeping
Python process without a web listener, provider token or API token. Permit its
label to reach MariaDB/Redis; do not give it the web Service label.

The external worker requires named Deployment reads, pod listing/exec, reads of
the application Secret/configuration/storage/Route, scale access to only the web
and helper Deployments, and get/patch on only the owning Argo Application to suspend
reconciliation during replacement. It does not restore Kubernetes objects or
credentials from the backup. Operators provide/requalify those separately. Enable
`restore_enabled` only after testing the exact hosting setup with disposable data.

Schedule the same operator command through your supervised scheduler:

```sh
python backup_operator.py --config /private/worker.json --profile event backup --scheduled
```

Schedule `check` and `drill` as well. All mutations, downloads and operator jobs
share a repository lock. A schedule overlapping a long restore fails visibly
rather than competing. Alert on failures and a last successful scheduled backup
older than two intervals; a manual backup must not hide a failed scheduled job.

Capture uses an InnoDB transactional SQL dump, checks stable schema/configuration
and upload inventories before/after, then verifies the dump by importing/re-exporting
it in an isolated MariaDB container. Ordinary solves may continue during capture;
coordinate schema upgrades separately. Restic stores raw SQL and individual files
for chunk deduplication. Portable downloads are deterministic signed ZIPs; format
v1 fixes upload gzip compression at level 9, independently of Python defaults.
No signing key or worker token is inside them. Default retention keeps every point
for ten days (daily pruning may retain up to eleven), plus at least the latest.
Redis and application logs are excluded; the durable event audit is in SQL.

`backup_operator.py` also offers `status`, `snapshots`, `init`, `check`, `extract`
and `drill`. **Extract writes only to private worker storage**, never the live
application. A drill imports the selected SQL into its recorded MariaDB image,
requires a byte-identical re-export, then verifies referenced uploads and native
login with the recorded CTFd/Redis images on an internal Podman network, without
published ports or cluster authority. This checks recovery, not replacement-cluster
provisioning or cutover time. Full failure recovery remains an operator procedure:
inspect the external job/recovery journal and safety snapshot, qualify the target,
complete/repair the replacement while fenced, and remove the marker only after
verification. Never simply resume Argo and clear the marker after a failed restore.
