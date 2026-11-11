import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { transformWithOxc } from "vite";
import { deleteChallenge, deletionFailure } from "../src/challenge-deletion.ts";

// Exercise the shipped request parser, including ApiError's TypeScript
// parameter property, without introducing a second test transport.
const source = await readFile(
  new URL("../src/api.ts", import.meta.url),
  "utf8",
);
const { code: compiled } = await transformWithOxc(source, "api.ts");
const { request, ApiError } = await import(
  `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`
);

async function failedDelete(t, response) {
  let requests = 0;
  let sent;
  t.mock.method(globalThis, "fetch", async (url, options) => {
    requests++;
    sent = { url, options };
    if (response instanceof Error) throw response;
    return response;
  });
  globalThis.window = {
    QUEST_INIT: { csrfNonce: "test-only" },
  };
  t.after(() => {
    delete globalThis.window;
  });
  try {
    await deleteChallenge(request, "test", 17);
    assert.fail("Expected deletion to fail");
  } catch (error) {
    assert.ok(error instanceof ApiError);
    assert.equal(requests, 1, "A failed DELETE must never retry automatically");
    assert.equal(sent.url, "/api/v1/quest/manage/rooms/test/challenges/17");
    assert.equal(sent.options.method, "DELETE");
    assert.equal(sent.options.headers["Content-Type"], "application/json");
    assert.equal(sent.options.headers["CSRF-Token"], "test-only");
    assert.equal(sent.options.body, "{}");
    return { error, message: deletionFailure(error.message, error.status) };
  }
}

test("the shipped deletion helper sends JSON and the session CSRF header on success", async (t) => {
  let sent;
  t.mock.method(globalThis, "fetch", async (url, options) => {
    sent = { url, options };
    return Response.json({ success: true, data: { id: 17, deleted: true } });
  });
  globalThis.window = { QUEST_INIT: { csrfNonce: "test-only" } };
  t.after(() => {
    delete globalThis.window;
  });
  assert.deepEqual(await deleteChallenge(request, "test", 17), {
    id: 17,
    deleted: true,
  });
  assert.equal(sent.url, "/api/v1/quest/manage/rooms/test/challenges/17");
  assert.equal(sent.options.method, "DELETE");
  assert.equal(sent.options.headers["Content-Type"], "application/json");
  assert.equal(sent.options.headers["CSRF-Token"], "test-only");
  assert.equal(sent.options.body, "{}");
});

test("Quest deletion blockers survive the real request parser unchanged", async (t) => {
  const reason =
    "This room has recorded starts. Its challenge content and order are locked.";
  const { error, message } = await failedDelete(
    t,
    Response.json({ success: false, error: reason }, { status: 409 }),
  );
  assert.equal(error.status, 409);
  assert.equal(message, reason);
});

test("native JSON denial is displayed even when its success field is true", async (t) => {
  const reason = "This challenge belongs to an ordered Quest room.";
  const { message } = await failedDelete(
    t,
    Response.json(
      { success: true, data: { message: reason } },
      { status: 403 },
    ),
  );
  assert.equal(message, reason);
});

test("a JSON server failure preserves its reason and requires reconciliation before retry", async (t) => {
  const { message } = await failedDelete(
    t,
    Response.json(
      { success: false, error: "A dependent native record prevented deletion" },
      { status: 500 },
    ),
  );
  assert.match(message, /A dependent native record prevented deletion/);
  assert.match(message, /HTTP 500/);
  assert.match(message, /Refresh the challenge list before retrying/);
});

test("the HTML 500 path becomes actionable without exposing its response markup", async (t) => {
  const { error, message } = await failedDelete(
    t,
    new Response(
      "<html>Internal Server Error <script>secret diagnostics</script></html>",
      { status: 500, headers: { "Content-Type": "text/html" } },
    ),
  );
  assert.equal(error.status, 500);
  assert.match(message, /Deletion was not confirmed/);
  assert.match(message, /Refresh the challenge list before retrying/);
  assert.doesNotMatch(message, /html|secret diagnostics/);
});

test("a lost connection does not assert that deletion rolled back or retry it", async (t) => {
  const { error, message } = await failedDelete(
    t,
    new TypeError("Failed to fetch"),
  );
  assert.equal(error.status, 0);
  assert.match(message, /request may already have completed/);
  assert.match(message, /Refresh the challenge list before retrying/);
  assert.doesNotMatch(message, /team.s saved progress/);
});

test("session expiry keeps the sign-in action rather than suggesting a destructive retry", async (t) => {
  const { message } = await failedDelete(
    t,
    new Response("<html>Login</html>", { status: 401 }),
  );
  assert.equal(message, "Please sign in to continue.");
});
