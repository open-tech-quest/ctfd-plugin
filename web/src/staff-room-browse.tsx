import { useEffect, useMemo, useRef } from "react";
import { request } from "./api";
import { useResource } from "./hooks";
import type { Bootstrap, Challenge } from "./types";
import { ParticipantModeContext } from "./participant-mode";
import { RoomPage } from "./participant";
import { Link, Notice } from "./ui";
import { EXAMPLE_PLACEHOLDER_CONTEXT } from "./placeholders";
import { staffBrowseRoomView, type PreviewCatalog } from "./preview-state";
import "./preview.css";

/** Staff-only content inspection; all transport stays on authorized read/check routes. */
export function StaffRoomBrowse({
  slug,
  staffData,
}: {
  slug: string;
  staffData: Bootstrap;
}) {
  const resource = useResource<PreviewCatalog>(
    "/manage/preview",
    10000,
    request,
    false,
  );
  const current = useRef(resource.data);
  current.current = resource.data;
  const revision = useRef(staffData.revisions.content);
  useEffect(() => {
    if (revision.current !== staffData.revisions.content) {
      revision.current = staffData.revisions.content;
      void resource.refresh();
    }
  }, [staffData.revisions.content]);
  const transport = useMemo<typeof request>(
    () =>
      async <T,>(
        path: string,
        method = "GET",
        body?: unknown,
        signal?: AbortSignal,
      ): Promise<T> => {
        const catalog = current.current;
        if (!catalog) throw new Error("The content has not loaded yet.");
        const roomMatch = /^\/rooms\/([^/]+)$/.exec(path);
        if (roomMatch && method === "GET") {
          const room = catalog.rooms.find(
            (room) => room.slug === decodeURIComponent(roomMatch[1]),
          );
          if (!room) throw new Error("This room is no longer available.");
          return staffBrowseRoomView(room, catalog) as T;
        }
        const challengeMatch =
          /^\/api\/v1\/challenges\/(\d+)(?:\?preview=true)?$/.exec(path);
        const hintMatch = /^\/api\/v1\/hints\/(\d+)(?:\?preview=true)?$/.exec(
          path,
        );
        if (hintMatch && method === "GET") {
          const hint = catalog.challenges
            .flatMap((challenge) => challenge.hints)
            .find((hint) => hint.id === Number(hintMatch[1]));
          if (!hint) throw new Error("This hint is not available.");
          return hint as T;
        }
        const attempt =
          /^\/api\/v1\/challenges\/attempt(?:\?preview=true)?$/.test(path);
        const id = challengeMatch
          ? Number(challengeMatch[1])
          : attempt
            ? (body as { challenge_id: number }).challenge_id
            : null;
        const challenge = catalog.challenges.find(
          (challenge) => challenge.id === id,
        );
        if (!challenge) throw new Error("This content is not available.");
        if (challengeMatch && method === "GET")
          return challenge satisfies Challenge as T;
        if (attempt && method === "POST") {
          const room = catalog.rooms.find(
            (room) => room.slug === challenge.room_slug,
          )!;
          const result = await request<T>(
            `/manage/rooms/${encodeURIComponent(room.slug)}/challenges/${challenge.id}/preview`,
            "POST",
            { submission: (body as { submission: string }).submission },
            signal,
            { "X-Quest-Preview-Revision": room.revision },
          );
          if (
            current.current?.rooms.find(
              (candidate) => candidate.slug === room.slug,
            )?.revision !== room.revision
          )
            throw new Error(
              "The room changed. Check the updated content before trying again.",
            );
          return result;
        }
        throw new Error("This action is not available in content inspection.");
      },
    [],
  );
  const catalog = resource.data,
    source = catalog?.rooms.find((room) => room.slug === slug);
  const data = catalog
    ? {
        ...staffData,
        rooms: catalog.rooms.map((room) => staffBrowseRoomView(room, catalog)),
        revisions: { ...staffData.revisions, progress: catalog.revision },
      }
    : null;
  return (
    <ParticipantModeContext.Provider
      value={{
        preview: false,
        inspect: true,
        stale: false,
        home: "/quest",
        room: (slug) => `/quest/rooms/${encodeURIComponent(slug)}`,
        request: transport,
      }}
    >
      <section
        className="participant-preview-banner"
        aria-label="Staff content inspection"
      >
        <div>
          <strong>Staff room inspection</strong>
          <p>
            Browse every saved challenge and hint, including hidden and locked
            steps. Answer checks record no attempts, solves or purchases.
          </p>
          <p>
            Description examples use team{" "}
            {EXAMPLE_PLACEHOLDER_CONTEXT["team.number"]},{" "}
            {EXAMPLE_PLACEHOLDER_CONTEXT["team.name"]},{" "}
            {EXAMPLE_PLACEHOLDER_CONTEXT["user.name"]} and zero team points.
            Start the user journey from your dashboard to try the participant
            flow.
          </p>
        </div>
        <div className="participant-preview-actions">
          <Link className="text-button" href="/quest">
            Back to dashboard
          </Link>
        </div>
      </section>
      {resource.error && (
        <Notice tone="error" retry={() => void resource.refresh()}>
          {resource.error}
        </Notice>
      )}
      {!catalog && !resource.error && <p role="status">Opening room…</p>}
      {catalog && !source && (
        <Notice>
          This room is no longer available.{" "}
          <Link href="/quest">Return to the dashboard</Link>
        </Notice>
      )}
      {data && source && (
        <RoomPage
          key={`${slug}-${source.revision}`}
          slug={slug}
          data={data}
          onRefresh={resource.refresh}
        />
      )}
    </ParticipantModeContext.Provider>
  );
}
