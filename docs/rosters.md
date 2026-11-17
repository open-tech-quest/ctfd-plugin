# Registration, attendance and rosters

This is the implementation contract. For organizer instructions, use
[event operations](operations.md). Current response types live in
[`web/src/types.ts`](../web/src/types.ts); routes and guards live in
[`api.py`](../plugin/open_tech_quest/api.py),
[`onboarding.py`](../plugin/open_tech_quest/onboarding.py) and
[`registration.py`](../plugin/open_tech_quest/registration.py).

## Independent controls

Public signup uses native CTFd `registration_visibility`, not a second setting.
Only `public` enables local signup. The audited Quest control works in any event
phase; closing it rejects even a previously loaded native form. Existing accounts
can still sign in. Native signup and registration changes serialize under the
shared event lock and read authoritative state after waiting; native config HTTP
writes cannot bypass this control.

Attendance defaults off on a new installation. Existing windows, readiness and
memberships survive restarts/upgrades. A participant explicitly confirms their
own presence; registration, login, opening a dialog and team assignment never
create readiness or start clocks. Confirmation uses the server time and is
idempotent. Staff and banned accounts are ineligible.

Opening increments the window generation; polling and same-state writes do not.
Closing rejects new confirmations from stale forms but retains previous ones.
Window changes do not implicitly change roster phase. Moving to attendance review
closes capture; reopening preparation does not reopen capture automatically.

## Formation policy, review and naming

**Require attendance before forming teams** defaults on. It is a saved event
policy independent of the attendance window. Change it only in Draft before a
plan has been applied, with capture closed. Existing installations retain the
attendance-based flow; upgrades do not create readiness or change memberships.

Phases remain `check_in`, `closed`, `naming`, `finalized`. The formation paths are:

- **Attendance required:** collect registrations, explicitly capture presence,
  close capture and move to review (`closed`), then preview/apply ready eligible
  participants.
- **Attendance not required:** close public registration, then preview/apply all
  eligible registered participant accounts directly from the participant pool or
  review. Login/check-in is unnecessary. Staff and banned accounts are excluded;
  registrations may include no-shows. Signup must still be closed when applying.

Applying enters final team review (`naming`). Finalize after checking membership
and capacity, then start the event separately. Every occupied competing team needs
a valid name, but generated `Team 1`, `Team 2`, … names already satisfy that rule.
Choosing a custom name is never a finalization or launch prerequisite.

Capacity defaults to five and is a hard maximum counting **all native members**.
The optimizer's 80% target is soft: it balances a short tail and reports unavoidable
small groups. Existing safe cohorts use minimum-move grouping; activity-bearing
rosters are protected. No-show accounts and unused team records are not deleted.
Native IDs start at 1 when no teams remain after either deliberate round reset;
new teams use the next available number under the event lock. Names remain
independent of these IDs.

Unchanged state produces the same secret-bound random preview. The fingerprint
covers account incarnations, eligibility, attendance policy and registration,
readiness, memberships, team identity/activity, capacity, naming policy and
attendance generation. Apply recomputes under lock and rejects stale reviews
atomically; client-edited groups are not accepted.

`allow_team_naming` defaults false. When enabled, an eligible current member of a
released, visible, non-banned team can rename it repeatedly throughout **Draft**,
including after roster finalization. Attendance is required for the naming member
only in attendance-required mode. Saving includes the displayed team ID and name;
a concurrent rename or transfer requires refreshing instead of overwriting newer
state. Existing custom or organizer-corrected names remain editable under this
policy. Disabling preserves names and stops participant edits. Organizers can
change this policy throughout Draft. Live, Paused, Closed and Published reject
renaming; roster finalization alone does not lock the display name.

## Organizer moves during Draft

**Move participant** supports unassigned → team, team → team and team → unassigned
throughout Draft, including final team review and finalized rosters. Use it to
rebalance groups and remove no-shows without deleting or banning their accounts.
The organizer supplies a reason and current roster fingerprint. A single locked
transaction rechecks authority, eligibility, source membership, destination
visibility/capacity and competition history, then repairs captain ownership and
invalidates both teams' cached state. No readiness, name or competition record is
deleted or fabricated. Naming history alone does not prevent a move.

Assigning requires readiness only in attendance-required mode. In that mode a
ready participant moved back to unassigned still needs placement before launch
under the usual queue rules. In registration-only mode organizers may deliberately
leave accounts unassigned; at least one eligible assigned team is required.
Changes after finalization are revalidated by Start. Moves never rerun the random
distribution. Teams with competition activity cannot be reorganized, even in an
unusual Draft database, and ordinary transfers stop at Live (including Paused).

## Late arrivals

Before apply, changes to the participant pool require a fresh preview. After
apply, use explicit placement or another team; there is no automatic reshuffle.
Draft moves remain available after finalization.

After finalization, dedicated admission remains available in **draft, live or
paused** until cutoff. Newcomers must be eligible, unassigned and history-free.
In attendance-required mode they explicitly confirm presence through a late-only
window; in registration-only mode no attendance confirmation is needed. Admission
requires a current fingerprint, organizer reason and exactly one destination:
a visible, non-banned team with capacity or a unique name for a new team.
Existing members cannot use late admission to switch teams.

Existing teams keep progress and clocks. New teams use configured clock origins
and explicitly start their rooms. Ready newcomers confirmed after finalization
may wait unassigned across Start once capture closes; earlier unresolved ready
queues still block attendance-based finalization/start. Closed/published/cutoff
events refuse admission. No path invents attendance timestamps.

### Place waiting participants

After finalization, **Place waiting participants** previews an add-only batch in
Draft, Live or Paused, before cutoff. It fills vacancies in existing competing
teams, then creates balanced new teams within capacity. Hidden, banned,
award-excluded and ineligible teams are skipped. Only eligible unassigned people
without competition history are included; attendance is required only under the
saved attendance policy. Existing memberships, names, numbers, locations and
competition history are preserved. New teams start without progress.

Registration and late check-in may stay open. The reviewed batch is frozen:
new arrivals remain queued for the next preview instead of invalidating this one.
The signed review expires after 15 minutes and is bound to its administrator,
selected account incarnations, affected team memberships/names/locations and
event policy. Apply rechecks these under the event lock and commits all placements
and the audit atomically; changed or already-placed people require a new preview.
Ordinary progress in existing teams does not invalidate a Live review. Each batch
contains at most 1,000 people. No client-supplied grouping is accepted.

Locations are a separate action. Run the default location distribution afterward
to seat teams without locations. Leave **Reassign existing locations** unchecked:
already-seated teams keep their tables. A location preview opened before team
placement must be refreshed. This is not a rerun of the initial optimizer, which
remains unavailable after its first apply. Use **Late admission** for a manually
selected group/destination.

## Privacy and native routes

The dashboard shows attendance and naming controls during draft preparation.
Once play starts, assigned participants see a collapsed **Team members** list
without readiness badges or setup messages. Unassigned late arrivals retain
check-in and placement guidance during live/paused play. Closed/published events
keep the member list for assigned teams and hide unfinished onboarding.

Before roster release, participant team data is withheld. Afterwards, users see
only their own team's names/readiness, never teammate email. Native self-service
team create/join/invite/rename and native administrator writes cannot bypass
capacity, naming or lifecycle rules. Ordinary account/password settings remain
available. Role/access/deletion changes invalidate reviewed plans.

Quest pages use the shared five-second `/sync` heartbeat and revision-triggered
full reads. Identity and CSRF nonce are accepted together; stale bootstrap results
cannot overwrite newer live state. Focus/visibility/online refreshes respect that
coordinator. Native account pages use `/onboarding` for a notice linking to Quest,
without discarding unsaved edits. The capability header is a compatibility signal,
not attendance authorization. An upgrade may require refreshing old open tabs;
it does not close an existing attendance window.

## API entry points

All paths below are under `/api/v1/quest`, with native session/CSRF protection.

| Operation | Route / body |
|---|---|
| Read own state | `GET /onboarding` |
| Confirm presence (attendance mode) | `POST /onboarding/ready`, empty body; no client timestamp |
| Save draft team name | `POST /onboarding/name`, `{name, confirm:true, team_id, expected_name}` |
| Configure signup/window | `PATCH /admin/registration` or `/admin/attendance`, `{enabled:boolean}` |
| Configure formation/naming | `PATCH /admin/onboarding`, `team_size`, `allow_team_naming` and/or `require_attendance` |
| Move participant in Draft | `POST /admin/onboarding/move`, `{user_id, team_id, fingerprint, reason}`; null team means unassigned |
| Review/finalize | `POST /admin/onboarding/action`; see server actions for phase-specific bodies |
| Preview/apply grouping | `POST /admin/onboarding/optimize`, `{preview:true}` then `{preview:false,fingerprint}` |
| Admit after finalization | `POST /admin/onboarding/admit`, `{user_ids,reason,fingerprint}` plus `team_id` or `new_team_name` |
| Place a waiting batch | `POST /admin/onboarding/waiting`, `{preview:true}` then `{preview:false,token,reason}` |

The older `/admin/teams/allocate` remains supported for eligible, unassigned
people before apply, with the same attendance policy and closed-capture rules.
Registration-only allocation requires signup closed. It has a separate
`groups` plus fingerprint apply contract; it is not the optimizer endpoint.
Initial legacy-event migration preserves existing active rosters/clocks and locks
their names. The naming migration preserves already-applied optional-name flows;
startup never renumbers teams, reshuffles users or resets attendance policy.

## Venue rooms and team locations

**Organize → Teams → Manage locations** configures physical venue rooms, separate
from the challenge rooms. Enter each venue name and table count; tables run from
1 within each room. Mark unavailable tables by number and order the rooms for
fill-first distribution. One table holds one whole team. Table numbers are
independent of the native team numbers used in description placeholders.

The venue layout remains editable throughout Draft, including after roster
finalization. Before removing an occupied room/table, reducing capacity below an
assigned table or marking it unavailable, move or clear the affected assignments.
Room renames update the displayed location without moving anyone.

Assign a room/table manually or preview automatic distribution. **Spread across
rooms** balances team counts within room capacity; **Fill rooms in order** fills
the first room before the next. By default, both preserve existing assignments and fill only
unassigned, occupied, eligible participant teams. Applying checks the current
review again, rejects a table shortage and never double-books. Occupied tables
require an explicit swap between two assigned teams. Repeated previews are stable;
repeating a default distribution makes no moves unless new teams need locations.

For a different Draft seating plan, explicitly select **Reassign existing
locations**. Its preview includes current/manual placements and their proposed
destinations; applying replaces active participant-team assignments atomically.
Other occupied tables remain reserved. The default continues to fill only
unassigned teams. Confirmation is bound to the current layout, teams, strategy,
scope and round; changing any requires another preview. Full redistribution is
Draft-only, including after roster finalization. Individual moves and placement
of new teams remain available during play.

Only administrators can write locations, through manual assignment or reviewed
distribution. Participants see their own saved team location wherever Team number
appears: the dashboard, pre-event team details and room view. Assignment follows
roster release; other teams' locations are not exposed. An unassigned team sees
**Location: Not assigned yet**. Renaming/moving participants does not move a team's
table; a participant moved to another team sees that destination team's location.
Location writes refresh through the existing sync heartbeat, without new polling.

Assignments can also be changed in Live or Paused, without altering memberships,
points or clocks. Closed/Published are read-only. Location assignment is separate
from formation/finalization and never silently distributes or reshuffles teams.
Starting another round clears assignments and retains the venue layout; Restore
defaults clears both. JSON reports retain the original layout/locations, while
full backups restore their database state.
