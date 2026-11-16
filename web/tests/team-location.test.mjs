import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

test("team identity shows its saved location read-only and escapes venue labels", async () => {
  const server = await createServer({configFile: false, server: {middlewareMode: true}, appType: "custom"});
  try {
    const {TeamNumber} = await server.ssrLoadModule("/src/team-number.tsx");
    const render = props => renderToStaticMarkup(createElement(TeamNumber, props));
    const assigned = render({number: 7, location: {venue_room_id: 2, room_name: "2.0234 titanium 3", table_number: 4, label: "2.0234 titanium 3 · Table 4"}});
    assert.match(assigned, /Team number:.*7/s);
    assert.match(assigned, /Location:.*2\.0234 titanium 3 · Table 4/s);
    assert.doesNotMatch(assigned, /<(?:input|select|button|form)\b/);
    assert.match(render({number: 1}), /Location:.*Not assigned yet/s);
    assert.equal(render({number: null, location: {label: "Private location"}}), "");
    const escaped = render({number: 1, location: {label: '<img src=x onerror="bad()"> · Table 1'}});
    assert.doesNotMatch(escaped, /<img/);
    assert.match(escaped, /&lt;img/);
  } finally {await server.close();}
});
