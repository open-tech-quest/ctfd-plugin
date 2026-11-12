# Room and challenge automation

Maintainers can automate standard challenges, metadata, solutions and owned
assets through Quest. They can manage all rooms; account names do not restrict
room access. Native CTFd remains responsible for storage, grading and accounts.
Its administrative APIs retain their administrator-only permissions.

## Authenticate and preflight

Create a native access token in **Account settings → Access Tokens**. Send both
headers on every request, including GET:

```sh
curl --fail-with-body "$BASE/api/v1/quest/manage/capabilities" \
  -H "Authorization: Token $CTFD_TOKEN" \
  -H "Content-Type: application/json"
```

CTFd authenticates token requests when their content type is JSON;
`Accept: application/json` alone is insufficient. Tokens need no separate CSRF
nonce. Cookie clients use native `/login`, read `csrfNonce` from
`/api/v1/quest/bootstrap` and send both `CSRF-Token` and
`Content-Type: application/json` on writes, including DELETE with an empty JSON
body (`{}`). Without JSON content type, native CTFd expects a form nonce instead
of the header. HTTP Basic and custom multipart uploads are not alternatives.

Before any writes, require `automation_version:1` and the needed features from
`GET /manage/capabilities`: `challenge_metadata`, `source_keys`, `owned_assets`,
`atomic_room_clear`. `asset_upload` reports `encoding:"base64"`, `max_bytes:5242880`
and kinds `inline`, `attachment`, `solution`. Version 1 identifies this contract,
not an image release. Check HTTP status and the `{success,data}` envelope, then
read room/challenge capability and reason fields before changing anything.

Writes recheck current role, signed session/password and banned status after the
event lock; challenge/asset writes also recheck token validity there. Draft
ordering/cleanup retain activity and demo protections. Live/paused challenge
maintenance requires current revisions and reviewed competitive changes; it never
requires or applies a pause. Never change phase or delete history to force a sync.
Users, teams, lifecycle and resets remain admin-only.

## Live maintenance

Read the current workspace `revision` before a live/paused write and send it as
`X-Quest-Content-Revision`. Draft callers retain the existing payload contract.
Safe text/connection updates save with that revision. To review a competitive
change, POST `{changes, revision}` to
`/manage/rooms/<slug>/challenges/<id>/review`; for an append use the collection's
`/manage/rooms/<slug>/challenges/review` route. The response contains a bound
review `revision`, competitive field names, point-in-time impact and warnings,
without secret answer values.

After deliberate confirmation, send the same changes with
`maintenance:{revision:<review revision>,reason:<3–1000 characters>,confirm:true}`
on the existing PATCH/POST route, plus the saved content revision header. Changed
content/round/phase/authority requires a new review; ongoing ordinary submissions do
not invalidate every confirmation. Apply recomputes current impact under lock.
Never retry a competitive write blindly after an uncertain response.

Live/paused point or existing-hint price edits must include an explicit scope in
both `changes` for review and the PATCH payload:

```json
{"pricing":{"challenge_existing":false,"hint_existing_ids":[]}}
```

False/empty means future solves and purchases only. Select `challenge_existing`
to align earlier solves, and list each existing hint ID whose earlier purchases
should receive the new effective cost (charge or refund). This also supports
aligning earlier cohorts to an unchanged current price. Scope requires the
corresponding value/hint payload. Review reports affected teams and newly negative
room/overall balances; those require `maintenance.acknowledge_negative:true` at
apply. Original solves, dates, native charges and receipts remain audit evidence.

New room creation uses the management catalog revision; room presentation uses
the room revision. Live/paused challenge creation appends only, and is refused
once any eligible team has completed that room. Existing path reorder/unmap,
room deletion, bulk cleanup and asset upload/replacement/deletion retain their
draft/history restrictions. Closed/published content is read-only; reopening or
unpublishing is an explicit administrator action. Pausing is never implicit.

For participant clients, fetch the next hint through authorized native
`GET /api/v1/hints/<id>` and send its `quote_revision` as `X-Quest-Hint-Quote`
when POSTing `{target:<id>,type:"hints"}` to `/api/v1/unlocks`. A changed quote
requires reopening confirmation, not silently paying a different price. Existing
team redemptions remain idempotent and retain their original receipt; only an
explicit organizer repricing changes its effective deduction.

## Endpoints and fields

All paths below start with `/api/v1/quest`.

| Task | Method and path |
|---|---|
| Rooms, mapped IDs and available native challenges | `GET /manage/rooms` |
| Create a room | `POST /manage/rooms` |
| Room presentation or complete ordered challenge IDs | `PATCH /admin/rooms/<slug>` |
| Remove an unused room, retaining native challenges | `DELETE /manage/rooms/<slug>` with `{"confirm":true}` |
| Private authoring workspace and capabilities | `GET /manage/rooms/<slug>/challenges` |
| Create and append a challenge | `POST /manage/rooms/<slug>/challenges` |
| Update/delete one mapped challenge | `PATCH` / `DELETE /manage/rooms/<slug>/challenges/<id>` |
| Atomically clear mapped room challenges | `DELETE /manage/rooms/<slug>/challenges` with `{"confirm":true}` |
| Grade without attempts, logs or progress | `POST /manage/rooms/<slug>/challenges/<id>/preview` with `{"submission":"..."}` |

Despite its name, the room PATCH permits Maintainers. Its `challenge_ids` replaces
the complete order. Native `category` is not room ownership. Quest derives native
prerequisites, next links and positions; do not write those fields yourself.
For a browser walkthrough with sequential unlocking and separate practice
progress, use [staff room preview](preview.md).

Creation requires `name`, `description`, `state`, `flags`; `value` defaults to 100.
PATCH accepts fields independently; omitted fields are preserved.
Point values, hint costs and attempt limits use integers from 0 to 2,147,483,647.

| Fields | Bounds/behavior |
|---|---|
| `name`, `description`, `state`, `value` | Name ≤80 characters; description ≤10,000; `hidden`/`visible`/`locked`; nonnegative integer points |
| `attribution`, `connection_info`, `max_attempts` | Text ≤10,000 each; nonnegative integer attempt limit (0 means native unlimited) |
| `tags`, `topics` | Up to 50 unique nonempty names each, ≤80/255 characters; replaces this challenge's associations |
| `hints` | Up to 20 `{id?,title,content,cost}` entries; title ≤80, content ≤10,000, cost nonnegative integer |
| `solution` | `null` or `{content,state}`; content ≤100,000; state `hidden`, `visible` or `solved`; reads include native `id` |
| `source_key` | Explicit immutable room-scoped identity, ≤255 characters, matching `[a-z0-9][a-z0-9._/-]*` |

Providing `flags` replaces the set (1–20). Use legacy `{content,case_insensitive}`
or native `{type,content,data?}`: type `static` or `regex`, content ≤1,024, data
`null` or a string ≤64 characters. Regex patterns must compile. Raw options are
preserved: native `case_insensitive` enables case folding; `case_sensitive` and
other data retain CTFd's default case-sensitive behavior. Preview uses the same
native checker and full-match rules.

Workspace `native_flags` retains native type/content/data. `flags` and `can_edit`
describe the simple static editor, where regex stays read-only. Automation checks
`can_automate`/`automation_reason` and `can_delete`/`delete_reason`; `can_edit:false`
is not a blanket API denial. Unsupported challenge/custom flag/hint types, dynamic
scoring and flag logic remain outside this interface. Authoring responses contain
saved answers/solutions: keep them private and out of Git.

Omitted hints survive; a supplied list replaces them. Keep existing IDs to retain
prerequisites; referenced/purchased hints cannot be removed. Solution updates keep
its ID; `solution:null` also removes its file metadata after dependency checks.
Participant reveal order is ascending native hint ID; rearranging existing IDs in
an update does not reorder them. Only the next unredeemed hint can be redeemed.
Existing native prerequisites remain in force; requiring a later hint conflicts
with progressive order and must be corrected by an organizer.
[`authoring.py`](../plugin/open_tech_quest/authoring.py) defines exact validation.

Descriptions may contain the [four supported placeholders](administration.md#personalized-descriptions).
Send and retain their raw template text; Quest resolves it for the viewer when
rendering room/challenge instructions. API reads and exports keep the template.

## Identity and reconciliation

`source_key` is unique per room/challenge and can be assigned to an unused keyless
mapped challenge by explicit PATCH. The server never adopts by name or changes an
assigned key. Resolve keys/IDs before creating; read again after an uncertain
response instead of blindly retrying POST.

Workspace `challenges` has `mapped:true`; `unmapped_catalog` has `mapped:false`.
Unmapping retains the challenge and its source identity. Before **any** writes,
check an unmapped candidate's `can_reattach`/`reattach_reason` and current room
capabilities. Explicitly PATCH the complete room order with current IDs plus the
exact owned IDs to reattach; only then edit those entries. This also applies to
refresh: mapped cleanup does not remove previously unmapped catalog entries.

Use the atomic collection DELETE instead of unmapping followed by category-wide
native deletion. It validates all mapped history, external challenge/hint
references and unknown dependent plugin tables before mutation; failures roll
back. It returns `deleted_ids` and preserves the room, unmapped content and
unrelated challenges sharing its category. Coordinate concurrent authoring:
serialized requests do not merge stale drafts automatically.

## Owned assets and solution access

Append `/assets` to a mapped challenge path:

- `GET` returns `{assets:[...]}`, including manual native files (`managed:false`).
- `POST` takes exactly `{kind,name,content_base64}` as JSON. Assets are nonempty
  and ≤5 MiB; filenames ≤160 characters without paths are normalized with native
  protections. Inline files require matching PNG/JPEG/GIF/WebP signatures and
  extensions; SVG/HTML inline content is rejected.
- `DELETE /<fileid>` takes `{}` and removes only a managed asset of that challenge.
- `GET /<fileid>/download` gives staff preview/download without play credit.

Metadata includes `id`, `managed`, `kind`, `name`, `size`, `sha256`, native participant
`url` and staff `preview_url`. Kinds are `inline`, `attachment`, `solution`. Inline
assets stay out of the native download list. Initialize the solution before
uploading its assets, then PATCH final Markdown using returned URLs. Do not send
global locations or another owner's file IDs.

POST upserts managed `(kind,name)`: identical bytes keep ID/URL; changed bytes keep
the ID but receive a new URL. Rewrite references accordingly. Prune only
`managed:true`, preserving manual attachments. Deletion/content cleanup removes
metadata transactionally; bytes remain for **operator garbage collection**,
unreachable through the deleted native file record. This API does not reclaim
storage. Archives retain metadata, not bytes.

Staff can preview managed native links with their normal room-management access. Participants still need the
released event, room start, challenge visibility and effective prerequisites.
Hidden solutions stay hidden; `solved` solution files require an eligible team
solve, and managed solution files also require the native team solution unlock.
Knowing a URL grants no access. Managed responses use private/no-store caching
and safe image/attachment handling; native admin permissions are not widened.
