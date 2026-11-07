# Repository and deployment source migration

The canonical source is [open-tech-quest/ctfd-plugin](https://github.com/open-tech-quest/ctfd-plugin),
with images at `ghcr.io/open-tech-quest/ctfd-plugin`. Changing repository ownership
or moving deployment manifests between infrastructure repositories does not require a new CTFd
instance. Accounts, event/room IDs, native submissions, clocks and storage must
remain intact.

Production manifests belong in the operator's infrastructure repository. See
[hosting](deployment.md) for runtime requirements and migration from the former
upstream manifest package. Pin the tested image version and digest; Git tags now
identify application source, not a Kubernetes deployment API.

## Moving repository ownership

Preserve the existing repository rather than recreating it: verify repository ID,
Git history, refs, tags, issues, pull requests, release IDs and license before and
after transfer. Back up the Git refs and release metadata privately. Recheck
organization access, workflow token permissions, hooks, branch/tag protection,
package visibility and anonymous image pulls. Update active clone remotes,
source links and OCI source labels.

Do not invent new registry paths for previously published images. Keep the exact
previously deployed image reference in the operator's rollback record; a source
repository transfer does not move existing registry packages automatically.

## Moving deployment ownership

Keep the current namespace, resource names, selectors, service account, PVCs and
runtime Secret. Preserve the existing application's ownership metadata. If using
Argo CD, update the source of the **same Application** after reviewing rendered
manifests; do not introduce a second Application that manages the same objects.
Keep the application's namespace and PVCs outside any prune/delete transition.
Retain an existing secret-manager integration if it already supplies the native
Secret; moving manifest ownership does not require replacing it.

Back up the database and uploads, compare manifests against live objects, and
verify migrations, storage attachment, authentication and event data before
restoring normal reconciliation. A repository/source move is never a reason to
run demo fixtures, regenerate session/database passwords, recreate the namespace
or reset native CTFd configuration. Database schema changes may require restoring
a matching private backup to roll back; an image downgrade alone is insufficient.

## Verified completion — 2026-11-05

The historical organization transfer retained repository ID `1444206634`, all 16
existing tags, all 15 release IDs, prior pull requests, public visibility and
Actions/token permissions. No application, frontend, branding, test or script
source changed from 0.1.15.

[Migration PR #3](https://github.com/open-tech-quest/ctfd-plugin/pull/3) passed the
[pre-transfer build gate](https://github.com/open-tech-quest/ctfd-plugin/actions/runs/42078547377).
Merge source `0b367861f9645030d97619021c2edb119b5cff2b` passed
[organization release CI](https://github.com/open-tech-quest/ctfd-plugin/actions/runs/42080318727):
150 cases per SQLite/MariaDB, three skips per dialect, frontend, empty-install
runtime and publication checks. CI published
[0.1.16](https://github.com/open-tech-quest/ctfd-plugin/releases/tag/v0.1.16).

An anonymous full linux/amd64 image pull and OCI source/revision/version checks
passed for:

```text
ghcr.io/open-tech-quest/ctfd-plugin:0.1.16@sha256:e3c482115fbd99609068177a8f9fbc9f0c645744f9199fb5733c5eb2ce8bea6d
```

The historical installation retained matching init/runtime pins, database/cache
pods, all three bound PVCs and runtime secrets. Read-only checks verified matching
frontend bytes and unchanged event, content, accounts, attendance and teams. A
private SQL/uploads backup preceded cutover. This records that transfer's
verification; it does not qualify other platform configurations.
