# Deploying Open Tech Quest

Use an immutable release of this repository to deploy CTFd with the plugin,
MariaDB, Redis and persistent storage. Kubernetes uses an Ingress; OpenShift uses
a Route. Neither Argo CD nor an external secret manager is required.

The examples use release **v0.1.22** and namespace **ctfd**. Review the release's
`release.json` for its version, source commit, image and digest. The manifests
and image version are updated together in a release-only commit, tested, and
published under the same immutable `vX.Y.Z` tag. Use that tag, never `main`, as a
deployment source. Pin the accompanying image digest for reproducible rollouts.

## Prerequisites and layout

You need a cluster with **Linux amd64 worker nodes**, dynamic persistent-volume
provisioning, namespace creation rights, a suitable ingress/router and HTTPS
certificates. The published application image currently supports **amd64 only**;
the public base constrains the CTFd pod with `kubernetes.io/arch: amd64`. This
release does not add multi-architecture image support. The OpenShift
overlay also grants its service account access to the existing `nonroot-v2` SCC;
applying that binding requires the corresponding cluster permission. Render with
**Kustomize 5.8.3**, the version used for manifest validation. A `kubectl`-bundled
Kustomize may be a different version.

| Path | Includes |
|---|---|
| `deploy/base` | Namespace, CTFd, service account, configuration/bootstrap and uploads/logs PVC; expects external database/cache URLs |
| `deploy/dependencies/mariadb` | MariaDB Deployment, Service and data PVC |
| `deploy/dependencies/redis` | Redis Deployment, Service and data PVC |
| `deploy/stack` | Base plus both bundled dependencies |
| `deploy/overlays/kubernetes` | Stack, Ingress and ingress network policy |
| `deploy/overlays/openshift` | Stack, Route, SCC binding and router network policy |
| `deploy/components/event-400` | Measured 400-attendee starting resources and eight CTFd workers |
| `deploy/examples/event-400` | OpenShift overlay plus the event component |

Default profiles use a small, two-worker CTFd allocation. They are not the
400-attendee allocation. All profiles keep one CTFd pod with a **Recreate** rollout
strategy and single-writer storage. PVC requests are 10 GiB for uploads/logs,
10 GiB for MariaDB and 1 GiB for Redis, all ReadWriteOnce. No storage class is
hard-coded; choose your cluster's class and capacity before creating the claims.

## Fresh installation

An existing event needs the [migration checklist](#migrating-an-existing-deployment)
instead. In particular, do not generate replacement secrets for an existing stack.

```sh
git clone --branch v0.1.22 --depth 1 https://github.com/open-tech-quest/ctfd-plugin.git
cd ctfd-plugin
kubectl create namespace ctfd
python3 scripts/create-deploy-env.py --output ctfd.env
kubectl -n ctfd create secret generic ctfd-secrets --from-env-file=ctfd.env
```

The helper writes random hex credentials with mode `0600` and refuses to overwrite
an existing file. It generates exactly these Secret keys:

| Key | Purpose |
|---|---|
| `SECRET_KEY` | Stable CTFd session/signing key |
| `DATABASE_URL` | CTFd connection to the bundled MariaDB service |
| `REDIS_URL` | CTFd connection to the authenticated bundled Redis service |
| `MARIADB_PASSWORD` | CTFd's MariaDB account password |
| `MARIADB_ROOT_PASSWORD` | MariaDB initialization password |
| `REDIS_PASSWORD` | Redis service password |
| `CTFD_ADMIN_PASSWORD` | Initial administrator password |

Keep a private backup of this file or move its values into your own secret store;
never commit it or put its contents in an issue, CI log or rendered artifact.
Kubernetes Secret encoding is not encryption. Configure cluster access and
at-rest protection appropriately. An optional secret controller can supply the
same native Secret; it is not part of this stack.

Before applying a profile, set the administrator email and public hostname in a
local overlay. The initial account name defaults to `admin` and email to
`admin@example.com`. Override `CTFD_ADMIN_NAME` and `CTFD_ADMIN_EMAIL` in the
`ctfd-config` ConfigMap generator. These are initialization settings, not a way to
reset an existing administrator's credentials.

### Kubernetes

The default Ingress uses `quest.example.com` and TLS Secret `ctfd-tls`. Configure
DNS, your ingress controller and a real certificate. No ingress class is forced;
set `spec.ingressClassName` if your cluster needs one.

```sh
kubectl -n ctfd create secret tls ctfd-tls --cert=/path/to/tls.crt --key=/path/to/tls.key
mkdir site
```

Create `site/kustomization.yaml`, replacing the email, hostname and image digest:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: ctfd
resources:
  - ../deploy/overlays/kubernetes
configMapGenerator:
  - name: ctfd-config
    behavior: merge
    literals:
      - CTFD_ADMIN_EMAIL=organizer@example.com
patches:
  - target:
      kind: Ingress
      name: ctfd
    patch: |-
      - op: replace
        path: /spec/rules/0/host
        value: quest.example.com
      - op: replace
        path: /spec/tls/0/hosts/0
        value: quest.example.com
images:
  - name: ghcr.io/open-tech-quest/ctfd-plugin
    newTag: 0.1.22
    digest: sha256:REPLACE_WITH_RELEASE_DIGEST
```

If required, add an Ingress patch operation with `op: add`, path
`/spec/ingressClassName` and your controller's class as its value. The Kubernetes
network policy permits inbound web traffic on port 8000; restrict its sources
for your ingress controller if your environment requires it.

```sh
kustomize build site > rendered.yaml
kubectl apply --dry-run=server -f rendered.yaml
kubectl apply -f rendered.yaml
```

### OpenShift

Use `../deploy/overlays/openshift` as the resource in the same local overlay,
remove the Ingress patch, and keep the email/digest overrides. The Route provides
HTTPS through the cluster router. Configure its hostname/certificate for your
installation and verify DNS. The router policy selects the OpenShift ingress
namespace; adapt it if your cluster uses a different router placement.

```sh
kustomize build site > rendered.yaml
oc apply --dry-run=server -f rendered.yaml
oc apply -f rendered.yaml
oc -n ctfd get route ctfd
```

The service account `ctfd-runtime` receives `nonroot-v2` through the namespaced
`ctfd-nonroot` RoleBinding. The CTFd pod runs as non-root UID 1001; it does not require
privileged containers or `anyuid`. Keep the platform-specific grant rather than
disabling admission checks globally.

## Event profile: 400 attendees

For a checked-out release, start with `deploy/examples/event-400`, then apply your
site's email, Route and image-digest overrides. To consume the release directly
from another repository, create a local Kustomization like this:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: ctfd
resources:
  - https://github.com/open-tech-quest/ctfd-plugin//deploy/overlays/openshift?ref=v0.1.22
components:
  - https://github.com/open-tech-quest/ctfd-plugin//deploy/components/event-400?ref=v0.1.22
configMapGenerator:
  - name: ctfd-config
    behavior: merge
    literals:
      - CTFD_ADMIN_EMAIL=organizer@example.com
images:
  - name: ghcr.io/open-tech-quest/ctfd-plugin
    newTag: 0.1.22
    digest: sha256:REPLACE_WITH_RELEASE_DIGEST
```

For Kubernetes, substitute the Kubernetes overlay and add the Ingress/TLS
configuration above. Create the native Secret separately before applying.

| Component | CPU request / limit | Memory request / limit |
|---|---|---|
| CTFd, 8 workers | 4 / 6 cores | 1 / 2 GiB |
| MariaDB | 500m / 1 core | 512 MiB / 1 GiB |
| Redis | 250m / 500m | 128 / 256 MiB |

The component encodes the **0.1.20 measured starting profile**: 400 authenticated
HTTP clients, 50 teams of eight, challenges running elsewhere. It is not a new
capacity measurement for every subsequent release or cluster. See
[sizing](sizing.md) for the test method, limits and unmeasured estimates. That
capacity evidence is from OpenShift; a manifest render or a fresh-install smoke
does not establish event-scale capacity or ingress/TLS readiness elsewhere.

## Verification and operations

```sh
kubectl -n ctfd rollout status deployment/database
kubectl -n ctfd rollout status deployment/cache
kubectl -n ctfd rollout status deployment/ctfd
kubectl -n ctfd get pods,pvc,svc
```

Check the public HTTPS URL, native login and `/quest/admin`. A fresh deployment
starts in draft with five empty rooms and no generated participants, teams or
solves. The initializer creates the initial administrator; the plugin never
loads demo event data at startup. Check init-container logs if bootstrap fails:
`kubectl -n ctfd logs deployment/ctfd -c initialize`.

Check the image architecture against your nodes and the published image metadata;
do not assume every node architecture is supported. Test the site's ingress,
certificate, storage permissions, restart persistence and backups before an event.
Local port forwarding can check the Service but does not qualify HTTPS or venue
networking. SMTP, database/uploads backups and monitoring remain operator setup;
see [operations](operations.md) for the event workflow.

For an external database/cache, consume `deploy/base` instead of the stack and
supply matching `DATABASE_URL`/`REDIS_URL` values in the native Secret. Do not add
the bundled dependency manifests. Configure network access, TLS, backups and
resource/pool limits for those services separately.

## Upgrading and rolling back

Read the release notes, take a private database/uploads backup, and render/diff
the next tagged release before applying it. Update the immutable manifest tag and
matching image digest together, including the `initialize` and `ctfd` containers.
`release.json` records the tested version, tag, source commit, image and digest;
repository maintainers must protect released tags from deletion or retargeting.

Keep the previous source tag and exact image reference in your rollback record.
Migrations run before serving requests. Rolling back code does not reverse a
database migration: verify compatibility or restore the matching private backup
with the event closed. Preserve `SECRET_KEY`, database/cache credentials and all
PVCs. Do not delete the namespace as an upgrade or rollback step.

## Migrating an existing deployment

A deployment-source change does not require a new application, namespace or data
import. Keep existing native user/team/challenge IDs, event history and application
ownership intact.

1. Back up the database and uploads privately. Record the current source revision,
   image digests, runtime Secret provider, resource names, selectors, storage
   mounts and PVC bindings. Retain a tested rollback reference.
2. Render the chosen immutable release with a local compatibility overlay. The
   defaults are namespace `ctfd`; Deployments/Services `ctfd`, `database`, `cache`;
   PVCs `ctfd-data`, `database-data`, `cache-data`; service account `ctfd-runtime`;
   Secret `ctfd-secrets`; and containers `initialize`, `ctfd`, `mariadb`, `redis`.
   Match existing names rather than provisioning a second empty stack.
3. Preserve the namespace's existing labels/annotations and application ownership
   metadata in the operator's overlay. If using Argo CD, change the source of the
   **same Application** to your tagged wrapper; never have two Applications manage
   the same objects. Review prune behavior before switching source. Keep Namespace
   and PVCs out of any prune/delete transition, then verify the rendered diff.
4. Keep the current native Secret and its existing provider. Do not regenerate
   session keys/passwords or bootstrap over an existing database. Review changes
   to admin initialization settings; they must not reset existing accounts.
5. Apply the reviewed source change, verify the init migration and rollout, check
   the same PVCs are still Bound, and compare authenticated read-only event,
   registration, attendance, accounts, teams, starts and solves before/after.
   Verify native login and the served version/assets without creating production
   test participants, starts or submissions.

To change namespace for a **new** installation, set `namespace` in the outer
Kustomization; it updates the Namespace and namespaced resources together. The
OpenShift binding uses the binding's namespace for its service account subject.
Create the native Secret and any TLS Secret in the same namespace and inspect the
rendered resources. Changing an existing event's namespace requires a separate
storage/data migration; a namespace transformer cannot move PVC data. See also
[repository migration](org-migration.md).
