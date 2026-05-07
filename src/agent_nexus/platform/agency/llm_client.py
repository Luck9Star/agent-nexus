"""LLMClient -- shared LLM API caller for the agency pipeline.

Uses LiteLLM as a unified calling layer for all API providers (Anthropic,
OpenAI, DeepSeek, Ollama, OpenAI-compatible APIs).  CLI Backend is preserved
unchanged.

Extracts the API calling logic from LLMExecutor so that LLMPlanner,
LLMIntegrator, and LLMQualityGate can all reuse it with different model
strings and prompts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import litellm

from agent_nexus.models.capability import ModelCapability, ModelCapabilityRegistry
from agent_nexus.models.config import ProviderApiType, ProviderConfig
from agent_nexus.models.errors import AgentNexusError
from agent_nexus.platform.agency.hooks import (
    CallContext,
    CallResult,
    HookEvent,
    HookManager,
)

logger = logging.getLogger(__name__)


class LLMCallError(AgentNexusError):
    """Raised when an LLM call fails (API error, CLI exit, timeout)."""


@dataclass
class LLMResponse:
    """Structured response from an LLM call."""

    text: str
    model: str
    provider: str
    metadata: dict[str, object] = field(default_factory=dict)


class LLMClient:
    """Reusable LLM API client using LiteLLM as the unified calling layer.

    Reads model config from ``~/.agent-nexus/config.toml`` and resolves
    API keys from environment variables.

    Maintains a persistent ``httpx.Client`` for connection reuse across
    calls.  Call ``close()`` when done, or use as a context manager.

    Usage::

        client = LLMClient(model_string="api:MiniMax-M2.7-highspeed")
        response = client.call(system_prompt="You are a planner.", user_message="Design X")
        print(response.text)
        client.close()
    """

    _TIMEOUT = 120.0

    # Provider name -> litellm prefix mapping
    _LITELLM_PROVIDER_MAP: dict[str, str] = {
        "anthropic": "anthropic",
        "openai": "openai",
        "deepseek": "deepseek",
        "ollama": "ollama",
        "api": "openai",  # OpenAI-compatible APIs (MiniMax, Qwen, etc.)
    }

    def __init__(
        self,
        model_string: str | None = None,
        stage: str | None = None,
        config_dir: Path | None = None,
        capability_registry: ModelCapabilityRegistry | None = None,
        session_store: Any | None = None,
    ) -> None:
        """Initialise the client (backward-compatible entry point).

        When called with a ``model_string`` or ``stage``, delegates config
        resolution to :meth:`from_config`.  This preserves the original
        API while keeping the constructor logic minimal.

        Parameters
        ----------
        model_string:
            Explicit ``provider:model`` string.  Takes priority.
        stage:
            Pipeline stage name (e.g. ``"planning"``).  Resolved via
            ``[models.stages]`` config, falls back to default.
        config_dir:
            Config directory override (default: ``~/.agent-nexus/``).
        """
        # Pre-init all resource-holding attrs so __del__ is safe even if
        # __init__ raises before reaching the normal init sites below.
        self._cli_backend: Any = None

        # Pre-init all data attrs that from_config assigns.  Without these
        # declarations the type checker cannot see the attributes on the class,
        # and __del__ / properties may fail if from_config raises partway.
        self._provider_name: str = ""
        self._model_name: str = ""
        self._provider_config: ProviderConfig = ProviderConfig()
        self._api_key: str = ""
        self._session_store: Any | None = session_store
        self._capability_registry: ModelCapabilityRegistry = (
            capability_registry if capability_registry is not None else ModelCapabilityRegistry()
        )
        self._capability: ModelCapability = ModelCapability(
            model_id="",
            provider="",
            max_output_tokens=0,
            context_window=0,
            supports_vision=False,
            supports_tool_use=False,
            supports_temperature=False,
            temperature_min=0.0,
            temperature_max=0.0,
            knowledge_cutoff="",
        )
        self._platform_config: Any = None
        self._hooks: HookManager = HookManager()

        # Delegate to from_config for config resolution, then apply the
        # resolved values to self.  This keeps __init__ thin while
        # preserving backward compatibility.
        type(self).from_config(
            model_string=model_string,
            stage=stage,
            config_dir=config_dir,
            capability_registry=capability_registry,
            session_store=session_store,
            _instance=self,
        )

    @classmethod
    def from_config(
        cls,
        model_string: str | None = None,
        stage: str | None = None,
        config_dir: Path | None = None,
        capability_registry: ModelCapabilityRegistry | None = None,
        session_store: Any | None = None,
        *,
        _instance: LLMClient | None = None,
    ) -> LLMClient:
        """Create or initialise an LLMClient from config string.

        Resolves all settings from ``config.toml``, detects the provider
        type, fetches model capabilities, and resolves API keys.  This
        is the preferred way to create an LLMClient when you have a
        model string or stage name.

        When ``_instance`` is provided (internal use by ``__init__``),
        the resolved values are applied to that instance instead of
        creating a new one.  This preserves backward compatibility with
        the ``LLMClient(model_string=...)`` calling convention.

        Parameters
        ----------
        model_string:
            Explicit ``provider:model`` string.  Takes priority.
        stage:
            Pipeline stage name (e.g. ``"planning"``).  Resolved via
            ``[models.stages]`` config, falls back to default.
        config_dir:
            Config directory override (default: ``~/.agent-nexus/``).
        capability_registry:
            Shared registry to avoid duplicate ModelDB fetches.
        session_store:
            Optional session store for conversation continuity.

        Returns:
            A fully initialised :class:`LLMClient`.
        """
        inst = _instance if _instance is not None else cls.__new__(cls)

        # Ensure resource-holding attrs are pre-initialised when creating a
        # fresh instance via from_config() (not via __init__ which already
        # sets them).  When _instance is provided, these are already set.
        if _instance is None:
            inst._cli_backend = None

        # Lazy imports to break circular import cycle:
        #   config.loader -> config.config_templates -> agency -> agency.llm_client -> config.loader
        from agent_nexus.platform.config.loader import ConfigLoader
        from agent_nexus.platform.config.model_config import ModelConfigManager
        from agent_nexus.platform.config.model_db import ModelDBClient

        loader = ConfigLoader(config_dir=config_dir)
        platform_config = loader.load_config()
        mgr = ModelConfigManager(platform_config)

        # Resolve model string: explicit > stage config > default
        resolved = (
            model_string
            or (mgr.resolve_stage_model(stage) if stage else None)
            or mgr.resolve_model(__name__)
        )

        if not resolved:
            raise ValueError(
                "No model string resolved -- set [models].default in config.toml "
                "or pass model_string explicitly"
            )

        inst._provider_name, inst._model_name = mgr.parse_model_string(resolved)
        inst._provider_config = mgr.get_provider_config(inst._provider_name)

        # Auto-detect CLI providers: if not in [models.providers.*], check [cli_backends.*]
        if (
            inst._provider_name not in platform_config.models.providers
            and inst._provider_name in loader.load_cli_backends()
        ):
            inst._provider_config = ProviderConfig(api=ProviderApiType.CLI)
        inst._session_store = session_store

        if inst._provider_config.api == ProviderApiType.CLI:
            inst._api_key = ""
            inst._cli_backend = inst._init_cli_backend(config_dir)
        else:
            inst._api_key = mgr.resolve_api_key(inst._provider_name)

            if not inst._api_key:
                raise ValueError(
                    f"API key for provider '{inst._provider_name}' is empty. "
                    f"Set the environment variable referenced in config.toml."
                )

        # CLI providers with no model name skip capability lookup -- the CLI
        # itself decides which model to use, so registry/ModelDB lookups are
        # unnecessary and just produce noisy warnings for empty model strings.
        is_cli_no_model = inst._provider_config.api == ProviderApiType.CLI and not inst._model_name

        if capability_registry is not None:
            inst._capability_registry = capability_registry
        else:
            inst._capability_registry = ModelCapabilityRegistry()

        if is_cli_no_model:
            inst._capability = ModelCapability(
                model_id="",
                provider="",
                max_output_tokens=0,
                context_window=0,
                supports_vision=False,
                supports_tool_use=False,
                supports_temperature=False,
                temperature_min=0.0,
                temperature_max=0.0,
                knowledge_cutoff="",
            )
        else:
            # Resolution order:
            #   1. Already enriched in shared registry -> reuse (no warning).
            #   2. ModelDB remote lookup -> build capability from remote data.
            #   3. Built-in registry.get() -> may warn (appropriate: both sources failed).
            if inst._capability_registry.is_enriched(inst._model_name):
                inst._capability = inst._capability_registry.get(inst._model_name)
            else:
                db_client = ModelDBClient()
                try:
                    remote_data = db_client.fetch_model(inst._model_name)
                except Exception:
                    logger.debug(
                        "ModelDB fetch failed, using built-in capability data",
                        exc_info=True,
                    )
                    remote_data = None
                finally:
                    db_client.close()

                if remote_data is not None:
                    cap_fallback = inst._capability_registry.get(
                        inst._model_name,
                    ) or inst._capability_registry.get_provider_default(
                        inst._provider_name,
                    )
                    enriched_cap = ModelCapability(
                        model_id=remote_data.get("id", cap_fallback.model_id),
                        provider=remote_data.get("provider", cap_fallback.provider),
                        max_output_tokens=remote_data.get(
                            "max_output_tokens", cap_fallback.max_output_tokens
                        ),
                        context_window=remote_data.get(
                            "context_window", cap_fallback.context_window
                        ),
                        supports_vision=remote_data.get(
                            "supports_vision", cap_fallback.supports_vision
                        ),
                        supports_tool_use=remote_data.get(
                            "supports_tool_use", cap_fallback.supports_tool_use
                        ),
                        supports_temperature=remote_data.get(
                            "supports_temperature", cap_fallback.supports_temperature
                        ),
                        temperature_min=remote_data.get(
                            "temperature_min", cap_fallback.temperature_min
                        ),
                        temperature_max=remote_data.get(
                            "temperature_max", cap_fallback.temperature_max
                        ),
                        knowledge_cutoff=remote_data.get(
                            "knowledge_cutoff", cap_fallback.knowledge_cutoff
                        ),
                    )
                    inst._capability_registry.set_override(
                        inst._model_name,
                        enriched_cap,
                    )
                    inst._capability = enriched_cap
                else:
                    inst._capability = inst._capability_registry.get(inst._model_name)

        logger.info(
            "LLMClient initialized: provider=%s model=%s api=%s max_output_tokens=%d",
            inst._provider_name,
            inst._model_name or "(cli)",
            inst._provider_config.api,
            inst._capability.max_output_tokens,
        )

        inst._platform_config = platform_config

        return inst

    def _init_cli_backend(self, config_dir: Path | None) -> Any:
        """Create a GenericCLIBackend using BackendConfig from config.toml."""
        from agent_nexus.platform.agency.cli_backend.base import GenericCLIBackend
        from agent_nexus.platform.agency.cli_backend.types import BackendConfig
        from agent_nexus.platform.config.loader import ConfigLoader

        loader = ConfigLoader(config_dir=config_dir)
        cli_backends = loader.load_cli_backends()
        if self._provider_name in cli_backends:
            config = cli_backends[self._provider_name]
        else:
            logger.warning(
                "CLI provider '%s' not found in config.toml, using minimal BackendConfig "
                "(command=%s). Output parsing may fail without json_paths/text_patterns config.",
                self._provider_name,
                self._provider_name,
            )
            config = BackendConfig(command=self._provider_name)
        return GenericCLIBackend(config)

    def close(self) -> None:
        """Close the underlying resources."""
        if self._cli_backend is not None:
            pass  # GenericCLIBackend has no resources to close

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            logger.debug("Error closing LLMClient in __del__", exc_info=True)

    def __enter__(self) -> LLMClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def capability(self) -> ModelCapability:
        """Return the capability record for the resolved model."""
        return self._capability

    @property
    def hooks(self) -> HookManager:
        """Access the hook manager for this client."""
        return self._hooks

    @property
    def supports_vision(self) -> bool:
        """Whether the resolved model supports vision/image inputs."""
        return self._capability.supports_vision

    # ------------------------------------------------------------------
    # LiteLLM unified calling layer
    # ------------------------------------------------------------------

    def _to_litellm_model(self) -> str:
        """Convert agent-nexus model format to litellm format.

        agent-nexus:  'anthropic:claude-sonnet-4-20250514'
        litellm:      'anthropic/claude-sonnet-4-20250514'

        agent-nexus:  'api:MiniMax-M2.7-highspeed'
        litellm:      'openai/MiniMax-M2.7-highspeed'  (routed via api_base)
        """
        provider = self._provider_name
        model = self._model_name
        litellm_provider = self._LITELLM_PROVIDER_MAP.get(provider, "openai")
        return f"{litellm_provider}/{model}"

    def _build_litellm_kwargs(
        self,
        ctx: CallContext,
        max_tokens: int | None,
        top_p: float | None,
    ) -> dict[str, Any]:
        """Build the kwargs dict for ``litellm.completion()``."""
        messages: list[dict[str, str]] = [
            {"role": "system", "content": ctx.system_prompt},
            {"role": "user", "content": ctx.user_message},
        ]

        kwargs: dict[str, Any] = {
            "model": self._to_litellm_model(),
            "messages": messages,
            "stream": self._capability.streaming_default
            if hasattr(self._capability, "streaming_default")
            else False,
        }

        effective_max_tokens = max_tokens or self._capability.max_output_tokens
        if effective_max_tokens:
            kwargs["max_tokens"] = effective_max_tokens

        if ctx.temperature is not None:
            if self._capability.supports_temperature:
                kwargs["temperature"] = max(
                    self._capability.temperature_min,
                    min(self._capability.temperature_max, ctx.temperature),
                )
            else:
                logger.warning(
                    "Model '%s' does not support temperature -- ignoring",
                    self._model_name,
                )

        if top_p is not None:
            kwargs["top_p"] = max(0.0, min(1.0, top_p))

        if ctx.timeout is not None:
            kwargs["timeout"] = ctx.timeout

        # API key + base_url: LiteLLM accepts these as parameters
        if self._api_key:
            kwargs["api_key"] = self._api_key
        base_url = self._provider_config.base_url or None
        if base_url:
            kwargs["api_base"] = base_url

        return kwargs

    # ------------------------------------------------------------------
    # Public call() entry point
    # ------------------------------------------------------------------

    def call(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        timeout: float | None = None,
        session_id: str | None = None,
        response_format: Literal["json"] | None = None,
    ) -> LLMResponse:
        """Call the LLM and return a structured response.

        Parameters
        ----------
        system_prompt:
            System instructions for the model.
        user_message:
            The user's message / task.
        max_tokens:
            Override max output tokens.  Defaults to the model's
            ``max_output_tokens`` from capability data.
        temperature:
            Sampling temperature.  When ``None`` the model's default is used.
        top_p:
            Nucleus sampling threshold.  When ``None`` the model's default is used.
        timeout:
            Override request timeout in seconds.
        response_format:
            When ``"json"``, requests the provider to enforce JSON output.

        Returns
        -------
        LLMResponse
        """
        import time

        ctx = CallContext(
            model=self._model_name,
            system_prompt=system_prompt,
            user_message=user_message,
            temperature=temperature,
            response_format=response_format,
            timeout=timeout,
        )
        self._hooks.dispatch(HookEvent.BEFORE_CALL, ctx=ctx)

        start = time.monotonic()
        try:
            cli_resp: LLMResponse | None = None
            if self._provider_config.api == ProviderApiType.CLI:
                cli_resp = self._call_cli(
                    ctx.system_prompt, ctx.user_message, session_id, ctx.timeout
                )
                text, actual_model = cli_resp.text, cli_resp.model
            else:
                # API Provider -- unified LiteLLM path
                kwargs = self._build_litellm_kwargs(ctx, max_tokens, top_p)

                if response_format == "json":
                    kwargs["response_format"] = {"type": "json_object"}

                logger.info(
                    "LLMClient.call: litellm model=%s provider=%s",
                    kwargs["model"],
                    self._provider_name,
                )

                response = litellm.completion(**kwargs)
                text = response.choices[0].message.content or ""
                actual_model = response.model or self._model_name

            self._update_capability_from_response(actual_model)

            latency_ms = (time.monotonic() - start) * 1000
            result = CallResult(
                content=text,
                model=actual_model,
                input_tokens=None,
                output_tokens=None,
                latency_ms=latency_ms,
            )
            self._hooks.dispatch(HookEvent.AFTER_CALL, ctx=ctx, result=result)

            if cli_resp is not None:
                return LLMResponse(
                    text=text,
                    model=actual_model,
                    provider=self._provider_name,
                    metadata=cli_resp.metadata,
                )
            return LLMResponse(
                text=text,
                model=actual_model,
                provider=self._provider_name,
            )
        except Exception as e:
            self._hooks.dispatch(HookEvent.ON_ERROR, ctx=ctx, error=e)
            raise

    # ------------------------------------------------------------------
    # CLI Backend (preserved unchanged)
    # ------------------------------------------------------------------

    def _call_cli(
        self,
        system_prompt: str,
        user_message: str,
        session_id: str | None,
        timeout: float | None,
    ) -> LLMResponse:
        """Execute a CLI backend call and return an LLMResponse."""
        if self._cli_backend is None:
            raise RuntimeError("CLI backend not available")
        backend = self._cli_backend

        result = backend.call(
            system_prompt,
            user_message,
            session_id=session_id,
            timeout=timeout,
        )

        if result.returncode != 0:
            raise LLMCallError(
                f"CLI backend '{backend.name}' exited with code {result.returncode}: "
                f"{result.raw_stderr[:500]}"
            )

        status = "success" if result.text else "empty_response"

        if self._session_store is not None:
            from agent_nexus.platform.agency.cli_backend.types import CLISessionRecord

            self._session_store.record_execution(
                task_id="",
                backend_type="cli",
                backend_name=backend.name,
                model=result.model or self._model_name,
                session_id=result.session_id,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                duration_ms=result.duration_ms,
                status=status,
            )
            if result.session_id:
                self._session_store.save_session(
                    CLISessionRecord(
                        session_id=result.session_id,
                        backend_name=backend.name,
                        model=result.model or self._model_name,
                    )
                )

        return LLMResponse(
            text=result.text,
            model=result.model or self._model_name,
            provider=self._provider_name,
            metadata={
                "session_id": result.session_id,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
            },
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _update_capability_from_response(self, actual_model: str) -> None:
        """Enrich capability data when the API returns a different model name."""
        if actual_model == self._model_name:
            return
        if self._capability_registry.is_enriched(actual_model):
            return
        try:
            real_cap = self._capability_registry.get(actual_model)
            if real_cap.model_id.startswith("__"):
                return  # provider default, not a real match
            self._capability_registry.set_override(self._model_name, real_cap)
            self._capability = real_cap
            logger.info(
                "Capability updated: '%s' -> real model '%s' (max_output_tokens=%d)",
                self._model_name,
                actual_model,
                real_cap.max_output_tokens,
            )
        except Exception:
            logger.debug(
                "Failed to update capability from response model '%s'",
                actual_model,
                exc_info=True,
            )
