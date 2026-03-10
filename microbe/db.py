"""
Microbe DB — Database session factory with auto-detection.

Defaults to SQLite for local dev (zero external services).
Override with DATABASE_URL env var for production (Postgres).
"""

import os
from typing import Optional

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

DEFAULT_SQLITE_URL = "sqlite+aiosqlite:///microbe.db"


def get_database_url(override: Optional[str] = None) -> str:
    """
    Resolve the database URL.

    Priority:
      1. Explicit override parameter
      2. DATABASE_URL environment variable
      3. Default SQLite file
    """
    if override:
        return override
    return os.getenv("DATABASE_URL", DEFAULT_SQLITE_URL)


def create_engine(url: Optional[str] = None) -> AsyncEngine:
    """Create an async SQLAlchemy engine."""
    db_url = get_database_url(url)

    # SQLite needs special settings for async
    if "sqlite" in db_url:
        return create_async_engine(
            db_url,
            echo=False,
            connect_args={"check_same_thread": False},
        )

    return create_async_engine(db_url, echo=False)


def create_session_factory(engine: AsyncEngine) -> sessionmaker:
    """Create an async session factory from an engine."""
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db(engine: AsyncEngine) -> None:
    """Create all tables. Safe to call multiple times (idempotent)."""
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
