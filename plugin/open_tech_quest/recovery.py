"""Reuse native grading/rate limits, replacing only excluded historical credit.

The native endpoint still processes every attempt. Its already_solved branch
grades the answer but cannot insert another Solves row because native team and
user uniqueness constraints retain the original. A transparent challenge-class
adapter captures that grade; the response hook records an additional native
submission only after repeating authoritative checks under the event lock.
"""
import json
import re
from flask import g, jsonify, request
from sqlalchemy.exc import IntegrityError
from CTFd.cache import clear_challenges, clear_standings
from CTFd.models import Challenges, Solves, Teams, Users, db
from CTFd.plugins.challenges import CHALLENGE_CLASSES, get_chal_class
from CTFd.utils import get_config
from CTFd.utils.dates import ctftime
from CTFd.utils.modes import generate_account_url
from CTFd.utils.user import get_ip
from . import lifecycle, onboarding
from .models import QuestAudit, QuestStart, QuestStep
from .recovery_models import QuestRecoverySolve
from .service import Projection, event, iso, now, participant_steps, projection, released, solve_map


def _result(status, message, code=200, success=True):
    payload = {"success": success, "data": {"status": status, "message": message}}
    if not success:
        payload["error"] = message
    response = jsonify(payload)
    response.status_code = code
    response.headers["Cache-Control"] = "private, no-store"
    return response


def _adapter(class_id):
    native = get_chal_class(class_id)
    if native.__dict__.get("quest_recovery_adapter", False):
        return

    class QuestChallenge(native):
        quest_recovery_adapter = True

        @classmethod
        def attempt(cls, challenge, req):
            result = native.attempt(challenge, req)
            pending = getattr(g, "quest_recovery", None)
            if pending and pending["challenge_id"] == challenge.id:
                if isinstance(result, tuple):
                    status, message = result
                    status = "correct" if status is True else "incorrect" if status is False else status
                else:
                    status, message = result.status, result.message
                g.quest_recovery_grade = (status, message)
            return result

    # CTFd's supported challenge registry remains authoritative; all read,
    # create, update, solve, fail and rate-limit methods delegate unchanged.
    CHALLENGE_CLASSES[class_id] = QuestChallenge


def prepare_attempt(user, challenge, e, solved):
    """Called only after the Quest native-attempt guard has authorized play."""
    if challenge.id in solved:
        return None
    original = Solves.query.filter_by(team_id=user.team_id, challenge_id=challenge.id).first()
    if original is None:
        return None
    contributor = Users.query.filter_by(id=original.user_id, banned=True).first()
    if contributor is None:
        return None
    data = request.get_json(silent=True) if request.is_json else request.form
    submission = data.get("submission") if hasattr(data, "get") else None
    if not isinstance(submission, str):
        return _result("incorrect", "A text submission is required", 400, False)
    _adapter(challenge.type)
    g.quest_recovery = {"user_id": user.id, "identity": lifecycle.identity(user),
                        "team_id": user.team_id, "challenge_id": challenge.id,
                        "room_id": QuestStep.query.filter_by(challenge_id=challenge.id).one().room_id,
                        "original_solve_id": original.id}
    from .maintenance import grading_revision
    g.quest_recovery["grading_revision"] = grading_revision(challenge)
    return None


def _fresh_attempt(pending):
    """Never trust users, grading eligibility or clocks across a native commit."""
    e = event(lock=True)
    user = lifecycle.current_user()
    if (not user or user.id != pending["user_id"]
            or not lifecycle.valid_identity(user, pending["identity"])):
        return None, _result("authentication_required", "Sign in again before submitting", 401, False)
    if not onboarding.eligible(user) or user.team_id != pending["team_id"]:
        return None, _result("authentication_required", "An active participant account on the same team is required", 403, False)
    team = Teams.query.filter_by(id=user.team_id, banned=False).first()
    if team is None:
        return None, _result("authentication_required", "An active team is required", 403, False)
    at = now()
    if e.status == "paused":
        return None, _result("paused", "The event is paused. Your answer was not submitted.", 409, False)
    from .room_pauses import active
    if active(pending["room_id"]):
        return None, _result("room_paused", "This room is paused. Your answer was not submitted.", 409, False)
    if (e.status != "live" or not e.start_at or at < e.start_at or not released(e)
            or not ctftime() or (e.end_at and at > e.end_at)):
        return None, _result("incorrect", "Submissions are accepted only while the event is live", 409, False)
    if get_config("verify_emails") and not user.verified:
        return None, _result("authentication_required", "Verify your email before submitting", 403, False)
    step = QuestStep.query.filter_by(challenge_id=pending["challenge_id"], room_id=pending["room_id"]).first()
    challenge = Challenges.query.filter_by(id=pending["challenge_id"], state="visible").first()
    if step is None or challenge is None:
        return None, _result("incorrect", "This challenge is not available", 404, False)
    start = QuestStart.query.filter_by(team_id=user.team_id, room_id=step.room_id).first()
    if start is None or at < start.started_at:
        return None, _result("incorrect", "Start the room before submitting", 403, False)
    solved = solve_map(e, team_id=user.team_id).get(user.team_id, {})
    prerequisites = {s.challenge_id for s in participant_steps(step.room_id) if s.position < step.position}
    if not set(solved) >= prerequisites:
        return None, _result("incorrect", "Solve the previous steps before submitting", 403, False)
    if challenge.id in solved:
        return None, _result("already_solved", "Your team already solved this challenge")
    original = Solves.query.filter_by(id=pending["original_solve_id"],
                                     team_id=user.team_id, challenge_id=challenge.id).first()
    contributor = Users.query.filter_by(id=original.user_id, banned=True).first() if original else None
    if contributor is None:
        return None, _result("incorrect", "Challenge credit changed. Refresh the room before submitting again.", 409, False)
    return (e, user, team, challenge, original, at), None


def _complete(response):
    pending = getattr(g, "quest_recovery", None)
    grade = getattr(g, "quest_recovery_grade", None)
    payload = response.get_json(silent=True) if response.is_json else None
    if (not pending or not grade or response.status_code >= 400 or not isinstance(payload, dict)
            or payload.get("data", {}).get("status") != "already_solved"):
        return response
    checked, rejection = _fresh_attempt(pending)
    if rejection is not None:
        db.session.rollback()
        return rejection
    e, user, team, challenge, original, at = checked
    from .maintenance import grading_revision
    if pending["grading_revision"] != grading_revision(challenge):
        db.session.rollback()
        return _result("incorrect", "Challenge answers or attempt limits changed. Refresh and submit again.", 409, False)
    status, message = grade
    native = get_chal_class(challenge.type)
    if status == "correct":
        data = request.get_json(silent=True) if request.is_json else request.form
        row = QuestRecoverySolve(user_id=user.id, credited_user_id=user.id,
                                 team_id=team.id, challenge_id=challenge.id,
                                 original_solve_id=original.id, date=at, credited_at=at,
                                 ip=get_ip(), provided=data["submission"].strip())
        db.session.add(row)
        db.session.add(QuestAudit(actor_id=user.id, action="challenge.recover",
                                 detail={"team_id": team.id, "challenge_id": challenge.id,
                                         "original_solve_id": original.id}))
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            if challenge.id in solve_map(team_id=team.id).get(team.id, {}):
                return _result("already_solved", "Your team already solved this challenge")
            return _result("incorrect", "Challenge credit changed. Refresh and try again.", 409, False)
    elif status == "incorrect":
        # The native already_solved branch does not save failures; do so using
        # its own implementation, preserving native timeout and attempt limits.
        native.fail(user=user, team=team, challenge=challenge, request=request)
    elif status == "partial":
        native.partial(user=user, team=team, challenge=challenge, request=request)
    else:
        db.session.rollback()
        return _result("incorrect", "The challenge did not accept this submission", 409, False)
    clear_standings()
    clear_challenges()
    return _result(status, message)


def _detail(response):
    match = re.fullmatch(r"/api/v1/challenges/(\d+)(?:/(solves|solution))?/?", request.path)
    if request.method != "GET" or match is None or response.status_code != 200 or not response.is_json:
        return response
    if lifecycle.administrator():
        return response
    user = lifecycle.current_user()
    if not user or not onboarding.eligible(user) or not user.team_id:
        return response
    challenge_id = int(match[1])
    if QuestStep.query.filter_by(challenge_id=challenge_id).first() is None:
        return response
    payload = response.get_json()
    if not payload.get("success"):
        return response
    view = projection(challenge_id=challenge_id)
    solved = view.solves.get(user.team_id, {})
    if match[2] == "solves":
        # Use the same earliest eligible credit as the room and prize views.
        teams = {t.id: t for t in Teams.query.filter_by(banned=False, hidden=False).all()}
        credits = [(tid, entries[challenge_id]) for tid, entries in view.solves.items()
                   if tid in teams and challenge_id in entries]
        payload["data"] = [{"account_id": tid, "name": teams[tid].name,
                            "date": iso(at), "account_url": generate_account_url(account_id=tid)}
                           for tid, at in sorted(credits, key=lambda item: (item[1], item[0]))]
    elif match[2] == "solution":
        challenge = Challenges.query.get(challenge_id)
        if challenge.solution and challenge.solution.state == "solved" and challenge_id not in solved:
            payload["data"]["id"] = None
    else:
        data = payload.get("data")
        if not isinstance(data, dict):
            return response
        data["solved_by_me"] = challenge_id in solved
        if data.get("solves") is not None:
            visible_teams = {tid for (tid,) in Teams.query.with_entities(Teams.id).filter_by(banned=False, hidden=False)}
            data["solves"] = sum(challenge_id in credits for tid, credits in view.solves.items() if tid in visible_teams)
        if data.get("solution_state") == "solved" and challenge_id not in solved:
            data["solution_id"] = None
    response.set_data(json.dumps(payload))
    response.headers["Cache-Control"] = "private, no-store"
    return response


def install_recovery(app):
    for class_id in list(CHALLENGE_CLASSES):
        _adapter(class_id)

    @app.after_request
    def recovery_response(response):
        return _detail(_complete(response))
