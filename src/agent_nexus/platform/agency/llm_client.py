"""LLMClient — shared LLM API caller for the agency pipeline.

Extracts the httpx-based API calling logic from LLMExecutor so that
LLMPlanner, LLMIntegrator, and LLMQualityGate can all reuse it with
different model strings and prompts.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx

try:
    import anthropic
except ImportError:
    anthropic = None  # type: ignore[assignment]

try:
    import openai
except ImportError:
    openai = None  # type: ignore[assignment]

from agent_nexus.models.capability import ModelCapability, ModelCapabilityRegistry
from agent_nexus.models.config import ProviderApiType, ProviderConfig
from agent_nexus.models.errors import AgentNexusError

logger = logging.getLogger(__name__)


class LLMCallError(AgentNexusError):
    """Raised when an LLM call fails (API error, CLI exit, timeout)."""


_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0  # seconds


@dataclass
class LLMResponse:
    """Structured response from an LLM call."""

    text: str
    model: str
    provider: str
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class _LLMCallParams:
    """Shared parameters for internal LLM call methods (data clump extraction)."""

    system_prompt: str
    user_message: str
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    timeout: float | None = None
    response_format: str | None = None


class LLMClient:
    """Reusable LLM API client supporting Anthropic and OpenAI formats.

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
        self._http_client: httpx.Client | None = None
        self._openai_sdk = None
        self._anthropic_sdk = None
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
            inst._http_client = None
            inst._openai_sdk = None
            inst._anthropic_sdk = None
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
                "No model string resolved — set [models].default in config.toml "
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

        # CLI providers with no model name skip capability lookup — the CLI
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
            #   1. Already enriched in shared registry → reuse (no warning).
            #   2. ModelDB remote lookup → build capability from remote data.
            #   3. Built-in registry.get() → may warn (appropriate: both sources failed).
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

    def _get_http_client(self) -> httpx.Client:
        """Return the persistent httpx.Client, creating it on first use."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.Client(timeout=self._TIMEOUT)
        return self._http_client

    def _get_openai_sdk(self):
        """Lazy-initialise and cache the OpenAI SDK client."""
        if openai is None:
            raise ImportError("openai package not installed")
        if self._openai_sdk is None:
            base_url = self._provider_config.base_url or None
            self._openai_sdk = openai.OpenAI(
                api_key=self._api_key,
                base_url=base_url,
            )
        return self._openai_sdk

    def _get_anthropic_sdk(self):
        """Lazy-initialise and cache the Anthropic SDK client."""
        if anthropic is None:
            raise ImportError("anthropic package not installed")
        if self._anthropic_sdk is None:
            base_url = self._provider_config.base_url or None
            self._anthropic_sdk = anthropic.Anthropic(
                api_key=self._api_key,
                base_url=base_url,
            )
        return self._anthropic_sdk

    def close(self) -> None:
        """Close the underlying HTTP client and SDK clients."""
        if self._cli_backend is not None:
            pass  # GenericCLIBackend has no resources to close
        if self._http_client is not None and not self._http_client.is_closed:
            self._http_client.close()
            self._http_client = None
        if self._openai_sdk is not None:
            self._openai_sdk.close()
            self._openai_sdk = None
        if self._anthropic_sdk is not None:
            self._anthropic_sdk.close()
            self._anthropic_sdk = None

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
    def supports_vision(self) -> bool:
        """Whether the resolved model supports vision/image inputs."""
        return self._capability.supports_vision

    @staticmethod
    def _is_retryable(status_code: int) -> bool:
        """Return True for transient HTTP status codes that warrant retry."""
        return status_code == 429 or status_code >= 500

    async def _call_with_retry(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: float | None,
        label: str,
    ) -> httpx.Response:
        """Execute a POST with exponential-backoff retry on transient errors."""
        client = self._get_http_client()
        effective_timeout = timeout or self._TIMEOUT
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = client.post(url, json=payload, headers=headers, timeout=effective_timeout)
                if resp.status_code == 200 or not self._is_retryable(resp.status_code):
                    return resp
                last_exc = RuntimeError(
                    f"{label} API call failed (status {resp.status_code}): {resp.text[:500]}"
                )
                logger.warning(
                    "%s: transient error %d, retry %d/%d",
                    label,
                    resp.status_code,
                    attempt + 1,
                    _MAX_RETRIES,
                )
            except httpx.TransportError as exc:
                last_exc = exc
                logger.warning(
                    "%s: transport error, retry %d/%d: %s",
                    label,
                    attempt + 1,
                    _MAX_RETRIES,
                    exc,
                )
            # Exponential backoff: 1s, 2s, 4s (skip on last attempt)
            if attempt < _MAX_RETRIES - 1:
                delay = _RETRY_BASE_DELAY * (2**attempt)
                await asyncio.sleep(delay)
        raise last_exc or RuntimeError(f"{label}: all retries exhausted")

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
            OpenAI-compatible APIs set ``response_format: {"type": "json_object"}``.
            Anthropic uses a prefill assistant message to guide JSON output.

        Returns
        -------
        LLMResponse
        """
        if self._provider_config.api == ProviderApiType.CLI:
            return self._call_cli(system_prompt, user_message, session_id, timeout)

        params = _LLMCallParams(
            system_prompt=system_prompt,
            user_message=user_message,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            timeout=timeout,
            response_format=response_format,
        )

        if self._provider_config.api == ProviderApiType.ANTHROPIC_MESSAGES:
            text, actual_model = self._call_anthropic(params)
        else:
            text, actual_model = self._call_openai(params)

        self._update_capability_from_response(actual_model)

        return LLMResponse(
            text=text,
            model=actual_model,
            provider=self._provider_name,
        )

    def _apply_sampling_params(
        self,
        payload: dict[str, Any],
        temperature: float | None,
        top_p: float | None,
    ) -> None:
        """Apply temperature and top_p to *payload* in-place."""
        if temperature is not None:
            if self._capability.supports_temperature:
                payload["temperature"] = max(
                    self._capability.temperature_min,
                    min(self._capability.temperature_max, temperature),
                )
            else:
                logger.warning(
                    "Model '%s' does not support temperature — ignoring",
                    self._model_name,
                )
        if top_p is not None:
            payload["top_p"] = max(0.0, min(1.0, top_p))

    def _should_stream(self) -> bool:
        """Resolve streaming mode: provider config -> global default -> True."""
        if self._provider_config.streaming is not None:
            return self._provider_config.streaming
        return self._platform_config.models.streaming_default

    @staticmethod
    def _restore_json_prefill(text: str, is_json: bool) -> str:
        """Restore the JSON prefill '{' if Anthropic omitted it from the response."""
        if is_json and text and not text.lstrip().startswith("{"):
            return "{" + text
        return text

    @staticmethod
    def _build_anthropic_messages(p: _LLMCallParams) -> list[dict[str, Any]]:
        """Build Anthropic-format message list with optional JSON prefill."""
        messages: list[dict[str, Any]] = [{"role": "user", "content": p.user_message}]
        if p.response_format == "json":
            messages.append({"role": "assistant", "content": "{"})
        return messages

    def _build_anthropic_base_kwargs(self, p: _LLMCallParams) -> dict[str, Any]:
        """Build Anthropic base kwargs dict (model, max_tokens, system, messages).

        Sampling params (temperature, top_p) and timeout are added to the
        returned dict but are *not* applied to the SDK streaming calls that
        pass them separately.
        """
        messages = self._build_anthropic_messages(p)
        kwargs: dict[str, Any] = {
            "model": self._model_name,
            "max_tokens": p.max_tokens or self._capability.max_output_tokens,
            "system": p.system_prompt,
            "messages": messages,
        }
        if p.temperature is not None and self._capability.supports_temperature:
            kwargs["temperature"] = max(
                self._capability.temperature_min,
                min(self._capability.temperature_max, p.temperature),
            )
        if p.top_p is not None:
            kwargs["top_p"] = max(0.0, min(1.0, p.top_p))
        if p.timeout is not None:
            kwargs["timeout"] = p.timeout
        return kwargs

    @staticmethod
    def _build_openai_messages(p: _LLMCallParams) -> list[dict[str, Any]]:
        """Build OpenAI-format message list (system + user)."""
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": p.system_prompt},
            {"role": "user", "content": p.user_message},
        ]
        return messages

    def _build_openai_base_kwargs(self, p: _LLMCallParams) -> dict[str, Any]:
        """Build OpenAI base kwargs dict (model, messages, max_tokens).

        Sampling params and response_format are included.
        """
        messages = self._build_openai_messages(p)
        kwargs: dict[str, Any] = {
            "model": self._model_name,
            "messages": messages,
            "max_tokens": p.max_tokens or self._capability.max_output_tokens,
        }
        if p.temperature is not None and self._capability.supports_temperature:
            kwargs["temperature"] = max(
                self._capability.temperature_min,
                min(self._capability.temperature_max, p.temperature),
            )
        if p.top_p is not None:
            kwargs["top_p"] = max(0.0, min(1.0, p.top_p))
        if p.response_format == "json":
            kwargs["response_format"] = {"type": "json_object"}
        if p.timeout is not None:
            kwargs["timeout"] = p.timeout
        return kwargs

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
                "Capability updated: '%s' → real model '%s' (max_output_tokens=%d)",
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

    def _call_anthropic(self, p: _LLMCallParams) -> tuple[str, str]:
        use_stream = self._should_stream()

        try:
            sdk = self._get_anthropic_sdk()
        except Exception:
            logger.warning("Anthropic SDK init failed, falling back to httpx", exc_info=True)
            return self._call_anthropic_raw(p)

        logger.info(
            "LLMClient._call_anthropic: model=%s stream=%s provider=%s",
            self._model_name,
            use_stream,
            self._provider_name,
        )

        kwargs = self._build_anthropic_base_kwargs(p)

        def _extract_text_delta(delta: Any) -> str:
            """Extract text from a content_block_delta, skipping ThinkingDelta."""
            return getattr(delta, "text", "") or ""

        if use_stream:
            text_parts: list[str] = []
            actual_model = self._model_name
            try:
                with sdk.messages.create(stream=True, **kwargs) as stream:
                    for event in stream:
                        if event.type == "message_start":
                            actual_model = event.message.model or self._model_name
                        elif event.type == "content_block_delta":
                            text_parts.append(_extract_text_delta(event.delta))
            except Exception:
                if not text_parts:
                    raise
                logger.warning(
                    "Anthropic stream interrupted, returning partial text (%d chars)",
                    len("".join(text_parts)),
                )
            text = "".join(text_parts)
        else:
            try:
                resp = sdk.messages.create(**kwargs)
                actual_model = resp.model or self._model_name
                text = "".join(block.text for block in resp.content if block.type == "text")
            except ValueError as exc:
                if "Streaming" not in str(exc) and "streaming" not in str(exc).lower():
                    raise
                logger.debug(
                    "Anthropic SDK requires streaming for this request, switching to stream mode"
                )
                text_parts: list[str] = []
                actual_model = self._model_name
                try:
                    with sdk.messages.create(stream=True, **kwargs) as stream:
                        for event in stream:
                            if event.type == "message_start":
                                actual_model = event.message.model or self._model_name
                            elif event.type == "content_block_delta":
                                text_parts.append(_extract_text_delta(event.delta))
                except Exception:
                    if not text_parts:
                        raise
                    logger.warning(
                        "Anthropic stream interrupted, returning partial text (%d chars)",
                        len("".join(text_parts)),
                    )
                text = "".join(text_parts)

        text = self._restore_json_prefill(text, p.response_format == "json")
        return text, actual_model

    def _call_anthropic_raw(self, p: _LLMCallParams) -> tuple[str, str]:
        """Fallback httpx-based Anthropic call (used when SDK init fails)."""
        base_url = self._provider_config.base_url.rstrip("/")
        url = f"{base_url}/v1/messages"

        headers: dict[str, str] = {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        payload = self._build_anthropic_base_kwargs(p)
        self._apply_sampling_params(payload, p.temperature, p.top_p)

        # NOTE: asyncio.run() requires no running event loop in the current thread.
        # Safe because all callers (LLMPlanner, LLMExecutor, LLMIntegrator, LLMQualityGate)
        # invoke LLMClient.call() synchronously. If async callers are added, use
        # httpx.AsyncClient and await _call_with_retry directly instead of asyncio.run().
        resp = asyncio.run(self._call_with_retry(url, headers, payload, p.timeout, "Anthropic"))

        data = resp.json()
        actual_model: str = data.get("model", self._model_name)
        content_blocks = data.get("content", [])
        text = "".join(
            block.get("text", "") for block in content_blocks if block.get("type") == "text"
        )
        text = self._restore_json_prefill(text, p.response_format == "json")
        return text, actual_model

    def _call_openai(self, p: _LLMCallParams) -> tuple[str, str]:
        use_stream = self._should_stream()

        try:
            sdk = self._get_openai_sdk()
        except Exception:
            logger.warning("OpenAI SDK init failed, falling back to httpx", exc_info=True)
            return self._call_openai_raw(p)

        logger.info(
            "LLMClient._call_openai: model=%s stream=%s provider=%s",
            self._model_name,
            use_stream,
            self._provider_name,
        )

        kwargs = self._build_openai_base_kwargs(p)

        if use_stream:
            text_parts: list[str] = []
            actual_model = self._model_name
            try:
                with sdk.chat.completions.create(stream=True, **kwargs) as stream:
                    for chunk in stream:
                        if chunk.model:
                            actual_model = chunk.model
                        delta = chunk.choices[0].delta if chunk.choices else None
                        if delta and delta.content:
                            text_parts.append(delta.content)
            except Exception:
                if not text_parts:
                    raise
                logger.warning(
                    "OpenAI stream interrupted, returning partial text (%d chars)",
                    len("".join(text_parts)),
                )
            return "".join(text_parts), actual_model
        else:
            resp = sdk.chat.completions.create(**kwargs)
            actual_model = resp.model or self._model_name
            content = resp.choices[0].message.content if resp.choices else ""
            return content or "", actual_model

    def _call_openai_raw(self, p: _LLMCallParams) -> tuple[str, str]:
        """Fallback httpx-based OpenAI call (used when SDK init fails)."""
        base_url = self._provider_config.base_url.rstrip("/")
        url = f"{base_url}/v1/chat/completions"

        headers: dict[str, str] = {
            "Authorization": f"Bearer {self._api_key}",
            "content-type": "application/json",
        }

        payload = self._build_openai_base_kwargs(p)
        self._apply_sampling_params(payload, p.temperature, p.top_p)

        if p.response_format == "json":
            payload["response_format"] = {"type": "json_object"}

        # Same asyncio.run() bridge as _call_anthropic_raw — see note there.
        resp = asyncio.run(self._call_with_retry(url, headers, payload, p.timeout, "OpenAI"))

        data = resp.json()
        actual_model: str = data.get("model", self._model_name)
        choices = data.get("choices", [])
        if choices:
            return choices[0].get("message", {}).get("content", ""), actual_model
        return "", actual_model
