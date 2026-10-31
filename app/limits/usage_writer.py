"""Bounded asynchronous writer for the usage ledger."""

import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import UsageLedger

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UsageRecord:
    """Values needed to persist one request's usage."""

    team_id: str
    api_key_id: str | None
    requested_model: str
    served_provider: str
    served_model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    fallback_used: bool
    status: str


class UsageWriter:
    """Drain usage records into Postgres using a bounded background queue."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], maxsize: int) -> None:
        """Create a bounded queue and retain the async session factory."""
        self._sessions = session_factory
        self._queue: asyncio.Queue[UsageRecord | None] = asyncio.Queue(maxsize=maxsize)
        self._task: asyncio.Task[None] | None = None
        self.dropped_records = 0

    async def start(self) -> None:
        """Start the single background database writer task."""
        self._task = asyncio.create_task(self._run(), name="usage-ledger-writer")

    def enqueue(self, record: UsageRecord) -> bool:
        """Queue a record without waiting; count and report queue overflow."""
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            self.dropped_records += 1
            logger.error("usage_ledger_queue_full dropped_records=%s", self.dropped_records)
            return False
        return True

    async def close(self) -> None:
        """Drain queued records before stopping the worker."""
        if self._task is None:
            return
        await self._queue.put(None)
        await self._task
        self._task = None

    async def _run(self) -> None:
        """Persist queued rows until the shutdown sentinel arrives."""
        while True:
            record = await self._queue.get()
            try:
                if record is None:
                    return
                async with self._sessions() as session:
                    session.add(UsageLedger(**record.__dict__))
                    await session.commit()
            except Exception:
                logger.exception("usage_ledger_write_failed")
            finally:
                self._queue.task_done()
