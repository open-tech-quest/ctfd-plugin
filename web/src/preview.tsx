import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, request } from "./api";
import { navigate } from "./hooks";
import { EXAMPLE_PLACEHOLDER_CONTEXT } from "./placeholders";
import type { Bootstrap, Challenge } from "./types";
import { ParticipantModeContext } from "./participant-mode";
import { Lobby, RoomPage } from "./participant";
import { Arrow, ConfirmDialog, Link, Notice } from "./ui";
import { previewHome, previewRoom } from "./preview-routing";
import {
  createPreviewState,
  mergePreviewCatalog,
  previewRoomView,
  previewScore,
  previewHintMetadata,
  previewSteps,
  recordPreviewResult,
  reloadPreviewRoom,
  revealPreviewHint,
  startPreviewRoom,
  type PreviewCatalog,
  type PreviewState,
} from "./preview-state";
import "./preview.css";
export function StartParticipantPreview({
  room,
  disabled = false,
}: {
  room?: string;
  disabled?: boolean;
}) {
  return (
    <button
      className="button secondary"
      disabled={disabled}
      onClick={() => navigate(room ? previewRoom(room) : previewHome)}
    >
      Start user journey
      <Arrow />
    </button>
  );
}
export function ParticipantPreview({
  slug,
  staffData,
}: {
  slug: string | null;
  staffData: Bootstrap;
}) {
  const [state, setState] = useState<PreviewState | null>(null),
    [error, setError] = useState(""),
    [reload, setReload] = useState(false),
    [removed, setRemoved] = useState("");
  const lifetime = useRef(0);
  const current = useRef<PreviewState | null>(null),
    live = useRef(true),
    sequence = useRef(0),
    controller = useRef<AbortController | null>(null);
  function publish(next: PreviewState) {
    current.current = next;
    setState(next);
  }
  async function load(roomToReload?: string) {
    const id = ++sequence.current;
    controller.current?.abort();
    const pending = new AbortController();
    controller.current = pending;
    try {
      const catalog = await request<PreviewCatalog>(
        "/manage/preview",
        "GET",
        undefined,
        pending.signal,
      );
      if (!live.current || id !== sequence.current) return;
      let next = current.current
        ? mergePreviewCatalog(current.current, catalog)
        : createPreviewState(catalog);
      if (roomToReload) next = reloadPreviewRoom(next, roomToReload);
      publish(next);
      setError("");
    } catch (error) {
      if (live.current && id === sequence.current)
        setError((error as Error).message);
    }
  }
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
      ++lifetime.current;
      ++sequence.current;
      controller.current?.abort();
      current.current = null;
    };
  }, []);
  useEffect(() => {
    // Native navigation can preserve the document in BFcache. Leaving still clears Preview.
    const clear = () => {
      ++lifetime.current;
      ++sequence.current;
      controller.current?.abort();
      current.current = null;
      setState(null);
      setError("");
      setReload(false);
      setRemoved("");
    };
    const resume = (event: PageTransitionEvent) => {
      if (event.persisted) {
        clear();
        void load();
      }
    };
    window.addEventListener("pagehide", clear);
    window.addEventListener("pageshow", resume);
    return () => {
      window.removeEventListener("pagehide", clear);
      window.removeEventListener("pageshow", resume);
    };
  }, []);
  useEffect(() => {
    void load();
  }, [staffData.revisions.content]);
  useEffect(() => {
    if (
      state &&
      slug &&
      !state.rooms.some((room) => room.source.slug === slug)
    ) {
      setRemoved("That room was removed. Choose another room to preview.");
      navigate(previewHome);
    }
  }, [state, slug]);
  const transport = useMemo<typeof request>(
    () =>
      async <T,>(
        path: string,
        method = "GET",
        body?: unknown,
        signal?: AbortSignal,
      ): Promise<T> => {
        const snapshot = current.current;
        if (!snapshot) throw new Error("The content has not loaded yet.");
        const roomMatch = /^\/rooms\/([^/]+)(\/start)?$/.exec(path);
        if (roomMatch) {
          const roomSlug = decodeURIComponent(roomMatch[1]);
          if (method === "POST" && roomMatch[2])
            publish(startPreviewRoom(snapshot, roomSlug));
          const room = current.current?.rooms.find(
            (room) => room.source.slug === roomSlug,
          );
          if (!room) throw new Error("This room is no longer available.");
          return previewRoomView(room, previewScore(current.current!)) as T;
        }
        const attempt = path === "/api/v1/challenges/attempt";
        const challengeMatch = /^\/api\/v1\/challenges\/(\d+)$/.exec(path);
        const hintMatch = /^\/api\/v1\/hints\/(\d+)$/.exec(path);
        const unlockHint =
          path === "/api/v1/unlocks" &&
          method === "POST" &&
          (body as { type?: string } | undefined)?.type === "hints";
        const hintId = unlockHint
          ? (body as { target: number }).target
          : hintMatch
            ? Number(hintMatch[1])
            : null;
        const id = attempt
          ? (body as { challenge_id: number }).challenge_id
          : Number(challengeMatch?.[1]);
        const room =
          hintId !== null
            ? snapshot.rooms.find((room) =>
                room.challenges.some((c) =>
                  c.hints.some((h) => h.id === hintId),
                ),
              )
            : snapshot.rooms.find((room) =>
                room.source.steps.some((step) => step.id === id),
              );
        if (!room) throw new Error("This content is not available.");
        if (unlockHint && hintId !== null) {
          // The normal dialog uses this shape; Preview handles it locally, never on the native API.
          publish(revealPreviewHint(snapshot, room.source.slug, hintId));
          return { target: hintId, type: "hints" } as T;
        }
        if (hintMatch && method === "GET" && hintId !== null)
          return previewHintMetadata(snapshot, room.source.slug, hintId) as T;
        if (
          !previewSteps(room).some(
            (step) => step.id === id && step.state !== "locked",
          )
        )
          throw new Error(
            "Start the room and finish its earlier visible challenges first.",
          );
        if (attempt && method === "POST") {
          if (room.stale)
            throw new Error(
              "The room changed. Check the updated challenge path before trying again.",
            );
          const epoch = lifetime.current;
          try {
            const result = await request<{ status: string; message?: string }>(
              `/manage/rooms/${encodeURIComponent(room.source.slug)}/challenges/${id}/preview`,
              "POST",
              { submission: (body as { submission: string }).submission },
              signal,
              { "X-Quest-Preview-Revision": room.source.revision },
            );
            if (
              !live.current ||
              !current.current ||
              epoch !== lifetime.current ||
              current.current.rooms.find(
                (current) => current.source.slug === room.source.slug,
              )?.source !== room.source
            )
              throw new Error(
                "The page changed. Check the room before trying again.",
              );
            publish(
              recordPreviewResult(
                current.current,
                room.source.slug,
                id,
                result.status,
                room.generation,
              ),
            );
            return result as T;
          } catch (error) {
            if (
              error instanceof ApiError &&
              error.status === 409 &&
              live.current &&
              epoch === lifetime.current
            )
              await load();
            throw error;
          }
        }
        if (challengeMatch && method === "GET") {
          const challenge = room.challenges.find((c) => c.id === id)!;
          return {
            ...challenge,
            max_attempts: 0,
            hints: challenge.hints.map((h) => ({
              ...h,
              content: room.revealed.includes(h.id) ? h.content : undefined,
            })),
          } satisfies Challenge as T;
        }
        throw new Error("This action is not available.");
      },
    [],
  );
  const room = state?.rooms.find((room) => room.source.slug === slug);
  const score = state ? previewScore(state) : null;
  const data = state
    ? {
        ...staffData,
        rooms: state.rooms.map((room) => previewRoomView(room, score!)),
        viewer: { ...staffData.viewer, team_name: "Example team" },
        revisions: {
          ...staffData.revisions,
          progress: JSON.stringify(
            state.rooms.map((room) => [
              room.generation,
              room.solved,
              room.revealed,
            ]),
          ),
        },
      }
    : null;
  return (
    <ParticipantModeContext.Provider
      value={{
        preview: true,
        stale: !!room?.stale,
        home: previewHome,
        room: previewRoom,
        request: transport,
      }}
    >
      <section
        className="participant-preview-banner"
        aria-label="Participant preview controls"
      >
        <div>
          <strong>Participant preview</strong>
          <p>
            Progress and hint reveals stay only in this tab and clear when you
            leave or refresh. No event progress or purchases are recorded.
          </p>
          <p>
            Example team: {EXAMPLE_PLACEHOLDER_CONTEXT["team.number"]},{" "}
            {EXAMPLE_PLACEHOLDER_CONTEXT["team.name"]}. User:{" "}
            {EXAMPLE_PLACEHOLDER_CONTEXT["user.name"]}. Points start at zero and
            come from this tab’s correct answers minus confirmed hint costs.
            Clocks stay at zero. Reloading a room clears its progress; costs in
            other rooms remain and can leave a negative balance. External lab
            links use real resources.
          </p>
          {error && (
            <Notice tone="error" retry={() => void load()}>
              {error}
            </Notice>
          )}
          {removed && <Notice>{removed}</Notice>}
          {!!state?.rooms.some((room) => room.stale) && (
            <Notice>
              Saved content changed in{" "}
              {state.rooms
                .filter((room) => room.stale)
                .map((room) => room.source.name)
                .join(", ")}
              . Reload each affected room to use the latest content and clear
              only its preview progress.
            </Notice>
          )}
          {room?.challenges.some((c) => c.unsupported_reason) && (
            <Notice>
              <details>
                <summary>
                  Some challenges cannot be checked in this preview
                </summary>
                <ul>
                  {room.challenges
                    .filter((c) => c.unsupported_reason)
                    .map((c) => (
                      <li key={c.id}>
                        <strong>{c.name}</strong>: {c.unsupported_reason}
                      </li>
                    ))}
                </ul>
              </details>
            </Notice>
          )}
        </div>
        <div className="participant-preview-actions">
          {slug && room && (
            <button
              className="button secondary"
              onClick={() => setReload(true)}
            >
              Reload room
            </button>
          )}
          <Link className="text-button" href="/quest/admin">
            Exit preview
          </Link>
        </div>
      </section>
      {!data && !error && <p role="status">Connecting to the quest…</p>}
      {reload && room && (
        <ConfirmDialog
          label="Reload room"
          description={`Load the latest saved ${room.source.name} content and clear this room’s steps and revealed hints. Other rooms stay unchanged.`}
          onClose={() => setReload(false)}
          onConfirm={async () => {
            await load(room.source.slug);
          }}
        />
      )}
      {data &&
        (slug && room ? (
          <>
            <RoomPage
              key={`${slug}-${room.generation}`}
              slug={slug}
              data={data}
              onRefresh={async () => {}}
            />
          </>
        ) : (
          <Lobby data={data} />
        ))}
    </ParticipantModeContext.Provider>
  );
}
