import assert from "node:assert/strict";
import test from "node:test";
import { createQuestSession } from "../src/quest-session.ts";

const viewer = {
  authenticated: true,
  id: 1,
  role: "participant",
  team_id: 10,
  name: "Player",
  team_name: "Team 10",
  admin: false,
  can_manage_rooms: false,
  can_manage_users: false,
};
function sync(overrides = {}) {
  return {
    event: { status: "live", server_now: "now" },
    viewer,
    registration: { enabled: true },
    csrfNonce: "nonce-1",
    onboarding: {
      ready_at: null,
      roster_released: false,
      attendance: { enabled: false, generation: 1 },
    },
    revisions: {
      content: "content-1",
      roster: "roster-1",
      progress: "team10-1",
    },
    ...overrides,
  };
}
function bootstrap(overrides = {}) {
  return { ...sync(), rooms: [{ id: 1, name: "Initial room" }], ...overrides };
}
async function flush() {
  for (let index = 0; index < 20; ++index) await Promise.resolve();
}
function fixture() {
  let now = 0,
    active = true,
    sequence = 0;
  const timers = new Map(),
    full = [],
    heartbeats = [],
    published = [];
  const load = (requests) => (signal) =>
    new Promise((resolve, reject) =>
      requests.push({ signal, resolve, reject, at: now }),
    );
  const session = createQuestSession({
    active: () => active,
    now: () => now,
    random: () => 0.5,
    loadBootstrap: load(full),
    loadSync: load(heartbeats),
    publish: (state) => published.push(state),
    setTimer(callback, delay) {
      const id = ++sequence;
      timers.set(id, { callback, at: now + delay });
      return id;
    },
    clearTimer: (id) => timers.delete(id),
  });
  return {
    session,
    full,
    heartbeats,
    published,
    get state() {
      return published.at(-1);
    },
    async start(value = bootstrap()) {
      session.start();
      assert.equal(full.length, 1);
      full[0].resolve(value);
      await flush();
    },
    async advance(ms) {
      const end = now + ms;
      while (true) {
        const next = [...timers.entries()]
          .filter(([, timer]) => timer.at <= end)
          .sort((a, b) => a[1].at - b[1].at)[0];
        if (!next) break;
        now = next[1].at;
        timers.delete(next[0]);
        next[1].callback();
        await flush();
      }
      now = end;
      await flush();
    },
    setActive(value) {
      active = value;
      session.activity();
    },
  };
}

test("initial bootstrap seeds attendance; unchanged five-second sync never rebuilds full data", async () => {
  const f = fixture();
  await f.start();
  assert.equal(f.heartbeats.length, 0);
  assert.equal(f.state.live.onboarding.attendance.enabled, false);
  for (let index = 0; index < 8; ++index) {
    await f.advance(5000);
    assert.equal(f.heartbeats.length, index + 1);
    f.heartbeats[index].resolve(sync());
    await flush();
  }
  assert.equal(f.full.length, 1);
  f.session.stop();
});

test("fresh attendance, event, registration and nonce are visible without room invalidation", async () => {
  const f = fixture();
  await f.start();
  await f.advance(5000);
  const current = sync({
    csrfNonce: "nonce-2",
    registration: { enabled: false },
    event: { status: "paused" },
    onboarding: {
      ready_at: null,
      attendance: { generation: 2, enabled: true, late_only: true },
      can_ready: true,
    },
  });
  f.heartbeats[0].resolve(current);
  await flush();
  assert.equal(f.state.data.csrfNonce, "nonce-2");
  assert.equal(f.state.data.event.status, "paused");
  assert.equal(f.state.data.registration.enabled, false);
  assert.equal(f.state.live.onboarding.attendance.late_only, true);
  assert.equal(f.full.length, 1);
  f.session.stop();
});

test("one full refresh serves a changed revision across repeated equal heartbeats", async () => {
  const f = fixture();
  await f.start();
  await f.advance(5000);
  const changed = sync({
    revisions: {
      content: "content-2",
      roster: "roster-1",
      progress: "team10-1",
    },
  });
  f.heartbeats[0].resolve(changed);
  await flush();
  assert.equal(f.full.length, 2);
  await f.advance(5000);
  f.heartbeats[1].resolve({ ...changed, csrfNonce: "fresh-nonce" });
  await flush();
  assert.equal(f.full.length, 2);
  f.full[1].resolve(
    bootstrap({
      ...changed,
      csrfNonce: "older-full-nonce",
      rooms: [{ name: "Changed room" }],
    }),
  );
  await flush();
  assert.equal(f.state.data.rooms[0].name, "Changed room");
  assert.equal(f.state.data.csrfNonce, "fresh-nonce");
  f.session.stop();
});

test("superseded full reads cannot overwrite newer revisions; follow-ups remain serialized", async () => {
  const f = fixture();
  await f.start();
  await f.advance(5000);
  const changed = sync({
    revisions: { content: "2", roster: "1", progress: "1" },
  });
  f.heartbeats[0].resolve(changed);
  await flush();
  await f.advance(5000);
  const newer = sync({
    csrfNonce: "newest",
    revisions: { content: "3", roster: "1", progress: "1" },
  });
  f.heartbeats[1].resolve(newer);
  await flush();
  assert.equal(f.full.length, 2);
  f.full[1].resolve(bootstrap({ ...changed, rooms: [{ name: "Stale room" }] }));
  await flush();
  assert.equal(f.full.length, 3);
  assert.notEqual(f.state.data.rooms[0].name, "Stale room");
  f.full[2].resolve(bootstrap({ ...newer, rooms: [{ name: "Newest room" }] }));
  await flush();
  assert.equal(f.state.data.rooms[0].name, "Newest room");
  assert.equal(f.state.data.csrfNonce, "newest");
  f.session.stop();
});

test("a newer full snapshot waits for sync confirmation instead of ordering opaque tokens", async () => {
  const f = fixture();
  await f.start();
  let finished = false;
  const navigation = f.session.navigation().then(() => (finished = true));
  f.heartbeats[0].resolve(sync());
  await flush();
  const newer = sync({
    csrfNonce: "confirmed-token",
    revisions: { content: "opaque-new", roster: "1", progress: "1" },
  });
  f.full[1].resolve(
    bootstrap({
      ...newer,
      csrfNonce: "unconfirmed-token",
      rooms: [{ name: "New snapshot" }],
    }),
  );
  await flush();
  assert.equal(finished, false);
  assert.equal(f.state.data.rooms[0].name, "Initial room");
  assert.equal(f.state.live.csrfNonce, "nonce-1");
  assert.equal(f.heartbeats.length, 2);
  f.heartbeats[1].resolve(newer);
  await navigation;
  assert.equal(f.state.data.rooms[0].name, "New snapshot");
  assert.equal(f.state.data.csrfNonce, "confirmed-token");
  assert.equal(f.full.length, 2);
  f.session.stop();
});

test("logout/login and team or role changes clear old full data before accepting a new identity", async () => {
  const f = fixture();
  await f.start();
  await f.advance(5000);
  const guest = sync({
    viewer: {
      ...viewer,
      authenticated: false,
      id: null,
      team_id: null,
      role: "guest",
    },
    onboarding: null,
    csrfNonce: null,
  });
  f.heartbeats[0].resolve(guest);
  await flush();
  assert.equal(f.state.data, null);
  assert.equal(f.state.live.viewer.role, "guest");
  f.full[1].resolve(bootstrap({ ...guest, rooms: [{ name: "Public room" }] }));
  await flush();
  await f.advance(5000);
  const staff = sync({
    viewer: {
      ...viewer,
      id: 2,
      role: "maintainer",
      team_id: null,
      can_manage_rooms: true,
    },
    onboarding: null,
    csrfNonce: "staff-token",
  });
  f.heartbeats[1].resolve(staff);
  await flush();
  assert.equal(f.state.data, null);
  f.full[2].resolve(bootstrap({ ...staff, rooms: [{ name: "Staff room" }] }));
  await flush();
  assert.equal(f.state.data.viewer.id, 2);
  assert.equal(f.state.data.csrfNonce, "staff-token");
  f.session.stop();
});

test("post-action refresh invalidates older sync and awaits fresh full progress", async () => {
  const f = fixture();
  await f.start();
  await f.advance(5000);
  let finished = false;
  const refreshed = f.session.refresh().then(() => (finished = true));
  f.heartbeats[0].resolve(sync());
  await flush();
  assert.equal(f.heartbeats.length, 2);
  const solved = sync({
    onboarding: { ready_at: "confirmed" },
    revisions: {
      content: "content-1",
      roster: "roster-2",
      progress: "team10-2",
    },
  });
  f.heartbeats[1].resolve(solved);
  await flush();
  assert.equal(finished, false);
  assert.equal(f.state.live.onboarding.ready_at, "confirmed");
  f.full[1].resolve(
    bootstrap({ ...solved, rooms: [{ name: "Progress updated" }] }),
  );
  await refreshed;
  assert.equal(f.state.data.rooms[0].name, "Progress updated");
  f.session.stop();
});

test("own-team progress and time-boundary roster tokens invalidate the lobby, stable tokens do not", async () => {
  const f = fixture();
  await f.start();
  const released = sync({
    viewer: { ...viewer, team_id: 11 },
    revisions: {
      content: "content-1",
      roster: "released",
      progress: "team11-1",
    },
    onboarding: { roster_released: true, team: { id: 11 } },
  });
  await f.advance(5000);
  f.heartbeats[0].resolve(released);
  await flush();
  assert.equal(f.state.data, null);
  f.full[1].resolve(bootstrap(released));
  await flush();
  const progressed = {
    ...released,
    revisions: { ...released.revisions, progress: "team11-2" },
  };
  await f.advance(5000);
  f.heartbeats[1].resolve(progressed);
  await flush();
  f.full[2].resolve(bootstrap(progressed));
  await flush();
  await f.advance(5000);
  f.heartbeats[2].resolve(progressed);
  await flush();
  assert.equal(f.full.length, 3);
  f.session.stop();
});

test("hidden/offline sessions suspend automatic work and resume one immediate heartbeat", async () => {
  const f = fixture();
  await f.start();
  f.setActive(false);
  await f.advance(60000);
  assert.equal(f.heartbeats.length, 0);
  f.setActive(true);
  f.session.activity();
  f.session.activity();
  await f.advance(0);
  assert.equal(f.heartbeats.length, 1);
  f.heartbeats[0].resolve(sync());
  await flush();
  assert.equal(f.full.length, 1);
  f.session.stop();
});

test("an initially hidden session waits for visibility before making automatic requests", async () => {
  const f = fixture();
  f.setActive(false);
  f.session.start();
  await f.advance(60000);
  assert.equal(f.full.length, 0);
  assert.equal(f.heartbeats.length, 0);
  f.setActive(true);
  await f.advance(0);
  f.heartbeats[0].resolve(sync());
  await flush();
  assert.equal(f.full.length, 1);
  f.full[0].resolve(bootstrap());
  await flush();
  assert.equal(f.state.data.viewer.id, 1);
  f.session.stop();
});

test("an explicit hidden-tab action still waits for its updated room data", async () => {
  const f = fixture();
  await f.start();
  f.setActive(false);
  const pending = f.session.refresh();
  const changed = sync({
    revisions: { content: "new", roster: "same", progress: "updated" },
  });
  f.heartbeats[0].resolve(changed);
  await flush();
  assert.equal(f.full.length, 2);
  f.full[1].resolve(bootstrap(changed));
  await pending;
  assert.deepEqual(f.state.data.revisions, changed.revisions);
  f.session.stop();
});

test("auth failure clears identity/token and invalidates a pending full read even for same-account recovery", async () => {
  const f = fixture();
  await f.start();
  const navigation = f.session.navigation();
  f.heartbeats[0].resolve(sync());
  await flush();
  assert.equal(f.full.length, 2);
  await f.advance(5000);
  f.heartbeats[1].reject(
    Object.assign(new Error("Sign in again"), { status: 401 }),
  );
  await flush();
  assert.equal(f.state.data, null);
  assert.equal(f.state.live, null);
  const recovered = f.session.refresh();
  f.heartbeats[2].resolve(sync({ csrfNonce: "new-session-token" }));
  await flush();
  f.full[1].resolve(bootstrap({ csrfNonce: "expired-session-token" }));
  await flush();
  assert.equal(f.state.data, null);
  assert.equal(f.full.length, 3);
  f.full[2].resolve(bootstrap({ csrfNonce: "old-full-token" }));
  await Promise.all([navigation, recovered]);
  assert.equal(f.state.data.csrfNonce, "new-session-token");
  assert.equal(f.state.error, "");
  f.session.stop();
});

test("a full read for another account stays private until the heartbeat confirms that account", async () => {
  const f = fixture();
  await f.start();
  const navigation = f.session.navigation();
  f.heartbeats[0].resolve(sync());
  await flush();
  const next = sync({
    viewer: { ...viewer, id: 2, team_id: 20 },
    csrfNonce: "account2-token",
  });
  f.full[1].resolve(
    bootstrap({ ...next, rooms: [{ name: "Account 2 rooms" }] }),
  );
  await flush();
  assert.equal(f.state.data.viewer.id, 1);
  assert.notEqual(f.state.data.rooms[0].name, "Account 2 rooms");
  f.heartbeats[1].resolve(next);
  await navigation;
  assert.equal(f.state.data.viewer.id, 2);
  assert.equal(f.state.data.rooms[0].name, "Account 2 rooms");
  assert.equal(f.state.data.csrfNonce, "account2-token");
  assert.equal(f.full.length, 2);
  f.session.stop();
});

test("content failures back off while explicit retry bypasses backoff", async () => {
  const f = fixture();
  await f.start();
  await f.advance(5000);
  const changed = sync({
    revisions: { content: "2", roster: "1", progress: "1" },
  });
  f.heartbeats[0].resolve(changed);
  await flush();
  f.full[1].reject(new Error("Room load failed"));
  await flush();
  await f.advance(5000);
  f.heartbeats[1].resolve(changed);
  await flush();
  assert.equal(f.full.length, 2);
  const retry = f.session.refresh();
  f.heartbeats[2].resolve(changed);
  await flush();
  assert.equal(f.full.length, 3);
  f.full[2].resolve(bootstrap(changed));
  await retry;
  assert.equal(f.state.error, "");
  f.session.stop();
});

test("stopping aborts both lanes, settles callers and ignores late results", async () => {
  const f = fixture();
  await f.start();
  const pending = f.session.navigation();
  f.heartbeats[0].resolve(sync());
  await flush();
  const before = f.published.length;
  f.session.stop();
  await pending;
  assert.equal(f.full[1].signal.aborted, true);
  f.full[1].resolve(bootstrap({ csrfNonce: "late-token" }));
  await flush();
  assert.equal(f.published.length, before);
});
