# Administration and contribution integrity

For operator steps, see [event operations](operations.md). This document records
constraints for changes to roles, authoring and counted progress. Actual payloads
are defined in [`web/src/types.ts`](../web/src/types.ts) and the backend routes;
never infer authorization from the visible UI.

## Roles and account lifecycle

| Role | Access |
|---|---|
| Administrator | Event, teams, users, rooms, exports and projector controls; native CTFd admin |
| Challenge maintainer | Room management and supported challenge authoring only |
| Participant | Own released team, attendance and eligible play |
| Guest | Public information only |

Maintainers remain native users with a plugin grant. There are no grants on
startup. Staff are excluded from attendance, allocation, naming, starts, attempts,
unlocks and prize credit. Banning a maintainer retains its grant so moderation
cannot turn staff into a player. Grants hide staff from native public listings,
saving the previous hidden value; revocation restores it and invalidates cached
role/team data.

Promotion or account deletion is refused for personal competition/naming history,
team activity or assigned optimized/frozen rosters. Safe provisional detachments
clear readiness, repair captain ownership and retain team records and other users.
Actual role transitions clear attendance; demotion or unbanning never restores old
readiness or invents a membership. Native administrators remain managed in CTFd,
with self/last-usable-admin and history protections.

Those per-account/history protections remain in force during ordinary operation.
The explicit [end-of-event reset](archives.md#two-danger-actions) is a separate
admin-only exception: in draft, closed or published state, after an atomic archive,
it removes every other account (including staff/admins) and teams/play. Defaults
mode also clears challenge content and restores empty default rooms. A mode-bound
fresh preview, typed event name, reason, CSRF and post-lock administrator validation
are mandatory. The executing administrator and existing server archives remain;
unsupported dependent plugin tables cause safe refusal. Native metadata writes
serialize with this transaction rather than racing account/content deletion.
Live or paused events must be closed first.

User role/access/delete writes require `identity` (account incarnation) and
`confirm:true`. Reused numeric IDs cannot authorize stale actions. Every write
rechecks persisted actor session/password, role and banned state after taking
the event lock, then checks the target and activity in that transaction. Queued
requests cannot succeed for a revoked actor or a deleted/replaced target.
Responses with user data or saved answers are `private, no-store`; audit records
contain IDs and action metadata rather than passwords, tokens or submitted flags.

Account routes under `/api/v1/quest`:

- `GET /admin/users` supplies identities and server-derived capability/reason fields.
- `PATCH /admin/users/<id>/role`: `{role, identity, confirm:true}`.
- `PATCH /admin/users/<id>/access`: `{active, identity, confirm:true}`.
- `DELETE /admin/users/<id>`: `{identity, confirm:true}`.

Native mutation paths enforce the same policy; participant/maintainer deletion
uses the identity-bound Quest action. See
[`lifecycle.py`](../plugin/open_tech_quest/lifecycle.py) and
[`roles.py`](../plugin/open_tech_quest/roles.py).

## Room and challenge authoring

Room managers can rename presentation, add custom rooms and remove unused rooms.
Slugs stay stable; deletion unmaps challenges but preserves native content and
refuses starts, activity or referenced history. Topology/order changes are draft
only. Theme edits cannot change IDs, starts or submissions.

The room editor creates standard challenges with static, any-flag grading and a
default value of 100, editable as a nonnegative whole number. It supports
Hidden/Visible/Locked, Markdown descriptions and
case-sensitive or explicitly case-insensitive flags. Accepted answers are readable
in the authorized editor. Each challenge can have multiple native CTFd hints,
with an optional title, Markdown text and nonnegative point cost; zero means free.
Saving preserves existing hint IDs and prerequisites. Referenced or purchased
hints cannot be removed. Advanced flags remain read-only in the simple editor;
the bounded [automation API](automation.md) also preserves native static/regex
flags and supports metadata, solutions and owned assets. Unsupported custom
challenge/flag/hint behavior and advanced settings/prerequisites remain native
administrator work. The editor never silently normalizes them. Editing preserves
native files and unchanged values; omitted hints are preserved by the API.

New authoring requires draft status, no relevant activity and no loaded demo.
Every challenge ID must belong to the requested room. Reordering updates Quest
steps, native Requirements, next links and positions atomically. Deleting a
challenge also rejects dependencies from outside the room. Preview checks native
flag comparison without recording attempts, progress or clock starts. Saved flags
are confined to authorized editor responses, not overview/bootstrap/audit output.

Room management uses `/manage/rooms`, room settings use `/admin/rooms/<slug>`
(the room-manager exception to admin-only routes), and challenge CRUD/preview
uses `/manage/rooms/<slug>/challenges`. See
[`authoring.py`](../plugin/open_tech_quest/authoring.py) and
[`room_topology.py`](../plugin/open_tech_quest/room_topology.py) for current bounds
and activity guards. Native editing remains subject to competition protections;
room-editor authoring is not a live-event editing workflow.

For scripts, the [automation guide](automation.md) maps these endpoints and native
CTFd permissions, including access-token and session/CSRF authentication. Stable
source keys never imply name-based adoption; unmapped owned entries require an
explicit, eligible reattachment. Atomic room clearing preflights every mapped
challenge and external dependency before deletion. Asset deletion removes owned
metadata, never another file owner's bytes; provider cleanup remains an operator
task. Maintainers do not gain global native authoring permissions.

### Links in Markdown

Links to another website open in a new tab automatically, so participants keep
their challenge page open. Use ordinary Markdown:

```md
[Open the lab](https://lab.example.org)
```

Same-site links and fragments use the current tab. For an explicit target, use
HTML, for example `<a href="/quest/standings" target="_blank">Standings</a>`.
`?target=_blank` is a URL parameter; `{target="_blank"}` attribute syntax is not
supported. New tabs receive opener protection automatically.

## Contributions and recovery

A ban revokes access and removes that user's counted contributions without deleting
native accounts, memberships, attempts, solves, flags or timestamps. It may detach
only an unused provisional member before apply; historical membership remains.
Changing a published contributor's eligibility requires explicit unpublication.

Each team/challenge counts its earliest eligible original or recovery solve once.
Losing an early solve relocks later steps but retains other eligible solves'
timestamps and distinct room/overall credit. Milestone times are
recomputed from currently counted solves, not retained from a disqualified faster
contribution. Recovery uses its new server time. Unbanning can restore the earlier
original time, with no duplicate credit and no clock reset.

CTFd's team/challenge native-solve uniqueness prevents inserting another ordinary
solve while retaining the original. The plugin therefore records a protected
`quest_recovery` submission and recovery mapping. Recovery still uses the native
attempt endpoint, challenge checker, rate/attempt limits and failure logging.
Ordinary attempts and staff previews retain native behavior.

After native code closes/commits a session, recovery reacquires the event lock and
rechecks account/session, membership, role, released roster, live/unpaused event,
room start, effective prerequisites and cutoff. Concurrent teammates cannot earn
duplicate effective credit. A correct response follows the committed recovery;
errors, partial/rate/auth failures and incorrect flags cannot become credit.

Retained native prerequisites can overstate eligible progress. Plugin guards must
therefore check the effective chain for detail, attempts, hints, files, unlocks and
solved-only solutions. Participant, public, projector, admin and CSV views share
the same counted projection; native `solved_by_me` alone is not sufficient.

Native submission history retains both original and recovery evidence. A restore
or rollback must include plugin code that understands `quest_recovery`; running
older code against that submission type is unsafe. See
[`recovery.py`](../plugin/open_tech_quest/recovery.py),
[`recovery_models.py`](../plugin/open_tech_quest/recovery_models.py) and
[`guards.py`](../plugin/open_tech_quest/guards.py).
