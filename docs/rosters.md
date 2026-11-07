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

## Review, apply and naming

Phases are `check_in`, `closed`, `naming`, `finalized`. The ordinary path is:

1. Collect unassigned registrations; capture attendance when the organizer opens it.
2. Close capture and move to review (`closed`). Preview only ready eligible people.
3. Apply the reviewed plan; enter final team review (`naming`), even if participant
   naming is disabled.
4. Finalize with capture closed, attended people assigned, valid capacity and final
   team names. Start the event separately.

Capacity defaults to five and is a hard maximum counting **all native members**.
The optimizer's 80% target is soft: it balances a short tail and reports unavoidable
small groups. Existing safe cohorts use minimum-move grouping; activity-bearing
rosters are protected. No-show accounts and unused team records are not deleted.

Unchanged state produces the same secret-bound random preview, not registration/
ID ordering or a client-selected seed. The fingerprint covers account incarnations,
eligibility, readiness, memberships, team identity/activity, capacity, naming policy
and attendance generation. Apply recomputes under lock and rejects a stale review
atomically. The optimizer never accepts edited client groups.

Generated `Team 1`, `Team 2`, … names are unique and locked by default.
`allow_team_naming` defaults false; when enabled, any ready member of a released
team can confirm one unique final name. Claims serialize. Enabling after apply
opens only generated, unclaimed names; chosen or administrator-corrected names
never reopen. Disabling locks names without discarding them. Capacity freezes
after apply; naming policy freezes after finalization. Administrative corrections
retain their reason and pre-start restrictions.

## Arrivals after review or finalization

During draft review/naming, the organizer can explicitly reopen capture and
place ready, unassigned late arrivals within capacity, or create an additional
team. Before apply, changed attendance requires another preview. After apply,
there is no automatic reshuffle.

After finalization, late-only capture and dedicated admission are allowed in
**draft, live or paused**, until cutoff. Only eligible, unassigned, history-free
newcomers can confirm/admit. Existing members cannot use late admission to switch
teams. Admission needs a current fingerprint and reason, plus exactly one target:
a named visible/non-banned team with space, or an agreed unique final new name.

Existing teams keep starts/progress; new teams follow the configured event/room
clock origins and explicitly start their rooms. Admission never rewrites solves,
creates readiness or resets clocks. Closed/published/cutoff events refuse it.
Ready newcomers confirmed after finalization may wait unassigned across Start
once capture closes; earlier unresolved ready queues still block finalization/
start. Late admission is the dedicated exception to frozen membership.

## Privacy and native routes

Before roster release, participant team data is withheld. Afterwards, users see
only their own team's names/readiness, never teammate email. Native self-service
team create/join/invite/rename and native administrator writes cannot bypass
capacity, naming or frozen-roster rules. Ordinary account/password settings remain
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
| Confirm presence | `POST /onboarding/ready`, empty body; no client timestamp |
| Claim final name | `POST /onboarding/name`, `{name, confirm:true}` |
| Configure signup/window | `PATCH /admin/registration` or `/admin/attendance`, `{enabled:boolean}` |
| Configure capacity/naming | `PATCH /admin/onboarding`, `team_size` and/or `allow_team_naming` |
| Review/finalize | `POST /admin/onboarding/action`; see server actions for phase-specific bodies |
| Preview/apply grouping | `POST /admin/onboarding/optimize`, `{preview:true}` then `{preview:false,fingerprint}` |
| Admit after finalization | `POST /admin/onboarding/admit`, `{user_ids,reason,fingerprint}` plus `team_id` or `new_team_name` |

The older `/admin/teams/allocate` remains supported for ready, eligible,
unassigned people before apply, with capture closed. It has a separate
`groups` plus fingerprint apply contract; it is not the optimizer endpoint.
Initial legacy-event migration preserves existing active rosters/clocks and locks
their names. The naming migration preserves already-applied optional-name flows;
startup never renumbers teams, reshuffles users or resets attendance policy.
