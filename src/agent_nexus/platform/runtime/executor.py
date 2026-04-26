"""IPythonExecutor: in-process code execution via IPython InteractiveShell.

Lazy-initialized shell per executor instance with disabled history, automagic,
and colors.  Runs SecurityChecker before execution for fail-fast security.

Reference: cave-agent/src/cave_agent/runtime/executor.py
"""

from __future__ import annotations

import asyncio
import io
import logging
import sys
import threading
from typing import Any

from agent_nexus.models._common import _MISSING
from agent_nexus.models.runtime import ExecutionResult

from .security_checker import SecurityChecker

logger = logging.getLogger(__name__)

# Module-level lock to prevent sys.stdout cross-contamination between
# concurrent IPythonExecutor instances.  sys.stdout is process-global, so
# without this, two executors redirecting stdout simultaneously would capture
# each other's output.  Using threading.Lock (not asyncio.Lock) to avoid
# event-loop binding issues across test fixtures that create new loops.
#
# NOTE: Not currently used because threading.Lock blocks the event loop and
# asyncio.Lock fails across test event loops.  The per-instance _exec_lock
# prevents concurrent access within a single executor.  Cross-executor
# contamination only occurs when multiple executors share a process, which
# doesn't happen in production (each agent is a subprocess).
# _global_exec_lock = threading.Lock()

# User namespace keys that are IPython internals, not user variables
_IPYTHON_INTERNALS = frozenset({
    "In", "Out", "exit", "quit", "get_ipython",
    "_", "__", "___", "_ih", "_oh", "_sh", "_dh",
})


class IPythonExecutor:
    """Execute Python code in an IPython InteractiveShell.

    The shell is **lazily created** on first code execution to avoid
    the heavy cost of ``InteractiveShell.__init__()`` when only metadata
    operations (inject_variable, describe_*) are needed.

    Usage::

        executor = IPythonExecutor()
        result = await executor.execute("x = 1 + 2")
        print(result.success)  # True
        print(executor.get("x"))  # 3

    Important:
        Call ``close()`` when done to release the InteractiveShell.
        Each shell is ~50-200 MB due to IPython's internal state
        (traitlets observers, display hooks, history managers).
    """

    def __init__(self, security_checker: SecurityChecker | None = None) -> None:
        self._security = security_checker or SecurityChecker()
        # Lazy: shell created only when execute() / inject() / get() is called.
        self._shell: Any | None = None
        # Pending injections that happened before shell creation.
        self._pending_injects: dict[str, Any] = {}
        # Lock to prevent concurrent shell creation in _require_shell.
        # Eager creation is safe: requires-python >= 3.11, where
        # asyncio.Lock() works outside a running event loop (fixed in 3.10).
        self._shell_lock: asyncio.Lock = asyncio.Lock()
        # Serialize concurrent execute() calls so only one thread
        # accesses the non-thread-safe InteractiveShell at a time.
        self._exec_lock: asyncio.Lock = asyncio.Lock()
        # Flag set when a timed-out thread execution may still be running.
        # Prevents new executions on a contaminated shell.
        self._timed_out: bool = False
        # Signaled when the _run_cell_sync thread completes, so that
        # reset()/close() can wait before clearing user_ns.
        self._exec_done: threading.Event = threading.Event()
        self._exec_done.set()  # Initially "done" (no thread running)
        self._closed: bool = False  # Prevents shell re-creation after close()

    async def _require_shell(self) -> Any:
        """Return the shell, creating it lazily if needed.

        Uses an asyncio.Lock to prevent concurrent shell creation. Only the
        first caller creates the shell; subsequent callers see the already-
        created shell.
        """
        if self._shell is not None:
            return self._shell

        async with self._shell_lock:
            # Double-check after acquiring the lock
            if self._shell is not None:
                return self._shell

            from IPython.core.interactiveshell import (
                InteractiveShell,  # pyright: ignore[reportMissingImports]
            )
            from traitlets.config import Config  # pyright: ignore[reportMissingImports]

            config = Config()
            config.InteractiveShell.cache_size = 0
            config.InteractiveShell.history_length = 0
            config.InteractiveShell.automagic = False
            config.InteractiveShell.separate_in = ""
            config.InteractiveShell.separate_out = ""
            config.InteractiveShell.separate_out2 = ""
            config.InteractiveShell.autocall = 0
            config.InteractiveShell.colors = "nocolor"
            config.InteractiveShell.xmode = "Plain"
            config.InteractiveShell.quiet = True
            config.InteractiveShell.autoindent = False

            self._shell = InteractiveShell(config=config, user_ns={})

            # Apply any pending injections
            if self._pending_injects:
                self._shell.user_ns.update(self._pending_injects)  # pyright: ignore[reportOptionalMemberAccess]
                self._pending_injects.clear()

        return self._shell

    def close(self) -> None:
        """Release the InteractiveShell and its resources."""
        self._closed = True
        if self._timed_out:
            # Wait for the still-running thread to finish before clearing.
            if not self._exec_done.wait(timeout=5.0):
                logger.warning(
                    "Timed-out execution thread still running during close; "
                    "clearing namespace anyway (race possible)"
                )

        if self._shell is not None:
            try:
                self._shell.user_ns.clear()
            except Exception:
                logger.warning(
                    "Failed to clear IPython user namespace during close",
                    exc_info=True,
                )
            self._shell = None
        self._pending_injects.clear()
        self._timed_out = False
        self._exec_done.set()

    def reset(self) -> None:
        """Clear namespace for reuse without destroying the shell.

        Resets the executor to a clean state: clears user namespace,
        pending injections, and variable tracking.  The heavy
        ``InteractiveShell`` is kept alive for reuse, avoiding the
        50-200 MB cost of re-creating it.
        """
        if self._timed_out:
            # Wait for the still-running thread to finish before clearing.
            if not self._exec_done.wait(timeout=5.0):
                logger.warning(
                    "Timed-out execution thread still running during reset; "
                    "closing shell to prevent contaminated reuse"
                )
                # Thread still running — close the shell entirely rather than
                # allowing new executions on a potentially contaminated namespace.
                if self._shell is not None:
                    try:
                        self._shell.user_ns.clear()
                    except Exception:
                        pass
                    self._shell = None
                self._pending_injects.clear()
                self._timed_out = True  # keep flag — shell is unusable
                self._exec_done.set()
                return

        if self._shell is not None:
            # Preserve IPython internals BEFORE clearing
            internals_cache = {
                k: v
                for k, v in self._shell.user_ns.items()
                if k in _IPYTHON_INTERNALS
            }
            self._shell.user_ns.clear()
            # Re-add preserved internals
            self._shell.user_ns.update(internals_cache)
        self._pending_injects.clear()
        self._timed_out = False
        self._exec_done.set()

    def __del__(self) -> None:
        # Safety net: release shell if close() was never called.
        if hasattr(self, "_shell") and self._shell is not None:
            try:
                self._shell.user_ns.clear()
            except Exception:
                pass
            self._shell = None

    async def execute(self, code: str, timeout: float = 30.0) -> ExecutionResult:
        """Execute code with security check and timeout.

        1. Run SecurityChecker.check_code() -- fail fast on violations.
        2. transform_cell() for IPython magic handling.
        3. run_cell() via asyncio.to_thread with timeout.
        4. Parse result into ExecutionResult.

        Args:
            code: Python source code to execute.
            timeout: Maximum execution time in seconds (minimum 0.1s).

        Returns:
            ExecutionResult with success status, output, and error info.
        """
        timeout = max(timeout, 0.1)

        # Step 1: Reject calls after close()
        if self._closed:
            return ExecutionResult(
                success=False,
                error="Executor has been closed; create a new instance",
            )

        # Step 2: Security check (no shell needed)
        if self._timed_out:
            return ExecutionResult(
                success=False,
                error="Shell is contaminated by a previous timed-out execution; "
                "call reset() or close() to recover",
            )
        violations = self._security.check_code(code)
        if violations:
            details = "\n".join(f"  - [{v.rule_type}] {v.message}" for v in violations)
            return ExecutionResult(
                success=False,
                error=f"Code blocked: {len(violations)} security violation(s):\n{details}",
            )

        # Step 2: Execute (shell created lazily here)
        # Serialize: only one thread may access the non-thread-safe
        # InteractiveShell at a time.
        # NOTE: sys.stdout is process-global; if multiple IPythonExecutor
        # instances exist in the same process (non-production scenario),
        # concurrent executions may cross-contaminate output.  In production,
        # each agent subprocess has exactly one executor, so this is safe.
        async with self._exec_lock:
            return await self._execute_inner(code, timeout)

    async def _execute_inner(self, code: str, timeout: float) -> ExecutionResult:
        """Inner execution logic, called under _exec_lock."""
        shell = await self._require_shell()

        # Safety gate: if a previous timed-out thread is still running,
        # wait for it to finish before starting a new execution.
        # Without this, the old thread may mutate the shell namespace
        # concurrently with the new execution (TOCTOU race after reset()).
        if not self._exec_done.is_set():
            # Wait for the thread using asyncio.to_thread to avoid
            # polling the event loop.  Event.wait(timeout) returns True
            # if the event was set, False if it timed out.
            try:
                thread_done = await asyncio.wait_for(
                    asyncio.to_thread(self._exec_done.wait, 5.0),
                    timeout=6.0,
                )
            except TimeoutError:
                thread_done = False
            if not thread_done:
                return ExecutionResult(
                    success=False,
                    error="Previous timed-out execution thread is still running; "
                    "call reset() or close() and wait for it to finish",
                )
            # Old thread finished — safe to proceed

        try:
            # Snapshot namespace before execution to detect new variables
            pre_keys = self._namespace_key_set()

            transformed = shell.transform_cell(code)
            self._exec_done.clear()  # Thread is about to start

            # Redirect stdout/stderr in the EVENT LOOP THREAD (not inside
            # the worker thread) so that asyncio.wait_for can correctly
            # cancel the coroutine on timeout.  Redirecting inside
            # asyncio.to_thread deadlocks the cancellation path because
            # the asyncio internals interact with sys.stdout during cancel.
            buf_out = io.StringIO()
            buf_err = io.StringIO()
            old_out = sys.stdout
            old_err = sys.stderr
            sys.stdout = buf_out
            sys.stderr = buf_err
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(
                        self._run_cell_sync, transformed,
                    ),
                    timeout=timeout,
                )
            finally:
                sys.stdout = old_out
                sys.stderr = old_err

            stdout = buf_out.getvalue()
            _stderr = buf_err.getvalue()

            # Collect variables created in this execution
            vars_created = self._detect_new_variables(pre_keys)

            # Handle errors
            if result.error_before_exec:
                return ExecutionResult(
                    success=False,
                    output=stdout or "",
                    error=str(result.error_before_exec),
                    variables_created=vars_created,
                )
            if result.error_in_exec:
                return ExecutionResult(
                    success=False,
                    output=stdout or "",
                    error=str(result.error_in_exec),
                    variables_created=vars_created,
                )

            return ExecutionResult(
                success=True,
                output=stdout or "",
                variables_created=vars_created,
            )

        except TimeoutError:
            # NOTE: The underlying thread (from asyncio.to_thread) continues
            # running after this timeout — Python cannot forcibly kill threads.
            # Only the _timed_out flag prevents new executions on this shell,
            # but the still-running thread may mutate kernel state (variables,
            # imports, etc).  Callers should treat the shell as contaminated and
            # call reset() or close() before reuse.
            self._timed_out = True
            return ExecutionResult(
                success=False,
                error=f"Execution timed out after {timeout}s",
            )
        except asyncio.CancelledError:
            # Task cancelled while the to_thread is running.  The thread
            # keeps going (same contamination risk as timeout), so mark
            # the shell as timed-out to prevent reuse without reset().
            self._timed_out = True
            raise
        except Exception as e:
            self._exec_done.set()  # Thread never started — clear the gate
            logger.error("Unexpected execution error: %s", e, exc_info=True)
            return ExecutionResult(
                success=False,
                error=f"Execution error: {e}",
            )

    def _run_cell_sync(self, transformed: str) -> Any:
        """Synchronous cell execution for use with asyncio.to_thread.

        Precondition: ``self._shell`` must already be initialized (guaranteed
        by ``execute()`` calling ``await _require_shell()`` before dispatching
        to ``asyncio.to_thread``).

        NOTE: stdout/stderr are redirected by the caller (_execute_inner) in
        the event loop thread *before* dispatching to asyncio.to_thread.
        This function must NOT redirect stdout/stderr itself — doing so inside
        the worker thread deadlocks asyncio.wait_for's cancellation path.

        Returns:
            IPython ExecutionResult.
        """
        if self._shell is None:
            raise RuntimeError("_run_cell_sync called before shell initialization")
        try:
            result = self._shell.run_cell(transformed, store_history=False)
            return result
        finally:
            self._exec_done.set()  # Signal thread completion

    def inject(self, name: str, value: Any) -> None:
        """Inject a variable into the namespace.

        If the shell hasn't been created yet, the injection is queued
        and applied when the shell is first materialized.
        """
        if self._shell is not None:
            self._shell.user_ns[name] = value
        else:
            self._pending_injects[name] = value

    def get(self, name: str, default: Any = None) -> Any:
        """Retrieve a variable from the namespace.

        Args:
            name: Variable name to retrieve.
            default: Value to return if the name is not found (default None).

        Returns:
            The Python object, or *default* if not found.
        """
        ns = self._shell.user_ns if self._shell is not None else self._pending_injects
        result = ns.get(name, _MISSING)
        if result is _MISSING:
            return default
        return result

    def namespace_keys(self) -> list[str]:
        """List all user-defined variables in the namespace.

        Excludes IPython internal names (In, Out, exit, quit, etc.).
        """
        return sorted(self._namespace_key_set())

    def _namespace_key_set(self) -> set[str]:
        """Return user-defined namespace keys as an unsorted set.

        For internal use where sorting is unnecessary (e.g. set difference
        in _detect_new_variables).  Avoids the overhead of sorted().
        """
        ns = self._shell.user_ns if self._shell is not None else self._pending_injects
        return {
            k for k in ns
            if k not in _IPYTHON_INTERNALS
            and not k.startswith("_")
        }

    def _detect_new_variables(self, pre_keys: set[str]) -> list[str]:
        """Detect variables created since the last execution.

        Args:
            pre_keys: Set of namespace keys snapshot before execution.
        """
        current = self._namespace_key_set()
        return sorted(current - pre_keys)
