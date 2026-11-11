import { useState } from "react";
import { request } from "./api";
import { useResource } from "./hooks";
import type { Projector, StandingTeam, Standings, Viewer } from "./types";
import { Link, Notice } from "./ui";
import { orderedTeams } from "./standings-presentation";
import {
  leadingTeams,
  rotationFrame,
  manualOverviewElapsed,
} from "./projector-rotation";
import { RoomOverview } from "./room-leaders";
import { StandingTable } from "./standing-table";
import { Awards } from "./awards";
import { useProjectorClock } from "./use-projector-clock";
export function Stage({ viewer }: { viewer?: Viewer }) {
  const result = useResource<Standings>("/standings", 8000),
    config = useResource<Projector>("/projector", 8000);
  const now = useProjectorClock(config.data?.server_now ?? result.data?.as_of);
  const [error, setError] = useState("");
  const [manualPause, setManualPause] = useState<number | null>(null);
  const [manualOffset, setManualOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const data = result.data;
  if (!data)
    return (
      <div className="stage">
        <h1>Open Tech Quest</h1>
        {result.error ? (
          <Notice tone="error" retry={() => void result.refresh()}>
            {result.error}
          </Notice>
        ) : (
          <p role="status">Connecting to the standings…</p>
        )}
      </div>
    );
  const frame = rotationFrame(config.data, data.rooms, now);
  const room = data.rooms.find((r) => r.slug === frame.room_slug);
  // Missing rooms and unpublished awards fall back to the room overview.
  const view =
    (frame.view === "room" && !room) ||
    (frame.view === "awards" && !data.event.results_published)
      ? "overview"
      : frame.view;
  const sorted = orderedTeams(data.teams, view === "room" ? room : undefined);
  const teams = view === "overall" ? leadingTeams(sorted, 5) : sorted;
  const rotation = config.data?.rotation;
  async function rotationAction(action: string) {
    setBusy(true);
    setError("");
    try {
      await request("/admin/projector", "PATCH", { rotation_action: action });
      await config.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const title =
    view === "overview"
      ? "Room award overview"
      : view === "matrix"
        ? "Every team. Every room."
        : view === "room"
          ? `${room?.name} standings`
          : view === "overall"
            ? data.scoring_version === 2
              ? "Overall points leaders"
              : "Overall solve leaders"
            : "The quest awards";
  return (
    <div
      className={`stage stage-${view}${teams.length > 25 ? " many-teams" : ""}`}
    >
      <header className="stage-header">
        <div>
          <Link href="/quest" className="stage-brand">
            <img
              src="/plugins/open_tech_quest/assets/branding/quest-mark.png"
              alt="Open Tech Quest"
            />
          </Link>
          <p>Red Hat Hands-On Day · Darmstadt</p>
        </div>
        <div className="stage-title">
          <h1 title={title}>{title}</h1>
          <span>
            {view === "overall" && <>Leading five places, including ties · </>}
            {data.event.results_published
              ? "Final results"
              : data.event.status === "closed"
                ? "Results under review"
                : data.event.status === "paused"
                  ? "Event paused · provisional results"
                  : data.event.status === "draft"
                    ? "Getting ready"
                    : "Provisional results"}
            {rotation?.enabled &&
              ` · Rotation ${rotation.paused_at ? "paused" : "on"}`}
          </span>
        </div>
      </header>
      {view === "room" && room?.pause?.paused && (
        <div className="stage-room-pause" role="status">
          <strong>{room.name} paused</strong>
          {room.pause.reason ? ` · ${room.pause.reason}` : ""}
        </div>
      )}
      {(result.error || config.error) && (
        <div className="stage-stale" role="status">
          Connection interrupted · showing saved results
        </div>
      )}
      {view === "overview" ? (
        <RoomOverview
          data={data}
          elapsed={
            rotation?.enabled
              ? frame.elapsed
              : manualOverviewElapsed((manualPause ?? now) - manualOffset)
          }
        />
      ) : view === "awards" ? (
        <Awards
          data={data}
          elapsed={manualOverviewElapsed((manualPause ?? now) - manualOffset)}
        />
      ) : !teams.length ? (
        <div className="stage-empty">
          <h2>
            {data.teams.length
              ? "A first solve sets the benchmark"
              : "The quest starts with your team"}
          </h2>
          <p>
            {data.teams.length
              ? "Every qualifying solve contributes to the awards."
              : "Teams appear after roster release."}
          </p>
        </div>
      ) : (
        <>
          <StageTables
            data={data}
            teams={teams}
            room={view === "room" ? room : undefined}
            overall={view === "overall"}
          />
        </>
      )}
      <footer className="stage-footer">
        <span>
          {view === "room"
            ? data.scoring_version === 2
              ? "Solved → net room points → active time"
              : "Consecutive solves → active time"
            : view === "overall"
              ? data.scoring_version === 2
                ? "Net points → active time"
                : "Distinct solves → active time"
              : view === "matrix"
                ? "Overall rank · room progress: solved / total"
                : "Room awards and an overall award"}{" "}
          · {data.teams.length} teams · {data.rooms.length} rooms · ties share
          rank
        </span>
        <span>
          Updated{" "}
          {new Intl.DateTimeFormat(undefined, {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
          }).format(new Date(data.as_of))}
        </span>
        {(view === "overview" || view === "awards") && !rotation?.enabled && (
          <button
            onClick={() => {
              if (manualPause !== null) {
                setManualOffset(
                  (offset) => offset + Math.max(0, now - manualPause),
                );
                setManualPause(null);
              } else setManualPause(now);
            }}
          >
            {manualPause !== null ? "Resume pages" : "Pause pages"}
          </button>
        )}
        {viewer?.admin && rotation?.enabled && (
          <button
            disabled={busy}
            onClick={() =>
              void rotationAction(rotation.paused_at ? "resume" : "pause")
            }
          >
            {rotation.paused_at ? "Resume rotation" : "Pause rotation"}
          </button>
        )}
        <button
          onClick={async () => {
            try {
              if (document.fullscreenElement) await document.exitFullscreen();
              else await document.documentElement.requestFullscreen();
            } catch {
              setError("Use your browser’s full screen command");
            }
          }}
        >
          Full screen
        </button>
        {error && <span role="alert">{error}</span>}
      </footer>
    </div>
  );
}
function StageTables({
  data,
  teams,
  room,
  overall,
}: {
  data: Standings;
  teams: StandingTeam[];
  room?: Standings["rooms"][number];
  overall: boolean;
}) {
  const split = Math.ceil(teams.length / 2);
  return (
    <div
      className={`stage-columns${teams.length <= 25 ? " single-panel" : ""}`}
      style={
        {
          "--room-count": Math.max(1, data.rooms.length),
        } as React.CSSProperties
      }
    >
      {(teams.length <= 25
        ? [teams]
        : [teams.slice(0, split), teams.slice(split)]
      ).map((part, index) => (
        <div className="stage-panel" key={index}>
          <StandingTable
            data={data}
            teams={part}
            room={room}
            stage
            overall={overall}
          />
        </div>
      ))}
    </div>
  );
}
