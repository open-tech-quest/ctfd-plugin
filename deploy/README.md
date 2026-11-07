# Kubernetes and OpenShift deployment

Start with the [deployment guide](../docs/deployment.md) for secrets, HTTPS,
storage and upgrades. Use a published `vX.Y.Z` tag.

- `overlays/kubernetes` and `overlays/openshift` are complete installations,
  including MariaDB and Redis.
- `components/event-400` adds the measured event resources and eight workers.
  `examples/event-400` combines it with the OpenShift overlay.
- `base` contains CTFd alone. `dependencies` contains the database/cache;
  `stack` assembles all three. Use the base when supplying existing services.

On `main`, `dev` marks an unpublished development build. CI selects the next
version from Git tags and stamps **one image pin**, in `base/kustomization.yaml`,
in the release commit. Kustomize applies it to both init and runtime containers.
CI checks the rendered stacks against that version before publishing the matching
image and immutable tag. No manual version bump or separate version file is needed.
