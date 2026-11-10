# Event archives and another round

Administrators use **Organize → Archives** to save a private event record and
read it later. **Danger zone** offers two separate, destructive ways to prepare
another event. Neither action is part of upgrading the plugin.

## Save and read

1. Select **Save and download archive**. This saves an immutable server copy and
   downloads JSON. Snapshots can be saved before or after closing/publication.
2. Keep the download privately with the event's operational records. Saved rows
   offer **View** and **Download JSON**. Verify the downloaded file before clearing
   an event or restoring a database backup.
3. Use **Read an archive → Open JSON file** to inspect a downloaded record. The
   file is read locally in the browser and is never uploaded. The viewer checks
   its schema and SHA-256 checksum before displaying results, teams, challenges,
   history and a filterable timeline. Its current file limit is 64 MiB.

The viewer is read-only: opening JSON cannot recreate accounts, import content
or change the running event. External media and links are disabled. A checksum
detects changed/incomplete content; it is not a digital signature or proof of
who produced a file.

## Two danger actions

Both actions allow a **draft, closed or published** event; close a live or paused
event first. Review their current counts,
type the exact current event name and supply a reason. The preview is bound to
the selected mode, administrator and current source state. If anything changes,
review again. Administrator/session authority and CSRF are checked on the server,
including after taking the event lock.

| Action | Content after the reset |
|---|---|
| **Start another round** (`new_round`) | Keeps rooms, ordered challenge mappings, challenge content, flags, hints, solutions and attachment records/files. |
| **Restore defaults** (`defaults`) | Also deletes all native challenges, flags, hints, solutions and their attachment records; removes the configured rooms and restores five empty rooms: OpenShift, Ansible, RHEL, Security and AI. |

**Both delete every account except the signed-in administrator performing the
action, including other administrators and maintainers.** Teams/memberships,
attendance, team naming, native submissions and recovery solves, starts, hint
purchases/unlocks/awards, ratings, round notifications, pauses, corrections,
exclusions and current-round audit history are cleared. Account-specific notes
and field values belonging to removed accounts are removed too.

Before deletion, the complete report is saved in the same transaction. A failure
rolls back the archive insertion and reset together. Existing server archives
survive either action. Unsupported challenge/submission types or additional
dependent plugin tables cause a refusal until an operator reviews their cleanup
and archive requirements.

The event returns to draft with registration and attendance closed, no scheduled
event/roster times or published results, and projector rotation disabled. The
event name, clock policies, team capacity, naming preference, hosting settings,
pages and unrelated assets remain. Create/register new accounts and repeat
attendance/team preparation explicitly; no participants are automatically seeded.

## What the JSON retains

Schema 1 uses format `open-tech-quest/event-archive`. Its envelope carries an
archive UUID, UTC export time, plugin/CTFd producer versions, source provenance,
`data` and SHA-256 integrity over canonical JSON excluding `integrity`.

The data includes event clocks/settings; authoritative standings and any saved
published snapshot; named accounts/roles and teams; attendance/naming; ordered
rooms, mapped and unmapped native challenges, points, hints and solutions;
attempt metadata and recovery links; starts/pauses; immutable hint receipts and
native unlocks/awards; corrections/exclusions; ratings, authored notes/custom
fields, tags/topics, notifications, demo ownership and safe audit metadata.
Timestamps preserve microseconds where available. Results retain the saved
publication and its scoring version; `scoring_credits` and attempt `credited`
describe the current projection at export, not a replacement for that snapshot.

Native email/authentication fields, passwords, API tokens, flag-value and
submitted-answer fields, and IP/tracking records are excluded. Attachments have metadata only,
without storage locations or embedded bytes. Authored content, solution text,
notes and custom field values can contain private information supplied by people;
keep the entire file private. Source fingerprints bind the source and transactional
read generations, including otherwise unexported flag changes. Prior archive
documents are excluded from new reports, avoiding recursive growth.

This is an event report, **not a database backup or restore format**. In particular,
it cannot restore deleted credentials, flags or files. Retain full database/upload
backups when rollback is required. An operator's full baseline restore also
removes server archives created after that baseline: download them first. See
[hosting](deployment.md) and [rehearsal reset](rehearsal.md#reset-and-repeat).
