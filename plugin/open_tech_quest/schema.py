"""Compatibility for CTFd's SQLite plugin runner, which only calls create_all."""
from importlib import import_module
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from CTFd.models import db


def upgrade_sqlite_attendance():
    if db.engine.dialect.name != "sqlite":
        return
    with db.engine.connect() as connection:
        with connection.begin():
            # Serialize introspection and ALTERs across starting workers.
            connection.execute(text("BEGIN IMMEDIATE"))
            operations = Operations(MigrationContext.configure(connection))
            for name in ("0005_attendance", "0008_attendance_first", "0010_points", "0011_catalog"):
                migration = import_module("CTFd.plugins.open_tech_quest.migrations." + name)
                migration.upgrade(operations)
