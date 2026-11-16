import test from "node:test";
import assert from "node:assert/strict";
import { comparisonAccess, standingsView, OPEN_STANDINGS } from "../src/standings-access.ts";

test("admins retain both comparisons and projector regardless of organizer switches", () => {
  const closed={room_comparison:false,overall_comparison:false,projector:false};
  assert.deepEqual(comparisonAccess({admin:true},closed),OPEN_STANDINGS);
  assert.deepEqual(comparisonAccess({admin:false},closed),closed);
});
test("disabled direct comparison links fall back to participant's own progress", () => {
  for (const room_comparison of [false,true]) for (const overall_comparison of [false,true]) {
    const access={room_comparison,overall_comparison,projector:room_comparison&&overall_comparison};
    assert.equal(standingsView("room",true,access),room_comparison?"room":"team");
    assert.equal(standingsView("overall",true,access),overall_comparison?"overall":"team");
    assert.equal(standingsView(null,true,access),"team");
  }
});
test("guests and maintainers use an allowed comparison or receive no comparison view", () => {
  assert.equal(standingsView(null,false,{room_comparison:false,overall_comparison:true,projector:false}),"overall");
  assert.equal(standingsView("overall",false,{room_comparison:true,overall_comparison:false,projector:false}),"room");
  assert.equal(standingsView("overall",false,{room_comparison:false,overall_comparison:false,projector:false}),null);
});
test("existing open settings remain the default", () => {
  assert.deepEqual(comparisonAccess(undefined),OPEN_STANDINGS);
  assert.equal(standingsView(null,false,OPEN_STANDINGS),"room");
});
