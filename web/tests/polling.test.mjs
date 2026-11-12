import assert from "node:assert/strict";
import test from "node:test";
import { createPoller } from "../src/polling.ts";

async function flush() {
  for (let index = 0; index < 5; ++index) await Promise.resolve();
}

function fixture(interval = 10000, random = 0.5, periodic = true, requestTimeoutMs) {
  let now = 0;
  let active = true;
  let nextTimer = 0;
  const timers = new Map();
  const requests = [];
  const accepted = [];
  const errors = [];
  const poller = createPoller({
    interval,
    periodic,
    requestTimeoutMs,
    random: () => random,
    now: () => now,
    active: () => active,
    setTimer(callback, delay) {
      const id = ++nextTimer;
      timers.set(id, { callback, at: now + delay });
      return id;
    },
    clearTimer: (id) => timers.delete(id),
    load: (signal) =>
      new Promise((resolve, reject) =>
        requests.push({ signal, resolve, reject, at: now }),
      ),
    accept: (value) => accepted.push(value),
    reject: (error) => errors.push(error),
  });
  return {
    poller,
    requests,
    accepted,
    errors,
    timers,
    setActive(value) {
      active = value;
      poller.activity();
    },
    async advance(ms) {
      const until = now + ms;
      while (true) {
        const next = [...timers.entries()]
          .filter(([, timer]) => timer.at <= until)
          .sort((a, b) => a[1].at - b[1].at)[0];
        if (!next) break;
        now = next[1].at;
        timers.delete(next[0]);
        next[1].callback();
        await flush();
      }
      now = until;
      await flush();
    },
  };
}

test("remote backup reads can finish after the ordinary read deadline", async () => {
  const f = fixture(60000, 0.5, true, 130000);
  f.poller.start();
  await f.advance(40000);
  assert.equal(f.requests.length, 1);
  assert.equal(f.requests[0].signal.aborted, false);
  assert.deepEqual(f.errors, []);
  f.requests[0].resolve("remote inventory");
  await flush();
  assert.deepEqual(f.accepted, ["remote inventory"]);
  await f.advance(19999);
  assert.equal(f.requests.length, 1);
  await f.advance(1);
  assert.equal(f.requests.length, 2);
  f.poller.stop();
  assert.equal(f.requests[1].signal.aborted, true);
  assert.equal(f.timers.size, 0);
});

test("a custom read deadline still releases a stranded request", async () => {
  const f = fixture(60000, 0.5, true, 130000);
  f.poller.start();
  await f.advance(129999);
  assert.equal(f.errors.length, 0);
  await f.advance(1);
  assert.equal(f.requests[0].signal.aborted, true);
  assert.equal(f.errors.length, 1);
  f.requests[0].resolve("late result");
  await flush();
  assert.deepEqual(f.accepted, []);
  f.poller.stop();
  assert.equal(f.timers.size, 0);
});

test("a slow server never accumulates periodic requests", async () => {
  const f = fixture(5000);
  f.poller.start();
  await f.advance(12000);
  assert.equal(f.requests.length, 1);
  f.requests[0].resolve("first");
  await flush();
  await f.advance(0);
  assert.equal(f.requests.length, 2);
  await f.advance(12000);
  assert.equal(f.requests.length, 2);
  f.poller.stop();
});

test("post-mutation refreshes coalesce but await a new server read", async () => {
  const f = fixture();
  f.poller.start();
  let resolved = 0;
  const refreshes = [1, 2, 3].map(() =>
    f.poller.refresh().then(() => ++resolved),
  );
  assert.equal(f.requests.length, 1);
  f.requests[0].resolve({ viewer: "old", nonce: "old" });
  await flush();
  assert.deepEqual(f.accepted, []);
  assert.equal(resolved, 0);
  assert.equal(f.requests.length, 2);
  f.requests[1].resolve({ viewer: "new", nonce: "new" });
  await Promise.all(refreshes);
  assert.equal(resolved, 3);
  assert.deepEqual(f.accepted, [{ viewer: "new", nonce: "new" }]);
  f.poller.stop();
});

test("a mutation during the follow-up still waits for fresh post-mutation data", async () => {
  const f = fixture();
  f.poller.start();
  const first = f.poller.refresh();
  f.requests[0].resolve("before first mutation");
  await flush();
  let resolved = false;
  const second = f.poller.refresh().then(() => (resolved = true));
  f.requests[1].resolve("before second mutation");
  await flush();
  assert.equal(resolved, false);
  assert.deepEqual(f.accepted, []);
  assert.equal(f.requests.length, 3);
  f.requests[2].resolve("after both mutations");
  await Promise.all([first, second]);
  assert.deepEqual(f.accepted, ["after both mutations"]);
  f.poller.stop();
});

test("focus, visibility and online events share one jittered refresh", async () => {
  const f = fixture();
  f.poller.start();
  f.requests[0].resolve("initial");
  await flush();
  f.poller.activity();
  f.poller.activity();
  f.poller.activity();
  assert.equal(f.timers.size, 1);
  await f.advance(499);
  assert.equal(f.requests.length, 1);
  await f.advance(1);
  assert.equal(f.requests.length, 2);
  f.requests[1].resolve("reconnected");
  await flush();
  assert.deepEqual(f.accepted, ["initial", "reconnected"]);
  f.poller.stop();
});

test("a returning tab discards the older in-flight account/nonce response", async () => {
  const f = fixture(5000);
  f.poller.start();
  f.poller.activity();
  f.poller.activity();
  await f.advance(0);
  assert.equal(f.requests.length, 1);
  f.requests[0].resolve({ viewer: "old account", nonce: "old" });
  await flush();
  assert.deepEqual(f.accepted, []);
  assert.equal(f.requests.length, 2);
  f.requests[1].resolve({ viewer: "current account", nonce: "current" });
  await flush();
  assert.deepEqual(f.accepted, [
    { viewer: "current account", nonce: "current" },
  ]);
  f.poller.stop();
});

test("healthy attendance checks never add jitter beyond the five-second cadence", async () => {
  const f = fixture(5000, 1);
  f.poller.start();
  await f.advance(350);
  f.requests[0].resolve("ready");
  await flush();
  await f.advance(4649);
  assert.equal(f.requests.length, 1);
  await f.advance(1);
  assert.equal(f.requests[1].at, 5000);
  f.poller.stop();
});

test("attendance refreshes immediately when a hidden tab becomes active", async () => {
  const f = fixture(5000);
  f.setActive(false);
  f.poller.start();
  await f.advance(10000);
  assert.equal(f.requests.length, 0);
  f.setActive(true);
  await f.advance(0);
  assert.equal(f.requests.length, 1);
  assert.equal(f.requests[0].at, 10000);
  f.poller.stop();
});

test("hidden/offline resources stop automatic work and resume once", async () => {
  const f = fixture();
  f.poller.start();
  f.requests[0].resolve("initial");
  await flush();
  f.setActive(false);
  await f.advance(100000);
  assert.equal(f.requests.length, 1);
  assert.equal(f.timers.size, 0);
  f.setActive(true);
  f.poller.activity();
  await f.advance(500);
  assert.equal(f.requests.length, 2);
  f.poller.stop();
});

test("an explicit refresh bypasses reconnect jitter without leaving another timer", async () => {
  const f = fixture();
  f.poller.start();
  f.requests[0].resolve("initial");
  await flush();
  f.poller.activity();
  const manual = f.poller.refresh();
  assert.equal(f.requests.length, 2);
  await f.advance(1000);
  assert.equal(f.requests.length, 2);
  f.requests[1].resolve("after mutation");
  await manual;
  assert.deepEqual(f.accepted, ["initial", "after mutation"]);
  f.poller.stop();
});

test("hiding the tab cannot strand an explicit caller behind a discarded response", async () => {
  const f = fixture();
  const manual = f.poller.refresh();
  f.poller.activity();
  f.setActive(false);
  f.requests[0].resolve("before account activity");
  await flush();
  assert.deepEqual(f.accepted, []);
  assert.equal(f.requests.length, 2);
  f.requests[1].resolve("current state");
  await manual;
  assert.deepEqual(f.accepted, ["current state"]);
  assert.equal(f.timers.size, 0);
  f.poller.stop();
});

test("failures back off, while an explicit refresh bypasses the wait", async () => {
  const f = fixture(5000);
  f.poller.start();
  f.requests[0].reject(new Error("offline"));
  await flush();
  await f.advance(9999);
  assert.equal(f.requests.length, 1);
  await f.advance(1);
  assert.equal(f.requests.length, 2);
  f.requests[1].reject(new Error("still offline"));
  await flush();
  await f.advance(19999);
  assert.equal(f.requests.length, 2);
  const manual = f.poller.refresh();
  assert.equal(f.requests.length, 3);
  f.requests[2].resolve("recovered");
  await manual;
  await f.advance(5000);
  assert.equal(f.requests.length, 4);
  assert.deepEqual(f.accepted, ["recovered"]);
  f.poller.stop();
});

test("a stalled GET times out, backs off, and cannot later replace recovered data", async () => {
  const f = fixture(5000);
  f.poller.start();
  await f.advance(14999);
  assert.equal(f.requests.length, 1);
  assert.equal(f.requests[0].signal.aborted, false);
  await f.advance(1);
  assert.equal(f.requests[0].signal.aborted, true);
  assert.equal(f.errors.length, 1);
  await f.advance(9999);
  assert.equal(f.requests.length, 1);
  await f.advance(1);
  assert.equal(f.requests.length, 2);
  f.requests[1].resolve("recovered");
  await flush();
  f.requests[0].resolve("late timed-out response");
  await flush();
  assert.deepEqual(f.accepted, ["recovered"]);
  f.poller.stop();
});

test("disposing a resource aborts old work, settles callers, and ignores late data", async () => {
  const f = fixture();
  const pending = f.poller.refresh();
  f.poller.stop();
  await pending;
  assert.equal(f.requests[0].signal.aborted, true);
  f.requests[0].resolve({ viewer: "old path or unmounted account" });
  await flush();
  f.poller.activity();
  await f.poller.refresh();
  await f.advance(100000);
  assert.deepEqual(f.accepted, []);
  assert.equal(f.requests.length, 1);
  assert.equal(f.timers.size, 0);
});

test("manual resources load initially and after actions without leaving a periodic timer", async () => {
  const f = fixture(7000, 0.5, false);
  f.poller.start();
  f.requests[0].resolve("initial");
  await flush();
  assert.equal(f.timers.size, 0);
  const refreshed = f.poller.refresh();
  f.requests[1].resolve("after-action");
  await refreshed;
  await flush();
  assert.deepEqual(f.accepted, ["initial", "after-action"]);
  assert.equal(f.timers.size, 0);
  f.poller.stop();
});
