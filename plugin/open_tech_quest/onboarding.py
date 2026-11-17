"""Attendance policy, deterministic roster formation and draft team management."""
import hashlib
import hmac
import json
from datetime import datetime
from flask import current_app
from CTFd.models import Awards, Configs, Ratings, Submissions, Teams, Unlocks, Users, db
from CTFd.utils.user import get_current_user
from .models import (QuestOnboarding, QuestReadiness, QuestTeamName, QuestStart,
                     QuestStep, QuestAudit, QuestCorrection)
from .onboarding_math import optimize
from .service import iso, now, released


class OnboardingError(Exception):
    def __init__(self, message, status=409):
        self.message, self.status = message, status


def initialize(e):
    if QuestOnboarding.query.get(1) is not None:
        return
    from .service import event
    e = event(lock=True)
    if QuestOnboarding.query.get(1) is not None:
        db.session.commit()
        return
    legacy = e.status != "draft"
    db.session.add(QuestOnboarding(id=1, phase="finalized" if legacy else "check_in", team_size=5,
                                 grandfathered=legacy, finalized_at=now() if legacy else None))
    if legacy:
        for team in Teams.query.all():
            db.session.add(QuestTeamName(team_id=team.id, name_locked=True))
    db.session.commit()


def settings():
    return QuestOnboarding.query.get(1)


ATTENDANCE_KEY = "quest_require_attendance"


def require_attendance():
    """Absent/invalid values retain attendance; never trust a worker-local cache."""
    row = Configs.query.filter_by(key=ATTENDANCE_KEY).order_by(Configs.id).first()
    return row is None or row.value != "false"


def eligible(user):
    from .roles import is_maintainer
    return user.type == "user" and not user.banned and not is_maintainer(user)


def is_locked(team_id):
    row = QuestTeamName.query.get(team_id)
    return bool(row and row.name_locked)


def lock_name(team_id, actor_id):
    row = QuestTeamName.query.get(team_id)
    if row is None:
        row = QuestTeamName(team_id=team_id)
        db.session.add(row)
    row.name_locked, row.claimed_by, row.claimed_at = True, actor_id, now()
    row.auto_named = False


def generated_name():
    names = {team.name.casefold() for team in Teams.query.all()}
    number = 1
    while f"Team {number}".casefold() in names:
        number += 1
    return f"Team {number}"


def new_team(**values):
    """Allocate native IDs within this round; caller holds the event write lock.

    DELETE does not rewind MariaDB's AUTO_INCREMENT. Explicit IDs let an empty
    new round start at 1 without implicit-commit DDL in the atomic reset. Every
    organizer creation path uses this allocator; native team creation is blocked.
    Caller adds/flushes each team before allocating the next in this transaction.
    """
    last = Teams.query.order_by(Teams.id.desc()).first()
    return Teams(id=last.id + 1 if last else 1, **values)


def generated_identity(team_id):
    row = QuestTeamName.query.get(team_id)
    if row is None:
        row = QuestTeamName(team_id=team_id)
        db.session.add(row)
    row.auto_named = True
    row.name_locked = not settings().allow_team_naming


def apply_name_policy():
    enabled = settings().allow_team_naming
    assigned = {tid for (tid,) in Users.query.with_entities(Users.team_id).filter(Users.team_id.isnot(None)).distinct().all()}
    for tid in assigned:
        row = QuestTeamName.query.get(tid)
        if row is None:
            row = QuestTeamName(team_id=tid, auto_named=False)
            db.session.add(row)
        if not enabled:
            row.name_locked = True
        elif row.auto_named and row.claimed_by is None and row.claimed_at is None:
            row.name_locked = False


def random_order(ids, roster_fingerprint):
    key = current_app.config["SECRET_KEY"]
    key = key.encode() if isinstance(key, str) else key
    return sorted(ids, key=lambda uid: hmac.new(key, f"quest-attendance:{roster_fingerprint}:{uid}".encode(), hashlib.sha256).digest())


def attendance_reason(e, state=None):
    state = state or settings()
    if not require_attendance():
        return "Attendance is not required for this event."
    if state.phase == "finalized":
        return late_admission_reason(e, state)
    if e.status != "draft":
        return "Attendance can open only before the competition starts."
    if state.phase not in ("check_in", "closed", "naming"):
        return "Attendance cannot reopen after team rosters are finalized."
    return None


def attendance(e, state=None):
    state = state or settings()
    reason = attendance_reason(e, state)
    return {"enabled": bool(state.attendance_open), "generation": state.attendance_generation,
            "opened_at": iso(state.attendance_opened_at), "closed_at": iso(state.attendance_closed_at),
            "can_open": reason is None, "reason": reason, "late_only": state.phase == "finalized"}


def late_admission_reason(e, state=None):
    state = state or settings()
    if state.phase != "finalized":
        return "Late admission is available only after rosters are finalized."
    if e.status not in ("draft", "live", "paused"):
        return "Late attendance and admission are unavailable after the event closes."
    if e.end_at and now() >= e.end_at:
        return "The event cutoff has passed; late attendance and admission are closed."
    return None


def late_admission(e):
    blocked = late_admission_reason(e)
    return {"allowed": blocked is None, "reason": blocked,
            "fingerprint": fingerprint(e) if blocked is None else None}


def can_confirm(user, e, state=None):
    state = state or settings()
    if not state.attendance_open or not eligible(user) or attendance_reason(e, state):
        return False
    if state.phase == "finalized":
        from .lifecycle import personal_activity
        return user.team_id is None and not personal_activity(user)
    return True


def change_attendance(e, enabled, source):
    state = settings()
    blocked = attendance_reason(e, state) if enabled else None
    if blocked:
        raise OnboardingError(blocked)
    if bool(state.attendance_open) == enabled:
        return
    previous = bool(state.attendance_open)
    state.attendance_open = enabled
    if enabled:
        state.attendance_generation += 1
        state.attendance_opened_at, state.attendance_closed_at = now(), None
    else:
        state.attendance_closed_at = now()
    actor = get_current_user()
    db.session.add(QuestAudit(actor_id=actor.id if actor else None, action="attendance.configure",
                             detail={"old": previous, "new": enabled, "generation": state.attendance_generation, "source": source}))


def participant(user, e):
    state = settings()
    required = require_attendance()
    ready = QuestReadiness.query.get(user.id)
    show = released(e)
    team = Teams.query.get(user.team_id) if user.team_id and show else None
    can_name = bool(eligible(user) and (ready or not required) and team and not team.banned and not team.hidden
                    and state.allow_team_naming and e.status in ("draft", "live", "paused"))
    roster = None
    if team:
        from .locations import location
        readiness = {r.user_id: r for r in QuestReadiness.query.filter(QuestReadiness.user_id.in_([u.id for u in team.members])).all()}
        roster = {"id": team.id, "name": team.name, "name_locked": not can_name, "location": location(team.id),
                  "members": [{"id": u.id, "name": u.name, "ready_at": iso(readiness[u.id].ready_at) if u.id in readiness else None} for u in sorted(team.members, key=lambda u: u.id)]}
    return {"phase": state.phase, "team_size": state.team_size, "allow_team_naming": bool(state.allow_team_naming),
            "require_attendance": required,
            "ready_at": iso(ready.ready_at) if ready else None, "late": bool(ready and ready.late),
            "roster_released": show, "team": roster,
            "attendance": attendance(e, state),
            "can_ready": bool(not ready and can_confirm(user, e, state)), "can_name": can_name}


def competing_teams():
    return [team for team in Teams.query.filter_by(banned=False, hidden=False).all()
            if any(eligible(user) for user in team.members)]


def finalization_reason(allow_late_queue=False):
    state = settings()
    required = require_attendance()
    if state.attendance_open:
        return "Close attendance capture before finalizing team rosters."
    ready = {r.user_id: r for r in QuestReadiness.query.all()}
    users = [u for u in Users.query.filter_by(type="user", banned=False).all() if eligible(u)]
    teams = {team.id: team for team in Teams.query.all()}
    if not any(user.team_id and (user.id in ready or not required) for user in users):
        return "At least one eligible assigned participant is required before finalization."
    for user in users:
        if required and user.id in ready and not user.team_id:
            row = ready[user.id]
            if (allow_late_queue and state.phase == "finalized" and state.finalized_at
                    and row.late and row.ready_at > state.finalized_at):
                from .lifecycle import personal_activity
                if not personal_activity(user):
                    continue
            return "Assign every ready late arrival before finalizing rosters."
        if required and user.team_id and user.id not in ready:
            return "Remove non-ready participants from provisional rosters before finalizing."
        if user.team_id and (teams[user.team_id].banned or teams[user.team_id].hidden):
            return "Participants must belong to visible, non-banned teams."
    for team in teams.values():
        from .roles import is_maintainer
        if any(user.type != "user" or is_maintainer(user) for user in team.members):
            return "Remove staff accounts from participant teams before finalizing."
        if len(team.members) > state.team_size:
            return "A team exceeds the configured capacity."
    return None


def start_reason(e):
    from .demo import active_batch
    if settings().attendance_open:
        return "Close attendance capture before starting the competition."
    if active_batch():
        return "Remove demo content before starting the competition; organizers can preview it while in draft."
    if QuestStep.query.first() is None:
        return "Configure at least one room challenge before starting the event."
    if settings().phase != "finalized":
        return "Finalize the team rosters before starting the competition."
    ready_ids = {row.user_id for row in QuestReadiness.query.all()}
    if not any(user.id in ready_ids or not require_attendance() for team in competing_teams() for user in team.members if eligible(user)):
        return "At least one eligible assigned team is required before starting the competition."
    if not settings().grandfathered:
        reason = finalization_reason(allow_late_queue=True)
        if reason:
            return reason
    if e.start_at and e.start_at > now():
        return "The configured start is still in the future."
    if e.end_at and e.end_at <= now():
        return "The configured end has already passed."
    return None


def summary(e):
    state = settings()
    ready = {r.user_id for r in QuestReadiness.query.all()}
    users = [u for u in Users.query.filter_by(type="user", banned=False).all() if eligible(u)]
    reason = start_reason(e) if e.status == "draft" else "The event has already started."
    return {"phase": state.phase, "team_size": state.team_size, "allow_team_naming": bool(state.allow_team_naming), "attendance": attendance(e, state),
            "require_attendance": require_attendance(), "eligible_count": len(users),
            "unassigned_count": sum(not u.team_id for u in users), "fingerprint": fingerprint(e),
            "finalization_reason": finalization_reason(),
            "ready_count": sum(u.id in ready for u in users),
            "pending_count": sum(u.id not in ready for u in users),
            "unassigned_ready_count": sum(u.id in ready and not u.team_id for u in users),
            "unnamed_count": 0,
            "can_start": reason is None, "start_reason": reason, "late_admission": late_admission(e)}


def activity_teams():
    ids = {row.team_id for row in QuestStart.query.all()}
    from .recovery_models import QuestRecoverySolve
    ids.update(row.team_id for row in QuestCorrection.query.all())
    ids.update(tid for tid, in db.session.query(Users.team_id).join(Ratings, Ratings.user_id == Users.id)
               .filter(Users.team_id.isnot(None)).distinct().all())
    for model in (Submissions, Awards, Unlocks, QuestRecoverySolve):
        ids.update(tid for tid, in model.query.with_entities(model.team_id).filter(model.team_id.isnot(None)).distinct().all())
    return ids


def fingerprint(e):
    from .models import QuestMaintainer
    from .lifecycle import identity
    state = settings()
    locked = {row.team_id for row in QuestTeamName.query.filter_by(name_locked=True).all()}
    from .registration import visibility
    payload = {"require_attendance": require_attendance(), "registration": visibility(), "phase": state.phase, "team_size": state.team_size, "allow_team_naming": bool(state.allow_team_naming),
               "attendance": [bool(state.attendance_open), state.attendance_generation], "event": [e.status, e.name, iso(e.start_at), iso(e.end_at), iso(e.rosters_release_at), e.room_clock, e.overall_clock],
               "users": [(u.id, identity(u), u.name, u.type, u.banned, u.team_id) for u in Users.query.order_by(Users.id).all()],
               "ready": [(r.user_id, iso(r.ready_at), r.late) for r in QuestReadiness.query.order_by(QuestReadiness.user_id).all()],
               "teams": [(t.id, t.name, t.banned, t.hidden, t.id in locked) for t in Teams.query.order_by(Teams.id).all()],
               "activity": sorted(activity_teams()),
               "maintainers": [row.user_id for row in QuestMaintainer.query.order_by(QuestMaintainer.user_id).all()],
               "name_provenance": [(row.team_id, row.auto_named, row.claimed_by, iso(row.claimed_at)) for row in QuestTeamName.query.order_by(QuestTeamName.team_id).all()]}
    key = current_app.config["SECRET_KEY"]
    key = key.encode() if isinstance(key, str) else key
    return hmac.new(key, json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(), hashlib.sha256).hexdigest()


def preview(e):
    state = settings()
    required = require_attendance()
    if e.status != "draft" or state.phase not in (("closed",) if required else ("check_in", "closed")) or state.applied_at is not None:
        raise OnboardingError("Close team preparation before previewing attendance optimization; it is unavailable after apply.")
    if not required:
        from .registration import status
        if status()["enabled"]:
            raise OnboardingError("Close public registration before previewing or applying registered-participant teams.")
    if state.attendance_open:
        raise OnboardingError("Close attendance capture before previewing or applying roster optimization.")
    ready = {r.user_id for r in QuestReadiness.query.all()}
    all_users = Users.query.order_by(Users.id).all()
    attendees = [u for u in all_users if eligible(u) and (u.id in ready or not required)]
    if not attendees:
        raise OnboardingError("No eligible participants are available for the selected formation policy.")
    teams = Teams.query.order_by(Teams.id).all()
    protected = activity_teams()
    for team in teams:
        if team.id in protected and any(not eligible(u) or (required and u.id not in ready) for u in team.members):
            raise OnboardingError(f"Team '{team.name}' has activity and absent or ineligible members. Resolve its attendance before optimizing; no history was changed.")
        if team.id in protected and (team.banned or team.hidden) and team.members:
            raise OnboardingError(f"Team '{team.name}' has activity but is hidden or banned. Resolve that team before optimizing.")
    candidates = [{"id": t.id, "name": t.name} for t in teams if not t.hidden and not t.banned]
    current_fingerprint = fingerprint(e)
    order = random_order([u.id for u in attendees], current_fingerprint)
    try:
        groups, warnings = optimize([u.id for u in attendees], {u.id: u.team_id for u in attendees}, candidates, state.team_size, protected, [t.name for t in teams], order=order)
    except ValueError as exc:
        raise OnboardingError(str(exc))
    moves = [{"user_id": uid, "from_team_id": next(u.team_id for u in attendees if u.id == uid), "to_group": index}
             for index, group in enumerate(groups) for uid in group["user_ids"]
             if group["team_id"] is None or next(u.team_id for u in attendees if u.id == uid) != group["team_id"]]
    return {"fingerprint": current_fingerprint, "groups": groups, "moves": moves,
            "unassigned_user_ids": [u.id for u in all_users if eligible(u) and u.id not in {a.id for a in attendees}], "warnings": warnings}


def competition_activity(user):
    """Naming provenance is identity history, not play, and does not prevent a move."""
    from .recovery_models import QuestRecoverySolve
    return any(model.query.filter_by(user_id=user.id).first()
               for model in (Submissions, Awards, Unlocks, Ratings, QuestRecoverySolve))
