import assert from "node:assert/strict";
import test from "node:test";
import { createBackupDayLoader } from "../src/backup-catalog.ts";
import { getBackupDay } from "../src/backup-transport.ts";
import { backupIntervalLabel, backupDayLabel, backupTimeLabel, backupReviewAvailable, backupRestoreSupported } from "../src/backup-presentation.ts";
import { restoreConfirmationValid } from "../src/backup-confirmation.ts";
async function flush() {for (let n=0;n<8;n++) await Promise.resolve();}
function page(day, ids, {total = ids.length, next = null, revision = "catalog-a"} = {}) {
  return {day, snapshots: ids.map(id => ({id})), next_cursor: next, total, catalog_revision: revision};
}
function fixture(load) {
  const calls = []; let state;
  const browser = createBackupDayLoader((...args) => {calls.push(args); return load(...args);}, value => {state = value;});
  return {browser, calls, state: () => state};
}
test("day transport requests only twenty records with an encoded opaque cursor and abort signal", async () => {
  const calls=[]; const signal = new AbortController().signal;
  const send = async (...args) => {calls.push(args); return {synthetic: true};};
  await getBackupDay(send, "2026-11-12", "opaque+cursor/=", signal);
  const url = new URL(calls[0][0], "https://event.example.test");
  assert.equal(url.pathname, "/admin/backups/snapshots");
  assert.equal(url.searchParams.get("limit"), "20");
  assert.equal(url.searchParams.get("day"), "2026-11-12");
  assert.equal(url.searchParams.get("before"), "opaque+cursor/=");
  assert.equal(calls[0][1], "GET"); assert.equal(calls[0][2], undefined); assert.equal(calls[0][3], signal);
});
test("unchecked rows can enter authoritative review but cannot confirm a restore", () => {
  const snapshot={origin:"manual", compatibility:{supported:false, reason:null, checked:false}};
  assert.equal(backupReviewAvailable(snapshot), true);
  assert.equal(backupRestoreSupported(snapshot), false);
  assert.equal(restoreConfirmationValid({snapshot,full_replace:true,safety_backup_required:true,confirmation_text:"Example event",expires_at:"2099-01-01T00:00:00Z"}, "Example event", "Review synthetic state"),false);
  assert.equal(backupReviewAvailable({...snapshot, origin:"legacy"}), false);
  assert.equal(backupReviewAvailable({...snapshot, compatibility:{supported:false,reason:"Different image",checked:true}}), false);
});
test("schedule uses readable intervals and backup history stays on UTC calendar boundaries", () => {
  assert.equal(backupIntervalLabel(900), "Every 15 minutes");
  assert.equal(backupIntervalLabel(3600), "Every 1 hour");
  assert.equal(backupIntervalLabel(86400), "Every 1 day");
  assert.equal(backupIntervalLabel(45), "Every 45 seconds");
  assert.match(backupDayLabel("2026-11-12"), /12/);
  assert.match(backupTimeLabel("2026-11-12T00:05:00Z"), /UTC/);
});
test("a large catalog stays lazy: selecting one day fetches only its first page", async () => {
  const ids = Array.from({length:1200}, (_,i)=>String(i));
  const f=fixture(async day => page(day,ids.slice(0,20),{total:1200,next:"after-first20"}));
  assert.equal(f.calls.length,0);
  f.browser.open("2026-11-12","day-count-1200"); await flush();
  assert.equal(f.calls.length,1); assert.equal(f.calls[0][1],null);
  assert.equal(f.state().page.snapshots.length,20); assert.equal(f.state().page.total,1200);
  f.browser.close();
});
test("closing a day aborts its request and ignores a provider that resolves late", async () => {
  let resolve; const f=fixture(()=>new Promise(done=>{resolve=done;}));
  f.browser.open("2026-11-12","count-20"); const signal=f.calls[0][2];
  f.browser.open(null,""); assert.equal(signal.aborted,true);
  resolve(page("2026-11-12",["private-row"])); await flush();
  assert.equal(f.state().day,null); assert.equal(f.state().page,null);
});
test("switching days discards late responses from the previous day", async () => {
  let resolve; const f=fixture(day => day === "2026-11-12" ? new Promise(done=>{resolve=done;}) : Promise.resolve(page(day,["other-day"])));
  f.browser.open("2026-11-12","first"); f.browser.open("2026-11-11","second"); await flush();
  resolve(page("2026-11-12",["stale"])); await flush();
  assert.equal(f.state().day,"2026-11-11"); assert.deepEqual(f.state().page.snapshots.map(s=>s.id),["other-day"]);
});
test("reopening unchanged historical days reuses their cached page; changed counts refetch", async () => {
  const f=fixture(async day=>page(day,["fixture"]));
  f.browser.open("2026-11-01","2026-11-01:1"); await flush();
  f.browser.open(null,""); f.browser.open("2026-11-01","2026-11-01:1"); await flush();
  assert.equal(f.calls.length,1);
  f.browser.open("2026-11-01","2026-11-01:2"); await flush(); assert.equal(f.calls.length,2);
  f.browser.refresh(); await flush(); assert.equal(f.calls.length,3);
});
test("pagination deduplicates stable keys when another day changes the global catalog", async () => {
  const f=fixture(async (day,before)=>before ? page(day,["b","c"],{total:3,revision:"changed-other-day"}) : page(day,["a","b"],{total:3,next:"next"}));
  f.browser.open("2026-11-12","count3"); await flush(); await f.browser.more();
  assert.deepEqual(f.state().page.snapshots.map(s=>s.id),["a","b","c"]);
  assert.equal(f.state().page.next_cursor,null); assert.equal(f.calls.length,2);
});
test("capture or retention changes within the selected day restart pagination instead of mixing generations", async () => {
  let first=0;
  const f=fixture(async (day,before)=>before ? page(day,["old-tail"],{total:4,revision:"new"}) : ++first===1 ? page(day,["old-head"],{total:3,next:"next"}) : page(day,["new-head"],{total:4,next:"new-next",revision:"new"}));
  f.browser.open("2026-11-12","count3"); await flush(); await f.browser.more();
  assert.equal(f.calls.length,3); assert.deepEqual(f.state().page.snapshots.map(s=>s.id),["new-head"]);
  assert.match(f.state().notice,/history changed/);
});
test("page failures keep visible rows and retry the same continuation", async () => {
  let more=0;
  const f=fixture(async (day,before)=>{if(before && ++more===1) throw new Error("Synthetic network interruption"); return before ? page(day,["b"],{total:2}) : page(day,["a"],{total:2,next:"next"});});
  f.browser.open("2026-11-12","count2"); await flush(); await f.browser.more();
  assert.match(f.state().error,/Synthetic/); assert.equal(f.state().page.snapshots[0].id,"a");
  await f.browser.retry(); assert.equal(f.state().error,""); assert.deepEqual(f.state().page.snapshots.map(s=>s.id),["a","b"]);
});
test("day cache is bounded and evicts old entries rather than retaining an unbounded history", async () => {
  const f=fixture(async day=>page(day,[day]));
  for(let n=1;n<=9;n++){f.browser.open(`2026-11-${String(n).padStart(2,"0")}`,"one");await flush();}
  f.browser.open("2026-11-01","one");await flush();assert.equal(f.calls.length,10);
});

test("schema comparison requires both Quest and native schema agreement and never grants restore compatibility", async () => {
  const {backupSchemaComparison} = await import("../src/backup-presentation.ts");
  const current = {ctfd_version:"3.8.9",plugin_version:"synthetic",schema_revision:"quest_0013",ctfd_schema_revision:"native-a"};
  assert.equal(backupSchemaComparison(current,current),"match");
  assert.equal(backupSchemaComparison({...current,schema_revision:"quest_0012"},current),"mismatch");
  assert.equal(backupSchemaComparison({...current,ctfd_schema_revision:"native-b"},current),"mismatch");
  assert.equal(backupSchemaComparison({...current,schema_revision:"unknown"},current),"unknown");
  assert.equal(backupSchemaComparison({...current,ctfd_schema_revision:""},current),"unknown");
  assert.equal(backupSchemaComparison(current,null),"unknown");
  assert.equal(backupSchemaComparison({...current,schema_revision:"legacy",ctfd_schema_revision:"native-b"},current),"mismatch");
  assert.equal(backupRestoreSupported({origin:"manual",compatibility:{supported:false,checked:false,reason:null}}),false);
});
