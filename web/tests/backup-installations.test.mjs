import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

test("restore review distinguishes source and destination even when their names match", async () => {
  const server = await createServer({
    configFile: false,
    server: { middlewareMode: true },
    appType: "custom",
  });
  try {
    const { RestoreInstallationDetails } = await server.ssrLoadModule(
      "/src/backup-details.tsx",
    );
    const review = {
      target: {
        name: "Open Tech Quest",
        id: "destination-b",
        origin: "https://replacement.example.test",
      },
      snapshot: {
        instance: { name: "Open Tech Quest", id: "source-a" },
      },
    };
    const render = (value) =>
      renderToStaticMarkup(
        createElement(RestoreInstallationDetails, { review: value }),
      );
    const cross = render(review);
    assert.match(cross, /Backup source.*source-a/s);
    assert.match(cross, /Restore destination.*destination-b/s);
    assert.match(cross, /Destination URL.*https:\/\/replacement\.example\.test/s);
    assert.match(
      render({ ...review, snapshot: {} }),
      /Backup source.*Not recorded/s,
    );
    assert.match(
      render({
        ...review,
        snapshot: { instance: { name: "<script>unsafe</script>", id: "source-a" } },
      }),
      /&lt;script&gt;unsafe&lt;\/script&gt;/,
    );
  } finally {
    await server.close();
  }
});
