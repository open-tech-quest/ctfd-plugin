import { useLayoutEffect, useRef, useState } from "react";
import { dateTime, duration } from "./api";
import { useResource } from "./hooks";
import type { Projector, StandingTeam, Standings } from "./types";
import { Empty, Link, Notice } from "./ui";
import { milestoneLayout, progressFraction } from "./stage-progress";
type StandingRoom = Standings["rooms"][number];
export function StandingsPage() {
  const state = useResource<Standings>("/standings", 10000),
    [roomSlug, setRoomSlug] = useState("overall");
  if (!state.data)
    return state.error ? (
      <Notice tone="error" retry={() => void state.refresh()}>
        {state.error}
      </Notice>
    ) : (
      <p role="status">Loading standings…</p>
    );
  const data = state.data;
  const room = data.rooms.find((r) => r.slug === roomSlug);
  const teams = orderedTeams(data.teams, room);
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">One team. Every solve.</p>
          <h1>Team standings</h1>
          <p className="lede">
            {data.event.results_published
              ? "Final results, published by the organizers."
              : "Shared progress across the configured rooms. Results update automatically."}
          </p>
        </div>
        <Link className="button secondary" href="/quest/stage">
          Open projector
        </Link>
      </div>
      {state.error && (
        <Notice tone="error" retry={() => void state.refresh()}>
          Showing saved standings. {state.error}
        </Notice>
      )}
      <nav className="room-tabs" aria-label="Standings category">
        <button
          className={!room ? "active" : ""}
          onClick={() => setRoomSlug("overall")}
        >
          Overall
        </button>
        {data.rooms.map((r) => (
          <button
            key={r.id}
            className={r.slug === roomSlug ? "active" : ""}
            onClick={() => setRoomSlug(r.slug)}
          >
            {r.name}
          </button>
        ))}
      </nav>
      {!teams.length ? (
        <Empty title="The standings are waiting">
          Teams appear after roster release. A first qualifying solve makes a
          team eligible for the awards.
        </Empty>
      ) : (
        <div className="standings-scroll">
          <StandingTable data={data} teams={teams} room={room} />
        </div>
      )}
      <p className="meta updated">
        Last updated {dateTime(data.as_of)} ·{" "}
        {data.event.results_published ? "Final snapshot" : "Live results"}
      </p>
      <AwardRules data={data} />
    </>
  );
}
function orderedTeams(teams: StandingTeam[], room?: StandingRoom) {
  if (!room)
    return [...teams].sort(
      (a, b) =>
        (a.rank ?? Infinity) - (b.rank ?? Infinity) ||
        a.name.localeCompare(b.name),
    );
  return [...teams].sort((a, b) => {
    const x = a.room_progress.find((p) => p.room_id === room.id),
      y = b.room_progress.find((p) => p.room_id === room.id);
    return (
      (x?.rank ?? Infinity) - (y?.rank ?? Infinity) ||
      a.name.localeCompare(b.name)
    );
  });
}
function StandingTable({
  data,
  teams,
  room,
  stage = false,
  overall = false,
}: {
  data: Standings;
  teams: StandingTeam[];
  room?: StandingRoom;
  stage?: boolean;
  overall?: boolean;
}) {
  const pathHeading = useRef<HTMLTableCellElement>(null);
  const [pathWidth, setPathWidth] = useState(0);
  useLayoutEffect(() => {
    const element = pathHeading.current;
    if (!element) return;
    const observer = new ResizeObserver(() =>
      setPathWidth(element.clientWidth - 16),
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [stage, room?.id]);
  const points = data.scoring_version === 2;
  const milestones = milestoneLayout(
    room?.total || 0,
    pathWidth * (points ? 0.5 : 1),
  );
  return (
    <table
      className={`standing-table${stage ? " stage-table" : ""}`}
      style={
        !room && !overall
          ? {
              minWidth:
                (stage ? 360 : 400) +
                (points ? 88 : 0) +
                data.rooms.length * (stage ? 80 : 100),
            }
          : undefined
      }
    >
      <thead>
        <tr>
          <th scope="col" className="rank-col">
            Rank
          </th>
          <th scope="col" className="team-col">
            Team
          </th>
          {!room &&
            !overall &&
            data.rooms.map((r) => (
              <th key={r.id} scope="col" className="room-col" title={r.name}>
                {r.name}
              </th>
            ))}
          {stage && room && (
            <th scope="col" className="path-col" ref={pathHeading}>
              <span>Challenge path</span>
              <span
                className={`stage-path-axis ${milestones.individual ? "individual" : "grouped"}`}
                aria-hidden="true"
              >
                {milestones.labels.map((value) => (
                  <span
                    key={value}
                    style={
                      milestones.individual
                        ? { width: `${100 / room.total}%` }
                        : { left: `${(100 * value) / room.total}%` }
                    }
                  >
                    {value}
                    {points && milestones.individual && (
                      <small>
                        {room.steps?.[value - 1]?.value.toLocaleString()} pts
                      </small>
                    )}
                  </span>
                ))}
              </span>
            </th>
          )}
          <th scope="col" className="score-col">
            Solved
          </th>
          {points && (
            <th scope="col" className="points-col">
              {room ? "Room pts" : "Points"}
            </th>
          )}
          {points && stage && room && (
            <th scope="col" className="team-points-col">
              Team pts
            </th>
          )}
          <th scope="col" className="time-col">
            Time
          </th>
        </tr>
      </thead>
      <tbody>
        {teams.map((team) => {
          const progress = room
            ? team.room_progress.find((p) => p.room_id === room.id)
            : undefined;
          const rank = room ? progress?.rank : team.rank;
          return (
            <tr key={team.id}>
              <td className="rank-col">{rank ?? "—"}</td>
              <th scope="row" title={team.name}>
                {team.name}
              </th>
              {!room &&
                !overall &&
                data.rooms.map((r) => {
                  const p = team.room_progress.find((p) => p.room_id === r.id);
                  return (
                    <td className="room-col" key={r.id}>
                      <span
                        className={`cell-progress ${p?.complete ? "complete" : ""}`}
                        role="img"
                        aria-label={`${r.name}: ${p?.solved || 0} of ${p?.total ?? r.total} challenges solved`}
                      >
                        <span>
                          {p?.solved || 0}
                          <small>/{p?.total ?? r.total}</small>
                        </span>
                        <i
                          style={{
                            transform: `scaleX(${progressFraction(p?.solved || 0, p?.total ?? r.total)})`,
                          }}
                        />
                      </span>
                    </td>
                  );
                })}
              {stage && room && (
                <td className="path-col">
                  <span
                    className={`stage-path-progress ${milestones.individual ? "individual" : "grouped"} ${progress?.complete ? "complete" : ""}`}
                    role="img"
                    aria-label={`${progress?.solved ?? 0} of ${progress?.total ?? room.total} challenges solved`}
                  >
                    {milestones.individual ? (
                      milestones.labels.map((value) => (
                        <i
                          key={value}
                          className={
                            (
                              progress?.solved_steps
                                ? progress.solved_steps.includes(value)
                                : value <= (progress?.solved ?? 0)
                            )
                              ? "filled"
                              : ""
                          }
                        />
                      ))
                    ) : (
                      <>
                        <i
                          className="stage-path-fill"
                          style={{
                            width: `${100 * progressFraction(progress?.solved ?? 0, progress?.total ?? room.total)}%`,
                          }}
                        />
                        {milestones.labels.slice(1, -1).map((value) => (
                          <i
                            key={value}
                            className="stage-path-guide"
                            style={{ left: `${(100 * value) / room.total}%` }}
                          />
                        ))}
                      </>
                    )}
                  </span>
                </td>
              )}
              <td className="score-col">
                {room ? progress?.solved || 0 : team.total}
                {room && <small>/{progress?.total ?? room.total}</small>}
              </td>
              {points && (
                <td
                  className="points-col"
                  title={`${(room ? progress?.gross_points : team.gross_points) ?? 0} earned − ${(room ? progress?.hint_costs : team.hint_costs) ?? 0} in hints`}
                >
                  {(room
                    ? (progress?.points ?? 0)
                    : (team.points ?? 0)
                  ).toLocaleString()}
                </td>
              )}
              {points && stage && room && (
                <td className="team-points-col">
                  {(team.points ?? 0).toLocaleString()}
                </td>
              )}
              <td className="time-col">
                {duration(
                  room
                    ? progress?.milestone_elapsed_ms
                    : team.overall_elapsed_ms,
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
function AwardRules({ data }: { data: Standings }) {
  return (
    <section className="award-rules">
      <h2>How the awards work</h2>
      <div>
        <article>
          <h3>Each room</h3>
          <p>
            {data.scoring_version === 2
              ? "Most challenges solved comes first, then room points minus that room’s hint costs, then active time to the last counted solve. A full clear leads every partial clear."
              : "Teams rank by the longest consecutively solved challenge path, then by active time to their last qualifying solve. A completed room leads every incomplete path."}
          </p>
          <p className="meta">
            Room time starts{" "}
            {data.event.room_clock === "event"
              ? "at the common event start"
              : "when your team starts the room"}
            .
          </p>
        </article>
        <article>
          <h3>Overall</h3>
          <p>
            {data.scoring_version === 2
              ? "Challenge points across all rooms, minus paid hints, come first. Active time to the last counted solve breaks ties. Simultaneous room clocks are never added together."
              : "Distinct challenge solves come first, then active time to the last qualifying solve. Simultaneous room clocks are never added together."}
          </p>
          <p className="meta">
            Overall time starts{" "}
            {data.event.overall_clock === "team"
              ? "at your team’s first room start"
              : "at the common event start"}
            .
          </p>
        </article>
        <article>
          <h3>Fair results</h3>
          <p>
            Organizer pauses are excluded. No qualifying solves means no prize
            eligibility. Genuine ties share the same rank. Published results are
            a fixed final snapshot.
          </p>
          <p>
            Only eligible contributions count. Banned accounts' contributions
            are excluded, and teammates can re-solve affected challenges.
            Unbanning restores eligibility of retained contributions without
            counting a challenge twice.
          </p>
        </article>
      </div>
    </section>
  );
}
export function Stage() {
  const result = useResource<Standings>("/standings", 8000),
    config = useResource<Projector>("/projector", 8000);
  const data = result.data;
  const [fullscreenError, setFullscreenError] = useState("");
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
  const view = config.data?.view || "overview";
  const room = data.rooms.find((r) => r.slug === config.data?.room_slug);
  const teams = orderedTeams(data.teams, view === "room" ? room : undefined);
  const split = Math.max(25, Math.ceil(teams.length / 2));
  return (
    <div className={`stage stage-${view}`}>
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
          <h1>
            {view === "awards"
              ? "The quest awards"
              : view === "room" && room
                ? `${room.name} standings`
                : view === "overall"
                  ? "Overall standings"
                  : "Every team. Every room."}
          </h1>
          <span>
            {view === "room" && room && data.scoring_version === 2 && (
              <>
                {room.available_points?.toLocaleString()} room points available
                ·{" "}
              </>
            )}
            {data.event.results_published
              ? "Final results"
              : data.event.status === "paused"
                ? "Event paused"
                : data.event.status === "live"
                  ? "Live team progress"
                  : data.event.status === "draft"
                    ? "Getting ready"
                    : "Event closed"}
          </span>
        </div>
      </header>
      {(result.error || config.error) && (
        <div className="stage-stale" role="status">
          Connection interrupted · Showing the last saved results
        </div>
      )}
      {!teams.length ? (
        <div className="stage-empty">
          <h2>The quest starts with your team</h2>
          <p>
            {data.event.status === "draft"
              ? "The organizers are preparing the rooms."
              : "Teams will appear here after the roster release."}
          </p>
          <div>
            {data.rooms.map((r) => (
              <span key={r.id}>{r.name}</span>
            ))}
          </div>
        </div>
      ) : view === "awards" ? (
        <Awards data={data} />
      ) : (
        <div className="stage-columns">
          {[teams.slice(0, split), teams.slice(split)].map((part, index) => (
            <div className="stage-panel" key={index}>
              <StandingTable
                data={data}
                teams={part}
                room={view === "room" ? room : undefined}
                stage
                overall={view === "overall"}
              />
            </div>
          ))}
        </div>
      )}
      <footer className="stage-footer">
        <span>
          {view === "room" && room
            ? data.scoring_version === 2
              ? "Progress → room points → active time · "
              : "Consecutive challenges solved · "
            : view === "overall"
              ? data.scoring_version === 2
                ? "Net points → active time · "
                : "Distinct challenges solved · "
              : view === "awards"
                ? "Prize standings · "
                : "Room progress: solved / total · "}
          {data.teams.length} teams · {data.rooms.length} rooms ·{" "}
          {data.event.results_published
            ? "Published results"
            : "Qualifying solves only · Ties share a rank"}
        </span>
        <span>
          Updated{" "}
          {new Intl.DateTimeFormat(undefined, {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
          }).format(new Date(data.as_of))}
        </span>
        <button
          onClick={async () => {
            try {
              if (document.fullscreenElement) await document.exitFullscreen();
              else await document.documentElement.requestFullscreen();
            } catch {
              setFullscreenError("Use your browser’s full screen command");
            }
          }}
        >
          Full screen
        </button>
        {fullscreenError && <span>{fullscreenError}</span>}
      </footer>
    </div>
  );
}
function Awards({ data }: { data: Standings }) {
  const overall = data.teams.filter((t) => t.rank === 1 && t.total > 0);
  return (
    <div className="stage-awards">
      <section className="overall-award">
        <span>Overall quest</span>
        <h2>
          {overall.length
            ? overall.map((t) => t.name).join(" · ")
            : "The first solve is still ahead"}
        </h2>
        <p>
          {overall.length
            ? `${data.scoring_version === 2 ? `${(overall[0].points ?? 0).toLocaleString()} net points` : `${overall[0].total} challenges solved`} · ${duration(overall[0].overall_elapsed_ms)}`
            : "Teams need a qualifying solve to enter the awards"}
        </p>
        {data.scoring_version === 2 && overall.length > 0 && (
          <p className="award-points">
            {overall[0].total} challenges solved · {overall[0].hint_costs ?? 0}{" "}
            points spent on hints
          </p>
        )}
        {overall.length > 1 && <span>Shared first place</span>}
      </section>
      <div className="room-awards">
        {data.rooms.map((room) => {
          const winners = data.teams.filter((team) => {
            const p = team.room_progress.find((p) => p.room_id === room.id);
            return p?.rank === 1 && p.solved > 0;
          });
          return (
            <section key={room.id}>
              <span>{room.name}</span>
              <h3>
                {winners.map((t) => t.name).join(" · ") ||
                  "Awaiting a qualifying solve"}
              </h3>
              {winners.length > 0 && (
                <p>
                  {
                    winners[0].room_progress.find((p) => p.room_id === room.id)
                      ?.solved
                  }{" "}
                  / {room.total} challenges solved ·{" "}
                  {duration(
                    winners[0].room_progress.find((p) => p.room_id === room.id)
                      ?.milestone_elapsed_ms,
                  )}
                </p>
              )}
              {data.scoring_version === 2 && winners.length > 0 && (
                <p className="award-points">
                  {(
                    winners[0].room_progress.find((p) => p.room_id === room.id)
                      ?.points ?? 0
                  ).toLocaleString()}{" "}
                  room points after hints
                </p>
              )}
              {winners.length > 1 && <small>Shared first place</small>}
            </section>
          );
        })}
      </div>
    </div>
  );
}
