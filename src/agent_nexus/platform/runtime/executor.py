"""IPythonExecutor: in-process code execution via IPython InteractiveShell.

Singleton shell per executor instance with disabled history, automagic, and colors.
Runs SecurityChecker before execution for fail-fast security.

Reference: cave-agent/src/cave_agent/runtime/executor.py
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from IPython.core.interactiveshell import InteractiveShell
from IPython.utils.capture import capture_output
from traitlets.config import Config

from agent_nexus.models.runtime import ExecutionResult

from .security_checker import SecurityChecker

logger = logging.getLogger(__name__)

# User namespace keys that are IPython internals, not user variables
_IPYTHON_INTERNALS = frozenset({
    "In", "Out", "exit", "quit", "get_ipython", "_", "__", "___",
    "_ih", "_oh", "_sh", "_dh", "_", "__", "___",
})


def _create_ipython_config() -> Config:
    """Create a clean IPython config optimized for agent code execution.

    Disables history, automagic, colors, and other interactive features
    that are unnecessary for programmatic execution.
    """
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
    return config


class IPythonExecutor:
    """Execute Python code in an IPython InteractiveShell.

    Each instance holds a singleton InteractiveShell configured for
    non-interactive agent code execution. Security checks run before
    any code is executed.

    Usage::

        executor = IPythonExecutor()
        result = await executor.execute("x = 1 + 2")
        print(result.success)  # True
        print(executor.get("x"))  # 3
    """

    def __init__(self, security_checker: SecurityChecker | None = None) -> None:
        self._config = _create_ipython_config()
        self._shell = InteractiveShell.instance(config=self._config)
        self._security = security_checker or SecurityChecker()

    async def execute(self, code: str, timeout: float = 30.0) -> ExecutionResult:
        """Execute code with security check and timeout.

        1. Run SecurityChecker.check_code() -- fail fast on violations.
        2. transform_cell() for IPython magic handling.
        3. run_cell_async() with asyncio.wait_for timeout.
        4. Parse result into ExecutionResult.

        Args:
            code: Python source code to execute.
            timeout: Maximum execution time in seconds.

        Returns:
            ExecutionResult with success status, output, and error info.
        """
        # Step 1: Security check
        violations = self._security.check_code(code)
        if violations:
            details = "\n".join(f"  - [{v.rule_type}] {v.message}" for v in violations)
            return ExecutionResult(
                success=False,
                error=f"Code blocked: {len(violations)} security violation(s):\n{details}",
            )

        # Step 2: Execute
        try:
            with capture_output() as captured:
                transformed = self._shell.transform_cell(code)
                result = await asyncio.wait_for(
                    self._shell.run_cell_async(transformed, transformed_cell=transformed),
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

    def inject(self, name: str, value: Any) -> None:
        """Inject a variable into the IPython namespace.

        Args:
            name: Variable name.
            value: Python object to bind.
        """
        self._shell.user_ns[name] = value

    def get(self, name: str) -> Any:
        """Retrieve a variable from the namespace.

        Args:
            name: Variable name to look up.

        Returns:
            The Python object, or None if not found.
        """
        return self._shell.user_ns.get(name)

    def namespace_keys(self) -> list[str]:
        """List all user-defined variables in the namespace.

        Excludes IPython internal names (In, Out, exit, quit, etc.).

        Returns:
            Sorted list of user-defined variable names.
        """
        return sorted(
            k for k in self._shell.user_ns
            if k not in _IPYTHON_INTERNALS
            and not k.startswith("_")
        )

    def _detect_new_variables(self) -> list[str]:
        """Detect variables that appear to be user-defined.

        Returns a list of names that look like user variables
        (not IPython internals or dunder names).
        """
        return [
            k for k in self._shell.user_ns
            if k not in _IPYTHON_INTERNALS
            and not k.startswith("_")
            and not callable(self._shell.user_ns[k])
        ]
