# Organizer control of public registration

Participants use native CTFd personal accounts. Organizers can open or close
public self-registration from **Organize → Event → Public registration**.
The existing native `registration_visibility` setting is authoritative: public
means open, private means closed. Other native modes are displayed as closed
for local self-registration. Startup and deployment preserve the current value.
There is no additional setting, migration, credential or service.

The control is independent of check-in and competition phases. Closing public
registration blocks creation of new accounts through the native signup form,
including submissions from an already-open form. Existing users can still log
in, confirm attendance where check-in permits, and participate with their team.
Opening registration after rosters finalize does not assign new users to frozen
teams. Administrator account provisioning remains native administrator behavior.

## API and UI

- GET `/api/v1/quest/bootstrap` and `/admin/state` gain
  `registration: {enabled: boolean}` derived from the native setting.
- PATCH `/api/v1/quest/admin/registration` accepts exactly `{enabled: boolean}`,
  requires the existing administrator session and CSRF protection, and returns
  the registration state. Its write and audit entry commit together.
- Organizer registration-setting mutations serialize with native signup
  submissions through the existing event lock. Signup writes
  check the authoritative database value after the lock, so a stale native
  config cache cannot admit an account after registration closes.
- Native config cache invalidation follows commit; native administration reads
  the same setting. As with quest-controlled clock settings, native HTTP writes
  to this setting are refused with guidance to use the organizer control. This
  keeps one audited write path without copying native bulk-config logic. No
  duplicate state can drift.
- The organizer control displays current status, confirmation, errors and busy
  state. Public registration links are shown only when the bootstrap confirms
  registration is open; a closed notice directs existing users to sign in.

## Verification — 0.1.10

[CI run 41716840923](https://github.com/open-tech-quest/ctfd-plugin/actions/runs/41716840923)
passed frontend checks, a real empty-install runtime smoke and 62 backend tests
per database. SQLite skipped three dialect-specific cases; MariaDB skipped two.

Focused coverage includes strict administrator/CSRF/input checks, unchanged
state across startup and event phases, real native signup/login/readiness,
loaded forms after closure, commit-before-cache-invalidation races and native
configuration aliases under MariaDB's case/accent/space-insensitive collation.
Closed native requests use an HTML page with a sign-in link by default,
including Chrome's full Accept header; explicit JSON clients retain errors.

In a disposable browser instance, upgrading from 0.1.9 preserved a private
registration setting and an existing participant. The organizer opened signup,
a new participant registered through the real form, then the organizer closed
signup. A previously loaded form was rejected without creating an account;
existing participant check-in still succeeded. Organizer status and audit
entries matched those actions. The organizer WCAG A/AA scan found zero violations.

The historical installation pinned the tested image in initialization and
runtime after manifest validation. A private SQL/uploads backup preceded rollout.

Live read-only checks verified exact served assets and agreement between public
bootstrap, authenticated organizer state and native CTFd configuration. The
existing public registration setting was preserved; no production signup or
toggle was used for testing. Production registration can subsequently be changed
through the organizer control without altering check-in or competition state.
