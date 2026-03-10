"""
Microbe Queue — In-memory queue backend for local development.

Replaces Redis/Arq for zero-dependency local execution. Uses asyncio.Queue
under the hood so the orchestrator and workers can run in a single process.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class Job:
    """A queued unit of work."""

    function: str
    kwargs: Dict[str, Any] = field(default_factory=dict)
    job_id: Optional[str] = None


class InMemoryQueue:
    """
    Async in-memory queue that mimics the Arq Redis pool interface.

    Usage:
        queue = InMemoryQueue()
        await queue.enqueue_job("process_step", step_id="abc", task_id="123")
        job = await queue.dequeue()
    """

    def __init__(self):
        self._queue: asyncio.Queue[Job] = asyncio.Queue()
        self._job_counter = 0

    async def enqueue_job(
        self,
        function: str,
        *,
        _job_id: Optional[str] = None,
        **kwargs: Any,
    ) -> Job:
        """Enqueue a job. Mirrors Arq's ArqRedis.enqueue_job() signature."""
        self._job_counter += 1
        job = Job(
            function=function,
            kwargs=kwargs,
            job_id=_job_id or f"job_{self._job_counter}",
        )
        await self._queue.put(job)
        return job

    async def dequeue(self, timeout: float = 1.0) -> Optional[Job]:
        """
        Dequeue the next job, waiting up to `timeout` seconds.

        Returns None if the queue is empty after the timeout.
        """
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    @property
    def empty(self) -> bool:
        return self._queue.empty()

    @property
    def size(self) -> int:
        return self._queue.qsize()
