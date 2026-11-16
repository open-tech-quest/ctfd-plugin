"""Optional display names stored and edited through CTFd's native user fields."""
from CTFd.cache import cache
from CTFd.models import Configs, UserFieldEntries, UserFields, db
from CTFd.utils import _get_config, get_config
from .models import QuestEvent

KEY = "quest_display_name_field_id"


def initialize():
    # A first-statement write serializes first startup on SQLite and MariaDB.
    # No event values change; native Configs.key itself has no unique constraint.
    with db.engine.begin() as connection:
        connection.execute(QuestEvent.__table__.update().where(QuestEvent.id == 1).values(id=QuestEvent.id))
        marker = connection.execute(Configs.__table__.select().where(Configs.key == KEY).order_by(Configs.id)).first()
        try:
            field_id = int(marker.value) if marker else None
        except (TypeError, ValueError):
            field_id = None
        field = connection.execute(UserFields.__table__.select().where(
            (UserFields.id == field_id) & (UserFields.type == "user"))).first() if field_id else None
        if field is None:
            result = connection.execute(UserFields.__table__.insert().values(
                type="user", name="Display name", field_type="text", required=False,
                public=False, editable=True,
                description="Optional name shown in Open Tech Quest. Your username remains your login; leaving this blank uses your username."))
            field_id = result.inserted_primary_key[0]
            if marker:
                connection.execute(Configs.__table__.update().where(Configs.id == marker.id).values(value=str(field_id)))
            else:
                connection.execute(Configs.__table__.insert().values(key=KEY, value=str(field_id)))
    cache.delete_memoized(_get_config, KEY)
    return field_id


def name(user):
    if user is None:
        return None
    try:
        field_id = int(get_config(KEY))
    except (TypeError, ValueError):
        return user.name
    value = (db.session.query(UserFieldEntries.value)
             .join(UserFields, UserFields.id == UserFieldEntries.field_id)
             .filter(UserFieldEntries.user_id == user.id, UserFieldEntries.field_id == field_id,
                     UserFields.type == "user").order_by(UserFieldEntries.id).limit(1).scalar())
    return value.strip() if isinstance(value, str) and value.strip() else user.name
