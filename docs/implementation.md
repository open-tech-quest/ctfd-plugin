# Delivery plan

This is tier L: persistent timing, authentication, prize fairness, and a
new frontend span multiple components. Delivery includes the public repository,
versioned GHCR images, a Compose hosting example and runtime/sizing guidance.
Operators maintain production manifests in their own infrastructure repository.

1. Plugin implementation owns `plugin/` and Python tests: schema/migrations,
   CTFd guards, event lifecycle, shared room progress, award calculations,
   allocation/admin API, app shell, export and final snapshot.
2. Frontend implementation owns `web/`: participant lobby/rooms/challenge
   interaction, themed room components, standings and 50-team stage, admin
   controls, responsive accessibility and purposeful motion. The API
   contract in design.md is shared; changes require explicit coordination.
3. Integration owns container/CI/docs/assets/deploy: pinned upstream, frontend
   build, GHCR pipeline, runnable Compose example, anonymous image pull,
   operations and isolated validation.
   Only curated web assets from public/approved sources enter the image.
4. Review risky plugin and frontend paths independently using review-bar.
   Findings require a concrete trigger, real consequence, verified path,
   and in-scope cause. Zero findings is valid; no style/hypothetical findings.
5. Run meaningful backend integration tests, frontend check/build and browser
   journeys. Exercise 50-team projector, load behavior, and persistence in an
   isolated environment, with production credentials absent.
6. Publish the tested source commit via CI: versioned image, matching immutable
   Git tag, OCI source/revision labels and a `latest` image alias for examples.
   Do not generate deployment metadata commits. Operators pin the image version
   and digest in their own manifests and verify migration, login and event state.
7. Update deployment documentation and asset intake guidance. Report shipped
   behavior and any real limitation. Do not claim authored event challenges
   or final assets exist when organizers have not supplied them.

Pre-dispatch verification: the current CTFd 3.8.9 source was audited; native
Requirements are enforced at reads/attempts; team account_id shares solves;
plugin migration and admin menu APIs exist; the 50-team layout study fits
1920x1080. Empty-install checks verify draft status and no generated challenge,
team or solve data. Runtime secrets are supplied by the operator. See [hosting](deployment.md) for
configuration and migration boundaries.

Unslop audit of the study: heavy type weights and inconsistent 2/3/5px
spacing need refinement; the miniature mobile matrix needs a separate
responsive presentation; room artwork slots must become real composition
with optional assets rather than visible placeholder boxes. Existing brand
direction and information order are retained.

Design process attribution: unslop by Damir Sokolovsky
(https://x.com/heydamir), CC BY 4.0, informed this design/redline process.
No skill source or reference imagery is distributed with the application.
