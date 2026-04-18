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
import json
import logging
import shlex
import time
from pathlib import Path
from typing import Any

import httpx

from agent_nexus.models.hooks import (
    AggregatedHookResult,
    HookDefinition,
    HookEvent,
    HookExecution,
    HookType,
)

logger = logging.getLogger(__name__)


class HookExecutor:
    """Execute lifecycle hooks for Agent events.

    Hooks are run sequentially for a given event. If a hook with
    ``block_on_failure=True`` fails, remaining hooks are skipped and
    the aggregated result is marked as blocked.
    """

    def __init__(self, hooks: list[HookDefinition] | None = None) -> None:
        self._hooks: list[HookDefinition] = hooks or []

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_yaml(cls, yaml_path: Path) -> HookExecutor:
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
        if not yaml_path.exists():
            return cls(hooks=[])

        try:
            import yaml  # noqa: F811

            raw: dict[str, Any] = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        except Exception:
            logger.warning("Failed to load hooks from %s", yaml_path, exc_info=True)
            return cls(hooks=[])

        hooks: list[HookDefinition] = []
        for event_name, hook_list in raw.items():
            try:
                event = HookEvent(event_name)
            except ValueError:
                logger.warning("Unknown hook event %r in %s, skipping", event_name, yaml_path)
                continue

            if not isinstance(hook_list, list):
                logger.warning(
                    "Expected list for event %r in %s, got %s",
                    event_name,
                    yaml_path,
                    type(hook_list).__name__,
                )
                continue

            for hook_dict in hook_list:
                if not isinstance(hook_dict, dict):
                    continue
                hook_dict_with_event = {**hook_dict, "event": event}
                try:
                    hooks.append(HookDefinition.model_validate(hook_dict_with_event))
                except Exception:
                    logger.warning(
                        "Invalid hook definition in %s under event %r: %s",
                        yaml_path,
                        event_name,
                        hook_dict,
                        exc_info=True,
                    )

        return cls(hooks=hooks)

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
        for hook in self._hooks:
            if hook.event != event:
                continue
            if not hook.enabled:
                continue
            if matcher is not None and hook.matcher is not None:
                if not fnmatch.fnmatch(matcher, hook.matcher):
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

    # ------------------------------------------------------------------
    # Type-specific executors
    # ------------------------------------------------------------------

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
        if not hook.command:
            return HookExecution(
                hook=hook,
                passed=False,
                blocked=hook.block_on_failure,
                error="COMMAND hook missing 'command' field",
            )

        stdin_data = json.dumps(context).encode("utf-8")
        args = shlex.split(hook.command)
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

            passed = proc.returncode == 0
            output = stdout_bytes.decode("utf-8", errors="replace").strip() or None
            error_text = stderr_bytes.decode("utf-8", errors="replace").strip() or None

            if not passed and error_text is None:
                error_text = f"Command exited with code {proc.returncode}"

            return HookExecution(
                hook=hook,
                passed=passed,
                blocked=(not passed and hook.block_on_failure),
                output=output,
                error=error_text if not passed else None,
                duration_ms=round(duration_ms, 2),
            )

        except asyncio.TimeoutError:
            duration_ms = (time.monotonic() - start) * 1000
            try:
                if proc is not None:
                    proc.kill()
                    await proc.wait()
            except Exception:
                pass
            return HookExecution(
                hook=hook,
                passed=False,
                blocked=hook.block_on_failure,
                error=f"Command timed out after {timeout}s",
                duration_ms=round(duration_ms, 2),
            )

        except Exception as exc:
            duration_ms = (time.monotonic() - start) * 1000
            if proc is not None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                try:
                    await proc.wait()
                except ProcessLookupError:
                    pass
            return HookExecution(
                hook=hook,
                passed=False,
                blocked=hook.block_on_failure,
                error=str(exc),
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
        if not hook.url:
            return HookExecution(
                hook=hook,
                passed=False,
                blocked=hook.block_on_failure,
                error="HTTP hook missing 'url' field",
            )

        payload = {
            "event": hook.event,
            "context": context,
            "hook_type": hook.type,
        }
        timeout = httpx.Timeout(hook.timeout_seconds)

        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(hook.url, json=payload)

            duration_ms = (time.monotonic() - start) * 1000
            passed = 200 <= resp.status_code < 300

            return HookExecution(
                hook=hook,
                passed=passed,
                blocked=(not passed and hook.block_on_failure),
                output=resp.text[:1024] or None,
                error=None if passed else f"HTTP {resp.status_code}: {resp.text[:512]}",
                duration_ms=round(duration_ms, 2),
            )

        except Exception as exc:
            duration_ms = (time.monotonic() - start) * 1000
            return HookExecution(
                hook=hook,
                passed=False,
                blocked=hook.block_on_failure,
                error=str(exc),
                duration_ms=round(duration_ms, 2),
            )

    async def _execute_prompt(
        self,
        hook: HookDefinition,
        context: dict[str, Any],
    ) -> HookExecution:
        """Execute a PROMPT hook (placeholder).

        For POC: returns ``{"ok": true}`` with the prompt text as output.
        Production would call a small LLM model here.
        """
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
        context: dict[str, Any],
    ) -> HookExecution:
        """Execute an AGENT hook (placeholder).

        For POC: returns ``{"ok": true}`` with the prompt text as output.
        Production would call a large LLM model here.
        """
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
