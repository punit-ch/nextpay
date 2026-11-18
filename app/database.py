from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import database_url

DATABASE_URL = database_url()

_is_sqlite = DATABASE_URL.startswith("sqlite")

engine = create_engine(
    DATABASE_URL,
    # `timeout` is SQLite's busy timeout: concurrent writers wait instead of
    # failing immediately with "database is locked".
    connect_args=(
        {"check_same_thread": False, "timeout": 15} if _is_sqlite else {}
    ),
)

if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_columns(bind=None) -> None:
    """Add columns introduced after a database was first created.

    ``Base.metadata.create_all`` creates missing TABLES but never adds a
    column to an existing table, so an older nextpay.db would fail with
    "no such column". Additive and idempotent; never drops or rewrites data.
    """
    bind = bind or engine
    inspector = inspect(bind)

    if "purchase_plans" not in inspector.get_table_names():
        return

    existing = {column["name"] for column in inspector.get_columns("purchase_plans")}

    if "access_token_hash" not in existing:
        with bind.begin() as connection:
            connection.execute(
                text("ALTER TABLE purchase_plans ADD COLUMN access_token_hash VARCHAR")
            )
