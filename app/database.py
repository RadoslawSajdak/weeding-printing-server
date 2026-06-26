"""Async SQLAlchemy engine and session factory for SQLite (aiosqlite)."""

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text
from .config import settings

engine = create_async_engine(settings.database_url, echo=False)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db() -> None:
    """Initialise the database: apply WAL mode, create tables, and run inline migrations.

    Called automatically on app startup via the FastAPI lifespan handler.
    """
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA journal_mode=WAL"))
        await conn.execute(text("PRAGMA synchronous=NORMAL"))
        await conn.run_sync(Base.metadata.create_all)
        # Migrate: add device_id column if the table pre-dates it
        columns = await conn.execute(text("PRAGMA table_info(print_jobs)"))
        col_names = {row[1] for row in columns.fetchall()}
        if "device_id" not in col_names:
            await conn.execute(text("ALTER TABLE print_jobs ADD COLUMN device_id VARCHAR(36)"))


async def get_db():
    """FastAPI dependency that yields an async database session per request."""
    async with AsyncSessionLocal() as session:
        yield session
