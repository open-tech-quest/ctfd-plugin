# Running an event

To explore this process with dummy accounts and a repeatable reset, use the
[full-event rehearsal](rehearsal.md).

## Prepare

1. Set up [hosting](deployment.md) and [backups](backups.md). Test restoration with matching
   plugin code and configure SMTP if you need password reset/email verification.
2. Ask staff to register accounts. In **Organize → Users**, grant **Challenge
   maintainer** to people who should author rooms without administering the event.
   Staff are excluded from attendance, teams and competition play. Use
   [staff browsing and user journey](preview.md) to inspect content or test paths
   with local progress.
3. In **Organize → Rooms**, configure the room set and ordered standard challenges.
   Edit points, instructions, accepted answers and multiple hints with individual
   texts/costs there. Review room point budgets and hints before play; zero-cost
   hints are free. Participants see redeemed hints and only the next hint in
   native creation order; free reveals advance the sequence too. Administrators
   use native CTFd for attachments, advanced types
   and hint prerequisites. Keep answers in the database, never Git or builds.
4. Configure event name, start/end, roster release and clock policy before play.
   The UI displays browser-local time; stored/API timestamps are UTC. Empty roster
   release means assignments become visible immediately.
5. Set maximum team size (default five). Participant naming defaults off: generated
   Team 1, Team 2, … names are valid defaults. Enable member renaming if wanted;
   it stays optional throughout Draft.
6. Test `/quest/stage` on the actual projector. **Organize → Projector** offers
   room-award overview, featured room, overall leaders, full matrix and published
   awards. Enable automatic rotation for a 60-second overview, 30-second featured
   room and 20-second overall view; each cycle features the next room. Durations
   are editable. Pause/resume preserves the cycle across reloads/projectors;
   changing durations or restarting begins a new cycle. Disable rotation to choose
   a manual view. Manual overview pages can be paused on that screen. Detailed
   room/matrix views fit 50 teams in two panels of 25 at 1080p. Large room sets and
   ties page without dropping leaders; long challenge chains use sparse milestones.
   Published awards page through room winners and shared overall winners, with a
   pause control on the projector.

Room titles, subtitles and themes are independent of competition identity.
Preview Markdown before saving. Room introductions and challenge instructions
support [personalized placeholders](administration.md#personalized-descriptions)
for team number/name, participant name and overall team points.
A blank Hero image URL uses the bundled scene;
custom assets must follow the [branding/provenance rules](../branding/README.md).

**Organize → Event → Participant standings** independently enables room comparisons
and overall standings. Both start enabled; teams always retain their own progress.
Administrators retain every view. If either comparison is disabled, the projector
and its data require an administrator login. Controls apply in any phase, including
published results, without changing awards. Settings remain across round cleanup.

## Registration and team formation

Registration and attendance are independent controls. Existing windows and saved
confirmations survive upgrades/restarts. **Require attendance before forming
teams** defaults on; choose the policy before applying teams, with capture closed.

1. Open **Event → Public registration** while collecting participants. Closing
   signup rejects even an already-loaded native form; existing users can sign in.
2. Choose the formation path in **Organize → Teams**:
   - With attendance required, open check-in at the venue. Participants confirm
     on their own devices. At cutoff, **Move to attendance review** closes capture.
     Preview checked-in teams; no-shows without check-in stay outside the groups.
   - Without attendance required, close registration and preview registered teams
     directly. All eligible registered participant accounts enter the pool without
     login/check-in, including possible no-shows. Staff/banned accounts are excluded.
3. Inspect the random balanced proposal and capacity warnings, then explicitly
   apply it. Changed accounts/settings require another preview. Registration must
   remain closed when applying the registration-only proposal.
4. During Draft, use **Move participant** to assign an unassigned person, transfer
   someone between teams, or move a no-show back to unassigned. Review the source,
   destination and team sizes, and enter a reason. Capacity is enforced. Assigning
   requires check-in only when attendance is required. Names and check-ins survive.
5. Custom names are optional. Generated names remain valid. If member renaming is
   enabled, eligible teammates can change their released team's name repeatedly
   throughout Draft, including after roster finalization. Renaming never starts
   a clock or changes membership. A stale naming form must be refreshed.
6. Finalize the reviewed rosters with capture closed and valid capacity. In
   attendance mode resolve the required ready queue; in registration-only mode
   deliberate unassigned accounts may remain outside the event. Remove demos and
   configure real challenges, then **Start event** separately. A future scheduled
   start prevents early opening; it does not replace the organizer's Start action.

Roster finalization confirms readiness to launch; organizers may still move
participants and members may still rename in Draft. Start rechecks the resulting
rosters. Once Live, ordinary transfers and all team renaming stop, including while
Paused. Dedicated admission handles eligible unassigned newcomers.

Participants see only their own released team; emails remain organizer-only.
Native team forms cannot bypass these rules. Signup, check-in, preview, apply,
renaming and reassignment never start competition clocks. Ordinary moves preserve
accounts and evidence; banning is an access/scoring action, not a no-show marker.

During play, room cards show the current active room time or the fixed completion
time. **Event time** counts from event start and stops during global pauses and at
event end. Completing all mapped challenges freezes your team's final event time.
A pause in one room stops only that room's clock. Standings label their separate
award measurement as time to the last counted solve.

Signup and **Account** include an optional native CTFd **Display name** field.
The Quest header and `{{user.name}}` descriptions use it; blank or missing values
use the username. Editing it keeps the login username unchanged. Native public
profiles do not expose this field by default.

## Late arrivals

Before applying teams, refresh the preview after pool changes. Afterwards, place
newcomers explicitly; existing groups are never automatically reshuffled. During
Draft, **Move participant** remains available even after roster finalization.

After finalization, dedicated admission works in draft/live/paused until cutoff.
If attendance is required, open late check-in and have the newcomer confirm
presence first. Otherwise select an eligible unassigned account directly. Reopen
signup only if new public accounts are needed. An administrator supplies a reason
and admits the newcomer into a visible team with space or a uniquely named new
team. Close signup/capture afterwards.

Existing team progress/clocks remain unchanged. New teams use configured clock
origins and explicitly start rooms. Eligible post-finalization queues may wait
unassigned across Start under the [roster contract](rosters.md); they cannot play
before admission. Existing members cannot switch teams through late admission.
Closed/published events and cutoff block admission.

## Demo and authoring

**Event → Demo content → Load** creates tracked practice content, free hints and
room introductions for organizer preview. It creates no participants, teams,
starts or solves. Loading requires draft and compatible empty room mappings;
the UI explains unavailable actions.

Remove the demo before real authoring and event start. Removal deletes only
tracked demo content and restores room text only where it still matches the demo.
It preserves later edits/unrelated content and refuses to erase activity history.
Live/paused challenge maintenance uses revision checks and reviewed competitive
changes, with pausing left to the organizer. See [live maintenance](administration.md#live-maintenance).
Unsupported native challenge/flag
logic stays read-only there. See [administration](administration.md).

## Accounts and moderation

Use **Organize → Users** for maintainer promotion, **Return to participant**,
safe unused-account deletion, and **Ban / Unban account**. Actual role changes
clear readiness and safe provisional membership. Full admins are managed in native
CTFd. Activity-bearing accounts or optimized/frozen memberships cannot be deleted;
use access moderation to retain evidence. The explicit end-of-event danger
actions are a separate bulk-deletion workflow; see [archives and reset](archives.md).

Banning excludes that user's counted solves while retaining raw submissions,
memberships and timing history. Teammates may re-solve affected steps with normal
flag checks. Recovery uses a new solve time; unbanning restores eligible retained
contributions without duplicate credit. Neither unbanning nor demotion creates
attendance or assigns a team. Published contributor changes require unpublishing.
Use **Submission history** for protected original and recovery records.

## During play and closing

- Any teammate starts a room once before content is revealed. Other rooms may run
  concurrently; navigation/reconnects do not reset starts. Server clocks decide awards.
- Use Quest **Pause/Resume** for interruptions; recorded intervals are excluded.
  Players cannot pause their clocks. Keep membership, room order and prize policy
  fixed; native destructive writes are guarded too.
- For a room-specific lab incident, an administrator can **Pause room** in room
  management and supply a reason visible to participants and the projector.
  **Resume room** also records a reason. The room stops new starts, answers and
  hint purchases for all teams; other rooms continue. Previously revealed content
  and hint receipts remain available. Only its room timing is compensated;
  overall time keeps running. Overlapping event/room pauses count once. Resuming
  the whole event does not resume a room incident; resume that room separately.
  External labs and the configured closing cutoff do not stop automatically.
- **Hidden** withdraws a step from paths and unpublished awards without deleting
  history. Restoring visibility restores eligible original solves. **Locked**
  retains the step and prior credit but blocks content/submissions. Configure
  visibility before play where possible; later changes can alter chains/rankings.
- Rooms rank by eligible challenges solved, then net room points, then active
  time to the last counted room solve. Incomplete rooms remain eligible; a full
  clear always leads every partial clear. Overall ranks by net points, then
  overall milestone time. Net points subtract paid hints from earned challenge
  values. Hints are shared team purchases, charged once. Zero qualifying solves
  have no prize rank; genuine ties share rank. Ranking times show milliseconds.
  **Progress & standings** starts with a participant’s own progress, points and
  hint deductions, without rank. Room/overall comparisons are optional and keep
  the team highlighted; other teams’ hint expenses are not displayed. All live
  comparisons are provisional. The awards screen is reserved for published results.
- Revealing a new hint asks for confirmation with its cost, team balance and
  resulting balance. Paid hints require sufficient overall points; free hints
  can be revealed at zero. Used hints are marked and shared with teammates.
  Staff journeys use local earned points and hint costs only; they create no
  event unlock or prize credit.
- Supply a reason for time corrections/exclusions. Corrections adjust room time,
  not native solve dates or overall timing. See [ranking rules](design.md#progress-timing-and-awards).
- **Close** records the cutoff. Review room/overall standings and CSV exports,
  then **Publish** the immutable result snapshot. **Unpublish/Reopen** requires a
  reason; reopening enters paused state, so resume explicitly when ready.

After review/publication, use **Organize → Archives → Save and download archive**
and check the private download in the local viewer. Keep a full database/uploads
backup if restoration may be needed. When the organizer explicitly intends to
clear a draft, closed or published event, **Danger zone** can start another round
with the same content or restore five empty default rooms. **Both delete every other
account, including other administrators and maintainers**, and clear teams/play.
They automatically save a server archive first; the JSON is a report, not a restore
file. Read the exact [scope and confirmation steps](archives.md#two-danger-actions)
before proceeding; close live/paused events first. A new draft can be reset again
without starting play. Deploying an upgrade does not request or perform a reset.

Public/projector views contain team names and aggregate results, not emails,
answers, session tokens or admin capabilities. Before roster release, team data
is withheld. Failed refreshes retain the previous view with a connection warning.
The footer shows the frontend's compiled version; refresh an old open/cached page
when deploying a new build. Image upgrades and rollback belong in the
[hosting guide](deployment.md#upgrades-and-existing-deployments).
