# Delivery plan

This is tier L: persistent timing, authentication, prize fairness, and a
new frontend span multiple components. Delivery includes the public repository,
versioned GHCR images and standalone deployment manifests. An operator may
consume releases directly or through their own deployment automation.

1. Plugin implementation owns `plugin/` and Python tests: schema/migrations,
   CTFd guards, event lifecycle, shared room progress, award calculations,
   allocation/admin API, app shell, export and final snapshot.
2. Frontend implementation owns `web/`: participant lobby/rooms/challenge
   interaction, themed room components, standings and 50-team stage, admin
   controls, responsive accessibility and purposeful motion. The API
   contract in design.md is shared; changes require explicit coordination.
3. Integration owns container/CI/docs/assets/deploy: pinned upstream, frontend
   build, GHCR pipeline, immutable release manifests, anonymous image pull,
   operations and isolated validation.
   Only curated web assets from public/approved sources enter the image.
4. Review risky plugin and frontend paths independently using review-bar.
   Findings require a concrete trigger, real consequence, verified path,
   and in-scope cause. Zero findings is valid; no style/hypothetical findings.
5. Run meaningful backend integration tests, frontend check/build and browser
   journeys. Exercise 50-team projector, load behavior, and persistence in an
   isolated environment, with production credentials absent.
6. Publish the tested version via CI. Main uses the `dev` image marker; CI derives
   the next version from Git tags and stamps one Kustomize image pin. That
   release-only commit passes the release gate and becomes the
   immutable `vX.Y.Z` tag. Consumers pin that tag and its published image digest
   in both CTFd init and runtime containers. Verify migration, root/room/admin
   routes, real session login, CTFd health and absence of seeded event data.
7. Update deployment documentation and asset intake guidance. Report shipped
   behavior and any real limitation. Do not claim authored event challenges
   or final assets exist when organizers have not supplied them.

Pre-dispatch verification: the current CTFd 3.8.9 source was audited; native
Requirements are enforced at reads/attempts; team account_id shares solves;
plugin migration and admin menu APIs exist; the 50-team layout study fits
1920x1080. Empty-install checks verify draft status and no generated challenge,
team or solve data. Runtime secrets are supplied by the operator's native
Secret, never by repository-specific infrastructure. See
[deployment](deployment.md) for platform setup and migration boundaries.

Unslop audit of the study: heavy type weights and inconsistent 2/3/5px
spacing need refinement; the miniature mobile matrix needs a separate
responsive presentation; room artwork slots must become real composition
with optional assets rather than visible placeholder boxes. Existing brand
direction and information order are retained.

Design process attribution: unslop by Damir Sokolovsky
(https://x.com/heydamir), CC BY 4.0, informed this design/redline process.
No skill source or reference imagery is distributed with the application.
