"""Explicit price cutovers without rewriting native solves or hint receipts."""
revision = "quest_0013"
down_revision = "quest_0012"
branch_labels = None
depends_on = None


def upgrade(op):
    from CTFd.plugins.open_tech_quest.models import QuestPriceChange
    QuestPriceChange.__table__.create(bind=op.get_bind(), checkfirst=True)
