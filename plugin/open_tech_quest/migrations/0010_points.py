"""Preserve the actual charge for native hints purchased before points scoring."""
revision = "quest_0010"
down_revision = "quest_0009"
branch_labels = None
depends_on = None


def upgrade(op):
    from sqlalchemy import select
    from CTFd.models import Awards, Hints, Unlocks
    from CTFd.plugins.open_tech_quest.models import QuestHintPurchase, QuestStep
    connection = op.get_bind()
    receipts = QuestHintPurchase.__table__
    receipts.create(bind=connection, checkfirst=True)
    known = list(connection.execute(select(receipts.c.unlock_id, receipts.c.award_id)))
    unlock_ids, used_awards = {r[0] for r in known}, {r[1] for r in known}
    unlocks = connection.execute(select(Unlocks.id, Unlocks.user_id, Unlocks.team_id,
                                       Unlocks.target, Unlocks.date, Hints.challenge_id)
                                .join(Hints, Hints.id == Unlocks.target)
                                .join(QuestStep, QuestStep.challenge_id == Hints.challenge_id)
                                .where(Unlocks.type == "hints", Unlocks.team_id.isnot(None)))
    awards = list(connection.execute(select(Awards.id, Awards.user_id, Awards.team_id,
                                            Awards.name, Awards.value, Awards.date)
                                     .where(Awards.category == "hints", Awards.value <= 0)))
    for uid, user_id, team_id, hint_id, at, challenge_id in unlocks:
        if uid in unlock_ids:
            continue
        matches = [a for a in awards if a.id not in used_awards and a.user_id == user_id
                   and a.team_id == team_id and a.name == f"Hint {hint_id}"
                   and at and a.date and abs((a.date - at).total_seconds()) <= 5]
        if len(matches) != 1:
            raise RuntimeError(f"Review native hint unlock {uid}: its original charge cannot be linked uniquely. No hint price was guessed.")
        award = matches[0]
        connection.execute(receipts.insert().values(team_id=team_id, challenge_id=challenge_id,
                           hint_id=hint_id, unlock_id=uid, award_id=award.id,
                           cost=-award.value, purchased_at=award.date))
        used_awards.add(award.id)
