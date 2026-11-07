# Participant onboarding and roster contract

## Approved behavior

Participants register their own native CTFd account with name, email and password.
Morning registration collects an unassigned pool; teams form only after actual
attendance. Each person later confirms “I'm ready to quest”; this never starts
competition clocks. Organizers close capture and preview/apply random teams from
ready eligible people. Default maximum size is five, persisted and editable
before apply. Teammates share native progress on their own devices. Default final
names are Team 1, Team 2, etc., locked against participant changes. Optional
participant naming is controlled separately. The current contract is
[attendance-first.md](attendance-first.md).

Onboarding phases are `check_in`, `closed`, `naming`, `finalized`. Initially team preparation
is open while attendance capture is off; see [attendance-window.md](attendance-window.md). An organizer closes it, previews ready-only random teams, and explicitly
applies the reviewed plan. Preview includes only ready, eligible participants,
drops no-show assignments without deleting accounts, preserves full groups where
possible and minimizes moves. Capacity is hard; 80% minimum is soft. Use the
minimum number of teams, spreading a tiny tail across the last groups. Examples
at size five: 21 => 5/4/4/4/4; 22 => 5/5/4/4/4; 11 => 4/4/3 (warning).
Size ten, 36 may retain 10/10/8/8. Stable random previews do not reshuffle an unchanged pool. Existing safe cohorts
use minimum-move optimization; historical rosters remain protected.
A server-bound fingerprint covers readiness, eligible participants, memberships,
team identities and settings; stale apply fails atomically. Do not trust edited
client groups. Empty ready pools cannot finalize. Existing native teams are
preserved if they have activity; no history is deleted by optimization.

After apply, phase is `naming` (final team review in the UI). By default the server
locks generated names immediately, so organizers can finalize without participant
name input. With `allow_team_naming` enabled, any ready current member chooses a
unique final name once. Explicit confirmation explains that it becomes fixed.
Concurrent claims serialize; only the first succeeds. Generated names are
provisional only in that mode. Existing chosen or organizer-corrected names do not
reopen when the toggle is re-enabled. Disabling naming locks current names and
retains chosen ones. Organizer corrections require a reason and audit entry.
Naming policy is fixed after finalization. Competition start requires finalized
rosters and final names. Existing live/closed events retain eligibility and clocks;
upgrades preserve native accounts/teams and never seed fake users.

Ready is personal, timestamped, idempotent and survives membership moves. It can
also be recorded during an explicitly opened late attendance window, appearing
as a late arrival; before apply
this invalidates any older preview. After apply, unassigned late arrivals appear
in an organizer queue and may be placed manually with a reason, before finalizing,
within capacity. If all groups are full, a reasoned create-late-team action
creates an additional team under the saved naming policy for ready unassigned arrivals without
reshuffling any existing group. Reopening closed check-in is allowed before apply. After apply,
no automatic rebalance is offered, because names belong to the final groups.
Native account bans remain available for moderation without moving frozen roster
members; banned accounts are excluded from eligible attendance, while all native
members still count toward capacity. Assigned account role changes freeze after
apply. Before finalization, organizers may remove ineligible members with a
reason. Empty unused native team records remain intact and are omitted from
standings unless they have historical activity.

No self-service native team create/join/invite/rename or native admin mutation may
bypass this lifecycle. Native password/account settings remain usable. Own-team
rosters respect the existing roster release setting and expose names/readiness,
never teammates' email addresses. Organizers retain email visibility.

## API seam

All existing envelopes/authentication/CSRF rules apply. New fields are additive.

- GET `/onboarding` (authenticated participant):
  `{phase, team_size, allow_team_naming, ready_at, late, roster_released, team, can_ready, can_name,
  attendance}`. The attendance window must be open for a new confirmation.
  `team` is null before release/unassigned, otherwise
  `{id,name,name_locked,members:[{id,name,ready_at}]}`. Never expose other teams or
  emails. `can_name` additionally requires participant naming enabled, naming phase, ready
  current membership and an unlocked team.
- POST `/onboarding/ready`: no timestamp input, idempotently records own readiness;
  returns the same participant shape. Guest/admin/banned cannot check in.
- POST `/onboarding/name`: `{name, confirm:true}`, claim once, returns own shape.
- GET admin state gains `onboarding:{phase,team_size,allow_team_naming,ready_count,pending_count,
  unassigned_ready_count,unnamed_count,attendance}`. Participants gain `ready_at`, `late`,
  `banned`; teams gain `name_locked`, `ready_count` (all existing fields retained).
- PATCH `/admin/onboarding`: nonempty subset of `{team_size,allow_team_naming}`.
  Capacity is editable before apply; naming policy also during draft final team
  review, before finalization.
- POST `/admin/onboarding/action`: `{action:close_checkin|reopen_checkin|finalize}`.
  Naming also supports `{action:create_late_team,user_ids:[...],reason}` for
  capacity-limited groups of ready unassigned late arrivals.
  Finalize checks ready eligible assigned members, capacity, names, no unresolved ready
  queue. Non-ready accounts remain outside teams after optimization.
- PATCH `/admin/attendance`: `{enabled:boolean}`; explicitly control capture
  independently of registration and team preparation. Closing team check-in also
  closes capture; reopening team preparation does not open capture.
- POST `/admin/onboarding/optimize`: `{preview:true}` =>
  `{fingerprint,groups:[{team_id:number|null,name,user_ids}],moves:[{user_id,
  from_team_id:number|null,to_group:number}],unassigned_user_ids,warnings}`.
  Apply `{preview:false,fingerprint}`; server recomputes the exact stable random plan
  under the event write lock and verifies the fingerprint, then applies once.
  Capture must be closed for preview/apply and finalization; opening and closing
  it invalidates a previously reviewed plan.
- Existing POST `/admin/teams/allocate` retains preview/groups contract for initial
  allocation during check-in only, restricted to ready eligible unassigned people
  with capture closed. The primary UI uses attendance-first preview/apply; configured
  capacity is authoritative.
- Existing PATCH `/admin/teams/<id>` accepts reasoned corrections. During naming,
  it may add ready unassigned late arrivals within capacity, with reason. In
  finalized phase, no membership or name edits except a reasoned admin name
  correction before competition start. Never bypass post-start identity locks.

## Implementation and verification plan

Backend owns plugin and Python tests, including additive migration, shared write
serialization, capacity/attendance optimizer, endpoint authorization, stale-plan
and concurrent name claims, native guard closure. Frontend owns web components,
types and styles: participant onboarding and teammate state; organizer attendance
workflow, search/filter, preview moves, warnings and irreversible naming copy.
Integration owns docs, browser journeys, release and deployment. No extra service,
secret, email sending or external participant collection is introduced.

Before dispatch, the existing CTFd account/team API and shared event write lock
were inspected. Native registration already handles account credentials. Existing
allocation can be evolved without replacing CTFd models. Demo content remains
loaded, draft, and continues to block event start independently of onboarding.
Review each component against the concrete contract, then the combined branch.
Run changed integration tests during implementation; full dialect/build/runtime
CI gates once at release, then exercise isolated real participant/admin sessions.

Account role, access and deletion operations are defined in [user-lifecycle.md](user-lifecycle.md). Initial allocation preview includes a server-bound fingerprint, required with groups on apply; account incarnations participate in that fingerprint as well as optimizer fingerprints.

Controlled post-finalization attendance and admission are defined in [late-admission.md](late-admission.md). They add only checked-in, history-free, unassigned newcomers, without moving existing players or resetting clocks; initial allocation and optimization stay locked.
