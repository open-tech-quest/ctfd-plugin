"""Explicit catalog identities and native file ownership metadata."""
revision = "quest_0011"
down_revision = "quest_0010"
branch_labels = None
depends_on = None


def upgrade(op):
    from CTFd.plugins.open_tech_quest.models import QuestChallengeSource, QuestAsset
    for model in (QuestChallengeSource, QuestAsset):
        model.__table__.create(bind=op.get_bind(), checkfirst=True)
