# Rehearse a complete event

For room content and sequential play while keeping your staff login, use
[room preview](preview.md). This guide covers the complete event process instead.

Use this walkthrough to explore the plugin or test a feature through registration,
staffing, attendance, team formation, play and results. Reserve a separate test
instance: its sample passwords and flags are deliberately **`test`**.

The organizer's **Demo content** is a preview and must be removed before event
start. The browser fixture in `tests/` supplies preassigned teams/progress. This
walkthrough instead starts with ordinary challenges and unassigned accounts so
you can exercise the complete [event process](operations.md).

## Start a disposable instance

From the repository root, create a separate local session key once:

```sh
python3 - <<'PY'
import secrets
from pathlib import Path
path = Path('.env.rehearsal')
with path.open('x') as output:
    path.chmod(0o600)
    output.write('SECRET_KEY=' + secrets.token_hex(32) + '\n')
PY
docker compose --env-file .env.rehearsal -p otq-rehearsal up --build -d
```

Open `http://127.0.0.1:8000`, complete native CTFd setup with a separate
administrator account, then visit `/quest/admin`. The local Compose file uses
port 8000; it must be free. Podman with a Compose provider can replace Docker.
The explicit project name gives this rehearsal its own database/upload/log volumes.
Keep the existing `.env.rehearsal` on subsequent starts rather than rotating its key.

## Fill the draft

Generate two CSV files using Python's standard library. These create 50 users
and 40 challenges across short and long room paths, with varied points and hints:

```sh
python3 - <<'PY'
import csv, json, random
from pathlib import Path

out = Path('private/rehearsal')
out.mkdir(parents=True, exist_ok=True)
with (out / 'users.csv').open('w', newline='') as output:
    writer = csv.writer(output)
    writer.writerow(['name', 'email', 'password', 'type', 'verified'])
    for number in range(1, 51):
        name = f'quest-test-{number:02d}'
        writer.writerow([name, f'{name}@example.invalid', 'test', 'user', 'true'])

rng = random.Random(42)
with (out / 'challenges.csv').open('w', newline='') as output:
    writer = csv.writer(output)
    writer.writerow(['name', 'category', 'description', 'type', 'value', 'state', 'flags', 'hints'])
    for room, count in [('OpenShift', 3), ('Ansible', 5), ('RHEL', 8), ('Security', 10), ('AI', 14)]:
        for number in range(1, count + 1):
            value = 100 if number == 1 else 200 if number == 2 else rng.choice([50, 100, 150, 300, 500])
            hint = json.dumps([{'content': 'The practice flag is test.', 'cost': 0 if number == 1 else 25}])
            writer.writerow([f'{room} {number:02d}', room, 'Practice task: submit `test`.',
                             'standard', value, 'visible', 'test', hint])
PY
```

1. In native CTFd **Admin → Config → Backup → Import CSV**, import `users.csv`
   as **Users**, then `challenges.csv` as **Challenges**. Import each once into
   the fresh test instance. CSV imports can partially succeed; inspect reported
   errors before retrying. Files under `private/` are ignored by Git.
2. In **Organize → Rooms**, add each room's imported, unmapped challenges with
   **Add step**, in numeric order, and save. Category alone does not map a native
   challenge into a Quest room. Check totals of 3, 5, 8, 10 and 14.
3. Keep the event in draft, public registration and attendance closed, and team
   capacity five. All 50 new users should be participants, unassigned and not
   checked in. There should be no teams, starts or submissions.

No external challenge cluster is needed. Before starting, edit titles, point
values, accepted answers and multiple hint texts/costs in the room editor.
Keep `test` as the accepted flag for easy play. The sample first hint in each
room is free; later hints cost 25 points. Set a positive cost before play to test
rejection at zero balance. Native CTFd manages attachments and advanced hint
prerequisites.

## Exercise the full flow

1. Sign in as the administrator and promote five accounts from `quest-test-01`
   through `quest-test-09` to maintainer in **Organize → Users**. Sign in as a maintainer and edit/preview a challenge.
   Staff should be excluded from attendance and competing teams.
2. Open attendance. Sign in as the remaining participants and explicitly confirm
   presence. Reserve accounts 01–09 for manual tests; bulk check-in uses only
   run-owned accounts 10 and higher, with separate native sessions and the normal
   ready endpoint. Exclude staff and throttle logins; leave team formation to the
   organizer.
3. Move to attendance review, preview the server's plan and apply it. For
   eligible participants, use the actual ready count in the server preview rather
   than assuming every sample account is present. If member naming is enabled,
   confirm names as participants; otherwise use the generated names.
   Finalize, then start the event separately. These are distinct actions.
4. Use two teammates to check shared room starts, prerequisite locks and duplicate
   flags. Solve a room's first challenge for 100 points; buy its second challenge's
   25-point hint and verify a shared balance of 75. Solving the 200-point second
   challenge should produce 275 net points. A teammate must not pay again.
5. Compare participant pages, standings and projector views. Rooms rank by solved
   count, then net room points, then active time; overall ranks by net points,
   then active time. Try different rooms, partial progress and unequal values.
   Include a phone-sized viewport and the long room path.
6. Pause/resume and check clocks, then close, review/export and publish. Published
   results should remain a fixed snapshot. See [operations](operations.md) for
   naming, late arrivals, corrections and moderation scenarios.

For feature work, record the plugin version, user role, event/roster phase,
expected result and observed result. Reproduce with dummy data and add a focused
regression test when fixing a bug. A rehearsal checks behavior; resource estimates
still need a separate [load test](sizing.md).

## Agent-assisted triggers

An operator runbook can persist two separate instructions:

- **“Check in the rehearsal bots”**: only while draft attendance is open, confirm
  presence for eligible sample users 10 and higher. Never touch 01–09 or advance
  allocation, naming, finalization or event start.
- **“Create the mid-event rehearsal snapshot”**: only during live, unpublished
  play, fill untouched bot-only teams with full clears, partial paths and unopened
  rooms. Skip any team with a reserved/non-bot member or existing activity, because
  progress is shared by the whole team.

Inspect the current run and preview the scope before either action. For synthetic
progress, use current challenge order/points and clearly mark generated native
history in an audit record. Keep dates chronological, within elapsed event time
and outside pauses; preserve manual progress. Allow one atomic, repeat-safe
snapshot per run and retain a backup. For a newly started disposable rehearsal
with no competition activity anywhere, an operator may backdate the Quest and
native CTFd start together to provide a realistic 45-minute window. Preview this
change, audit it with the fixture and commit it atomically. Existing activity must
prevent backdating; use elapsed history, wait or choose a shorter window instead.

Synthetic history populates standings; it does not test browser traffic or native
submission grading. Use the reserved accounts for those manual flows. Keep these
operator-only tools outside the production plugin/API, and run them only when
explicitly requested. Installing a helper or opening attendance must not trigger
simulation automatically.

## Reset and repeat

Choose the reset that matches the requested outcome. **Organize → Danger zone**
is an application action for a draft, closed or published event, not a return to
the original rehearsal baseline. Both modes archive first, remove every account except the
executing administrator and clear teams/play; **Start another round** retains
content, while **Restore defaults** also removes challenges/custom rooms and
restores five empty default rooms. Read [the exact scope](archives.md) first.
Close a live/paused event first. Neither mode recreates the rehearsal's sample
accounts or staff grants. A new draft can be cleared again or restored to defaults
without starting play.

For the disposable Compose project above, remove **its** database, uploads and
logs, then start it again:

```sh
docker compose --env-file .env.rehearsal -p otq-rehearsal down --volumes
docker compose --env-file .env.rehearsal -p otq-rehearsal up --build -d
```

This discards the entire rehearsal, including its administrator, edits, users,
teams, attendance, hints, solves and publication. Complete setup and import the
CSVs again. Keep the project name on every command so reset targets the same
isolated volumes.

For an existing playground with staff/content to preserve, the operator should
take a full database and uploads snapshot **before** seeding. Persist a run ID,
target identity, schema/image versions and backup checksums outside Git. Reset
with web writers stopped, restore the matching snapshot and uploads, clear the
dedicated application cache, restart and compare the baseline. Record these
platform-specific commands in the operator's runbook. Resume an active run rather
than reseeding it, and inspect its current state before reset. All changes since
the snapshot will be discarded, including server archives saved after that
baseline. Download any archives that must survive before restoring it. This full
undo is distinct from either application reset and includes credentials/uploads
that an event JSON report deliberately excludes.

After an application reset, old helper manifests, account IDs and native sessions
refer to the previous round. Do not resume a helper against them or automatically
reseed deleted accounts. Start a new, explicitly authorized rehearsal setup with
fresh identities, or restore the operator's reviewed baseline when that is the
requested outcome.

Native CTFd's selective reset does not reset every Quest lifecycle/history table.
Use the plugin's reviewed application reset for another round, or the full snapshot
or disposable-stack reset for a complete undo; do not delete selected records or
disable history guards.
