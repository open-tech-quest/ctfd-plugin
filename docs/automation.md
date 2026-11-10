# Room and challenge automation

Scripts can use a **Maintainer account's native CTFd access token** with the
existing Quest management API. No additional role or authentication endpoint is
needed. Maintainers can manage all rooms; account names do not restrict room access.

## Authenticate

Create an access token in native **Account settings → Access Tokens**. Use both
headers on every request, including GET:

```sh
# BASE is your instance URL; CTFD_TOKEN is your account's access token.
curl --fail-with-body "$BASE/api/v1/quest/manage/rooms" \
  -H "Authorization: Token $CTFD_TOKEN" \
  -H "Content-Type: application/json"
```

CTFd 3.8.9 authenticates token requests when their content type is JSON. An
`Accept: application/json` header alone is insufficient. Token-authenticated
management requests do not need a separate CSRF token. Tokens retain the
account's current permissions; demotion/banning and token expiry/revocation apply.

Alternatively, scripts may use native `/login`, retain its session cookies, read
`csrfNonce` from `/api/v1/quest/bootstrap`, and send it as `CSRF-Token` on writes.
HTTP Basic authentication is not the login mechanism for these routes.

## Management endpoints

All paths below start with **`/api/v1/quest`**. Responses use CTFd's
`{success, data}` envelope. Check the HTTP status as well as `success`.

| Task | Method and path |
|---|---|
| List rooms, ordered challenge IDs and available native challenges | `GET /manage/rooms` |
| Create a room | `POST /manage/rooms` |
| Edit room name, description, subtitle/theme, position or ordered challenge IDs | `PATCH /admin/rooms/<slug>` |
| Remove an unused room, preserving its native challenges | `DELETE /manage/rooms/<slug>` with `{"confirm":true}` |
| Read editable challenges, flags, hints and capability/reason fields | `GET /manage/rooms/<slug>/challenges` |
| Create and append a standard challenge, including flags and optional hints | `POST /manage/rooms/<slug>/challenges` |
| Edit a mapped standard challenge | `PATCH /manage/rooms/<slug>/challenges/<id>` |
| Delete an unused mapped challenge | `DELETE /manage/rooms/<slug>/challenges/<id>` |
| Check a flag without recording an attempt/solve | `POST /manage/rooms/<slug>/challenges/<id>/preview` with `{"submission":"..."}` |

Despite its `/admin/` name, the room PATCH route explicitly allows Maintainers.
Other organizer controls, including users, teams, event transitions and reset,
remain administrator-only.

Challenge creation requires `name`, `description`, `state` and `flags`; optional
`value` defaults to 100 and optional `hints` defaults to none. Supported state
values are `hidden`, `visible` and `locked`. Flags use
`{content, case_insensitive}`; hints use `{title, content, cost}`. Updates accept
those fields independently. Omitting hints preserves them; providing hints
replaces the set, so retain existing hint IDs when updating their text/cost.
Providing flags replaces the accepted flag set. Current payload validation is in
[`authoring.py`](../plugin/open_tech_quest/authoring.py).

## Native CTFd and the room path

Native CTFd stores challenges, flags and hints, but **its authoring endpoints
require native administrator access**. A Maintainer token cannot directly create
or edit them via `/api/v1/challenges`, `/flags`, `/hints`, `/files` or `/solutions`.
Use Quest's supported challenge API above for Maintainer automation. Advanced
challenge/flag/hint types, attachments, solutions and tags/topics require native
administrator tooling; they have no Maintainer authoring route at present.

Even a standard challenge can need fields outside the bounded Quest editor:
`connection_info`, `attribution` and `max_attempts` are not currently accepted.
A full catalog importer using these fields, writeups or images therefore needs
more than replacing native challenge CRUD calls. It needs scoped Quest metadata,
solution and asset support before it can run entirely as a Maintainer.

An administrator's native importer can create challenges, then map/order their
IDs with `PATCH /admin/rooms/<slug>` and `{"challenge_ids":[12,13,14]}`. This list
replaces the room's complete order. Native `category` alone does not assign a
challenge to a Quest room. Let Quest derive requirements, next links and positions;
do not write those fields separately. Each challenge belongs to at most one room.

Read current IDs and capability/reason fields before reconciling an import. Room
creation expects a unique slug; challenge creation is not an idempotent upsert.
Do not blindly retry a timed-out create. Coordinate edits to the same room or
challenge; serialized writes are not automatic merging of stale drafts.

Challenge authoring, assignment/order and room deletion require **draft**, no
relevant activity and no loaded demo. Room creation and room position changes also
require draft; room presentation has separate rules.
Treat a refusal as a workflow constraint, rather than changing the event phase or
deleting history automatically. See [administration](administration.md#room-and-challenge-authoring).

Importers should check the account's role and required capabilities before their
first write. In particular, do not unmap a room and then discover that the account
cannot delete or recreate its native challenges. Prefer ID-based room ownership
and normal challenge updates over deleting everything with a matching category.
