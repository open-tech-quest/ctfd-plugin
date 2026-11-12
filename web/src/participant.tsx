import { useEffect, useRef, useState, type FormEvent } from "react";
import { ApiError, dateTime, decode, duration, fileUrl } from "./api";
import type { Bootstrap, Challenge, Room, Step, Score } from "./types";
import { useResource } from "./hooks";
import {
  Arrow,
  Check,
  ConfirmDialog,
  Empty,
  Link,
  Markdown,
  Notice,
} from "./ui";
import { RoomArt, RoomLogo, roomThemeStyle } from "./room-art";
import { ParticipantOnboarding } from "./onboarding";
import { useParticipantMode } from "./participant-mode";
import { purchaseQuotedHint } from "./hint-purchase";
import { hintProgression, redeemedHintIds } from "./hint-progression";
import {
  participantPlaceholderContext,
  type PlaceholderContext,
} from "./placeholders";
import { ChallengePath } from "./challenge-path";
import { challengePoints, hintCostAdjustment } from "./score-presentation";
import { roomHintReadable, roomIsPaused, roomPlayEnabled } from "./room-pause";
export function EventNotice({ data }: { data: Bootstrap }) {
  const mode = useParticipantMode();
  if (mode.preview || mode.inspect) return null;
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
  const mode = useParticipantMode();
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
                {solved} / {total} challenges solved
                {" · Paid hints are deducted"}
              </p>
              <ProgressLine solved={solved} total={total} />
            </>
          )}
        </div>
      </section>
      {!mode.preview && <ParticipantOnboarding viewer={data.viewer} />}
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
            href={mode.room(room.slug)}
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
              {roomIsPaused(room.pause, mode.preview || mode.inspect) && (
                <p className="room-pause-summary">
                  <strong>Room paused</strong>
                  {room.pause?.reason ? ` · ${room.pause.reason}` : ""}
                </p>
              )}
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
          {!mode.preview && (
            <Link href="/quest/standings">
              How the awards work <Arrow />
            </Link>
          )}
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
  const mode = useParticipantMode();
  const resource = useResource<Room>(
    `/rooms/${encodeURIComponent(slug)}`,
    7000,
    mode.request,
    !mode.preview && !mode.inspect,
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
  const preview = !!mode.inspect || (data.viewer.admin && !mode.preview);
  const teamScore =
    mode.preview || mode.inspect
      ? (data.rooms.find((candidate) => candidate.id === room.id)?.team_score ??
        room.team_score)
      : room.team_score;
  const bodyPlaceholders = participantPlaceholderContext(
    data.viewer,
    teamScore.points,
    mode.preview || preview,
  );
  if (mode.preview || mode.inspect)
    bodyPlaceholders["team.points"] = teamScore.points;
  const started = !!room.progress.started_at || preview;
  const roomPaused = roomIsPaused(room.pause, mode.preview || preview);
  const playEnabled = roomPlayEnabled(
    data.event.status,
    room.pause,
    mode.preview || preview,
  );
  const canStart =
    (mode.preview ||
      (data.viewer.role === "participant" &&
        !!data.viewer.team_id &&
        playEnabled)) &&
    room.startable &&
    room.progress.total > 0 &&
    !mode.stale;
  return (
    <>
      <nav className="room-tabs" aria-label="Rooms">
        {data.rooms.map((r) => (
          <Link
            className={r.slug === slug ? "active" : ""}
            aria-current={r.slug === slug ? "page" : undefined}
            key={r.id}
            href={mode.room(r.slug)}
          >
            {r.name}
            {r.progress.status === "complete" && <Check />}
          </Link>
        ))}
      </nav>
      <EventNotice data={data} />
      {roomPaused && (
        <Notice>
          <strong>{room.name} is paused.</strong> {room.pause?.reason} This
          room’s clock is stopped. Your progress and redeemed hints stay saved.
          {data.event.status === "live" && (
            <>
              {" "}
              You can continue in another room; overall event time keeps
              running.
            </>
          )}
        </Notice>
      )}
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
            {preview && !mode.inspect && (
              <p className="meta placeholder-example-note">
                Placeholder examples: team number{" "}
                {bodyPlaceholders["team.number"]}, team name{" "}
                {bodyPlaceholders["team.name"]}, user name{" "}
                {bodyPlaceholders["user.name"]}, team points{" "}
                {bodyPlaceholders["team.points"]}.
              </p>
            )}
            <Markdown
              placeholders={bodyPlaceholders}
              source={
                room.description ||
                "Work through the challenges in order. Every teammate shares the path."
              }
            />
          </div>
          <div className="room-facts">
            <>
              <span>
                Room{" "}
                <strong>{room.progress.points.toLocaleString()} pts</strong>
              </span>
              <span>
                Team <strong>{teamScore.points.toLocaleString()} pts</strong>
              </span>
              <span>
                Room hints{" "}
                <strong>
                  {room.progress.hint_costs.toLocaleString()} pts spent
                </strong>
              </span>
            </>
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
                      !mode.preview &&
                      !mode.inspect &&
                      room.progress.status === "active" &&
                      playEnabled
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
            {!room.startable && !roomPaused && (
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
                await mode.request(
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
              : roomPaused
                ? "Room paused"
                : mode.preview || data.event.status === "live"
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
              <Link className="button secondary" href={mode.home}>
                Choose another room <Arrow />
              </Link>
            </div>
          )}
          {step ? (
            <ChallengePanel
              key={step.id}
              step={step}
              enabled={!mode.stale && playEnabled}
              unavailableMessage={
                roomPaused
                  ? "This room is paused. New answers and hints are available when the organizers resume it."
                  : "New answers and hints are available while the event is live."
              }
              preview={preview}
              score={teamScore}
              placeholders={bodyPlaceholders}
              roomName={room.name}
              progressRevision={data.revisions.progress}
              contentRevision={data.revisions.content}
              usedHintCosts={room.progress.used_hint_costs}
              usedHintOriginalCosts={room.progress.used_hint_original_costs}
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
  unavailableMessage,
  preview,
  onSolved,
  score,
  onHintPurchased,
  roomName,
  progressRevision,
  contentRevision,
  usedHintCosts = {},
  usedHintOriginalCosts = {},
  placeholders,
}: {
  step: Step;
  enabled: boolean;
  unavailableMessage: string;
  preview: boolean;
  score: Score;
  roomName: string;
  progressRevision: string;
  contentRevision: string;
  usedHintCosts: Record<number, number>;
  usedHintOriginalCosts?: Record<number, number>;
  placeholders: PlaceholderContext;
  onHintPurchased: () => Promise<void>;
  onSolved: (message: string) => Promise<void>;
}) {
  const mode = useParticipantMode();
  const content = useResource<Challenge>(
    `/api/v1/challenges/${step.id}${preview ? "?preview=true" : ""}`,
    60000,
    mode.request,
    !mode.preview && !mode.inspect,
  );
  const [answer, setAnswer] = useState(""),
    [busy, setBusy] = useState(false),
    [feedback, setFeedback] = useState<{
      message: string;
      good: boolean;
    } | null>(null),
    [hints, setHints] = useState<Record<number, string>>({}),
    [hintBusy, setHintBusy] = useState<number | null>(null),
    [hintError, setHintError] = useState(""),
    [pendingHint, setPendingHint] = useState<
      | (Challenge["hints"][number] & {
          number: number;
          origin: HTMLButtonElement;
          quote_revision?: string;
        })
      | null
    >(null);
  const input = useRef<HTMLInputElement>(null);
  const hintSection = useRef<HTMLElement>(null);
  const hintStatus = useRef<HTMLParagraphElement>(null);
  const revealOrigin = useRef<HTMLButtonElement | null>(null);
  const focusedHint = useRef<HTMLButtonElement | null>(null);
  function hintFocusTarget() {
    return (
      hintSection.current?.querySelector<HTMLButtonElement>(
        "button[data-next-hint]:not(:disabled)",
      ) ??
      hintStatus.current ??
      hintSection.current
    );
  }
  useEffect(() => {
    const origin = revealOrigin.current ?? focusedHint.current;
    if (!origin || hintBusy !== null) return;
    revealOrigin.current = null;
    if (origin.isConnected) {
      if (document.activeElement !== origin) focusedHint.current = null;
      return;
    }
    focusedHint.current = null;
    if (
      document.activeElement === document.body ||
      document.activeElement === document.documentElement ||
      !document.activeElement?.isConnected
    )
      hintFocusTarget()?.focus({ preventScroll: true });
  }, [hints, content.data, hintBusy, usedHintCosts]);
  const previousRevision = useRef({
    progress: progressRevision,
    content: contentRevision,
  });
  useEffect(() => {
    if (
      previousRevision.current.progress !== progressRevision ||
      previousRevision.current.content !== contentRevision
    ) {
      previousRevision.current = {
        progress: progressRevision,
        content: contentRevision,
      };
      void content.refresh();
    }
  }, [progressRevision, contentRevision]);
  useEffect(() => {
    if (
      pendingHint &&
      hintBusy === null &&
      content.data?.hints.some(
        (hint) =>
          hint.id === pendingHint.id && typeof hint.content === "string",
      )
    )
      setPendingHint(null);
  }, [content.data, pendingHint, hintBusy]);
  async function openHint(
    hint: Challenge["hints"][number],
    number: number,
    origin: HTMLButtonElement,
  ) {
    setHintBusy(hint.id);
    setHintError("");
    try {
      const result = await mode.request<{
        content?: string;
        cost: number;
        quote_revision?: string;
      }>(`/api/v1/hints/${hint.id}${preview ? "?preview=true" : ""}`);
      if (typeof result.content === "string") {
        revealOrigin.current = origin;
        setHints((previous) => ({ ...previous, [hint.id]: result.content! }));
        if (!preview && !mode.preview) await onHintPurchased();
      } else if (preview) {
        throw new Error("This hint is not available in preview.");
      } else if (Object.hasOwn(usedHintCosts, hint.id)) {
        throw new Error(
          "Your team’s redeemed hint could not be loaded. Refresh before trying again.",
        );
      } else {
        setPendingHint({
          ...hint,
          cost: result.cost,
          quote_revision: result.quote_revision,
          number,
          origin,
        });
      }
    } catch (error) {
      setHintError((error as Error).message);
    } finally {
      setHintBusy(null);
    }
  }
  async function purchaseHint() {
    if (!pendingHint) return;
    if (!enabled) throw new Error(unavailableMessage);
    const hint = pendingHint;
    setHintBusy(hint.id);
    try {
      let result: { content?: string } | undefined;
      try {
        await purchaseQuotedHint(mode.request, hint, mode.preview);
      } catch (error) {
        // A teammate may have purchased it, or the acknowledgement was lost.
        // Read the durable unlock; never retry the purchase POST automatically.
        result = await mode.request<{ content?: string }>(
          `/api/v1/hints/${hint.id}`,
        );
        if (typeof result.content !== "string") throw error;
      }
      if (typeof result?.content !== "string")
        result = await mode.request<{ content?: string }>(
          `/api/v1/hints/${hint.id}`,
        );
      if (typeof result.content !== "string")
        throw new Error(
          "Your hint could not be loaded. Refresh before trying again.",
        );
      setHints((previous) => ({ ...previous, [hint.id]: result.content! }));
    } finally {
      setHintBusy(null);
      await onHintPurchased();
      await content.refresh();
    }
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!enabled || busy || !answer.trim()) return;
    setBusy(true);
    setFeedback(null);
    try {
      const result = await mode.request<{ status: string; message?: string }>(
        `/api/v1/challenges/attempt${preview ? "?preview=true" : ""}`,
        "POST",
        { challenge_id: step.id, submission: answer },
      );
      const good = ["correct", "already_solved"].includes(result.status);
      const messages: Record<string, string> = {
        correct: mode.inspect
          ? "Correct."
          : preview
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
  const hintBalance = score.points;
  const insufficientHintPoints =
    !!pendingHint && pendingHint.cost > 0 && pendingHint.cost > hintBalance;
  const challenge = content.data;
  const progression = hintProgression(
    challenge?.hints ?? [],
    redeemedHintIds(challenge?.hints ?? [], usedHintCosts, hints),
    preview,
  );
  return (
    <section className="challenge-panel">
      {preview && !mode.inspect && (
        <Notice>
          Organizer preview · Answers are checked and hints are shown without
          recording team solves or spending points
        </Notice>
      )}
      <div className="challenge-heading">
        <div>
          <span className="meta">
            {step.state === "solved" ? "Solved challenge" : "Current challenge"}
          </span>
          <h2>{challenge?.name || step.title}</h2>
          <span className="challenge-points">
            {challengePoints(step, challenge?.value).toLocaleString()} points
            {step.state === "solved" ? " earned" : ""}
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
      {pendingHint && (
        <ConfirmDialog
          label={`Reveal hint ${pendingHint.number}`}
          description={`This hint is shared with your team. Its cost is deducted once from your overall score and your ${roomName} room score.`}
          disabled={!enabled || insufficientHintPoints}
          onClose={() => setPendingHint(null)}
          onConfirm={purchaseHint}
          focusFallback={() =>
            pendingHint.origin.isConnected && !pendingHint.origin.disabled
              ? pendingHint.origin
              : hintFocusTarget()
          }
        >
          {pendingHint.title && (
            <p className="hint-dialog-title">{pendingHint.title}</p>
          )}
          <dl className="hint-purchase-summary">
            <div>
              <dt>Your team has</dt>
              <dd>{hintBalance.toLocaleString()} points</dd>
            </div>
            <div>
              <dt>Hint cost</dt>
              <dd>
                {pendingHint.cost === 0
                  ? "Free (0 points)"
                  : `${pendingHint.cost.toLocaleString()} points`}
              </dd>
            </div>
            <div>
              <dt>After revealing</dt>
              <dd>
                {insufficientHintPoints
                  ? "Not enough points"
                  : `${(hintBalance - pendingHint.cost).toLocaleString()} points`}
              </dd>
            </div>
          </dl>
          {insufficientHintPoints && (
            <Notice>
              Earn {(pendingHint.cost - hintBalance).toLocaleString()} more
              points before revealing this hint. Your balance cannot go below
              zero when buying hints.
            </Notice>
          )}
          {!enabled && <Notice>{unavailableMessage}</Notice>}
        </ConfirmDialog>
      )}
      {challenge && (
        <>
          <Markdown
            source={challenge.description}
            omitTitle={challenge.name}
            placeholders={placeholders}
          />
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
              {!enabled && <small>{unavailableMessage}</small>}
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
          <section
            className="hints"
            ref={hintSection}
            tabIndex={-1}
            aria-labelledby={`hint-section-${step.id}`}
          >
            <h3 id={`hint-section-${step.id}`}>Hints</h3>

            {progression.visible.map(({ hint, number }) => {
              const usedCost = usedHintCosts[hint.id];
              const revealed =
                hints[hint.id] !== undefined ||
                typeof hint.content === "string";
              const used = usedCost !== undefined || (mode.preview && revealed);
              const hintCost = usedCost ?? hint.cost;
              const costAdjustment = hintCostAdjustment(
                usedCost,
                usedHintOriginalCosts[hint.id],
              );
              return (
                <div
                  className={`hint${used ? " hint-used" : ""}`}
                  key={hint.id}
                >
                  <div className="hint-heading">
                    <h4 id={`hint-heading-${step.id}-${hint.id}`}>
                      Hint {number}
                      {hint.title ? ` · ${hint.title}` : ""}
                    </h4>
                    <span
                      className="hint-cost"
                      id={`hint-cost-${step.id}-${hint.id}`}
                    >
                      {costAdjustment
                        ? `${hintCost.toLocaleString()} points deducted`
                        : hintCost === 0
                          ? "Free"
                          : used
                            ? `${hintCost.toLocaleString()} points spent`
                            : `${hint.cost.toLocaleString()} points`}
                    </span>
                  </div>
                  {costAdjustment && (
                    <p className="hint-cost-adjustment">{costAdjustment}</p>
                  )}
                  {revealed ? (
                    <>
                      <p className="hint-status">
                        {preview && !mode.inspect
                          ? "Organizer preview"
                          : used
                            ? "Used by your team"
                            : "Revealed"}
                      </p>
                      <Markdown source={hints[hint.id] ?? hint.content ?? ""} />
                    </>
                  ) : (
                    <button
                      className="text-button"
                      aria-describedby={`hint-heading-${step.id}-${hint.id} hint-cost-${step.id}-${hint.id}`}
                      disabled={
                        hintBusy !== null || !roomHintReadable(enabled, used)
                      }
                      data-next-hint={
                        hint.id === progression.nextId ? "" : undefined
                      }
                      onFocus={(event) => {
                        if (hint.id === progression.nextId)
                          focusedHint.current = event.currentTarget;
                      }}
                      onBlur={(event) => {
                        if (
                          focusedHint.current === event.currentTarget &&
                          event.relatedTarget instanceof HTMLElement &&
                          event.relatedTarget !== document.body &&
                          event.relatedTarget !== document.documentElement
                        )
                          focusedHint.current = null;
                      }}
                      onClick={(event) =>
                        void openHint(hint, number, event.currentTarget)
                      }
                    >
                      {hintBusy === hint.id
                        ? "Loading hint…"
                        : preview
                          ? `Preview hint ${number}`
                          : used
                            ? `Show hint ${number}`
                            : `Reveal hint ${number}`}
                      <Arrow />
                    </button>
                  )}
                </div>
              );
            })}
            {progression.empty || (progression.complete && !preview) ? (
              <p ref={hintStatus} role="status" tabIndex={-1}>
                {progression.empty
                  ? "No hints available."
                  : "No more hints available."}
              </p>
            ) : null}
            {hintError && <Notice tone="error">{hintError}</Notice>}
          </section>
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
