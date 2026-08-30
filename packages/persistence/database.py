"""Async SQLAlchemy engine and session construction."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_database_engine(database_url: str) -> AsyncEngine:
    """Create the process-wide async database engine.

    Parameters
    ----------
    database_url : str
        SQLAlchemy async database URL.

    Returns
    -------
    AsyncEngine
        Lazily connecting SQLAlchemy engine.
    """
    return create_async_engine(database_url, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create sessions with explicit transaction boundaries.

    Parameters
    ----------
    engine : AsyncEngine
        Database engine shared by the process.

    Returns
    -------
    async_sessionmaker[AsyncSession]
        Factory for non-expiring async sessions.
    """
    return async_sessionmaker(engine, expire_on_commit=False)
