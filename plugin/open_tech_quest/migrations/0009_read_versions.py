"""Add transactional read-cache generations without changing event history."""
revision = "quest_0009"
down_revision = "quest_0008"
branch_labels = None
depends_on = None


def upgrade(op):
    from CTFd.plugins.open_tech_quest.models import QuestReadVersion
    QuestReadVersion.__table__.create(bind=op.get_bind(), checkfirst=True)
