"""IPythonExecutor: in-process code execution via IPython InteractiveShell.

Lazy-initialized shell per executor instance with disabled history, automagic,
and colors.  Runs SecurityChecker before execution for fail-fast security.

Reference: cave-agent/src/cave_agent/runtime/executor.py
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from agent_nexus.models.runtime import ExecutionResult

from .security_checker import SecurityChecker

logger = logging.getLogger(__name__)

# User namespace keys that are IPython internals, not user variables
_IPYTHON_INTERNALS = frozenset({
    "In", "Out", "exit", "quit", "get_ipython", "_", "__", "___",
    "_ih", "_oh", "_sh", "_dh", "_", "__", "___",
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
        self._pre_keys: set[str] = set()
        # Lazy: shell created only when execute() / inject() / get() is called.
        self._shell: Any | None = None
        # Pending injections that happened before shell creation.
        self._pending_injects: dict[str, Any] = {}

    def _require_shell(self) -> Any:
        """Return the shell, creating it lazily if needed."""
        if self._shell is None:
            from IPython.core.interactiveshell import InteractiveShell
            from traitlets.config import Config

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
                self._shell.user_ns.update(self._pending_injects)
                self._pending_injects.clear()

        return self._shell

    def close(self) -> None:
        """Release the InteractiveShell and its resources."""
        if self._shell is not None:
            try:
                self._shell.user_ns.clear()
            except Exception:  # noqa: BLE001
                pass
            self._shell = None
        self._pending_injects.clear()

    def reset(self) -> None:
        """Clear namespace for reuse without destroying the shell.

        Resets the executor to a clean state: clears user namespace,
        pending injections, and variable tracking.  The heavy
        ``InteractiveShell`` is kept alive for reuse, avoiding the
        50-200 MB cost of re-creating it.
        """
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
        self._pre_keys.clear()

    def __del__(self) -> None:
        # Safety net: release shell if close() was never called.
        if hasattr(self, "_shell") and self._shell is not None:
            try:
                self._shell.user_ns.clear()
            except Exception:  # noqa: BLE001
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
            timeout: Maximum execution time in seconds.

        Returns:
            ExecutionResult with success status, output, and error info.
        """
        # Step 1: Security check (no shell needed)
        violations = self._security.check_code(code)
        if violations:
            details = "\n".join(f"  - [{v.rule_type}] {v.message}" for v in violations)
            return ExecutionResult(
                success=False,
                error=f"Code blocked: {len(violations)} security violation(s):\n{details}",
            )

        # Step 2: Execute (shell created lazily here)
        shell = self._require_shell()

        try:
            from IPython.utils.capture import capture_output

            # Snapshot namespace before execution to detect new variables
            self._pre_keys = set(self.namespace_keys())

            with capture_output() as captured:
                transformed = shell.transform_cell(code)
                result = await asyncio.wait_for(
                    asyncio.to_thread(
                        self._run_cell_sync, transformed,
                    ),
                    timeout=timeout,
                )

            # Collect variables created in this execution
            vars_created = self._detect_new_variables()

            # Handle errors
            if result.error_before_exec:
                return ExecutionResult(
                    success=False,
                    output=captured.stdout or "",
                    error=str(result.error_before_exec),
                    variables_created=vars_created,
                )
            if result.error_in_exec:
                return ExecutionResult(
                    success=False,
                    output=captured.stdout or "",
                    error=str(result.error_in_exec),
                    variables_created=vars_created,
                )

            return ExecutionResult(
                success=True,
                output=captured.stdout or "",
                variables_created=vars_created,
            )

        except asyncio.TimeoutError:
            return ExecutionResult(
                success=False,
                error=f"Execution timed out after {timeout}s",
            )
        except Exception as e:
            logger.error("Unexpected execution error: %s", e, exc_info=True)
            return ExecutionResult(
                success=False,
                error=f"Execution error: {e}",
            )

    def _run_cell_sync(self, transformed: str) -> Any:
        """Synchronous cell execution for use with asyncio.to_thread."""
        return self._require_shell().run_cell(transformed, store_history=False)

    def inject(self, name: str, value: Any) -> None:
        """Inject a variable into the namespace.

        If the shell hasn't been created yet, the injection is queued
        and applied when the shell is first materialized.
        """
        if self._shell is not None:
            self._shell.user_ns[name] = value
        else:
            self._pending_injects[name] = value

    def get(self, name: str) -> Any:
        """Retrieve a variable from the namespace.

        Returns:
            The Python object, or None if not found.
        """
        if self._shell is not None:
            return self._shell.user_ns.get(name)
        return self._pending_injects.get(name)

    def namespace_keys(self) -> list[str]:
        """List all user-defined variables in the namespace.

        Excludes IPython internal names (In, Out, exit, quit, etc.).
        """
        ns = self._shell.user_ns if self._shell is not None else self._pending_injects
        return sorted(
            k for k in ns
            if k not in _IPYTHON_INTERNALS
            and not k.startswith("_")
        )

    def _detect_new_variables(self) -> list[str]:
        """Detect variables created since the last execution."""
        current = set(self.namespace_keys())
        return sorted(current - self._pre_keys)
