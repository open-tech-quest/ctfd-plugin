# Preview rooms as a participant

Administrators and Maintainers can use **Preview as participant** on the staff
homepage or a room's management screen. It opens the participant landing page
and room layout using the current saved content, while the real event keeps its
existing state.

Start a room, submit answers and follow the challenge path. Correct answers
unlock the next step locally. Hidden challenges are omitted and locked challenges
block the path. Unsupported challenge types are identified explicitly.

CTFd checks answers on the server using its native static or regex flag handling.
Saved flags stay on the server. The preview records no native attempts, solves,
room starts or hint purchases. Hints show their configured costs and can be
revealed without spending points. Timing, balances, attempt limits, awards and
rankings are not simulated.

Hints follow the same progressive order: revealed text remains visible and only
the next hint can be opened. The final reveal shows **No more hints available**;
challenges without hints show **No hints available**.

Description placeholders use labelled example values, including team number 1
and a fixed 250 points. They show how personalized content looks; local solves
and free hint reveals do not change that example balance. See the
[placeholder guide](administration.md#personalized-descriptions).

Progress lives only in the current browser tab's memory. Moving between preview
rooms keeps it; leaving the preview or refreshing starts fresh. There are no
saved runs, slots or cleanup controls to manage. Separate tabs are independent.
Use **Reload room** to try a path again, or **Exit preview** to return to staff
tools.

When saved content changes, the preview shows a notice and lets you reload the
affected room with cleared local progress. Answer checks against outdated
content ask you to refresh before continuing.

Staff keep their normal account and permissions. External lab links still use
real resources; use a test lab for tasks that change a cluster. For registration,
attendance, team formation and complete event testing, use the
[full-event rehearsal](rehearsal.md).
