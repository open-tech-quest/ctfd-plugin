import { useLayoutEffect, useRef, useState } from "react";
import { rankingDuration } from "./standings-presentation";
import { milestoneLayout, progressFraction } from "./stage-progress";
import type { StandingTeam, Standings } from "./types";
type StandingRoom = Standings["rooms"][number];
export function StandingTable({
  data,
  teams,
  room,
  stage = false,
  overall = false,
  ownTeamId,
}: {
  data: Standings;
  teams: StandingTeam[];
  room?: StandingRoom;
  stage?: boolean;
  overall?: boolean;
  ownTeamId?: number | null;
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
      className={`standing-table${stage ? " stage-table" : ""}${room ? " room-ranking" : ""}`}
      style={
        !stage && (room || overall)
          ? { minWidth: 560 }
          : !room && !overall
            ? {
                minWidth:
                  (stage ? 360 : 440) +
                  (points ? 88 : 0) +
                  data.rooms.length * (stage ? 48 : 100),
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
          {!room && points && (
            <>
              <th scope="col" className="points-col">
                Net points
              </th>
              <th scope="col" className="time-col">
                Active time
              </th>
            </>
          )}
          {!room &&
            !overall &&
            data.rooms.map((r) => (
              <th key={r.id} scope="col" className="room-col" title={r.name}>
                {r.name}
              </th>
            ))}
          {stage && room && (
            <th scope="col" className="path-col" ref={pathHeading}>
              <span>Challenge progress</span>
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
          {points && room && (
            <th scope="col" className="points-col">
              Room net pts
            </th>
          )}
          {(room || !points) && (
            <th scope="col" className="time-col">
              Active time
            </th>
          )}
          {points && stage && room && (
            <th
              scope="col"
              className="team-points-col"
              title="Overall score; not used for the room rank"
            >
              Team net pts
            </th>
          )}
        </tr>
      </thead>
      <tbody>
        {teams.map((team) => {
          const progress = room
            ? team.room_progress.find((p) => p.room_id === room.id)
            : undefined;
          const rank = room ? progress?.rank : team.rank;
          const score = room ? progress : team;
          const pointCell = points && (
            <td
              className="points-col"
              title={
                team.id === ownTeamId
                  ? `${score?.gross_points ?? 0} earned − ${score?.hint_costs ?? 0} in hints`
                  : undefined
              }
            >
              {(score?.points ?? 0).toLocaleString()}
              {!stage && team.id === ownTeamId && (
                <small className="hint-cost-breakdown">
                  {(score?.hint_costs ?? 0).toLocaleString()} in hints
                </small>
              )}
            </td>
          );
          const timeCell = (
            <td className="time-col">
              {rankingDuration(
                room ? progress?.milestone_elapsed_ms : team.overall_elapsed_ms,
              )}
            </td>
          );
          const solvedCell = (
            <td className="score-col">
              {room ? progress?.solved || 0 : team.total}
              {room && <small>/{progress?.total ?? room.total}</small>}
            </td>
          );
          return (
            <tr
              key={team.id}
              className={team.id === ownTeamId ? "own-team-row" : undefined}
              aria-current={team.id === ownTeamId ? "true" : undefined}
            >
              <td className="rank-col">{rank ?? "—"}</td>
              <th scope="row" title={team.name}>
                {team.name}
                {team.id === ownTeamId && (
                  <span className="your-team-label">Your team</span>
                )}
              </th>
              {!room && points && (
                <>
                  {pointCell}
                  {timeCell}
                </>
              )}
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
              {room ? (
                <>
                  {solvedCell}
                  {pointCell}
                  {timeCell}
                </>
              ) : (
                <>
                  {solvedCell}
                  {!points && timeCell}
                </>
              )}
              {points && stage && room && (
                <td
                  className="team-points-col"
                  title="Overall net points across all rooms; not a room ranking measure"
                >
                  {(team.points ?? 0).toLocaleString()}
                </td>
              )}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
