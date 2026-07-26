from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.settings import settings


class Database:
    """Singleton owning the process-wide async engine and session factory.

    The engine holds the connection pool, so exactly one instance must exist
    per process. Construction is cheap and lazy: no socket is opened until
    `connect()` runs (from the app lifespan).
    """

    _instance: "Database | None" = None

    def __new__(cls) -> "Database":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._engine = None
            cls._instance._sessionmaker = None
        return cls._instance

    _engine: AsyncEngine | None
    _sessionmaker: async_sessionmaker[AsyncSession] | None

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            raise RuntimeError("Database.connect() has not been called")
        return self._engine

    @property
    def sessionmaker(self) -> async_sessionmaker[AsyncSession]:
        if self._sessionmaker is None:
            raise RuntimeError("Database.connect() has not been called")
        return self._sessionmaker

    def connect(self) -> None:
        """Build the engine + session factory. Idempotent."""
        if self._engine is not None:
            return

        self._engine = create_async_engine(
            settings.DATABASE_URL,
            echo=settings.ENVIRONMENT == "development",
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            pool_recycle=1800,
        )
        self._sessionmaker = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

    async def disconnect(self) -> None:
        """Dispose the pool. Idempotent."""
        if self._engine is None:
            return

        await self._engine.dispose()
        self._engine = None
        self._sessionmaker = None

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Transactional scope: commit on success, rollback on failure."""
        async with self.sessionmaker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise


db = Database()


@asynccontextmanager
async def db_lifespan() -> AsyncIterator[None]:
    db.connect()
    try:
        yield
    finally:
        await db.disconnect()


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: `session: AsyncSession = Depends(get_db)`."""
    async with db.session() as session:
        yield session
