"""Hook system models: HookType, HookEvent, HookDefinition, HookExecution."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

_utc_now = lambda: datetime.now(timezone.utc)


class HookType(StrEnum):
    """How a hook is executed.

    - COMMAND: Shell subprocess, low latency (file checks, scripts).
    - HTTP: HTTP POST to external service (CI/CD triggers, notifications).
    - PROMPT: Short LLM call with a small model (quick validation, format checks).
    - AGENT: Deep LLM call with a large model (quality assessment, complex reasoning).
    """

    COMMAND = "command"
    HTTP = "http"
    PROMPT = "prompt"
    AGENT = "agent"


class HookEvent(StrEnum):
    """When a hook is triggered in the Agent lifecycle.

    - PRE_EXECUTION: Before Agent execution (input validation, environment checks).
    - POST_EXECUTION: After Agent execution (output quality, notifications).
    - PRE_TOOL_USE: Before a tool call (parameter validation, permission augmentation).
    - POST_TOOL_USE: After a tool call (result audit, logging).
    - ON_ERROR: On error (error notification, degradation strategy).
    - ON_EVOLUTION: After Skill evolution (evolution audit, quality gates).
    """

    PRE_EXECUTION = "pre_execution"
    POST_EXECUTION = "post_execution"
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    ON_ERROR = "on_error"
    ON_EVOLUTION = "on_evolution"


class HookDefinition(BaseModel):
    """Declarative definition of a lifecycle hook.

    Defined in hooks/hooks.yaml within an Agent Package, or in
    global/project-level hooks.yaml files.

    Example hooks.yaml:
        pre_execution:
          - type: prompt
            prompt: "Validate input file exists and is .docx format"
            block_on_failure: true
            timeout_seconds: 10

        post_execution:
          - type: command
            command: "notify-send 'Document filled successfully'"
            block_on_failure: false
    """

    model_config = ConfigDict(frozen=True)

    type: HookType
    event: HookEvent
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    block_on_failure: bool = False
    timeout_seconds: float = 10.0
    matcher: str | None = None  # fnmatch glob for tool name matching

    # Hook-type-specific fields
    command: str | None = None  # COMMAND type: shell command to run
    url: str | None = None  # HTTP type: URL to POST to
    prompt: str | None = None  # PROMPT/AGENT type: LLM prompt text
    model: str | None = None  # PROMPT/AGENT type: model to use (e.g. "haiku", "sonnet")


class HookExecution(BaseModel):
    """Result of executing a single hook.

    Records whether the hook passed or blocked, execution duration,
    and any output or error.
    """

    model_config = ConfigDict(frozen=True)

    hook: HookDefinition
    passed: bool
    blocked: bool = False
    output: str | None = None
    error: str | None = None
    duration_ms: float = 0.0
    executed_at: datetime = Field(default_factory=_utc_now)


class AggregatedHookResult(BaseModel):
    """Aggregated result of all hooks for a single event.

    If any hook with block_on_failure=True fails, the entire
    result is marked as blocked.
    """

    model_config = ConfigDict(frozen=True)

    event: HookEvent
    results: list[HookExecution] = Field(default_factory=list)
    blocked: bool = False
    errors: list[str] = Field(default_factory=list)
