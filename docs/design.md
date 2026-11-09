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
- Draft is for room topology, challenge ordering, timing policy and roster setup.
  Presentation fields can change independently. Native writes cannot bypass
  the same history and lifecycle restrictions.
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

Staff edit multiple native hints per supported challenge, including text and cost,
before activity starts. Existing IDs and native prerequisites survive saves.
Participants confirm each new reveal with its cost and current overall balance;
paid purchases cannot make that balance negative. A free hint is available at
zero points. Staff previews are separate from participant purchases.

The room editor supports fixed point values, including zero. Scoring and hint
prices are frozen after competition activity; supported native dynamic scoring
retains CTFd's automatic value changes. New publication snapshots include point
breakdowns and a scoring version. Existing published count-based snapshots keep
their original rankings until an explicit reasoned unpublish/review/republish.

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

## Staff, demos and privacy

Administrators manage the event and roles; maintainers manage rooms and supported
standard challenges. Staff are excluded from attendance, team allocation, naming,
play and awards. Authorization must be enforced by the server, not hidden buttons.
See the [administration contract](administration.md).

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
