"""Room-chain mutations staged under the caller's event lock, without commits."""
from sqlalchemy import or_
from CTFd.models import Challenges, Hints, Ratings, Solutions, Submissions, Unlocks, db
from . import demo
from .models import QuestCorrection, QuestStart, QuestStep, QuestChallengeSource
from .service import event, room_steps


def sync_native_chain(room_id):
    """Derive CTFd's own access rules from the non-hidden quest path.

    No commit or topology mutation: callers own the event lock/transaction.
    In particular, CTFd checks Requirements again after our participant guard.
    """
    rows = (db.session.query(QuestStep, Challenges)
            .join(Challenges, Challenges.id == QuestStep.challenge_id)
            .filter(QuestStep.room_id == room_id).order_by(QuestStep.position).all())
    active = [c.id for _, c in rows if c.state != "hidden"]
    prerequisites = []
    changed = False
    for _, challenge in rows:
        requirements = {"prerequisites": list(prerequisites), "anonymize": False}
        if challenge.state != "hidden":
            prerequisites.append(challenge.id)
        next_id = next((cid for cid in active if cid not in prerequisites), None)
        if challenge.requirements != requirements or challenge.next_id != next_id:
            challenge.requirements, challenge.next_id = requirements, next_id
            changed = True
    return changed


def _sync_visibility(session, flush_context, instances):
    # Native challenge update() commits itself. Derive before its flush so
    # visibility and Requirements become visible atomically to other workers.
    from sqlalchemy import inspect
    ids = [c.id for c in session.dirty if isinstance(c, Challenges)
           and inspect(c).attrs.state.history.has_changes()]
    if ids:
        room_ids = {s.room_id for s in QuestStep.query.filter(QuestStep.challenge_id.in_(ids)).all()}
        for room_id in room_ids:
            sync_native_chain(room_id)


def initialize_visibility():
    from sqlalchemy import event as sqlalchemy_event
    from CTFd.cache import clear_challenges
    if not sqlalchemy_event.contains(db.session, "before_flush", _sync_visibility):
        sqlalchemy_event.listen(db.session, "before_flush", _sync_visibility)
    # Backfill older installations with hidden steps, serialized with normal
    # challenge editing and event operations. Never change starts or solves.
    event(lock=True)
    changed = False
    for room_id, in db.session.query(QuestStep.room_id).distinct().all():
        changed = sync_native_chain(room_id) or changed
    db.session.commit()
    if changed:
        clear_challenges()


def activity_reason(room, challenge_ids=None):
    """Preserve room timing and all native challenge activity, including failures."""
    if QuestStart.query.filter_by(room_id=room.id).first():
        return "This room has recorded starts. Its challenge content and order are locked."
    if QuestCorrection.query.filter_by(room_id=room.id).first():
        return "This room has recorded corrections. Its challenge content and order are locked."
    ids = list(challenge_ids) if challenge_ids is not None else [s.challenge_id for s in room_steps(room)]
    if not ids:
        return None
    if Submissions.query.filter(Submissions.challenge_id.in_(ids)).first():
        return "A challenge has recorded submissions. Changes are disabled to preserve activity history."
    hint_ids = db.session.query(Hints.id).filter(Hints.challenge_id.in_(ids))
    solution_ids = db.session.query(Solutions.id).filter(Solutions.challenge_id.in_(ids))
    if Unlocks.query.filter(or_(
        (Unlocks.type == "hints") & Unlocks.target.in_(hint_ids),
        (Unlocks.type == "solutions") & Unlocks.target.in_(solution_ids),
    )).first():
        return "A challenge has recorded unlocks. Changes are disabled to preserve activity history."
    if Ratings.query.filter(Ratings.challenge_id.in_(ids)).first():
        return "A challenge has recorded ratings. Changes are disabled to preserve activity history."
    return None


def topology_reason(room, e=None, challenge_ids=None):
    e = e if e is not None else event()
    if e.status != "draft":
        return "Challenge content and order are editable only while the event is in Draft."
    if demo.active_batch():
        return "An administrator must remove the loaded demo before real challenge authoring or ordering."
    ids = {s.challenge_id for s in room_steps(room)}
    if challenge_ids is not None:
        ids.update(challenge_ids)
    return activity_reason(room, ids)


def set_challenge_order(room, ids):
    """Validate and rebuild both native Requirements and QuestStep rows atomically.

    Call event(lock=True) before staging any changes. The caller owns the
    transaction so a failed challenge/flag write rolls back its mapping too.
    """
    from .api import fail

    if (not isinstance(ids, list) or any(type(i) is not int or i < 1 for i in ids)
            or len(ids) != len(set(ids))):
        fail("challenge_ids must be a list of unique challenge IDs")
    challenges = {c.id: c for c in Challenges.query.filter(Challenges.id.in_(ids)).all()}
    if set(challenges) != set(ids):
        fail("One or more challenges do not exist")
    if QuestStep.query.filter(QuestStep.room_id != room.id, QuestStep.challenge_id.in_(ids)).first():
        fail("A challenge belongs to another room", 409)
    reason = topology_reason(room, challenge_ids=ids)
    if reason:
        fail(reason, 409)
    for step in room_steps(room):
        challenge = Challenges.query.get(step.challenge_id)
        if challenge:
            challenge.requirements, challenge.next_id = None, None
        db.session.delete(step)
    # Delete old positions before inserting replacements (MariaDB unique key).
    db.session.flush()
    for position, challenge_id in enumerate(ids):
        challenge = challenges[challenge_id]
        challenge.requirements = {"prerequisites": ids[:position], "anonymize": False}
        challenge.next_id = ids[position + 1] if position + 1 < len(ids) else None
        challenge.position = position
        source = QuestChallengeSource.query.get(challenge_id)
        if source and source.room_id != room.id:
            source.room_id = room.id
        db.session.add(QuestStep(room_id=room.id, challenge_id=challenge_id, position=position))
    db.session.flush()
    sync_native_chain(room.id)

