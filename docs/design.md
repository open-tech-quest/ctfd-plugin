# Product contract

Open Tech Quest provides team challenge events inside CTFd. People use personal
accounts; teammates share room starts and progress and can work in different
rooms simultaneously. CTFd owns accounts, teams, challenges, flags and native
submissions. The plugin adds event lifecycle, room chains, timing and awards.

The initial rooms are OpenShift, Ansible, RHEL, Security and AI. Room names,
themes and the room set are customizable. Stable IDs/slugs preserve links and
history. Removing every room intentionally leaves an empty configuration.

## Event and room lifecycle

The event moves through `draft → live ↔ paused → closed → published`.

- New installations start in draft with five empty rooms, attendance closed
  and no generated participants, challenges, starts or solves.
- Native team mode is required. Existing individual solves block automatic
  conversion because changing their ownership would change history.
- Draft supports initial topology, timing policy and roster setup. Live/paused
  events support reviewed challenge maintenance and append-only additions.
  Pausing is always the organizer's manual decision, never a maintenance gate.
  Native and automation writes cannot bypass the same content/history protections.
- Start requires finalized attended teams, capture closed, at least one mapped
  challenge and no loaded demo. A future scheduled start prevents early opening;
  the organizer must still start the event explicitly.
- A team starts a room once, atomically on the server, before content is released.
  Starts are shared and idempotent across teammates and survive reconnects.
  A new room cannot start if its first non-hidden challenge is not Visible.
- Pauses are timestamped intervals, not merely a current boolean. Closing records
  the cutoff and closes attendance. Unpublish and reopen require a reason;
  reopening enters paused state and accounts for the closed interval as a pause.
- Publishing records a standings snapshot. Later database changes must not
  silently change announced results.
- A separate, explicit reset in draft, closed or published state first archives
  the source and result snapshot atomically. It deletes all accounts except the executing admin
  and clears teams/play; the defaults mode additionally replaces challenges and
  configured rooms with five empty defaults. This is the deliberate exception to
  ordinary history/identity preservation, never an automatic startup or upgrade
  action. Close live/paused events first. Existing server archives survive.
  See [archives](archives.md).

Registration, attendance, team formation and event start are separate actions.
Naming defaults to fixed generated names; member naming is optional. See the
[roster contract](rosters.md) for phases, privacy, reviewed plans and late arrivals.

## Progress, timing and awards

| Ranking | Order, from first to last |
|---|---|
| Room | Eligible challenges solved in the room, descending; room net points, descending; active time to the last counted room solve, ascending |
| Overall | Net points across all rooms, descending; active time to the last counted solve, ascending |

A complete room outranks an incomplete one. A zero-solve room has no prize rank.
Net points are native challenge values earned once per eligible team solve,
minus paid hint costs. A room's hint costs affect its room score and the overall
score. Incomplete rooms remain eligible: 9/10 always outranks 8/10 regardless of
points or time. True ties across every ranking measure share rank; database ID
is not a sporting tie breaker. Counts remain separate from points. A stuck
team's milestone time does not grow. Sequential path access still uses the
consecutive solved prefix, independently of credited solve counts.

Hints are purchased once per team and shared. Fresh net balance must cover the
price; purchases serialize with event changes and record a native unlock,
native negative award and immutable receipt atomically. Only genuine hint
purchases affect scores, not arbitrary native bonus/penalty awards. Charges stay
with the team if the purchasing member is later banned; revoked solve credit
can leave a negative score. Hidden challenges withdraw both credit and associated
hint costs, without deleting history. Locked challenges retain both.

Hints progress in ascending native ID order. Previously redeemed hints stay
visible; only the first unredeemed hint is offered. Revealing a free hint also
advances the sequence; cancelled or failed purchases do not. The server enforces
this order for new reads/purchases, preserving existing out-of-order unlocks.
After the last reveal the interface says **No more hints available**; an empty
challenge says **No hints available**. Native prerequisite rules still apply.
A prerequisite on a later hint conflicts with this order and requires organizer
review; authored requirements and purchase history are never rewritten.

Staff edit multiple native hints per supported challenge, including text and cost.
Live/paused price changes require reviewed confirmation; past receipt costs stay
fixed and new prices apply only to future purchases. Existing IDs and native
prerequisites survive saves; purchased hints cannot be deleted. New purchases
require the displayed quote's current cost/content revision, checked after locking;
a stale quote must be reopened rather than silently charged at a new price.
Participants confirm each new reveal with its cost and current overall balance;
paid purchases cannot make that balance negative. A free hint is available at
zero points. Staff journey starts at zero and derives local points from solved
challenge values minus confirmed hint costs, once per ID. It uses the same hint
dialog and affordability rule without recording native activity. Free staff room
browsing is separate and does not collect progress or points. Journey guidance
stays in its blue banner; player labels are reused and clocks remain stopped.
See [staff browsing and journey](preview.md).

The room editor supports fixed point values, including zero. Reviewed live/paused
point changes apply uniformly to all eligible prior and future solves, preserving
original solve times. Accepted-answer changes affect future attempts; old solves
and failures are not regraded automatically. Supported native dynamic scoring
retains CTFd's automatic value changes. Published snapshots keep their original
rankings until an explicit reasoned unpublish/review/republish.

Live review is stateless and bound to saved content, proposed changes, event round/phase
and actor identity. Competitive changes require an impact preview and reason.
Ordinary ongoing progress does not invalidate every review: apply rechecks safety
under the event lock, recomputes the impact and records a safe audit summary.
Content conflicts preserve the staff draft for a new review. Secret flag values never enter
review responses or audit details.

New challenges may be appended during live/paused play, without changing existing
steps. A room already completed by any eligible team cannot be extended; staff can
create another room instead. Existing starts, receipts and competition history
remain intact. Closed/published content is read-only until explicitly reopened or
unpublished. Ordinary deletion/unmapping retains its draft/history protections.

Room timing defaults to explicit room start; common event origin is configurable.
Overall timing defaults to common event start; team-first-room origin is an
explicit alternative. Never sum overlapping room durations. Store UTC with
microsecond precision, use server timestamps and subtract recorded pauses.
Only eligible solves within the configured cutoff count. Corrections are
reasoned, audited room-time adjustments, bounded cumulatively to ±24 hours and
clamped at zero elapsed time; they never rewrite native solve dates or overall
award timing. Exclusions omit a team from prize/public standings.

Bans filter counted contributions without deleting evidence. Teammates can
legitimately recover lost credit; unbanning deduplicates original and recovery
solves. See [administration](administration.md#contributions-and-recovery).

## Challenge access and visibility

Each native challenge belongs to at most one room. Its active prerequisite chain
is enforced through native CTFd Requirements and plugin checks on content,
attempts, hints, unlocks, solutions and known attachment URLs.

- **Visible:** playable only after normal event, roster, room and prerequisite
  checks pass.
- **Hidden:** omitted from participant paths, prerequisites and unpublished award
  counts. Retained solves regain eligibility at their original time when restored.
- **Locked:** remains a blocked step in the chain and totals; previous credit is
  retained, but content and submissions are denied.

Visibility changes preserve starts and raw history. Later steps relock if an
earlier effective solve stops counting. Locked metadata never contains challenge
content, saved flags or unreleased hint/file information. Staff have explicit
preview access; being an administrator does not create participation credit.

Maintainer automation uses explicit source identity and room/challenge-owned
metadata/assets, without global native administrator access. Unmapping retains
catalog identity; reattachment is explicit and history-checked. Bulk room cleanup
validates all mapped content before deleting any of it. Native grading/solution
eligibility stays authoritative, including supported regex flags. See the
[automation contract](automation.md).

## Staff, demos and privacy

Administrators manage the event and roles; maintainers manage rooms and supported
standard challenges. Staff are excluded from attendance, team allocation, naming,
competition play and awards. Authorization must be enforced by the server,
not hidden buttons.
See the [administration contract](administration.md).

Staff dashboard rooms allow free inspection. The explicit [user journey](preview.md)
uses the participant interface with tab-local progress and points from solved
challenges minus confirmed hint costs. Native flag checks record no activity.
Leaving or reloading clears progress; saved edits require a room reload. Neither
mode creates competition records, awards, running clocks or purchases.

The explicit draft-only demo owns the content it creates and never creates
participants or progress. Removing it preserves unrelated content and later room
edits, refuses history deletion, and must precede competition start.

Participants receive only their own released roster, with names/readiness rather
than emails. Public/projector responses contain team names and aggregate results;
roster release also gates public team visibility. Saved answers, submitted flags,
credentials and administrator capabilities never belong in public responses.

## Presentation

Use the event's indigo/white/red/cyan identity and Red Hat Display/Text fonts,
readable neutral surfaces, visible keyboard focus and restrained motion respecting
`prefers-reduced-motion`. Approved assets and license/provenance rules are in
[branding](../branding/README.md). Theme changes never change competition identity.

Room introductions and challenge instructions can use the fixed placeholders
`team.number`, `team.name`, `user.name` and `team.points`. Team number means the
native team ID, and points mean the current overall net score. Resolve only the
viewer's released team context, retain raw templates in storage/shared caches,
and treat substituted names as data. Staff editor/browsing views use explicit
sample identities; journey points come from local progress.
See [personalized descriptions](administration.md#personalized-descriptions).

Participant room paths scroll within a bounded horizontal rail with navigation
controls. Cards fit their titles within a bounded width; long titles wrap in full,
including locked steps. The home screen balances any room count into rows of two
or three, uses a horizontal card for a single room, and stacks cards on phones.
Progress & standings opens on an assigned participant’s own points, hint costs and
room progress, without ranks. Room and overall comparisons remain available by
choice and highlight the viewer’s team. Other teams’ hint-cost breakdowns are not
emphasized in the interface; public aggregate scores and award rules stay transparent.
Unassigned participants see a release/assignment message, not another team’s data.

The projector defaults to a room-award overview: leading three ranks including
all ties, completed-team counts and an explanation of room/overall awards. Overall
leaders show five places including ties; the complete matrix is a manual view.
Room views show solved count, net room points and active time in ranking order.
Final awards appear only after publication; other results are labelled provisional.

Organizers can enable a server-anchored overview → featured room → overall cycle
(default 60/30/20 seconds), pause/resume without losing its position, restart it,
or select a manual view. Each cycle features the next room. Room and tied-leader
pages share display time fairly; empty rooms and changing room sets remain safe.
Manual overview pages have their own pause control. Motion uses plain view changes,
with no decorative animation. Ranking times retain millisecond precision; published
legacy count-based snapshots retain their original ranking and explanations.
Projector, organizer, participant and exports use the same authoritative projection.


## Design process attribution

The [unslop workflow by Damir Sokolovsky](https://x.com/heydamir), licensed
CC BY 4.0, informed the design review. No skill source or reference imagery is
distributed with this application.
