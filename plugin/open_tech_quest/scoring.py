"""Team hint purchases use the same fresh net balance as the Quest standings."""
import re
import hmac

from flask import jsonify, request, session
from CTFd.cache import clear_standings
from CTFd.models import Awards, Hints, HintUnlocks, db
from CTFd.schemas.unlocks import UnlockSchema
from CTFd.utils import get_config
from .models import QuestAudit, QuestHintPurchase, QuestRoom, QuestStep
from .service import now, projection


def purchase_hint(user, hint, e):
    """The native-route guard has locked the event and checked room access."""
    from .guards import denied
    from .onboarding import eligible
    nonce, provided = session.get("nonce"), request.headers.get("CSRF-Token")
    if not isinstance(nonce, str) or not isinstance(provided, str) or not hmac.compare_digest(nonce, provided):
        return denied("Refresh the page before purchasing a hint", 400)
    if not eligible(user) or not user.team_id:
        return denied("An active participant team is required")
    existing = HintUnlocks.query.filter_by(team_id=user.team_id, target=hint.id).first()
    if existing:
        if not QuestHintPurchase.query.filter_by(unlock_id=existing.id).first():
            return denied("This hint's purchase history needs organizer review", 409)
        return jsonify(success=True, data={"id": existing.id, "target": hint.id, "type": "hints"})
    at = now()
    if e.status != "live" or not e.start_at or at < e.start_at or (e.end_at and at > e.end_at):
        return denied("Hints can be purchased only while the event is live", 409)
    if get_config("verify_emails") and not user.verified:
        return denied("Verify your email before buying a hint")
    requirements = hint.prerequisites or []
    existing_ids = {hid for (hid,) in db.session.query(Hints.id).filter(Hints.id.in_(requirements))}
    unlocked_ids = {u.target for u in HintUnlocks.query.filter_by(team_id=user.team_id).all()}
    if not unlocked_ids >= existing_ids:
        return denied("Unlock the previous hints first")
    if type(hint.cost) is not int or hint.cost < 0:
        return denied("This hint has an invalid price; ask an organizer", 409)
    balance = projection(e, team_id=user.team_id).score(user.team_id)["points"]
    if hint.cost > 0 and hint.cost > balance:
        return denied(f"Your team has {balance} points; this hint costs {hint.cost}", 400)
    unlock = HintUnlocks(user_id=user.id, team_id=user.team_id, target=hint.id, date=at)
    award = Awards(user_id=user.id, team_id=user.team_id, name=hint.name,
                   description=hint.description, category=hint.category, value=-hint.cost, date=at)
    db.session.add_all([unlock, award])
    db.session.flush()
    db.session.add(QuestHintPurchase(team_id=user.team_id, challenge_id=hint.challenge_id,
                   hint_id=hint.id, unlock_id=unlock.id, award_id=award.id,
                   cost=hint.cost, purchased_at=at))
    db.session.add(QuestAudit(actor_id=user.id, action="hint.purchase", detail={
        "team_id": user.team_id, "challenge_id": hint.challenge_id,
        "hint_id": hint.id, "cost": hint.cost}))
    db.session.commit()
    clear_standings()
    return jsonify(success=True, data=UnlockSchema().dump(unlock).data)


def protect_native_score_write(path, data):
    """Keep prices and receipts intact when native administrator forms are used."""
    from .guards import denied
    from .service import event
    from .room_topology import activity_reason
    match = re.fullmatch(r"/api/v1/awards/(\d+)", path)
    if match:
        event(lock=True)
        if QuestHintPurchase.query.filter_by(award_id=int(match[1])).first():
            return denied("Native hint charges are preserved as team purchase history", 409)
    match = re.fullmatch(r"/api/v1/hints/(\d+)", path)
    if match:
        e = event(lock=True)
        hint = Hints.query.get(int(match[1]))
        if not hint:
            return None
        if request.method == "DELETE" and QuestHintPurchase.query.filter_by(hint_id=hint.id).first():
            return denied("Purchased hints and their charges must be preserved", 409)
        step = QuestStep.query.filter_by(challenge_id=hint.challenge_id).first()
        if "challenge_id" in data and str(data["challenge_id"]) != str(hint.challenge_id):
            target_step = QuestStep.query.filter_by(challenge_id=data["challenge_id"]).first() if str(data["challenge_id"]).isdigit() else None
            if (step or target_step) and (e.status != "draft" or
                    QuestHintPurchase.query.filter_by(hint_id=hint.id).first() or
                    (step and activity_reason(QuestRoom.query.get(step.room_id), [hint.challenge_id]))):
                return denied("Event hint ownership is frozen after competition activity", 409)
        if step and "cost" in data and str(data["cost"]) != str(hint.cost):
            if e.status != "draft" or activity_reason(QuestRoom.query.get(step.room_id), [hint.challenge_id]):
                return denied("Hint prices are frozen after competition activity", 409)
    return None
