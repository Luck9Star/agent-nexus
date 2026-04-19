"""SubtaskController -- timeout, retry, and parallel execution for subtasks.

Manages individual agent interactions within each workflow phase.
Provides composable primitives: run_with_timeout, run_with_retry, run_parallel.

Design decisions:
- run_parallel uses asyncio.Semaphore to limit concurrency to max_parallel.
- Failed parallel tasks return exceptions in the results list (don't raise).
  Caller decides how to handle failures.
- coro_factory pattern for retry: caller passes a callable that creates
  a fresh coroutine each attempt, since coroutines cannot be awaited twice.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable, Coroutine, Sequence

logger = logging.getLogger(__name__)


@dataclass
class SubtaskConfig:
    """Configuration for SubtaskController behavior."""

    timeout_seconds: float = 60.0
    max_retries: int = 2
    max_parallel: int = 3


class SubtaskController:
    """Execute subtasks with timeout, retry, and parallel support.

    Used by PlatformRouter to manage individual agent interactions
    within each workflow phase.
    """

    def __init__(self, config: SubtaskConfig | None = None) -> None:
        self._config = config or SubtaskConfig()

    async def run_with_timeout(
        self,
        coro: Coroutine[Any, Any, Any],
        timeout: float | None = None,
    ) -> Any:
        """Run a coroutine with timeout.

        Args:
            coro: The coroutine to execute.
            timeout: Override timeout in seconds. Falls back to config default.

        Returns:
            The coroutine's return value.

        Raises:
            TimeoutError: If the coroutine exceeds the timeout.
        """
        effective_timeout = timeout if timeout is not None else self._config.timeout_seconds
        return await asyncio.wait_for(coro, timeout=effective_timeout)

    async def run_with_retry(
        self,
        coro_factory: Callable[[], Coroutine[Any, Any, Any]],
        max_retries: int | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Run with retry on failure.

        Args:
            coro_factory: A callable that creates a fresh coroutine each attempt.
                This is necessary because coroutines cannot be awaited more than once.
            max_retries: Override max retry count. Falls back to config default.
            timeout: Override timeout per attempt. Falls back to config default.

        Returns:
            The coroutine's return value on the first successful attempt.

        Raises:
            The last exception if all attempts fail.
        """
        attempts = max_retries if max_retries is not None else self._config.max_retries
        last_exc: BaseException = RuntimeError("no attempts made")

        for attempt in range(attempts + 1):
            try:
                return await self.run_with_timeout(coro_factory(), timeout=timeout)
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)):
                    raise
                last_exc = exc
                logger.warning(
                    "Subtask attempt %d/%d failed: %s",
                    attempt + 1,
                    attempts + 1,
                    exc,
                )
                if attempt < attempts:
                    # Brief backoff before retry
                    await asyncio.sleep(0.1 * (attempt + 1))

        raise last_exc

    async def run_parallel(self, coros: Sequence[Coroutine[Any, Any, Any]]) -> list[Any]:
        """Run multiple coroutines in parallel with max_parallel concurrency.

        Uses asyncio.Semaphore to limit concurrency.
        Returns results in the same order as input coroutines.
        Failed tasks return exceptions (don't raise) -- caller decides.

        Note: tasks that have not yet started are skipped when an earlier
        task fails, to avoid unnecessary resource consumption.  Already-
        running tasks continue to completion since their partial results
        may still be valuable to the caller (e.g. parallel research phase
        in composite workflows).

        Args:
            coros: List of coroutines to execute.

        Returns:
            List of results or exceptions, one per input coroutine.
            Successful tasks return their value; failed tasks return the
            Exception instance.
        """
        if not coros:
            return []

        semaphore = asyncio.Semaphore(self._config.max_parallel)
        results: list[Any] = [None] * len(coros)
        failed = asyncio.Event()

        async def _guarded(index: int, coro: Coroutine[Any, Any, Any]) -> None:
            async with semaphore:
                # If another task already failed, skip this one to avoid
                # consuming IPC / subprocess resources for work that will
                # be discarded.  Already-running tasks are NOT cancelled
                # because their partial results may be useful.
                if failed.is_set():
                    coro.close()
                    results[index] = RuntimeError(
                        "cancelled: another parallel task failed"
                    )
                    return
                try:
                    results[index] = await coro
                except BaseException as exc:
                    if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)):
                        raise
                    results[index] = exc
                    failed.set()

        await asyncio.gather(
            *(_guarded(i, c) for i, c in enumerate(coros))
        )

        return results
