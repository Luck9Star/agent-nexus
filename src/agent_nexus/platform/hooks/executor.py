"""HookExecutor -- execute lifecycle hooks for Agent events.

Supports four hook types:
- COMMAND: Shell subprocess execution
- HTTP: HTTP POST to external service
- PROMPT: Short LLM call (placeholder)
- AGENT: Deep LLM call (placeholder)

Hooks are loaded from:
1. hooks.yaml in Agent package directory
2. ~/.agent-nexus/hooks.yaml (global)
3. .agent-nexus/hooks.yaml (project-level)
"""

from __future__ import annotations

import asyncio
import fnmatch
import ipaddress
import json
import logging
import shlex
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from agent_nexus.models.hooks import (
    AggregatedHookResult,
    HookDefinition,
    HookEvent,
    HookExecution,
    HookType,
)

logger = logging.getLogger(__name__)


def _is_private_url(url: str) -> bool:
    """Block requests to private/internal IP ranges."""
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        return False
    try:
        ip = ipaddress.ip_address(hostname)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
    except ValueError:
        # hostname is a domain, not IP — allow but block known internal endpoints
        blocked_hostnames = {"localhost", "metadata.google.internal", "metadata.internal"}
        return hostname.lower() in blocked_hostnames


class HookExecutor:
    """Execute lifecycle hooks for Agent events.

    Hooks are run sequentially for a given event. If a hook with
    ``block_on_failure=True`` fails, remaining hooks are skipped and
    the aggregated result is marked as blocked.

    Security: COMMAND hooks are restricted to an allowlist of base
    commands (the first token after shell splitting).  By default the
    allowlist is empty, meaning all COMMAND hooks are rejected.  Pass
    ``allowed_commands`` to permit specific commands (e.g. ``["git",
    "npm"]``).
    """

    def __init__(
        self,
        hooks: list[HookDefinition] | None = None,
        *,
        allowed_commands: list[str] | set[str] | None = None,
    ) -> None:
        self._hooks: list[HookDefinition] = hooks or []
        self._allowed_commands: set[str] = set(allowed_commands or [])
        self._http_client: Any = None  # httpx.AsyncClient, lazy-init
        self._hooks_by_event: dict[HookEvent, list[HookDefinition]] = {}
        self._build_event_index()

    def _build_event_index(self) -> None:
        """Build an index from HookEvent to the list of hooks for that event.

        Called once during init.  Since the hook list is immutable after
        construction, the index stays valid for the lifetime of the executor.
        """
        self._hooks_by_event.clear()
        for hook in self._hooks:
            self._hooks_by_event.setdefault(hook.event, []).append(hook)

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def _parse_hooks_for_event(
        cls,
        event: HookEvent,
        hook_list: list[Any],
        source_path: Path,
    ) -> list[HookDefinition]:
        hooks: list[HookDefinition] = []
        for hook_dict in hook_list:
            if not isinstance(hook_dict, dict):
                continue
            hook_dict_with_event = {**hook_dict, "event": event}
            try:
                hooks.append(HookDefinition.model_validate(hook_dict_with_event))
            except Exception:
                logger.warning(
                    "Invalid hook definition in %s under event %r: %s",
                    source_path,
                    event.value,
                    hook_dict,
                    exc_info=True,
                )
        return hooks

    @staticmethod
    def _load_yaml_raw(yaml_path: Path) -> dict[str, Any]:
        """Load raw YAML dict from file. Returns empty dict on any failure."""
        if not yaml_path.exists():
            return {}
        try:
            import yaml

            return yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        except Exception:
            logger.warning("Failed to load hooks from %s", yaml_path, exc_info=True)
            return {}

    @staticmethod
    def _resolve_event(event_name: str, yaml_path: Path) -> HookEvent | None:
        """Parse event name, return None and log on failure."""
        try:
            return HookEvent(event_name)
        except ValueError:
            logger.warning("Unknown hook event %r in %s, skipping", event_name, yaml_path)
            return None

    @classmethod
    def from_yaml(
        cls,
        yaml_path: Path,
        *,
        allowed_commands: list[str] | set[str] | None = None,
    ) -> HookExecutor:
        """Load hooks from a hooks.yaml file.

        Expected format::

            pre_execution:
              - type: command
                command: "echo hello"
                block_on_failure: false
                timeout_seconds: 5

        Top-level keys are HookEvent names; values are lists of hook
        definition dicts.

        Returns an executor with an empty hook list if the file does not
        exist or cannot be parsed.
        """
        cmds = allowed_commands or []
        raw = cls._load_yaml_raw(yaml_path)
        if not raw:
            return cls(hooks=[], allowed_commands=cmds)

        hooks: list[HookDefinition] = []
        for event_name, hook_list in raw.items():
            event = cls._resolve_event(event_name, yaml_path)
            if event is None:
                continue
            if not isinstance(hook_list, list):
                logger.warning(
                    "Expected list for event %r in %s, got %s",
                    event_name,
                    yaml_path,
                    type(hook_list).__name__,
                )
                continue
            hooks.extend(cls._parse_hooks_for_event(event, hook_list, yaml_path))

        return cls(hooks=hooks, allowed_commands=cmds)

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_hooks_for_event(
        self,
        event: HookEvent,
        matcher: str | None = None,
    ) -> list[HookDefinition]:
        """Return hooks matching *event* and optional *matcher*.

        Only hooks with ``enabled=True`` are returned.  If *matcher* is
        provided, only hooks whose ``matcher`` field matches via
        :func:`fnmatch.fnmatch` are included (hooks with ``matcher=None``
        always match).
        """
        result: list[HookDefinition] = []
        candidates = self._hooks_by_event.get(event, [])
        for hook in candidates:
            if not hook.enabled:
                continue
            if (
                matcher is not None
                and hook.matcher is not None
                and not fnmatch.fnmatch(matcher, hook.matcher)
            ):
                continue
            # hook.matcher is None means "match everything"
            result.append(hook)
        return result

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def execute_event(
        self,
        event: HookEvent,
        context: dict[str, Any] | None = None,
        matcher: str | None = None,
    ) -> AggregatedHookResult:
        """Execute all hooks for *event* sequentially.

        If any hook with ``block_on_failure=True`` fails, remaining
        hooks are skipped and the result is marked as blocked.
        """
        hooks = self.get_hooks_for_event(event, matcher=matcher)
        results: list[HookExecution] = []
        errors: list[str] = []
        blocked = False

        for hook in hooks:
            execution = await self._execute_hook(hook, context)
            results.append(execution)

            if not execution.passed:
                errors.append(execution.error or f"Hook failed: {hook.type}:{hook.event}")
                if hook.block_on_failure:
                    blocked = True
                    break  # stop executing remaining hooks

        return AggregatedHookResult(
            event=event,
            results=results,
            blocked=blocked,
            errors=errors,
        )

    async def _execute_hook(
        self,
        hook: HookDefinition,
        context: dict[str, Any] | None = None,
    ) -> HookExecution:
        """Dispatch execution based on hook type."""
        ctx = context or {}
        handlers = {
            HookType.COMMAND: self._execute_command,
            HookType.HTTP: self._execute_http,
            HookType.PROMPT: self._execute_prompt,
            HookType.AGENT: self._execute_agent,
        }
        handler = handlers.get(hook.type)
        if handler is None:
            return HookExecution(
                hook=hook,
                passed=False,
                blocked=hook.block_on_failure,
                error=f"Unknown hook type: {hook.type}",
            )
        return await handler(hook, ctx)

    @staticmethod
    def _validate_command_args(
        hook: HookDefinition,
    ) -> tuple[list[str] | None, HookExecution | None]:
        """Validate and parse COMMAND hook arguments.

        Returns (args, error_result).  If *error_result* is set the
        caller should return it immediately.
        """
        if not hook.command:
            return None, HookExecution(
                hook=hook,
                passed=False,
                blocked=hook.block_on_failure,
                error="COMMAND hook missing 'command' field",
            )
        try:
            args = shlex.split(hook.command)
        except ValueError as exc:
            return None, HookExecution(
                hook=hook,
                passed=False,
                blocked=hook.block_on_failure,
                error=f"Malformed command string: {exc}",
                duration_ms=0.0,
            )
        if not args:
            return None, HookExecution(
                hook=hook,
                passed=False,
                blocked=hook.block_on_failure,
                error="COMMAND hook has empty command after parsing",
            )
        return args, None

    def _check_command_allowlist(
        self,
        hook: HookDefinition,
        base_command: str,
    ) -> HookExecution | None:
        """Return an error result if *base_command* is not allowed, else None."""
        if self._allowed_commands and base_command in self._allowed_commands:
            return None
        return HookExecution(
            hook=hook,
            passed=False,
            blocked=hook.block_on_failure,
            error=f"COMMAND hook base command '{base_command}' not in allowlist",
        )

    @staticmethod
    def _build_command_result(
        hook: HookDefinition,
        returncode: int,
        stdout_bytes: bytes,
        stderr_bytes: bytes,
        duration_ms: float,
    ) -> HookExecution:
        """Build a HookExecution from subprocess exit status."""
        passed = returncode == 0
        output = stdout_bytes.decode("utf-8", errors="replace").strip() or None
        error_text = stderr_bytes.decode("utf-8", errors="replace").strip() or None
        if not passed and error_text is None:
            error_text = f"Command exited with code {returncode}"
        return HookExecution(
            hook=hook,
            passed=passed,
            blocked=(not passed and hook.block_on_failure),
            output=output,
            error=error_text if not passed else None,
            duration_ms=round(duration_ms, 2),
        )

    @staticmethod
    async def _kill_subprocess(proc: asyncio.subprocess.Process) -> None:
        """Best-effort kill + wait for a subprocess."""
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            logger.debug("Failed to kill subprocess", exc_info=True)

    # ------------------------------------------------------------------
    # Type-specific executors
    # ------------------------------------------------------------------


    async def _kill_and_build_error(
        self,
        hook: HookDefinition,
        error_msg: str,
        proc: asyncio.subprocess.Process | None,
        start: float,
        error_type: str | None = None,
    ) -> HookExecution:
        """Kill subprocess (if running) and build error HookExecution."""
        duration_ms = (time.monotonic() - start) * 1000
        if proc is not None:
            await self._kill_subprocess(proc)
        return HookExecution(
            hook=hook,
            passed=False,
            blocked=hook.block_on_failure,
            error=error_msg,
            error_type=error_type,
            duration_ms=round(duration_ms, 2),
        )

    async def _execute_command(
        self,
        hook: HookDefinition,
        context: dict[str, Any],
    ) -> HookExecution:
        """Execute a COMMAND hook via asyncio subprocess.

        Uses ``asyncio.create_subprocess_exec`` (not shell=True) for
        safety.  The *context* dict is serialized to JSON and passed via
        stdin pipe.  Exit code 0 = pass, non-zero = fail.
        """
        # --- input validation ---
        args, validation_err = self._validate_command_args(hook)
        if validation_err is not None:
            return validation_err

        allowlist_err = self._check_command_allowlist(hook, args[0])
        if allowlist_err is not None:
            return allowlist_err

        # --- run subprocess ---
        stdin_data = json.dumps(context).encode("utf-8")
        timeout = hook.timeout_seconds
        start = time.monotonic()
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=stdin_data),
                timeout=timeout,
            )
            duration_ms = (time.monotonic() - start) * 1000
            return self._build_command_result(
                hook,
                proc.returncode,
                stdout_bytes,
                stderr_bytes,
                duration_ms,
            )

        except TimeoutError:
            return await self._kill_and_build_error(
                hook, f"Command timed out after {timeout}s", proc, start,
            )

        except asyncio.CancelledError:
            if proc is not None:
                await self._kill_subprocess(proc)
            raise

        except Exception as exc:
            return await self._kill_and_build_error(
                hook, str(exc), proc, start, error_type=type(exc).__name__,
            )

    @staticmethod
    def _validate_http_url(hook: HookDefinition) -> HookExecution | None:
        """Validate HTTP hook URL. Returns error result on failure, None on success."""
        if not hook.url:
            return HookExecution(
                hook=hook,
                passed=False,
                blocked=hook.block_on_failure,
                error="HTTP hook missing 'url' field",
            )
        if not hook.url.startswith(("http://", "https://")):
            return HookExecution(
                hook=hook,
                passed=False,
                blocked=hook.block_on_failure,
                error=f"HTTP hook URL has unsupported scheme (only http/https): {hook.url}",
            )
        if _is_private_url(hook.url):
            return HookExecution(
                hook=hook,
                passed=False,
                blocked=hook.block_on_failure,
                error=f"HTTP hook URL targets private/internal address: {hook.url}",
            )
        return None

    @staticmethod
    def _build_http_result(
        hook: HookDefinition,
        resp: Any,
        duration_ms: float,
    ) -> HookExecution:
        """Build HookExecution from HTTP response."""
        passed = 200 <= resp.status_code < 300
        return HookExecution(
            hook=hook,
            passed=passed,
            blocked=(not passed and hook.block_on_failure),
            output=resp.text[:256] or None,
            error=None if passed else f"HTTP {resp.status_code}: {resp.text[:512]}",
            duration_ms=round(duration_ms, 2),
        )

    async def _execute_http(
        self,
        hook: HookDefinition,
        context: dict[str, Any],
    ) -> HookExecution:
        """Execute an HTTP hook via httpx POST.

        POSTs a JSON body ``{event, context, hook_type}`` to the hook
        URL.  Any 2xx response = pass, non-2xx or error = fail.
        """
        url_err = self._validate_http_url(hook)
        if url_err is not None:
            return url_err

        payload = {"event": hook.event, "context": context, "hook_type": hook.type}
        import httpx

        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))

        start = time.monotonic()
        try:
            resp = await self._http_client.post(
                hook.url,
                json=payload,
                timeout=httpx.Timeout(hook.timeout_seconds),
            )
            return self._build_http_result(hook, resp, (time.monotonic() - start) * 1000)
        except Exception as exc:
            duration_ms = (time.monotonic() - start) * 1000
            return HookExecution(
                hook=hook,
                passed=False,
                blocked=hook.block_on_failure,
                error=str(exc),
                error_type=type(exc).__name__,
                duration_ms=round(duration_ms, 2),
            )

    async def close(self) -> None:
        """Shut down the persistent HTTP client (if any)."""
        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()

    async def _execute_prompt(
        self,
        hook: HookDefinition,
        _context: dict[str, Any],
    ) -> HookExecution:
        """Execute a PROMPT hook (placeholder).

        For POC: returns ``{"ok": true}`` with the prompt text as output.
        Production would call a small LLM model here.
        """
        logger.warning("Prompt hook type is not yet implemented, returning unconditional pass")
        start = time.monotonic()
        output = hook.prompt or "PROMPT hook (no prompt text)"
        duration_ms = (time.monotonic() - start) * 1000
        return HookExecution(
            hook=hook,
            passed=True,
            blocked=False,
            output=output,
            duration_ms=round(duration_ms, 2),
        )

    async def _execute_agent(
        self,
        hook: HookDefinition,
        _context: dict[str, Any],
    ) -> HookExecution:
        """Execute an AGENT hook (placeholder).

        For POC: returns ``{"ok": true}`` with the prompt text as output.
        Production would call a large LLM model here.
        """
        logger.warning("Agent hook type is not yet implemented, returning unconditional pass")
        start = time.monotonic()
        output = hook.prompt or "AGENT hook (no prompt text)"
        duration_ms = (time.monotonic() - start) * 1000
        return HookExecution(
            hook=hook,
            passed=True,
            blocked=False,
            output=output,
            duration_ms=round(duration_ms, 2),
        )
