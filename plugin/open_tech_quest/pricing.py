"""Price cohorts are defined by committed record IDs, never browser timestamps."""
from CTFd.models import Hints, Submissions, db
from .models import QuestHintPurchase, QuestPriceChange


def checked_scope(value):
    from .api import fail
    if (not isinstance(value, dict) or set(value) != {"challenge_existing", "hint_existing_ids"}
            or type(value["challenge_existing"]) is not bool
            or not isinstance(value["hint_existing_ids"], list)
            or len(value["hint_existing_ids"]) > 20
            or any(type(hid) is not int or hid <= 0 for hid in value["hint_existing_ids"])
            or len(set(value["hint_existing_ids"])) != len(value["hint_existing_ids"])):
        fail("Choose whether challenge points and each changed hint price also apply to existing activity.")
    return {"challenge_existing": value["challenge_existing"], "hint_existing_ids": sorted(value["hint_existing_ids"])}


def plan(challenge, changes, e):
    from .api import fail
    scope = changes.get("pricing")
    if challenge is None or e.status not in ("live", "paused"):
        if scope is not None:
            fail("Existing-activity price choices apply only to live or paused challenge maintenance.", 409)
        return []
    hints = {h.id: h for h in Hints.query.filter_by(challenge_id=challenge.id)}
    proposed = {h["id"]: h for h in changes.get("hints", []) if "id" in h}
    changed_hints = {hid for hid, hint in proposed.items() if hid in hints and hint["cost"] != hints[hid].cost}
    changed_value = "value" in changes and changes["value"] != challenge.value
    if scope is None:
        if changed_value or changed_hints:
            fail("Refresh the editor and explicitly choose whether new prices also adjust existing solves or hint purchases.", 409)
        return []
    selected = set(scope["hint_existing_ids"])
    if not selected <= (proposed.keys() & hints.keys()) or (scope["challenge_existing"] and "value" not in changes):
        fail("Price choices must refer to this challenge's supplied point value and existing hints.", 409)
    if not (changed_value or changed_hints or scope["challenge_existing"] or selected):
        fail("The price choices no longer apply to a changed price. Reload and review the current draft.", 409)
    result = []
    if changed_value or scope["challenge_existing"]:
        cutoff = db.session.query(db.func.max(Submissions.id)).filter_by(challenge_id=challenge.id).scalar() or 0
        result.append({"kind": "challenge", "challenge_id": challenge.id, "hint_id": None,
                       "before_value": challenge.value, "after_value": changes["value"],
                       "existing_through_id": cutoff, "apply_existing": scope["challenge_existing"]})
    for hid in sorted(changed_hints | selected):
        cutoff = db.session.query(db.func.max(QuestHintPurchase.id)).filter_by(hint_id=hid).scalar() or 0
        result.append({"kind": "hint", "challenge_id": challenge.id, "hint_id": hid,
                       "before_value": hints[hid].cost, "after_value": proposed[hid]["cost"],
                       "existing_through_id": cutoff, "apply_existing": hid in selected})
    return result


def record(changes):
    from .service import now
    at = now()
    rows = [QuestPriceChange(**change, changed_at=at) for change in changes]
    db.session.add_all(rows)
    return rows


def solve_value(current_value, submission_id, changes):
    for row in reversed(changes):
        if row["apply_existing"] or submission_id > row["existing_through_id"]:
            return row["after_value"]
    return changes[0]["before_value"] if changes else current_value


def hint_cost(original_cost, purchase_id, changes):
    for row in reversed(changes):
        if row["apply_existing"] and purchase_id <= row["existing_through_id"]:
            return row["after_value"]
    return original_cost
