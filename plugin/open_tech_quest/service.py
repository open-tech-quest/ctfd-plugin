"""Server-side projections of native solves and durable quest history."""
from datetime import datetime, timezone
from types import SimpleNamespace
from sqlalchemy import exists, or_
from CTFd.models import Awards, Challenges, Solves, Submissions, Teams, Unlocks, Users
from .models import (QuestEvent, QuestRoom, QuestStep, QuestStart, QuestPause,
                     QuestExclusion, QuestCorrection, QuestMaintainer, QuestHintPurchase)
from .ranking import active_ms, prefix, assign_point_ranks
from . import read_cache


def now():
    return datetime.utcnow()


def iso(value):
    return value.isoformat(timespec="milliseconds") + "Z" if value else None


def parse_time(value):
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("Timestamps must be ISO 8601 UTC strings")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Timestamps must specify a UTC offset")
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def event(lock=False):
    from flask import g
    reuse = not lock and read_cache.read_request()
    if reuse and hasattr(g, "quest_read_event"):
        return g.quest_read_event
    if lock:
        # Authentication can open a MariaDB REPEATABLE READ snapshot before
        # this lock is acquired. Every mutation calls this boundary before
        # staging changes: discard that read transaction so subsequent checks
        # see the population/history committed by the preceding lock holder.
        from CTFd.models import db
        db.session.rollback()
        read_cache.clear_request_reads()
        if db.engine.dialect.name == "sqlite":
            # SQLite ignores FOR UPDATE. Its writer transaction must start
            # before lifecycle/history reads so native mutations and demo
            # actions cannot interleave with a competition start or cleanup.
            from sqlalchemy import text
            db.session.execute(text("BEGIN IMMEDIATE"))
    query = QuestEvent.query.filter_by(id=1)
    result = query.populate_existing().with_for_update().one() if lock else (query.populate_existing().one() if reuse else query.one())
    if reuse:
        g.quest_read_event = result
    if lock:
        from .backup_maintenance import require_available
        require_available()
        from flask import abort, g, has_request_context, request
        if has_request_context() and getattr(g, "quest_administrator", False):
            from .lifecycle import administrator
            if not administrator():
                if request.blueprint in ("quest_api", "quest_authoring"):
                    from .api import fail
                    fail("Administrator access required", 403)
                abort(403, description="Administrator access required")
        if has_request_context() and hasattr(g, 'quest_native_round_epoch'):
            from .models import QuestAudit
            epoch = db.session.query(db.func.max(QuestAudit.id)).filter_by(action='event.new_round').scalar()
            if epoch != g.quest_native_round_epoch:
                abort(409, description='A new round started while this request was waiting. Refresh before trying again.')
    return result


def event_data(e=None):
    e = e or event()
    return {"name": e.name, "status": e.status, "start_at": iso(e.start_at),
            "end_at": iso(e.end_at), "paused_at": iso(e.paused_at),
            "rosters_release_at": iso(e.rosters_release_at), "room_clock": e.room_clock,
            "overall_clock": e.overall_clock, "server_now": iso(now()),
            "results_published": e.status == "published"}


def released(e=None):
    e = e or event()
    return e.rosters_release_at is None or now() >= e.rosters_release_at


def room_steps(room):
    return QuestStep.query.filter_by(room_id=room.id).order_by(QuestStep.position).all()


def participant_steps(room_id):
    """Hidden challenges are withdrawn; locked challenges remain gated steps."""
    return (QuestStep.query.join(Challenges, Challenges.id == QuestStep.challenge_id)
            .filter(QuestStep.room_id == room_id, Challenges.state != "hidden")
            .order_by(QuestStep.position).all())


def _stamp(value):
    # Internal inputs retain database microseconds; public timestamps keep the
    # existing millisecond wire format. Ranking must never round stored time.
    return value.isoformat(timespec="microseconds") + "Z" if value else None


def _metadata_inputs():
    rooms = QuestRoom.query.order_by(QuestRoom.position, QuestRoom.id).all()
    steps = QuestStep.query.order_by(QuestStep.position).all()
    mapped = [step.challenge_id for step in steps]
    challenges = Challenges.query.with_entities(Challenges.id, Challenges.name, Challenges.state, Challenges.value).filter(Challenges.id.in_(mapped)).all()
    return {"rooms": [{key: getattr(room, key) for key in ("id", "slug", "name", "subtitle", "description", "position", "theme")} for room in rooms],
            "steps": [{"room_id": step.room_id, "challenge_id": step.challenge_id, "position": step.position} for step in steps],
            "challenges": [{"id": cid, "name": name, "state": state, "value": value or 0} for cid, name, state, value in challenges]}


def _history_inputs(mapped, counted_ids, team_id, challenge_id):
    starts = QuestStart.query
    corrections = QuestCorrection.query
    if team_id is not None:
        starts = starts.filter_by(team_id=team_id)
        corrections = corrections.filter_by(team_id=team_id)
    if challenge_id is not None:
        room_id = mapped.get(challenge_id)
        starts = starts.filter_by(room_id=room_id)
        corrections = corrections.filter_by(room_id=room_id)
    from .recovery_models import QuestRecoverySolve
    contributions = []
    for model, timestamp in ((Solves, Solves.date), (QuestRecoverySolve, QuestRecoverySolve.credited_at)):
        query = (model.query.with_entities(model.team_id, model.challenge_id, timestamp)
                 .join(Users, Users.id == model.user_id)
                 .filter(Users.type == "user", Users.banned.isnot(True), model.team_id.isnot(None), model.challenge_id.in_(counted_ids),
                         ~model.user_id.in_(QuestMaintainer.query.with_entities(QuestMaintainer.user_id))))
        if team_id is not None:
            query = query.filter(model.team_id == team_id)
        if challenge_id is not None:
            query = query.filter(model.challenge_id == challenge_id)
        contributions.extend(query.all())
    purchases = QuestHintPurchase.query.filter(QuestHintPurchase.challenge_id.in_(counted_ids))
    if team_id is not None:
        purchases = purchases.filter_by(team_id=team_id)
    if challenge_id is not None:
        purchases = purchases.filter_by(challenge_id=challenge_id)
    return {"purchases": [[p.team_id, p.challenge_id, p.hint_id, p.cost, _stamp(p.purchased_at)] for p in purchases.all()],
            "starts": [[s.team_id, s.room_id, _stamp(s.started_at)] for s in starts.all()],
            "pauses": [[_stamp(p.started_at), _stamp(p.ended_at)] for p in QuestPause.query.order_by(QuestPause.started_at).all()],
            "corrections": [[c.team_id, c.room_id, c.adjustment_ms] for c in corrections.all()],
            "contributions": [[tid, cid, _stamp(at)] for tid, cid, at in contributions]}


class Projection:
    """One batched read per table, regardless of team/room count.

    No participant credentials or raw submitted flags are selected. Reuse one
    instance throughout a response so every view uses the same input history.
    """
    def __init__(self, e=None, team_id=None, challenge_id=None, challenge_overrides=None, at=None):
        self.event = e or event()
        self.at = at or now()
        generation = read_cache.versions(team_id) if read_cache.read_request() else {}
        metadata = read_cache.cached("metadata", "rooms", [generation.get("content")], _metadata_inputs)
        self.rooms = [SimpleNamespace(**row) for row in metadata["rooms"]]
        self.steps = {}
        self.mapped = {}
        for row in metadata["steps"]:
            step = SimpleNamespace(**row)
            self.steps.setdefault(step.room_id, []).append(step)
            self.mapped[step.challenge_id] = step.room_id
        self.challenges = {row["id"]: SimpleNamespace(**{**row, **(challenge_overrides or {}).get(row["id"], {})})
                           for row in metadata["challenges"]}
        self.participant_steps = {
            room_id: [s for s in steps if s.challenge_id in self.challenges
                      and self.challenges[s.challenge_id].state != "hidden"]
            for room_id, steps in self.steps.items()}
        counted_ids = [s.challenge_id for steps in self.participant_steps.values() for s in steps]
        history_tokens = [generation.get(k) for k in ("event", "content", "roster", "history")]
        history_tokens.append(generation.get("progress" if team_id is None else "team"))
        history = read_cache.cached("history", [team_id, challenge_id, challenge_overrides] if challenge_overrides else [team_id, challenge_id], history_tokens,
                                   lambda: _history_inputs(self.mapped, counted_ids, team_id, challenge_id))
        self.starts = {(tid, rid): parse_time(at) for tid, rid, at in history["starts"]}
        self.pauses = [(parse_time(left), parse_time(right)) for left, right in history["pauses"]]
        self.corrections = {}
        for tid, rid, adjustment in history["corrections"]:
            key = tid, rid
            self.corrections[key] = self.corrections.get(key, 0) + adjustment
        self.solves, self.hint_costs, self.used_hints = {}, {}, {}
        if not self.event.start_at:
            return
        e = self.event
        # Only raw eligible contributions are shared. Time filtering runs on
        # every read, so future/imported dates, open pauses and cutoffs never
        # freeze until a cache expires or another write happens.
        contributions = [(tid, cid, parse_time(at)) for tid, cid, at in history["contributions"]]
        for team_id, challenge_id, at in contributions:
            room_id = self.mapped[challenge_id]
            start = self.starts.get((team_id, room_id))
            if not start or not at or at > self.at or at < max(start, e.start_at) or (e.end_at and at > e.end_at):
                continue
            if any(left <= at < (right or self.at) for left, right in self.pauses):
                continue
            solves = self.solves.setdefault(team_id, {})
            solves[challenge_id] = min(at, solves.get(challenge_id, at))
        for tid, cid, hint_id, cost, stamp in history["purchases"]:
            at = parse_time(stamp)
            start = self.starts.get((tid, self.mapped[cid]))
            if not start or not at or at < max(start, e.start_at) or at > self.at or (e.end_at and at > e.end_at):
                continue
            if any(left <= at < (right or self.at) for left, right in self.pauses):
                continue
            costs = self.hint_costs.setdefault(tid, {})
            costs[cid] = costs.get(cid, 0) + cost
            self.used_hints.setdefault(tid, {}).setdefault(cid, {})[hint_id] = cost

    def score(self, team_id, ids=None):
        ids = set(self.mapped if ids is None else ids)
        solves = self.solves.get(team_id, {})
        gross = sum(self.challenges[cid].value for cid in ids if cid in solves)
        costs = sum(cost for cid, cost in self.hint_costs.get(team_id, {}).items() if cid in ids)
        return {"gross_points": gross, "hint_costs": costs, "points": gross - costs}

    def progress(self, room, team_id, solves=None):
        e = self.event
        ids = [s.challenge_id for s in self.participant_steps.get(room.id, [])]
        solves = solves if solves is not None else self.solves.get(team_id, {})
        milestones = prefix(ids, solves)
        start = self.starts.get((team_id, room.id))
        origin = e.start_at if e.room_clock == "event" else start
        milestone = max(milestones) if milestones else None
        elapsed = active_ms(origin, milestone, self.pauses)
        if elapsed is not None:
            elapsed = max(0, elapsed + self.corrections.get((team_id, room.id), 0))
        complete = bool(ids) and len(milestones) == len(ids)
        clock_to = milestone if complete else min(self.at, e.end_at or self.at)
        counted = [solves[cid] for cid in ids if cid in solves]
        score_time = active_ms(origin, max(counted) if counted else None, self.pauses)
        if score_time is not None:
            score_time = max(0, score_time + self.corrections.get((team_id, room.id), 0))
        used_costs = {hid: cost for cid in ids
                      for hid, cost in self.used_hints.get(team_id, {}).get(cid, {}).items()}
        return {**self.score(team_id, ids), "available_points": sum(self.challenges[cid].value for cid in ids),
                "used_hint_ids": sorted(used_costs), "used_hint_costs": dict(sorted(used_costs.items())),
                "solved_steps": [index + 1 for index, cid in enumerate(ids) if cid in solves],
                "credited_solved": len(counted), "score_elapsed_ms": score_time,
                "solved": len(milestones), "total": len(ids), "started_at": iso(start),
                "elapsed_ms": active_ms(origin, clock_to, self.pauses) if start else None,
                "milestone_elapsed_ms": elapsed,
                "status": "complete" if complete else ("active" if start else "not_started")}

    def room(self, room, team_id=None, admin=False):
        e = self.event
        solves = self.solves.get(team_id, {})
        p = self.progress(room, team_id, solves)
        participant_steps = self.participant_steps.get(room.id, [])
        steps = self.steps.get(room.id, []) if admin else participant_steps
        enabled = bool(p["started_at"]) and e.status in ("live", "paused", "closed", "published")
        prefix_open, data = True, []
        for position, step in enumerate(steps):
            challenge = self.challenges.get(step.challenge_id)
            if challenge is None:
                continue
            solved = step.challenge_id in solves
            visible = challenge.state == "visible"
            if not admin and (not prefix_open or not visible):
                state = "locked"
            else:
                state = "solved" if solved else ("available" if admin or (enabled and visible) else "locked")
            data.append({"id": step.challenge_id, "title": challenge.name, "position": step.position if admin else position,
                         "state": state, "value": challenge.value, "solved_at": iso(solves.get(step.challenge_id))})
            prefix_open = prefix_open and solved
        result = {"id": room.id, "slug": room.slug, "name": room.name, "subtitle": room.subtitle,
                  "description": room.description, "position": room.position, "theme": room.theme,
                  "steps": data, "progress": p, "team_score": self.score(team_id),
                  "startable": bool(participant_steps) and self.challenges[participant_steps[0].challenge_id].state == "visible"}
        if admin:
            result["challenge_ids"] = [s.challenge_id for s in steps]
        return result


def projection(e=None, team_id=None, challenge_id=None):
    """Reuse one projection per request; share versioned raw inputs for GETs.

    Writes always rebuild after their locking boundary. Shared inputs are
    invalidated transactionally; permissions and time are evaluated fresh.
    Scope 0 means no participant team; None deliberately means all teams.
    """
    from flask import g, has_request_context, request
    if not has_request_context() or request.method != "GET":
        return Projection(e, team_id=team_id, challenge_id=challenge_id)
    key = (e.id if e is not None else 1, team_id, challenge_id)
    if not hasattr(g, "quest_projections"):
        g.quest_projections = {}
    if key not in g.quest_projections:
        g.quest_projections[key] = Projection(e, team_id=team_id, challenge_id=challenge_id)
    return g.quest_projections[key]


def solve_map(e=None, team_id=None):
    return projection(e, team_id=team_id).solves


def progress(room, team_id, solves=None, e=None):
    return projection(e, team_id=team_id or 0).progress(room, team_id, solves)


def room_data(room, team_id=None, admin=False, e=None):
    return projection(e, team_id=team_id or 0).room(room, team_id, admin)


def _standings_inputs():
    visible = Teams.query.with_entities(Teams.id, Teams.name).filter_by(banned=False, hidden=False).filter(or_(
        exists().where(Users.team_id == Teams.id),
        exists().where(QuestStart.team_id == Teams.id),
        exists().where(Submissions.team_id == Teams.id),
        exists().where(Awards.team_id == Teams.id),
        exists().where(Unlocks.team_id == Teams.id),
    )).all()
    return {"teams": [{"id": tid, "name": name} for tid, name in visible],
            "excluded": [row.team_id for row in QuestExclusion.query.all()]}


def standings(e=None, public=False):
    e = e or event()
    if e.status == "published" and e.snapshot:
        data = dict(e.snapshot)
        data.setdefault("scoring_version", 1)
        data["event"] = event_data(e)
        if public and not released(e):
            data["teams"], data["room_leaders"] = [], []
        return data
    view = projection(e)
    rooms = view.rooms
    generations = read_cache.versions() if read_cache.read_request() else {}
    roster = read_cache.cached("standings-roster", "all", [generations.get(k) for k in ("roster", "progress")], _standings_inputs)
    excluded = set(roster["excluded"])
    teams = []
    for team in (SimpleNamespace(**row) for row in roster["teams"]):
        if team.id in excluded:
            continue
        solves = view.solves.get(team.id, {})
        starts = [at for (tid, rid), at in view.starts.items() if tid == team.id]
        origin = e.start_at if e.overall_clock == "event" else min(starts, default=None)
        elapsed = active_ms(origin, max(solves.values()) if solves else None, view.pauses)
        # Corrections adjust room awards only; overall uses the event clock.
        rp = []
        for room in rooms:
            p = view.progress(room, team.id, solves)
            rp.append({"room_id": room.id, "solved": p["credited_solved"], "path_solved": p["solved"], "total": p["total"],
                       "solved_steps": p["solved_steps"],
                       "points": p["points"], "gross_points": p["gross_points"], "hint_costs": p["hint_costs"],
                       "milestone_elapsed_ms": p["score_elapsed_ms"], "rank": None,
                       "complete": p["status"] == "complete"})
        teams.append({**view.score(team.id), "id": team.id, "name": team.name, "total": len(solves), "overall_elapsed_ms": elapsed, "room_progress": rp})
    assign_point_ranks(teams, "overall_elapsed_ms")
    leaders = []
    by_id = {t["id"]: t for t in teams}
    for room in rooms:
        rows = []
        for team in teams:
            p = next(x for x in team["room_progress"] if x["room_id"] == room.id)
            rows.append(dict(p, id=team["id"], name=team["name"]))
        assign_point_ranks(rows, "milestone_elapsed_ms", room=True)
        for row in rows:
            next(p for p in by_id[row["id"]]["room_progress"] if p["room_id"] == room.id)["rank"] = row["rank"]
        leaders.append({"room_id": room.id, "room_slug": room.slug,
                        "teams": [row for row in rows if row["rank"] == 1]})
    if public and not released(e):
        teams, leaders = [], []
    return {"scoring_version": 2, "event": event_data(e), "rooms": [
                {"id": r.id, "slug": r.slug, "name": r.name,
                 "total": len(view.participant_steps.get(r.id, [])),
                 "steps": [{"id": step.challenge_id, "value": view.challenges[step.challenge_id].value}
                           for step in view.participant_steps.get(r.id, [])],
                 "available_points": sum(view.challenges[step.challenge_id].value for step in view.participant_steps.get(r.id, []))}
                for r in rooms],
            "teams": teams, "room_leaders": leaders, "as_of": iso(view.at)}
