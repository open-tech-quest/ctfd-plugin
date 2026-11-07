# Attendance-first team formation and optional naming

Approved 2026-11-05: morning registration collects native individual accounts;
team formation waits until attendance has been captured. Registration never
assigns a team or records readiness. Staff and banned accounts remain excluded.

## Organizer and participant flow

1. Open public registration; leave attendance capture closed. Registrants wait
   without a team or permission to play.
2. Open attendance later. New and existing signed-in participants explicitly
   confirm readiness through the existing automatic dialog.
3. At the attendance cutoff, close registration as appropriate and move to
   attendance review, which closes capture. No team is created automatically.
4. Preview random teams from ready eligible participants only. Use the saved
   maximum size (default five) and existing 80% tail-balancing rules. There is
   no initial allocation prerequisite. Review and explicitly apply the proposal.
5. By default the server assigns final names **Team 1, Team 2, Team 3, …**.
   Participants cannot rename them. Finalize rosters, then explicitly Start event.
6. If the organizer enabled participant naming, any ready current teammate can
   choose the final unique name once before finalization. Finalization waits for
   these names. A captain is not required to perform that action.

The main Teams view offers this attendance-first path. Legacy allocation cannot
assign unconfirmed registrants. Previewing/applying/finalizing never starts a
competition clock. Registration and attendance remain independent controls.

## Randomness, review and history

Random placement must not follow registration or numeric user-ID order. Repeated
previews for unchanged state must return the same proposal, and Apply must use
exactly that reviewed proposal. A secret-bound stable random ordering is suitable;
clients cannot select a seed or alter groups. The existing roster fingerprint
must cover the naming policy, eligibility, account incarnations, readiness,
capacity, memberships and activity. Changed state rejects an older proposal.

Existing activity-bearing rosters are preserved or rejected with an actionable
reason; random formation cannot move historical contributions. Safe prior groups
may retain cohorts under the existing minimum-move optimizer. No account/team/
submission history is deleted. The ordinary new-event path starts with no teams.

## Naming policy and API

Add persisted `allow_team_naming:boolean`, default **false**, to participant and
organizer onboarding state. PATCH `/admin/onboarding` accepts `team_size` and/or
`allow_team_naming`. Capacity stays editable only before apply. Naming policy is
editable while draft and before finalization, including the final team-review
phase currently called `naming` internally. Native team APIs cannot bypass it.

When naming is disabled, apply locks generated names as final. When enabled,
generated names remain provisional and existing personal claim rules apply.
Enabling naming after apply opens only generated, unclaimed names for one claim;
it never reopens an already chosen or organizer-corrected name. Disabling naming
locks current names without discarding a chosen name. Track provenance explicitly
so repeated toggles cannot reset the once-only guarantee. All policy changes are
audited, invalidate outstanding proposals and refresh held participant/admin views.
Policy is immutable after finalization/start. Organizer corrections retain their
existing reason requirement and timing restrictions.

New names use unpadded numbers, avoid case-insensitive collisions with retained
teams and are not renumbered when identities must be preserved. Migrations retain
existing named/finalized/active teams and their clocks. Existing applied naming
workflows must remain completable; new/unapplied events default to naming off.

Pre-finalization additional teams follow the saved policy. Post-finalization
controlled admission still requires ready history-free newcomers, capacity,
fingerprint and a reason. Admin-agreed generated Team N names are valid final
names there. Existing competing players, names, starts and progress stay fixed.

## Verification and release

Exercise the actual morning registration → later readiness → random ready-only
preview/apply → automatic locked names → finalize/start journey. Separately check
optional one-time member naming, repeated toggles, stale requests/previews,
concurrent policy/name actions, staff/banned exclusion, capacity and short tails,
legacy upgrade and late admissions, with no premature clocks or history changes.
Run focused SQLite/MariaDB regression cases and frontend checks during development,
independent correctness review, isolated real-browser journeys, then the full CI
gate. Publish a source-matching GHCR image, pin both containers to the tested tag and digest,
verify healthy rollout and preserved live event data, and update fleet notes.

## Isolated acceptance

An actual 0.1.14 upgrade retained four fictional morning registrations and an
already-authored challenge, with no attendance or team assignment. The new
policy defaulted off. An existing signed-in user received the attendance dialog
when capture opened; a newly registered user received it immediately. Six ready
participants and one no-show still had no teams until the reviewed plan was
explicitly applied. Repeated previews matched; apply created exactly Team 1 and
Team 2 with three members each, warned about the unavoidable 80% shortfall, kept
the no-show unassigned and left all clocks unset. Generated names were locked,
native/direct rename paths remained blocked, and the organizer finalized and
started the event without any participant name confirmation.

A separate disposable branch exercised optional naming: a ready teammate chose
one final name; disabling/re-enabling preserved that claim and reopened only the
other generated/unclaimed name. Disabling while a teammate held a confirmation
closed the dialog through polling and rejected a stale direct request. Both
chosen names remained unique and locked. The organizer screen's WCAG A/AA audit
reported zero violations and zero incomplete checks. No production users, teams,
attendance or competition state were modified for these checks.

The optional-naming journey also finalized and started successfully. A normal
native challenge solve was shared with a randomly assigned teammate. Restart
preserved naming policy, chosen names, readiness, memberships, solve credit,
starts and award/ranking values; only response-generation timestamps advanced.
The Teams view fit a 390px viewport without document-wide horizontal overflow.
All owned disposable browser/database resources were removed after verification.
