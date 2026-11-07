# Challenge maintainers and integrated authoring

Approved 2026-11-04: administrators manage all accounts in an admin-only Users
tab, promoting self-registered accounts to Challenge maintainer. Maintainers
see only Rooms in Organize and can configure all five rooms and author their
standard flag challenges there. Teams remains the attendance/roster workspace.
CTFd retains accounts, password handling, challenges, flags and grading.

Additional approved scope: the five topic rooms stay installation defaults.
Room managers may rename display titles, add custom rooms and remove unused
rooms. Slugs remain stable identifiers; a rename does not break links/history.
Custom rooms default to a neutral `generic` layout with optional branded assets.
Room navigation, standings and projector derive their room count from data.
An intentionally empty room set remains empty across restarts.

## Authorization and staff lifecycle

Keep native user types unchanged. Add a plugin-owned, user-FK-cascading maintainer
grant table/migration; no grants on startup. Native admins remain administrators;
this feature never promotes/demotes a full administrator. Only admins can grant
or revoke maintainer access. Check the database on every privileged request so
revocation applies to open tabs and API requests. Existing CSRF/session rules apply.

Viewer adds `role: guest|participant|maintainer|administrator`,
`can_manage_rooms: boolean`, `can_manage_users: boolean`, retaining `admin`.
Regular participants, banned accounts and guests cannot call management APIs.
Maintainers cannot use native `/admin` or administrative native APIs, or read
quest admin state, users, email lists, teams, audit, corrections, event controls,
exports, projector controls, registration or attendance controls. They must not
receive those payloads even if tabs are hidden. Admins retain existing access.

Exclude maintainers from participant resources, attendance prompts and capture,
allocation pools, optimization, finalization counts, team naming, room starts,
native attempts/unlocks and award eligibility. Include grants in roster plan
fingerprints. Reject native team assignment of staff through existing guards.

Role writes serialize under the event lock. Promotion of an account on an unused
provisional team is allowed only while draft/preparation or attendance review
(`check_in`/`closed`); detach it without deleting the team or other users. Clear
its readiness on an actual role transition so role removal never resurrects old
attendance. Reject promotion if the account has competition activity, or its
assigned team has activity, or assigned rosters have already been optimized.
Unassigned inactive accounts may become staff later. Revocation always remains
available and leaves the account unassigned/unconfirmed; normal onboarding and
roster lifecycle still govern subsequent participation. Native administrators
are displayed read-only in Users; their management stays native CTFd.
Audit transitions without credentials/flag values and invalidate native user/
team caches. Hide staff from native public listings while granted, preserving
their previous hidden value for revocation. Preserve all existing event data.

Account demotion, safe deletion, deactivation and incarnation-bound actions are
extended by [user-lifecycle.md](user-lifecycle.md). Its identity-token contract
supersedes the original role-write body below.

## Shared API seam

Existing `/api/v1/quest` envelope and session/CSRF rules apply.

- GET `/admin/users` (admin only): `{users: ManagedUser[]}`.
- PATCH `/admin/users/<id>/role`: `{role: participant|maintainer, confirm:true}`
  returns ManagedUser. Idempotent same-role writes preserve readiness/history.
- `ManagedUser`: `{id,name,email,role,banned,team_id,team_name,ready_at,
  can_change_role,role_change_reason}`. Blocked actions explain why.
- GET `/manage/rooms` (admin/maintainer): `{event,rooms,available_challenges}`.
  Shape matches those fields of existing admin state, with admin room metadata.
  No participant, team, email, audit or flags in this overview. Rooms include
  `can_configure_topology`, `topology_reason`, `can_delete`, `delete_reason`
  for actionable lifecycle/demo/history restrictions.
- Existing PATCH `/admin/rooms/<slug>` admits room managers only; all other
  `/admin/*` routes retain native-admin authorization. Existing field validation
  and draft topology restrictions remain. Room writes are audited.
- POST `/manage/rooms`: `{slug,name,subtitle?,description?,theme?}`, draft only.
  Unique slug is 1–32 lowercase letters/digits/hyphens; position appends. Default
  theme is `{layout:generic,accent:#00a6b2,logo_url:null,hero_url:null}`.
- DELETE `/manage/rooms/<slug>`: `{confirm:true}`, draft only. Refuse starts,
  challenge activity or referenced history. Unmap its ordered chain and preserve
  native challenges, flags, hints and files; never cascade-delete event history.
  Removing all rooms is allowed; the UI provides Add room in the empty state.

### Challenge editor

- GET `/manage/rooms/<slug>/challenges`: ChallengeWorkspace.
- POST same collection: `{name,description,state,flags}` creates a native
  standard challenge with fixed value 100, appends/maps it atomically and returns
  EditableChallenge. State is `hidden|visible|locked`; flags is a nonempty array of
  `{content:string,case_insensitive:boolean}`. Empty descriptions allowed.
- PATCH `/manage/rooms/<slug>/challenges/<id>` accepts those same four fields
  (partial updates allowed); returns EditableChallenge.
- DELETE same item removes an unused challenge and rebuilds the chain atomically.
- POST `/manage/rooms/<slug>/challenges/<id>/preview`: `{submission:string}`
  returns `{status:correct|incorrect,message}` using native flag comparison,
  without starts, solves, fails, unlocks, prizes or participant submission logs.
- `ChallengeWorkspace`: `{challenges:EditableChallenge[],can_create,create_reason}`.
- `EditableChallenge`: `{id,name,description,state,type,flags,
  can_edit,edit_reason,can_delete,delete_reason}`. Flags use the same input shape.
  Unsupported existing types/flag logic are read-only with a clear reason and
  no destructive normalization; preserve existing files/hints/value on updates.

Authoring creates only standard/static/any-flag challenges. Exact case-sensitive
matching is default, with an explicit per-flag case-insensitive option. Validate
input strictly and bound names/descriptions/flag sizes; never echo secrets in
errors, logs, audits, Git or bootstrap. Challenge reads and flag previews require
room management permission; require actual room membership for every ID.
Authoring responses use `Cache-Control: private, no-store`. Flag content cannot
have edge whitespace because native attempts trim submitted answers.

Create/update/delete only in Draft. Refuse modifying challenges with activity;
never erase solves/fails/history. Lock mutations of demo content while an active
demo batch exists, with instructions for an administrator to remove the demo
before real authoring. Do not silently unload demo content in production. Native
upstream admin challenge editing remains available subject to existing guards.
Ordering uses one shared helper to rebuild native requirements/next_id/positions
and QuestStep rows under the event lock. New authoring writes use one transaction;
do not call upstream helpers that commit halfway through challenge/flag mapping.

## Frontend and verification

Admin gets Users alongside existing tabs, searchable by name/email and filterable
by role. Show role, attendance, team, banned state and explicit confirmed role
change, explaining effects. Maintainer enters Organize directly at Rooms, with
no native admin link or other tabs. Shared room editing for both roles includes
new/edit/remove challenge forms, sanitized Markdown preview, static flags and
answer-check preview. Avoid resetting unsaved forms on polling. Reuse existing
visual language, accessible focus/labels/contrast and reduced-motion behavior.
Staff lobby should explain their role and link to Organize without attendance
or Start actions; ordinary participant journeys remain unchanged.

Backend owns plugin/tests, authoring owner owns its new module/helper and focused
tests, frontend owns web, parent owns docs/browser/release/deployment. No concurrent
staging/commits: parent commits completed units. Review independently using
review-bar; findings need a concrete trigger, consequence, verified in-scope path.
Meaningful checks cover authorization/PII, native endpoint bypass, staff exclusion
everywhere, fresh/upgrade SQLite/MariaDB, stale role/readiness/optimization,
grant/revoke in held sessions, CRUD/flags/native participant solving, lifecycle
and activity locks, room isolation, no logged flag values, and actual admin/
maintainer/participant browser journeys before CI publication and deployment.

## Isolated acceptance

Verified a real 0.1.11 upgrade with two self-registered accounts, a provisional
team and an attendance confirmation. The upgrade preserved all records and
created no staff grants. Promotion in Users detached the ready provisional member,
cleared readiness, hid the native public account and changed the held SPA to its
staff workspace. Maintainer requests for private admin state/users and personal
onboarding were rejected. A participant could not read the flag workspace.

Browser checks created a sixth custom room, renamed its display title with the
slug intact, authored Markdown challenges with masked static answers, verified
case-sensitive and case-insensitive previews, and reordered the native chain.
Removal retained native content, and deleting all rooms survived an application
restart with no default rooms recreated. The empty workspace could create a first
custom room and remap both retained challenges. After role revocation, a write
from the held editor returned 403. Regranting did not resurrect old attendance.

A normal participant then completed attendance, roster optimization, naming and
finalization without the maintainer in its pool. After isolated event start, the
second authored challenge stayed locked until the first native solve; both solved
through the normal participant UI and contributed only to the participant team's
awards. No production accounts, grants, event starts or content changes were used.

Users and editor WCAG A/AA scans reported zero violations and no incomplete checks.
Both layouts fit a 390px mobile viewport without document-wide horizontal overflow.
Independent review found a queued role-change race after native actor demotion;
the route now rechecks persisted admin/banned status after the shared event lock.
Real native demotion and banning regressions passed on both databases and were
independently rechecked; no findings remain.

## Release verification — 2026-11-04

Released [0.1.12](https://github.com/open-tech-quest/ctfd-plugin/releases/tag/v0.1.12)
from source `30128f4`. [Image CI](https://github.com/open-tech-quest/ctfd-plugin/actions/runs/41746150419)
passed frontend check/build, empty-install smoke, both database suites and the
container test target. Each backend run discovered 96 cases: SQLite skipped
three MariaDB-specific cases and MariaDB skipped two SQLite-specific cases;
all remaining cases passed. Every dialect-specific case ran on its target DB.

The historical rollout validated its manifests, deployed matching
initialization/runtime image digests and retained its persistent volumes and
runtime secrets. A private SQL/uploads backup preceded migration.

Read-only live verification confirmed administrator capabilities, the Users
list without seeded maintainer grants, a private-data-free room overview and
demo-locked challenge workspaces. All four served SPA/native assets matched
the tested build. The draft event, five default rooms, 25 demo challenges,
public registration and attendance off/generation zero remained unchanged;
there were still no participant/team/readiness records. Production QA did
not create users, grant roles, alter rooms/demo content or start the event.
