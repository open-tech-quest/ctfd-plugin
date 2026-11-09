import { useEffect, useRef, useState, type FormEvent } from "react";
import { ApiError, dateTime, decode, duration, fileUrl, request } from "./api";
import type { Bootstrap, Challenge, Room, Step, Score } from "./types";
import { useResource } from "./hooks";
import { Arrow, Check, Empty, Link, Markdown, Notice } from "./ui";
import { RoomArt, RoomLogo, roomThemeStyle } from "./room-art";
import { ParticipantOnboarding } from "./onboarding";
import { ChallengePath } from "./challenge-path";
export function EventNotice({ data }: { data: Bootstrap }) {
  const { event, viewer } = data;
  return (
    <>
      {event.status === "draft" && (
        <Notice>
          The quest is being prepared. Rooms will open when the organizers start
          the event.
        </Notice>
      )}
      {event.status === "paused" && (
        <Notice>
          The event is paused. Recorded pause time is excluded from your team’s
          clocks.
        </Notice>
      )}
      {event.status === "closed" && (
        <Notice>
          The event has closed. Your team’s results are saved; the organizers
          are checking the awards.
        </Notice>
      )}
      {event.status === "published" && (
        <Notice tone="success">
          The results are final.{" "}
          <Link href="/quest/standings">
            See the standings <Arrow />
          </Link>
        </Notice>
      )}
      {!viewer.authenticated ? (
        <Notice>
          <a href="/login?next=/quest">Sign in</a> with your own account to join
          your team’s progress.{" "}
          {data.registration?.enabled ? (
            <>
              New here? <a href="/register?next=/quest">Register</a>
            </>
          ) : (
            <>
              Public registration is closed. Existing participants can still
              sign in.
            </>
          )}
        </Notice>
      ) : !viewer.team_id && viewer.role === "participant" ? (
        <Notice>
          <Link href="/quest#your-team">
            Check in and see your team in the quest lobby
          </Link>
          . Your team appears after organizers form the checked-in teams and
          release the rosters.
          {event.rosters_release_at && (
            <> Roster release: {dateTime(event.rosters_release_at)}.</>
          )}
        </Notice>
      ) : null}
    </>
  );
}
export function Lobby({ data }: { data: Bootstrap }) {
  const solved = data.rooms.reduce((a, r) => a + r.progress.credited_solved, 0),
    total = data.rooms.reduce((a, r) => a + r.progress.total, 0);
  return (
    <>
      <section className="lobby-intro">
        <div>
          <p className="eyebrow">Red Hat Hands-On Day · Darmstadt</p>
          <h1>
            {data.rooms.length === 5
              ? "Five rooms."
              : data.rooms.length === 1
                ? "One room."
                : data.rooms.length === 0
                  ? "A quest in the making."
                  : `${data.rooms.length} rooms.`}
            <br />
            One team quest.
          </h1>
          <p className="lede">
            Pick a topic. Follow the challenges. Bring your team’s knowledge
            together.
          </p>
        </div>
        <div className="team-summary">
          <span className="meta">
            {data.viewer.team_name ? "Your team" : "How it works"}
          </span>
          <h2>{data.viewer.team_name || "Own account. Shared progress."}</h2>
          <p>
            {data.viewer.team_name
              ? "Teammates can work in different rooms at the same time. Every solve moves your team forward."
              : "Everyone signs in on their own device. Your team shares room starts, challenge progress and results."}
          </p>
          {data.viewer.team_name && (
            <>
              <div className="summary-value">
                {(data.rooms[0]?.team_score.points ?? 0).toLocaleString()}
                <span> team points</span>
              </div>
              <p className="meta">
                {solved} / {total} challenges solved · Paid hints are deducted
              </p>
              <ProgressLine solved={solved} total={total} />
            </>
          )}
        </div>
      </section>
      <ParticipantOnboarding viewer={data.viewer} />
      <EventNotice data={data} />
      {!!data.rooms.length && (
        <div className="section-heading">
          <h2>{data.rooms.length === 1 ? "Your room" : "Choose your room"}</h2>
          <span className="meta">
            Each room has its own ordered challenge path
          </span>
        </div>
      )}
      {!data.rooms.length && (
        <div className="empty">
          <h2>The rooms are being prepared</h2>
          <p>
            The organizers will add the room paths here before the quest begins.
          </p>
        </div>
      )}
      <div className="room-grid">
        {data.rooms.map((room) => (
          <Link
            key={room.id}
            href={`/quest/rooms/${room.slug}`}
            className={`room-card room-card-${room.slug}`}
            style={roomThemeStyle(room)}
          >
            <RoomArt room={room} compact />
            <div className="room-card-content">
              <div className="room-card-heading">
                <RoomLogo room={room} />
                <h2>{room.name}</h2>
                <Arrow />
              </div>
              <p>{room.subtitle || "Explore the challenges with your team"}</p>
              <div className="room-card-progress">
                <span>
                  {room.progress.total
                    ? `${room.progress.credited_solved} of ${room.progress.total} solved`
                    : "Challenges being prepared"}
                </span>
                <span>
                  {room.progress.status === "complete"
                    ? "Complete"
                    : room.progress.status === "active"
                      ? "In progress"
                      : "Not started"}
                </span>
              </div>
              <div className="room-card-points">
                <strong>{room.progress.points.toLocaleString()} pts</strong>
                <span>
                  {room.progress.available_points.toLocaleString()} available
                </span>
              </div>
              <ProgressLine
                solved={room.progress.credited_solved}
                total={room.progress.total}
              />
            </div>
          </Link>
        ))}
      </div>
      {!!data.rooms.length && (
        <div className="rules-strip">
          <h3>Before you start</h3>
          <p>
            Starting a room starts a shared team clock. It keeps running while
            you explore another room. The next step opens when anyone on your
            team solves the current challenge.
          </p>
          <Link href="/quest/standings">
            How the awards work <Arrow />
          </Link>
        </div>
      )}
    </>
  );
}
export function ProgressLine({
  solved,
  total,
}: {
  solved: number;
  total: number;
}) {
  return (
    <div
      className="progress-line"
      role="progressbar"
      aria-valuenow={solved}
      aria-valuemin={0}
      aria-valuemax={total || 1}
      aria-label={`${solved} of ${total} challenges solved`}
    >
      <span
        style={{
          transform: `scaleX(${total ? Math.min(1, solved / total) : 0})`,
        }}
      />
    </div>
  );
}
export function RoomPage({
  slug,
  data,
  onRefresh,
}: {
  slug: string;
  data: Bootstrap;
  onRefresh: () => Promise<void>;
}) {
  const resource = useResource<Room>(
    `/rooms/${encodeURIComponent(slug)}`,
    7000,
  );
  const [starting, setStarting] = useState(false),
    [startError, setStartError] = useState(""),
    [selected, setSelected] = useState<number | null>(null),
    [solveMessage, setSolveMessage] = useState(""),
    [progressMessage, setProgressMessage] = useState("");
  const previousProgress = useRef<{ roomId: number; solved: number } | null>(
    null,
  );
  const room = resource.data;
  useEffect(() => {
    const next = room
      ? { roomId: room.id, solved: room.progress.solved }
      : null;
    const previous = previousProgress.current;
    if (next && previous && next.roomId === previous.roomId) {
      if (next.solved < previous.solved) {
        setSolveMessage("");
        setProgressMessage(
          "Your team's counted progress changed. Check the updated challenge path.",
        );
      } else if (next.solved > previous.solved) setProgressMessage("");
    }
    previousProgress.current = next;
  }, [room?.id, room?.progress.solved]);
  const current = room?.steps.find((s) => s.state === "available");
  const step =
    room?.steps.find((s) => s.id === selected && s.state !== "locked") ||
    current;
  useEffect(() => {
    if (
      room &&
      selected !== null &&
      !room.steps.some((s) => s.id === selected && s.state !== "locked")
    ) {
      setSelected(null);
      setSolveMessage("");
    }
  }, [room, selected]);
  useEffect(() => {
    setSelected(null);
    setSolveMessage("");
    setProgressMessage("");
  }, [slug]);
  if (!room)
    return resource.error ? (
      <Notice tone="error" retry={() => void resource.refresh()}>
        {resource.error}
      </Notice>
    ) : (
      <div className="loading" role="status">
        Opening room…
      </div>
    );
  const preview = data.viewer.admin;
  const started = !!room.progress.started_at || preview;
  const canStart =
    data.viewer.role === "participant" &&
    !!data.viewer.team_id &&
    data.event.status === "live" &&
    room.startable &&
    room.progress.total > 0;
  return (
    <>
      <nav className="room-tabs" aria-label="Rooms">
        {data.rooms.map((r) => (
          <Link
            className={r.slug === slug ? "active" : ""}
            aria-current={r.slug === slug ? "page" : undefined}
            key={r.id}
            href={`/quest/rooms/${r.slug}`}
          >
            {r.name}
            {r.progress.status === "complete" && <Check />}
          </Link>
        ))}
      </nav>
      <EventNotice data={data} />
      {resource.error && (
        <Notice tone="error" retry={() => void resource.refresh()}>
          Showing progress saved{" "}
          {resource.updatedAt
            ? new Intl.DateTimeFormat(undefined, {
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
              }).format(resource.updatedAt)
            : "earlier"}
          . {resource.error}
        </Notice>
      )}
      <section className={`room-intro theme-${room.slug}`}>
        <div>
          <div className="room-name">
            <RoomLogo room={room} />
            <p className="eyebrow">{room.subtitle || "Your next topic"}</p>
          </div>
          <h1>The {room.name} room</h1>
          <div className="room-description">
            <Markdown
              source={
                room.description ||
                "Work through the challenges in order. Every teammate shares the path."
              }
            />
          </div>
          <div className="room-facts">
            <span>
              Room <strong>{room.progress.points.toLocaleString()} pts</strong>
            </span>
            <span>
              Team{" "}
              <strong>{room.team_score.points.toLocaleString()} pts</strong>
            </span>
            <strong>{data.viewer.team_name || "Shared team progress"}</strong>
            <span>
              {room.progress.credited_solved} of {room.progress.total} solved
            </span>
            {started && (
              <span>
                Room time{" "}
                <b className="numeric">
                  <RoomClock
                    elapsed={room.progress.elapsed_ms}
                    receivedAt={resource.updatedAt}
                    active={
                      room.progress.status === "active" &&
                      data.event.status === "live"
                    }
                  />
                </b>
              </span>
            )}
          </div>
        </div>
        <RoomArt room={room} />
      </section>
      {!room.steps.length ? (
        <Empty title="The room is taking shape">
          The organizers are preparing this room’s challenges. Check back when
          the event opens.
        </Empty>
      ) : !started ? (
        <section className="start-panel">
          <div>
            <h2>Start together, explore your way</h2>
            <p>
              Starting records one shared start for your team and makes the
              first challenge available. The clock continues across devices and
              rooms.
            </p>
            <p className="meta">
              {data.event.room_clock === "event"
                ? "This event ranks room time from the common event start."
                : "This event ranks room time from your team’s room start."}{" "}
              Organizer pauses are excluded.
            </p>
            {!room.startable && (
              <p role="status">
                The organizers have not opened this room’s first challenge yet.
                Your team’s timer will not start until it is available.
              </p>
            )}
          </div>
          <button
            className="button"
            disabled={!canStart || starting}
            onClick={async () => {
              setStarting(true);
              setStartError("");
              try {
                await request(
                  `/rooms/${encodeURIComponent(slug)}/start`,
                  "POST",
                  {},
                );
                await Promise.all([resource.refresh(), onRefresh()]);
              } catch (e) {
                setStartError((e as Error).message);
              } finally {
                setStarting(false);
              }
            }}
          >
            {starting
              ? "Starting room…"
              : data.event.status === "live"
                ? room.startable
                  ? "Start room"
                  : "Room not ready"
                : "Room not open"}
            <Arrow />
          </button>
          {startError && <Notice tone="error">{startError}</Notice>}
        </section>
      ) : (
        <>
          {solveMessage && <Notice tone="success">{solveMessage}</Notice>}
          {progressMessage && <Notice>{progressMessage}</Notice>}
          <ChallengePath
            key={`room-path-${room.id}`}
            steps={room.steps}
            selectedId={step?.id}
            onSelect={setSelected}
            style={roomThemeStyle(room)}
          />
          {room.progress.status === "complete" && (
            <div className="completion" role="status">
              <Check />
              <div>
                <h2>{room.name} complete</h2>
                <p>
                  Your team finished every challenge. Milestone time:{" "}
                  <span className="numeric">
                    {duration(room.progress.milestone_elapsed_ms)}
                  </span>
                </p>
              </div>
              <Link className="button secondary" href="/quest">
                Choose another room <Arrow />
              </Link>
            </div>
          )}
          {step ? (
            <ChallengePanel
              key={step.id}
              step={step}
              enabled={preview || data.event.status === "live"}
              preview={preview}
              score={room.team_score}
              onHintPurchased={async () => {
                await Promise.all([resource.refresh(), onRefresh()]);
              }}
              onSolved={async (message) => {
                setSolveMessage(message);
                setSelected(null);
                await Promise.all([resource.refresh(), onRefresh()]);
              }}
            />
          ) : room.progress.status !== "complete" ? (
            <Empty title="Waiting for your next step">
              Progress refreshes automatically. Your teammates may be working on
              the current challenge.
            </Empty>
          ) : null}
        </>
      )}
    </>
  );
}
function ChallengePanel({
  step,
  enabled,
  preview,
  onSolved,
  score,
  onHintPurchased,
}: {
  step: Step;
  enabled: boolean;
  preview: boolean;
  score: Score;
  onHintPurchased: () => Promise<void>;
  onSolved: (message: string) => Promise<void>;
}) {
  const content = useResource<Challenge>(
    `/api/v1/challenges/${step.id}${preview ? "?preview=true" : ""}`,
    60000,
  );
  const [answer, setAnswer] = useState(""),
    [busy, setBusy] = useState(false),
    [feedback, setFeedback] = useState<{
      message: string;
      good: boolean;
    } | null>(null),
    [hints, setHints] = useState<Record<number, string>>({}),
    [hintBusy, setHintBusy] = useState<number | null>(null),
    [hintError, setHintError] = useState("");
  const input = useRef<HTMLInputElement>(null);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || !answer.trim()) return;
    setBusy(true);
    setFeedback(null);
    try {
      const result = await request<{ status: string; message?: string }>(
        `/api/v1/challenges/attempt${preview ? "?preview=true" : ""}`,
        "POST",
        { challenge_id: step.id, submission: answer },
      );
      const good = ["correct", "already_solved"].includes(result.status);
      const messages: Record<string, string> = {
        correct: preview
          ? "Correct. Preview only; no solve was recorded."
          : "Correct. Your team’s progress is saved.",
        incorrect: "That answer isn’t correct. Check your work and try again.",
        already_solved:
          "This challenge already counts as solved for your team. Your shared progress is up to date.",
        ratelimited: "Too many attempts. Wait a moment before trying again.",
        paused:
          "The event is paused. You can submit when the organizers resume it.",
        authentication_required: "Your session has ended. Sign in to continue.",
        partial: "Partially correct. Keep working on the remaining part.",
      };
      setFeedback({
        good,
        message:
          messages[result.status] ||
          result.message ||
          "The server did not confirm this attempt. Refresh before trying again.",
      });
      await content.refresh();
      if (good && !preview) {
        setAnswer("");
        await onSolved(
          messages[result.status] ||
            result.message ||
            "Your team’s progress is up to date.",
        );
      }
    } catch (e) {
      setFeedback({ good: false, message: (e as Error).message });
      if (e instanceof ApiError && e.status === 401)
        setFeedback({
          good: false,
          message: "Your session has ended. Sign in to continue.",
        });
    } finally {
      setBusy(false);
    }
  }
  const challenge = content.data;
  return (
    <section className="challenge-panel">
      {preview && (
        <Notice>
          Organizer preview · Answers are checked without recording team solves
        </Notice>
      )}
      <div className="challenge-heading">
        <div>
          <span className="meta">
            {step.state === "solved" ? "Solved challenge" : "Current challenge"}
          </span>
          <h2>{challenge?.name || step.title}</h2>
          <span className="challenge-points">
            {(challenge?.value ?? step.value).toLocaleString()} points
          </span>
        </div>
        {step.state === "solved" && (
          <span className="solved-label">
            <Check /> Solved by your team
          </span>
        )}
      </div>
      {content.error && (
        <Notice tone="error" retry={() => void content.refresh()}>
          {content.error}
        </Notice>
      )}
      {!challenge && !content.error && <p role="status">Loading challenge…</p>}
      {!preview && (
        <p className="team-points-balance">
          <strong>Your team: {score.points.toLocaleString()} points</strong>
          <span>
            {score.gross_points.toLocaleString()} earned −{" "}
            {score.hint_costs.toLocaleString()} in hints
          </span>
        </p>
      )}
      {challenge && (
        <>
          <Markdown source={challenge.description} omitTitle={challenge.name} />
          {challenge.connection_info && (
            <div className="connection-info">
              <span className="meta">Connection</span>
              <pre>{challenge.connection_info}</pre>
            </div>
          )}
          {!!challenge.files?.length && (
            <div className="attachments">
              <h3>Challenge files</h3>
              {challenge.files.map((file, i) => {
                const url = fileUrl(file);
                return url ? (
                  <a className="attachment" key={file} href={url} download>
                    {decode(
                      file.split("?")[0].split("/").pop() || `File ${i + 1}`,
                    )}{" "}
                    <Arrow />
                  </a>
                ) : null;
              })}
            </div>
          )}
          {/* The Quest projection, rather than retained native solve rows, controls the form. */}
          {step.state !== "solved" && (
            <form className="answer-form" onSubmit={submit}>
              <label htmlFor={`answer-${step.id}`}>Your answer</label>
              <div className="answer-controls">
                <input
                  ref={input}
                  id={`answer-${step.id}`}
                  autoComplete="off"
                  spellCheck={false}
                  value={answer}
                  onChange={(e) => setAnswer(e.target.value)}
                  disabled={!enabled || busy}
                  placeholder="Enter the challenge answer"
                />
                <button
                  className="button"
                  disabled={!enabled || busy || !answer.trim()}
                >
                  {busy ? "Checking…" : "Check answer"}
                  <Arrow />
                </button>
              </div>
              {!enabled && (
                <small>Submissions are available while the event is live</small>
              )}
              {challenge.max_attempts ? (
                <small>
                  {challenge.attempts || 0} of {challenge.max_attempts} attempts
                  used
                </small>
              ) : null}
            </form>
          )}
          {feedback && (
            <Notice tone={feedback.good ? "success" : "error"}>
              {feedback.message}
              {feedback.message.includes("Sign in") && (
                <>
                  {" "}
                  <a href="/login?next=/quest">Sign in</a>
                </>
              )}
            </Notice>
          )}
          {!!challenge.hints?.length && (
            <section className="hints">
              <h3>Hints</h3>
              {challenge.hints.map((hint, i) => (
                <div className="hint" key={hint.id}>
                  {hints[hint.id] || hint.content ? (
                    <Markdown source={hints[hint.id] || hint.content || ""} />
                  ) : (
                    <button
                      className="text-button"
                      disabled={hintBusy !== null || !enabled}
                      onClick={async () => {
                        setHintBusy(hint.id);
                        setHintError("");
                        const hintPath = `/api/v1/hints/${hint.id}${preview ? "?preview=true" : ""}`;
                        try {
                          let result = await request<{ content?: string }>(
                            hintPath,
                          );
                          if (preview && typeof result.content !== "string")
                            throw new Error(
                              "This hint is not available in organizer preview.",
                            );
                          if (typeof result.content !== "string") {
                            if (
                              hint.cost > 0 &&
                              !window.confirm(
                                `Reveal hint ${i + 1} for ${hint.cost} points from your team? This is paid once and shared with every teammate.`,
                              )
                            )
                              return;
                            try {
                              await request("/api/v1/unlocks", "POST", {
                                target: hint.id,
                                type: "hints",
                              });
                            } catch (unlockError) {
                              const shared = await request<{
                                content?: string;
                              }>(hintPath);
                              if (typeof shared.content !== "string")
                                throw unlockError;
                              result = shared;
                            }
                            if (typeof result.content !== "string")
                              result = await request<{ content: string }>(
                                hintPath,
                              );
                          }
                          if (typeof result.content !== "string")
                            throw new Error("This hint is not available yet.");
                          setHints((previous) => ({
                            ...previous,
                            [hint.id]: result.content!,
                          }));
                          if (!preview) await onHintPurchased();
                        } catch (e) {
                          setHintError((e as Error).message);
                        } finally {
                          setHintBusy(null);
                        }
                      }}
                    >
                      {hintBusy === hint.id
                        ? "Loading hint…"
                        : preview
                          ? `Preview hint ${i + 1}`
                          : `Reveal hint ${i + 1}${hint.cost ? ` · ${hint.cost} points` : ""}`}
                      <Arrow />
                    </button>
                  )}
                </div>
              ))}
              {hintError && <Notice tone="error">{hintError}</Notice>}
            </section>
          )}
        </>
      )}
    </section>
  );
}

function RoomClock({
  elapsed,
  receivedAt,
  active,
}: {
  elapsed: number | null;
  receivedAt: number | null;
  active: boolean;
}) {
  const [display, setDisplay] = useState(elapsed);
  useEffect(() => {
    setDisplay(elapsed);
    if (!active || elapsed === null) return;
    const anchor = performance.now();
    const tick = window.setInterval(
      () => setDisplay(elapsed + performance.now() - anchor),
      1000,
    );
    return () => clearInterval(tick);
  }, [elapsed, receivedAt, active]);
  return <>{duration(display)}</>;
}
