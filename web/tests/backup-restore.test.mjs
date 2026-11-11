import assert from "node:assert/strict";
import test from "node:test";
import {
  createBackup,
  reviewBackupRestore,
  restoreBackup,
  getBackupJob,
  uploadBackup,
  workerStatusUrl,
  readBackupStatus,
  backupDownloadPath,
} from "../src/backup-transport.ts";
import { createBackupProgress } from "../src/backup-progress.ts";
import {
  backupReasonValid,
  restoreConfirmationValid,
} from "../src/backup-confirmation.ts";

const origin = "https://backups.example.test";
function operation(id = "run-a", kind = "restore", status = "queued") {
  return {
    job: {
      id,
      operation_id: id,
      kind,
      status,
      stage: "queued",
      message: "Queued",
      created_at: "2026-11-12T00:00:00Z",
      updated_at: "2026-11-12T00:00:00Z",
    },
    ticket: {
      status_url: `${origin}/v1/jobs/${id}/status`,
      status_token: "synthetic-readonly-capability",
    },
  };
}
const review = {
  full_replace: true,
  safety_backup_required: true,
  confirmation_text: "RESTORE Example event / instance-a",
  expires_at: "2026-11-12T00:10:00Z",
  snapshot: { compatibility: { supported: true } },
};
const at = Date.parse("2026-11-12T00:00:00Z");
async function flush() {
  for (let n = 0; n < 8; n++) await Promise.resolve();
}
function fixture() {
  let now = 0,
    sequence = 0,
    watching = 0,
    activity;
  const timers = new Map(),
    requests = [];
  const progress = createBackupProgress({
    active: () => true,
    clock: {
      now: () => now,
      random: () => 0.5,
      setTimer(fn, delay) {
        const id = ++sequence;
        timers.set(id, { fn, at: now + delay });
        return id;
      },
      clearTimer: (id) => timers.delete(id),
    },
    watchActivity(fn) {
      watching++;
      activity = fn;
      return () => {
        watching--;
      };
    },
    load: (worker, op, signal) =>
      new Promise((resolve, reject) =>
        requests.push({ worker, op, signal, resolve, reject }),
      ),
  });
  return {
    progress,
    requests,
    timers,
    watching: () => watching,
    activity: () => activity?.(),
    async advance(ms) {
      const end = now + ms;
      for (;;) {
        const next = [...timers.entries()]
          .filter(([, x]) => x.at <= end)
          .sort((a, b) => a[1].at - b[1].at)[0];
        if (!next) break;
        now = next[1].at;
        timers.delete(next[0]);
        next[1].fn();
        await flush();
      }
      now = end;
      await flush();
    },
  };
}

test("restore requires exact target text, current review, compatibility, full replacement and safety backup", () => {
  assert.equal(
    restoreConfirmationValid(
      review,
      review.confirmation_text,
      "Recover event",
      at,
    ),
    true,
  );
  for (const text of [
    "",
    review.confirmation_text.toLowerCase(),
    `${review.confirmation_text} `,
  ])
    assert.equal(
      restoreConfirmationValid(review, text, "Recover event", at),
      false,
    );
  for (const change of [
    { expires_at: "invalid" },
    { expires_at: "2026-11-11T23:59:00Z" },
    { full_replace: false },
    { safety_backup_required: false },
    { snapshot: { compatibility: { supported: false } } },
  ])
    assert.equal(
      restoreConfirmationValid(
        { ...review, ...change },
        review.confirmation_text,
        "Recover event",
        at,
      ),
      false,
    );
  assert.equal(
    restoreConfirmationValid(review, review.confirmation_text, "OK", at),
    false,
  );
});
test("reason validation matches trimmed whole-character limits without accepting NUL", () => {
  assert.equal(backupReasonValid("  Restore exercise  "), true);
  assert.equal(backupReasonValid("😀😀"), false);
  assert.equal(backupReasonValid("😀😀😀"), true);
  assert.equal(backupReasonValid("a".repeat(1001)), false);
  assert.equal(backupReasonValid("reason\0"), false);
});
test("review is separate from restore and mutation helpers retain exact confirmation and operation identity", async () => {
  const calls = [],
    send = async (...args) => {
      calls.push(args);
      return {};
    };
  await reviewBackupRestore(send, "snapshot-a");
  assert.deepEqual(calls, [["/admin/backups/snapshot-a/review", "POST", {}]]);
  const exact = " RESTORE exact target ";
  await restoreBackup(
    send,
    "snapshot-a",
    "review-token",
    " Recovery ",
    exact,
    "operation-a",
  );
  assert.deepEqual(calls[1], [
    "/admin/backups/snapshot-a/restore",
    "POST",
    {
      review_token: "review-token",
      reason: "Recovery",
      confirmation: exact,
      operation_id: "operation-a",
    },
  ]);
  await getBackupJob(send, "operation-a");
  assert.deepEqual(calls[2], ["/admin/backups/jobs/operation-a"]);
  await createBackup(send, " Before event ", "operation-b");
  assert.deepEqual(calls[3], [
    "/admin/backups",
    "POST",
    { reason: "Before event", operation_id: "operation-b" },
  ]);
  assert.equal(
    backupDownloadPath("snapshot/a"),
    "/api/v1/quest/admin/backups/snapshot%2Fa/download",
  );
});
test("a lost restore acknowledgement propagates once; the helper never repeats destructive POST", async () => {
  let count = 0;
  const failure = new Error("Synthetic lost acknowledgement");
  await assert.rejects(
    restoreBackup(
      async () => {
        count++;
        throw failure;
      },
      "snapshot-a",
      "review-token",
      "Recovery",
      "Exact target",
      "same-operation",
    ),
    (e) => e === failure,
  );
  assert.equal(count, 1);
});
test("opaque upload sends native multipart nonce plus CSRF header and never reads backup contents", async () => {
  const file = new File(["synthetic opaque bytes"], "synthetic.otq");
  file.text = () => {
    throw new Error("UI must not parse backup");
  };
  file.arrayBuffer = () => {
    throw new Error("UI must not parse backup");
  };
  let calls = 0;
  const result = await uploadBackup(
    file,
    " Upload exercise ",
    "operation-a",
    "test-session-nonce",
    async (path, options) => {
      calls++;
      assert.equal(path, "/api/v1/quest/admin/backups/upload");
      assert.equal(options.credentials, "same-origin");
      assert.equal(options.headers["CSRF-Token"], "test-session-nonce");
      assert.equal(options.headers["Content-Type"], undefined);
      assert.equal(options.headers.Authorization, undefined);
      assert.equal(options.body.get("file"), file);
      assert.equal(options.body.get("nonce"), "test-session-nonce");
      assert.equal(options.body.get("reason"), "Upload exercise");
      assert.equal(options.body.get("operation_id"), "operation-a");
      return Response.json(
        { success: true, data: operation("operation-a", "upload") },
        { status: 202 },
      );
    },
  );
  assert.equal(calls, 1);
  assert.equal(result.job.kind, "upload");
});
test("status capability is sent only to the exact configured HTTPS worker path without cookies or redirects", async () => {
  const op = operation(),
    controller = new AbortController();
  await readBackupStatus(
    origin,
    op,
    controller.signal,
    async (url, options) => {
      assert.equal(url, `${origin}/v1/jobs/run-a/status`);
      assert.equal(options.credentials, "omit");
      assert.equal(options.redirect, "error");
      assert.equal(options.referrerPolicy, "no-referrer");
      assert.equal(options.signal, controller.signal);
      assert.equal(
        options.headers.Authorization,
        "Bearer synthetic-readonly-capability",
      );
      assert.equal(options.headers["CSRF-Token"], undefined);
      return Response.json({ job: { ...op.job, status: "running" } });
    },
  );
});
test("foreign status URLs, alternate paths, redirects embedded in URLs and insecure remote workers never receive capability", async () => {
  for (const url of [
    "https://foreign.example.test/v1/jobs/run-a/status",
    `${origin}/v1/jobs/other/status`,
    `${origin}/v1/jobs/run-a/status?token=oops`,
    `${origin}/v1/jobs/run-a/status#fragment`,
    "https://user:password@backups.example.test/v1/jobs/run-a/status",
    "javascript:alert(1)",
  ]) {
    let calls = 0;
    await assert.rejects(
      readBackupStatus(
        origin,
        {
          ...operation(),
          ticket: { status_url: url, status_token: "must-not-send" },
        },
        undefined,
        async () => {
          calls++;
        },
      ),
    );
    assert.equal(calls, 0);
  }
  assert.throws(
    () => workerStatusUrl("http://remote.example.test", operation()),
    /configured/,
  );
  assert.equal(
    workerStatusUrl("http://127.0.0.1:8899", {
      ...operation(),
      ticket: {
        status_url: "http://127.0.0.1:8899/v1/jobs/run-a/status",
        status_token: "fixture",
      },
    }),
    "http://127.0.0.1:8899/v1/jobs/run-a/status",
  );
});
test("worker failures do not expose capability or accept another job or kind", async () => {
  await assert.rejects(
    readBackupStatus(origin, operation(), undefined, async () => {
      throw new Error("synthetic-readonly-capability");
    }),
    (e) => !e.message.includes("synthetic-readonly-capability"),
  );
  for (const job of [
    { ...operation().job, id: "other" },
    { ...operation().job, kind: "backup" },
    { ...operation().job, status: "unknown" },
  ])
    await assert.rejects(
      readBackupStatus(origin, operation(), undefined, async () =>
        Response.json({ job }),
      ),
      /unexpected status/,
    );
});
test("restore polling runs every two seconds and continues after an auth-dependent UI unsubscribes", async (t) => {
  const f = fixture();
  t.after(() => f.progress.clear());
  const unsubscribe = f.progress.subscribe(() => {});
  f.progress.begin(operation(), origin);
  assert.equal(f.requests.length, 1);
  unsubscribe(); // Auth subtree unmounts while the database is replaced.
  f.requests[0].resolve({ ...operation().job, status: "running" });
  await flush();
  await f.advance(2000);
  assert.equal(f.requests.length, 2);
  assert.equal(f.progress.getSnapshot().operation.job.kind, "restore");
  f.requests[1].resolve({
    ...operation().job,
    status: "succeeded",
    requires_login: true,
  });
  await flush();
  await f.advance(10000);
  assert.equal(f.requests.length, 2);
  assert.equal(f.watching(), 0);
});
test("StrictMode-style subscription/recovery does not duplicate requests or activity listeners", async (t) => {
  const f = fixture();
  t.after(() => f.progress.clear());
  const unsubscribe = f.progress.subscribe(() => {});
  f.progress.recover(operation(), origin);
  unsubscribe();
  f.progress.subscribe(() => {});
  f.progress.recover(operation(), origin);
  assert.equal(f.requests.length, 1);
  assert.equal(f.watching(), 1);
  f.progress.recover(operation("stale-index-job", "backup"), origin);
  assert.equal(f.progress.getSnapshot().operation.job.id, "run-a");
});
test("late reads from a cleared job cannot overwrite a new operation", async (t) => {
  const f = fixture();
  t.after(() => f.progress.clear());
  f.progress.begin(operation("old"), origin);
  f.progress.clear();
  assert.equal(f.requests[0].signal.aborted, true);
  f.progress.begin(operation("new"), origin);
  f.requests[0].resolve({ ...operation("old").job, status: "failed" });
  await flush();
  assert.equal(f.progress.getSnapshot().operation.job.id, "new");
});
test("unconfirmed restore is retained without retry and reconciles through operation identity", async (t) => {
  const f = fixture();
  t.after(() => f.progress.clear());
  f.progress.unconfirmedRestore("operation-a", origin);
  assert.equal(f.requests.length, 0);
  assert.equal(f.progress.getSnapshot().pendingRestoreId, "operation-a");
  f.progress.recover(operation("other"), origin);
  assert.equal(f.requests.length, 0);
  f.progress.recover(operation("operation-a"), origin);
  assert.equal(f.requests.length, 1);
  assert.equal(f.progress.getSnapshot().pendingRestoreId, undefined);
});
test("completed jobs do not resurrect from stale index responses", async (t) => {
  const f = fixture();
  t.after(() => f.progress.clear());
  f.progress.begin(operation(), origin);
  f.requests[0].resolve({ ...operation().job, status: "succeeded" });
  await flush();
  f.progress.clear();
  f.progress.recover(operation(), origin);
  assert.equal(f.progress.getSnapshot(), null);
  assert.equal(f.requests.length, 1);
});
test("a new authoritative restore is recovered after an earlier ordinary backup completed", async (t) => {
  const f = fixture();
  t.after(() => f.progress.clear());
  f.progress.begin(operation("earlier", "backup"), origin);
  f.requests[0].resolve({
    ...operation("earlier", "backup").job,
    status: "succeeded",
  });
  await flush();
  f.progress.recover(operation("current-restore"), origin);
  assert.equal(f.progress.getSnapshot().operation.job.id, "current-restore");
  assert.equal(f.requests.length, 2);
});
test("a bad accepted restore ticket keeps a visible restore state without contacting another origin", () => {
  const f = fixture();
  f.progress.begin(
    {
      ...operation(),
      ticket: {
        status_url: "https://foreign.example.test/v1/jobs/run-a/status",
        status_token: "never-send",
      },
    },
    origin,
  );
  assert.equal(f.requests.length, 0);
  assert.equal(f.progress.getSnapshot().operation.job.kind, "restore");
  assert.match(f.progress.getSnapshot().error, /does not match/);
  f.progress.clear();
});
