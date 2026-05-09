"""E2E tests for Platform Router: SubtaskController, SubtaskConfig, routing.

Tests router primitives (timeout, retry, parallel) and configuration validation.
"""

import asyncio

import pytest


class TestRouterE2E:
    """E2E router scenarios."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_subtask_config_validation(self):
        """SubtaskConfig rejects invalid values."""
        from agent_nexus.platform.router.subtask import SubtaskConfig

        with pytest.raises(ValueError, match="timeout_seconds"):
            SubtaskConfig(timeout_seconds=0.01)

        with pytest.raises(ValueError, match="max_retries"):
            SubtaskConfig(max_retries=-1)

        with pytest.raises(ValueError, match="max_parallel"):
            SubtaskConfig(max_parallel=0)

        # Valid config
        cfg = SubtaskConfig(timeout_seconds=30.0, max_retries=3, max_parallel=5)
        assert cfg.timeout_seconds == 30.0

    def test_run_with_timeout_success(self):
        """SubtaskController runs coroutine within timeout."""
        from agent_nexus.platform.router.subtask import SubtaskController

        ctrl = SubtaskController()
        result = self._run(ctrl.run_with_timeout(asyncio.sleep(0.01, "done"), timeout=5.0))
        assert result == "done"

    def test_run_with_timeout_exceeds(self):
        """SubtaskController raises TimeoutError when coroutine too slow."""
        from agent_nexus.platform.router.subtask import SubtaskController

        ctrl = SubtaskController()
        with pytest.raises(TimeoutError):
            self._run(ctrl.run_with_timeout(asyncio.sleep(10.0), timeout=0.05))

    def test_run_with_retry_succeeds_eventually(self):
        """SubtaskController retries on failure and succeeds."""
        from agent_nexus.platform.router.subtask import SubtaskController

        ctrl = SubtaskController()
        attempts = 0

        async def flaky():
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise RuntimeError("not yet")
            return "ok"

        result = self._run(ctrl.run_with_retry(flaky, max_retries=4))
        assert result == "ok"
        assert attempts == 3

    def test_run_parallel_limits_concurrency(self):
        """SubtaskController runs tasks in parallel with semaphore limit."""
        from agent_nexus.platform.router.subtask import SubtaskConfig, SubtaskController

        config = SubtaskConfig(max_parallel=2)
        ctrl = SubtaskController(config)
        peak = 0
        current = 0

        async def tracked(n):
            nonlocal peak, current
            current += 1
            peak = max(peak, current)
            await asyncio.sleep(0.05)
            current -= 1
            return n

        coros = [tracked(i) for i in range(5)]
        results = self._run(ctrl.run_parallel(coros))
        assert len(results) == 5
        assert peak <= 2
