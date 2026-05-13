"""Unit tests for agent_nexus.platform.router.subtask — SubtaskController, SubtaskConfig."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from agent_nexus.platform.router.subtask import SubtaskConfig, SubtaskController

# ---------------------------------------------------------------------------
# SubtaskConfig
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# SubtaskController — run_with_timeout
# ---------------------------------------------------------------------------


class TestRunWithTimeout:
    @pytest.mark.asyncio
    async def test_override_timeout(self):
        ctrl = SubtaskController(SubtaskConfig(timeout_seconds=10.0))
        with pytest.raises(TimeoutError):
            await ctrl.run_with_timeout(asyncio.sleep(10), timeout=0.05)

    @pytest.mark.asyncio
    async def test_propagates_exception(self):
        ctrl = SubtaskController()

        async def fail():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            await ctrl.run_with_timeout(fail())

    @pytest.mark.asyncio
    async def test_uses_config_default_timeout(self):
        """When no override passed, config default is used."""
        ctrl = SubtaskController(SubtaskConfig(timeout_seconds=0.1))
        with pytest.raises(TimeoutError):
            await ctrl.run_with_timeout(asyncio.sleep(10))

    @staticmethod
    def _immediate(value):
        async def _coro():
            return value

        return _coro()


# ---------------------------------------------------------------------------
# SubtaskController — run_with_retry
# ---------------------------------------------------------------------------


class TestRunWithRetry:
    @pytest.mark.asyncio
    async def test_succeeds_first_try(self):
        ctrl = SubtaskController(SubtaskConfig(max_retries=2))
        result = await ctrl.run_with_retry(lambda: self._immediate("yes"))
        assert result == "yes"

    @pytest.mark.asyncio
    async def test_override_max_retries(self):
        ctrl = SubtaskController(SubtaskConfig(max_retries=0))
        attempts = {"n": 0}

        async def factory():
            attempts["n"] += 1
            raise RuntimeError("fail")

        with pytest.raises(RuntimeError):
            await ctrl.run_with_retry(factory, max_retries=1)
        assert attempts["n"] == 2  # 1 original + 1 retry

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates_immediately(self):
        ctrl = SubtaskController(SubtaskConfig(max_retries=3))

        async def factory():
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await ctrl.run_with_retry(factory)

    @staticmethod
    def _immediate(value):
        async def _coro():
            return value

        return _coro()


# ---------------------------------------------------------------------------
# SubtaskController — run_parallel
# ---------------------------------------------------------------------------


class TestRunParallel:
    @pytest.mark.asyncio
    async def test_empty_list_returns_empty(self):
        ctrl = SubtaskController()
        result = await ctrl.run_parallel([])
        assert result == []

    @pytest.mark.asyncio
    async def test_all_succeed(self):
        ctrl = SubtaskController(SubtaskConfig(max_parallel=3))

        async def val(i):
            return i

        coros = [val(i) for i in range(4)]
        results = await ctrl.run_parallel(coros)
        assert results == [0, 1, 2, 3]

    @pytest.mark.asyncio
    async def test_mixed_success_and_failure(self):
        ctrl = SubtaskController(SubtaskConfig(max_parallel=3))

        async def ok():
            return "good"

        async def bad():
            raise ValueError("oops")

        results = await ctrl.run_parallel([ok(), bad(), ok()])
        assert results[0] == "good"
        assert isinstance(results[1], ValueError)
        # results[2] may be "good" or RuntimeError depending on
        # whether it started before bad() set the failed flag.
        assert results[2] == "good" or isinstance(results[2], RuntimeError)

    @pytest.mark.asyncio
    async def test_unstarted_tasks_cancelled_on_failure(self):
        """When max_parallel=1, tasks waiting in queue are cancelled after failure."""
        ctrl = SubtaskController(SubtaskConfig(max_parallel=1))

        async def fail():
            raise RuntimeError("boom")

        async def slow():
            await asyncio.sleep(10)
            return "should not reach"

        results = await ctrl.run_parallel([fail(), slow()])
        assert isinstance(results[0], RuntimeError)
        # slow() never started because fail() set the failed flag first
        assert isinstance(results[1], RuntimeError)
        assert "cancelled" in str(results[1]).lower()

    @pytest.mark.asyncio
    async def test_running_tasks_not_cancelled_on_failure(self):
        """Already-running tasks continue to completion even after another fails."""
        ctrl = SubtaskController(SubtaskConfig(max_parallel=3))
        started = asyncio.Event()
        allow_finish = asyncio.Event()

        async def blocker():
            started.set()
            await allow_finish.wait()
            return "completed"

        async def fail():
            raise RuntimeError("boom")

        async def ok():
            return "good"

        results_future = asyncio.ensure_future(ctrl.run_parallel([blocker(), fail(), ok()]))
        # Wait for blocker to start
        await started.wait()
        # fail() may or may not have run yet; give it a chance
        await asyncio.sleep(0.05)
        # Let blocker finish
        allow_finish.set()
        results = await results_future
        # blocker() was already running when fail() happened
        assert results[0] == "completed"

    @pytest.mark.asyncio
    async def test_results_order_matches_input(self):
        ctrl = SubtaskController()

        async def delayed(val, delay):
            await asyncio.sleep(delay)
            return val

        coros = [delayed(i, 0.05 * (3 - i)) for i in range(4)]
        results = await ctrl.run_parallel(coros)
        assert results == [0, 1, 2, 3]

    # iter97 regression — skipped coroutines must be .close()d
    @pytest.mark.asyncio
    async def test_skipped_coroutines_do_not_emit_runtime_warning(self):
        """Skipped coroutines (due to earlier failure) are properly closed,
        so no 'coroutine was never awaited' RuntimeWarning is emitted."""
        import warnings

        ctrl = SubtaskController(SubtaskConfig(max_parallel=1))

        async def fail():
            raise RuntimeError("boom")

        async def never_started():
            return "should not run"

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            results = await ctrl.run_parallel([fail(), never_started()])
            runtime_warnings = [
                w
                for w in caught
                if issubclass(w.category, RuntimeWarning) and "never awaited" in str(w.message)
            ]
        assert len(runtime_warnings) == 0, (
            f"Expected no 'never awaited' warnings, got: {runtime_warnings}"
        )
        assert isinstance(results[0], RuntimeError)
        assert isinstance(results[1], RuntimeError)

    # iter121 regression — max_parallel=0 must not deadlock (Semaphore(0) guard)
    @pytest.mark.asyncio
    async def test_max_parallel_zero_rejected(self):
        """max_parallel=0 raises ValueError in SubtaskConfig.__post_init__."""
        with pytest.raises(ValueError, match="max_parallel"):
            SubtaskController(SubtaskConfig(max_parallel=0))


# ---------------------------------------------------------------------------
# iter122 regression: SubtaskConfig __post_init__ validation
# ---------------------------------------------------------------------------


class TestSubtaskConfigValidation:
    """SubtaskConfig rejects invalid values via __post_init__."""

    def test_timeout_seconds_tiny_raises(self):
        with pytest.raises(ValueError, match="timeout_seconds must be >= 0.1"):
            SubtaskConfig(timeout_seconds=0.05)

    def test_timeout_seconds_at_boundary_accepted(self):
        cfg = SubtaskConfig(timeout_seconds=0.1)
        assert cfg.timeout_seconds == 0.1

    def test_max_retries_zero_accepted(self):
        cfg = SubtaskConfig(max_retries=0)
        assert cfg.max_retries == 0

    def test_max_parallel_one_accepted(self):
        cfg = SubtaskConfig(max_parallel=1)
        assert cfg.max_parallel == 1


# ---------------------------------------------------------------------------
# iter110d: SystemExit / GeneratorExit propagate immediately, not retried
# ---------------------------------------------------------------------------


class TestSubtaskSystemExit:
    """SystemExit and GeneratorExit propagate immediately in run_with_retry."""

    @pytest.mark.asyncio
    async def test_run_with_retry_propagates_system_exit(self) -> None:
        """SystemExit in run_with_retry is propagated immediately."""
        ctrl = SubtaskController(SubtaskConfig(max_retries=3))
        attempts = {"n": 0}

        async def raise_system_exit():
            attempts["n"] += 1
            raise SystemExit(42)

        async def _bypass_wait_for(coro, timeout=None):
            return await coro

        with patch.object(ctrl, "run_with_timeout", _bypass_wait_for):
            with pytest.raises(SystemExit):
                await ctrl.run_with_retry(raise_system_exit)

        assert attempts["n"] == 1  # no retries
