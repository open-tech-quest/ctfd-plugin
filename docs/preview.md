# Staff room browsing and user journey

Administrators and Maintainers have two ways to inspect the event:

- Click a room on the staff dashboard to **browse freely**. Every mapped challenge
  and hint is readable, including hidden or locked challenges, without starting a
  room or solving earlier steps. Supported answers can be checked without recording
  a submission, solve, hint purchase or progress.
- Choose **Start user journey** to use the participant landing page and room flow.
  Start rooms, solve challenges in order and confirm hints as a player would.
  Hidden challenges are omitted; locked or unsupported challenges block the path.

Both use saved content and keep the account's normal staff authorization.
Maintainers do not gain native CTFd administrator permissions. CTFd compares saved
static/regex flags on the server; raw flag values stay there.

## Local journey points and hints

A new journey starts with **0 points**. Points are derived from the local solved
and revealed IDs: each correct challenge contributes its saved value once, and
an eligible hint confirmation deducts its configured cost once. Room totals cover
that room; overall points cover all preview rooms. Wrong answers, repeated solves,
metadata reads and cancelled hint dialogs leave points unchanged.

Hints open the same confirmation dialog as participant play, with the configured
price, current local balance and remaining balance. Paid hints require sufficient
points; free hints remain available at zero. Revealed hints stay visible and only
the next hint is offered. The last reveal shows **No more hints available**;
challenges without hints show **No hints available**.

All preview guidance stays in the blue banner. The participant screens use player
labels and the same affordability states. No real event points, purchases,
attendance, teams, starts, submissions or solves are created. Clock displays are
stopped examples; awards, rankings and event timing are not simulated.

Description placeholders use an example team identity; `team.points` follows the
same derived local balance as the score displays and hint dialog. The separate
Markdown editor preview still uses static example values. See
[personalized descriptions](administration.md#personalized-descriptions).

## Changes and cleanup

Journey progress lives only in the current tab's memory. Moving between journey
rooms keeps it; leaving or refreshing starts fresh. Browsing rooms is separate
and never carries over journey progress. There are no saved runs or slots.

Saved-content changes prompt a room reload. If a challenge moves between rooms,
reload its stale source room before starting the destination so it cannot count
twice. Reloading clears that room's local
solves and hints, preserving other rooms. Totals are recomputed from the remaining
state: removing earned points can leave a negative total when hints used in other
rooms remain. No points are silently granted or clamped. Exit and start a new
journey for a completely clean state. Stale content cannot be used to confirm
another reveal or check an outdated answer.

External lab links still use real resources; use a test lab for tasks that change
a cluster. For registration, attendance, team formation and full event testing,
use the [full-event rehearsal](rehearsal.md).
