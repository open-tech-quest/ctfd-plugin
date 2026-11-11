import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { transformWithOxc } from "vite";
import {
  changeRoomPause,
  roomHintReadable,
  roomIsPaused,
  roomPauseFailure,
  roomPlayEnabled,
} from "../src/room-pause.ts";

const source = await readFile(
  new URL("../src/api.ts", import.meta.url),
  "utf8",
);
const { code } = await transformWithOxc(source, "api.ts");
const { request, ApiError } = await import(
  `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`
);
const paused = {
  paused: true,
  started_at: "2026-11-12T09:00:00Z",
  reason: "Room lab outage",
  revision: "saved-room-state",
};

test("a room incident blocks only that room during live play, and global pause remains authoritative", () => {
  assert.equal(roomIsPaused(paused), true);
  assert.equal(roomPlayEnabled("live", paused), false);
  assert.equal(roomPlayEnabled("live", { ...paused, paused: false }), true);
  for (const phase of ["draft", "paused", "closed", "published"])
    assert.equal(roomPlayEnabled(phase, { ...paused, paused: false }), false);
  assert.equal(roomPlayEnabled("paused", paused), false);
  assert.equal(
    roomPlayEnabled("live", undefined),
    true,
    "Legacy snapshots have no local pause",
  );
});

test("staff journeys remain independent of the real incident and real event phase", () => {
  assert.equal(roomIsPaused(paused, true), false);
  for (const phase of ["draft", "live", "paused", "closed", "published"])
    assert.equal(roomPlayEnabled(phase, paused, true), true);
});

test("redeemed hints stay readable while both room and event play are blocked", () => {
  assert.equal(roomHintReadable(false, true), true);
  assert.equal(roomHintReadable(false, false), false);
  assert.equal(roomHintReadable(true, false), true);
});

test("pause and resume send the reviewed action, reason and revision with native CSRF", async (t) => {
  const sent = [];
  globalThis.window = { QUEST_INIT: { csrfNonce: "synthetic-nonce" } };
  t.after(() => delete globalThis.window);
  t.mock.method(globalThis, "fetch", async (url, options) => {
    sent.push({ url, options });
    return Response.json({ success: true, data: { pause: paused } });
  });
  await changeRoomPause(
    request,
    "openshift",
    "pause",
    "  Room lab outage  ",
    "round-1-pause-0",
  );
  await changeRoomPause(
    request,
    "openshift",
    "resume",
    "Lab recovered",
    "round-1-pause-1",
  );
  assert.equal(sent.length, 2);
  for (const { url, options } of sent) {
    assert.equal(url, "/api/v1/quest/admin/rooms/openshift/pause");
    assert.equal(options.method, "POST");
    assert.equal(options.headers["Content-Type"], "application/json");
    assert.equal(options.headers["CSRF-Token"], "synthetic-nonce");
  }
  assert.deepEqual(JSON.parse(sent[0].options.body), {
    action: "pause",
    reason: "Room lab outage",
    revision: "round-1-pause-0",
  });
  assert.deepEqual(JSON.parse(sent[1].options.body), {
    action: "resume",
    reason: "Lab recovered",
    revision: "round-1-pause-1",
  });
});

test("a stale or unconfirmed pause is never retried or silently turned into the opposite action", async (t) => {
  let calls = 0;
  globalThis.window = { QUEST_INIT: { csrfNonce: "synthetic-nonce" } };
  t.after(() => delete globalThis.window);
  t.mock.method(globalThis, "fetch", async () => {
    calls++;
    return Response.json(
      { success: false, error: "Room status changed" },
      { status: 409 },
    );
  });
  await assert.rejects(
    changeRoomPause(request, "openshift", "pause", "Outage", "old-state"),
    (error) => {
      assert.ok(error instanceof ApiError);
      assert.match(
        roomPauseFailure(error.message, error.status),
        /Refresh room status/,
      );
      return true;
    },
  );
  assert.equal(calls, 1);
  assert.match(
    roomPauseFailure("Connection lost", 0),
    /may already have completed/,
  );
  assert.match(roomPauseFailure("Server failed", 500), /not confirmed/);
});

test("missing consent metadata cannot issue a pause mutation", () => {
  let calls = 0;
  const send = async () => calls++;
  assert.throws(
    () => changeRoomPause(send, "openshift", "pause", "   ", "current"),
    /provide a reason/,
  );
  assert.throws(
    () => changeRoomPause(send, "openshift", "resume", "Recovered", ""),
    /Refresh/,
  );
  assert.equal(calls, 0);
});
