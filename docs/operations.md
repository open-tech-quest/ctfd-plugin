# Running an event

To explore this process with dummy accounts and a repeatable reset, use the
[full-event rehearsal](rehearsal.md).

## Prepare

1. Set up [hosting and backups](deployment.md). Test restoration with matching
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
   Team 1, Team 2, … names are final. Enable member naming only if wanted.
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

## Registration, attendance and final teams

**Registration creates accounts; attendance confirms presence; Start opens play.**
These are separate controls. New installations default to attendance closed;
existing settings and confirmations survive upgrades and restarts.

1. Open **Event → Public registration** while collecting registrations. Accounts
   remain unassigned. Closing signup also rejects an already-loaded native form;
   existing users can still sign in.
2. At the venue, open **Attendance check-in** from Event or Teams. Unconfirmed
   participants on active Quest pages receive a prompt through the five-second
   sync; returning tabs refresh it. They must press **I'm ready to quest**.
   **Not yet** dismisses that opening's dialog and leaves a visible check-in action.
3. At cutoff, close registration as appropriate and **Move to attendance review**.
   This closes capture. Preview checked-in teams, inspect assignments/capacity
   warnings, then apply exactly the reviewed proposal. Changed attendance or
   settings require another preview. No-show accounts remain unassigned.
4. Generated names are already locked. If member naming is enabled, any ready
   teammate can confirm one unique final name. Enabling it after apply opens only
   generated/unclaimed names; chosen names never reopen. Disabling retains names.
5. Finalize with capture closed, ready people assigned, valid team sizes and final
   names. Remove any demo and configure real challenges, then **Start event**
   separately. A future scheduled start prevents early opening; it does not
   replace the organizer's Start action.

Registration, check-in, preview, apply and naming never start competition clocks.
Participants see only their own released team and members' names/readiness; emails
are organizer-only. Native team forms cannot bypass this process. If a request
says its reviewed plan is stale, refresh and review again rather than retrying it.

## Late arrivals

Before apply, reopen capture as needed, then close it and preview again. During
final team review, place ready unassigned people with a reason in teams with room
or create additional teams; existing groups are not automatically reshuffled.

After finalization, open the explicitly labelled **late attendance** window for
unassigned newcomers and reopen signup if needed. This works in draft/live/paused
until cutoff. The newcomer registers/logs in and confirms presence; an administrator
then admits them into a final-named team with space, or an additional agreed named
team, with a reason. Close the windows afterwards.

Existing members, progress and clocks stay fixed. New teams use the configured
clock origins and explicitly start their rooms. Ready post-finalization newcomers
can wait unassigned across Start once capture is closed; they cannot play until
admitted. Closed/published events and the cutoff block admission. The detailed
[roster contract](rosters.md) explains eligibility and reviewed-plan guarantees.

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
