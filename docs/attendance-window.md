# Controlled attendance window

## Behavior

Public registration, attendance capture and competition start are independent
organizer actions. Registration may be open for days while attendance is off.
Accounts created then remain unconfirmed and cannot start draft-event rooms.

Attendance starts **off** on fresh installs and upgrades. An organizer explicitly
opens it from **Organize → Event → Attendance check-in** (the same control is
available in Teams). Only then can a participant confirm **I'm ready to quest**.
Opening a page, registering or signing in never records attendance automatically.
The prompt explicitly asks the person to confirm they are at the event and
participating. No competition clock starts during attendance.

An unconfirmed user already signed in to any Quest SPA route receives a dialog
within five seconds of opening attendance. Returning to a background tab checks
immediately. A user registering or signing in while attendance is open sees the
same dialog immediately. “Not yet” dismisses that opening's prompt, leaving a
visible way to confirm later; a later reopening can prompt unconfirmed users
again. Confirmed users keep their confirmation and are not asked again.

Closing attendance hides the prompt and rejects new confirmations on the server,
even from an old form or a stale page. Existing confirmations, original timestamps,
rosters and progress are retained. Already-confirmed retries are idempotent.
Native account pages show an attendance notice linking back to Quest; they must
not silently navigate away from an unsaved account form.

## State and lifecycle

Add fields to QuestOnboarding with migration quest_0005: attendance_open=false,
attendance_generation=0, attendance_opened_at and attendance_closed_at (UTC with
the same microsecond precision as existing timestamps). Increment generation on
each closed-to-open transition, not on polls or idempotent writes. Audit each
actual window change with its source. Startup never reopens capture or clears
old readiness. Production was verified as draft/check_in with zero users/teams
and zero ready records before this implementation; no prior check-ins need reset.
Existing populated/live events preserve their native records and eligibility.

Opening is allowed only in draft and onboarding phases check_in, closed or
naming. A naming-phase opening provides explicit late-arrival capture. Finalized
or started events cannot reopen attendance. New confirmations also require these
conditions under the existing shared event write lock. Off always means no new
confirmation, including in closed/naming, which previously allowed implicit late
check-in. Late classification still follows the roster-review/naming phase.

Capture toggles do not implicitly change roster phases. The existing close_checkin
roster action closes capture atomically and moves to review. Reopen_checkin returns
to preparation without implicitly opening capture. UI phase names/actions must
clearly distinguish team preparation/review from the separate attendance window.
Optimization preview/apply and roster finalization require capture closed. Event
start also requires capture closed. Include window state/generation in the plan
fingerprint so any intervening open/close invalidates an older review.

Migration must handle fresh installation: quest_0004 creates tables from current
ORM metadata, so quest_0005 must introspect and add only missing columns. Preserve
existing times, solves, teams, accounts and readiness during actual 0.1.10 upgrade.
Pinned CTFd skips Alembic for SQLite and only calls create_all; run the same
introspective schema step under BEGIN IMMEDIATE before SQLite ORM reads so old
columns are upgraded and simultaneous worker startups serialize.

## API seam

`AttendanceWindow`: `{enabled,generation,opened_at,closed_at,can_open,reason}`.
Reason explains why opening is unavailable; it is null when opening is allowed.

- GET `/onboarding` gains `attendance: AttendanceWindow` and derives can_ready
  from the window and existing eligibility. Ready POST retains its `{}` body.
- Admin state's `onboarding` gains the same `attendance` field.
- PATCH `/admin/attendance` accepts exactly `{enabled:boolean}`, requires the
  existing administrator session and CSRF, and returns AttendanceWindow.
- Bootstrap returns the current session’s `csrfNonce` with its viewer and uses
  `Cache-Control: private, no-store`. Apply both only from the accepted bootstrap
  response before interaction, including after native logout/login in another tab.
- No new public participant details or administrator tokens are exposed.

Use one shared authenticated-participant onboarding resource in the SPA, polling
at five seconds, with focus/visibility/online refresh. Share it between the lobby
card and global dialog, and reset its identity when the signed-in account changes.
Native branding JS may poll the same authenticated endpoint for the native-page
notice; skip guests, stop on 401/403 (including administrators), and skip SPA
paths so notices are not duplicated. Keep motion restrained and respect reduced
motion; dialogs need labels, focus management and an explicit confirmation.

For tabs left open on pre-feature SPA code, new clients send
`X-Quest-Attendance-UI: 1` on API requests. When attendance is open and an
unconfirmed eligible participant's old browser fetch polls bootstrap
(`Sec-Fetch-Dest: empty`, no capability header), return a clear 409 message
asking for one refresh to load attendance. Existing ordinary SPA error handling displays
it. A pre-feature Stage page suppresses detailed bootstrap errors, so a tab
holding that older Stage/app version needs one initial refresh to load the new
participant session. Ask participants with pages left open across this upgrade
to refresh once; thereafter attendance toggles update automatically on every
Quest route, including Stage. Guests, administrators, confirmed users, closed windows and ordinary
non-browser API consumers keep normal bootstrap responses. The header signals
UI support only and never authorizes attendance. New clients get the dialog;
legacy tabs can also use their existing inline ready button while capture is open.

## Implementation and verification

Tier L because attendance is persistent eligibility used for roster optimization.
Backend agent owns plugin and Python tests: additive migration, state/API, shared
write serialization, lifecycle guards/fingerprint and compatibility response.
Frontend agent owns web: shared participant resource/dialog/card, organizer
controls in Event/Teams, native notice, styles and capability header. Both use
this seam; changes require coordination. Parent owns docs, isolated browser
journeys, CI/release/deployment. Review each unit independently and the combined change.

Pre-dispatch checks inspected native account template init (userId is available),
current polling/focus hooks, API locks, readiness ownership, migration structure
and production aggregate state. No external secret, new service or reset needed.

Meaningful cases: register early/off; register during open; already signed in
on lobby and another room/standings route; return from a background tab; dismiss
and reopen; two devices for one user; close versus stale/in-flight confirmation;
invalid/admin/CSRF input; no fake clock starts; explicit late window; closed
optimization/finalization guards; legacy browser refresh message; old DB upgrade
and fresh install on SQLite/MariaDB. Native-page notice must not interrupt edits.
Run changed tests during implementation, full required gates once before release.
Production rollout leaves attendance off and registration in its current state;
validation is read-only, with no production users or confirmations seeded.

## Isolated browser acceptance

Verified an actual 0.1.10 SQLite upgrade with accounts and native sessions
preserved. Tested early registration with capture off; registration while open;
automatic prompts on Standings and Stage; background-tab activation; dismissal
and reopening; confirmation across two devices; closing against a deliberately
stale form; and native account switching without a full-page reload. Native
account notices preserved an unsaved settings edit. Existing confirmations kept
their timestamps and capture never started competition clocks.

Organizer and mobile-dialog WCAG A/AA scans reported no violations. The scanner
could not automatically determine one dialog paragraph’s background because of
the underlying Stage overlap; its rendered dark text on white was inspected.
The mobile dialog fit a 390px viewport without horizontal scrolling.

## Release verification — 2026-11-03

Released [0.1.11](https://github.com/open-tech-quest/ctfd-plugin/releases/tag/v0.1.11)
from source `9b80b7d`. [CI](https://github.com/open-tech-quest/ctfd-plugin/actions/runs/41725040989)
passed 72 backend tests on each of SQLite and MariaDB, frontend checks/build,
empty-install runtime smoke and the container’s test target. The historical
installation validated its manifests, deployed matching initialization/runtime
images and retained its persistent volumes and runtime secrets. A private
SQL/uploads backup preceded migration.

Read-only live checks verified capture off at generation zero, registration
still public, the draft event and all 25 demo challenges unchanged, and zero
participants/teams/readiness records unchanged. All four served application and
native branding assets matched the tested local build byte-for-byte. No production
participants or confirmations were created and capture was never opened for QA.
