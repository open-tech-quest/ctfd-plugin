import assert from "node:assert/strict";
import test from "node:test";
import { competitionElapsed, previewClock } from "../src/competition-time.ts";
const clock = (extra = {}) => ({elapsed_ms:60000,state:"running",sampled_at:"2026-11-16T10:00:00.000Z",remaining_ms:null,finished_at:null,...extra});
test("running clocks use monotonic snapshot receipt, including a cached remount",()=>{
 assert.equal(competitionElapsed(clock(),1000,61000),120000);
 assert.equal(competitionElapsed(clock(),1000,900),60000);
 assert.equal(competitionElapsed(clock(),null,61000),60000);
});
test("complete, paused, ended and not-started snapshots never run locally",()=>{
 for(const state of ["complete","paused","ended"])assert.equal(competitionElapsed(clock({state}),0,999999),60000);
 assert.equal(competitionElapsed(clock({state:"not_started",elapsed_ms:null}),0,999999),null);
});
test("scheduled cutoff caps clocks even while the event still says live",()=>{
 assert.equal(competitionElapsed(clock({remaining_ms:2500}),0,10000),62500);
 assert.equal(competitionElapsed(clock(),0,10000,{status:"live",paused_at:null,end_at:"2026-11-16T10:00:02.500Z"}),62500);
});
test("a newly received global pause caps older room snapshots at the pause instant",()=>{
 assert.equal(competitionElapsed(clock(),0,10000,{status:"paused",paused_at:"2026-11-16T10:00:03Z",end_at:null}),63000);
});
test("closed timestamps freeze previously running snapshots until fresh results arrive",()=>{
 assert.equal(competitionElapsed(clock(),0,10000,{status:"closed",paused_at:null,end_at:"2026-11-16T10:00:04Z"}),64000);
});
test("negative organizer corrections stay at zero until the adjustment catches up",()=>{
 const c=clock({elapsed_ms:0,delay_ms:5000});
 assert.equal(competitionElapsed(c,1000,5000),0);
 assert.equal(competitionElapsed(c,1000,7000),1000);
 assert.equal(competitionElapsed({...c,remaining_ms:3000},1000,9000),0);
});
test("participant preview clocks remain zero and never acquire real elapsed time",()=>{
 for(const complete of [false,true])assert.equal(competitionElapsed(previewClock(complete),null,999999),0);
 assert.equal(previewClock(true).state,"complete");
});
