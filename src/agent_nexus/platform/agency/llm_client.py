"""LLMClient — shared LLM API caller for the agency pipeline.

Extracts the httpx-based API calling logic from LLMExecutor so that
LLMPlanner, LLMIntegrator, and LLMQualityGate can all reuse it with
different model strings and prompts.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from agent_nexus.models.capability import ModelCapability, ModelCapabilityRegistry
from agent_nexus.models.config import ProviderApiType
from agent_nexus.platform.config.loader import ConfigLoader
from agent_nexus.platform.config.model_config import ModelConfigManager
from agent_nexus.platform.config.model_db import ModelDBClient

logger = logging.getLogger(__name__)


class LLMCallError(Exception):
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
        """Initialise the client.

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
        loader = ConfigLoader(config_dir=config_dir)
        platform_config = loader.load_config()
        mgr = ModelConfigManager(platform_config)

        # Resolve model string: explicit > stage config > default
        resolved = model_string or (
            mgr.resolve_stage_model(stage) if stage else None
        ) or mgr.resolve_model(__name__)

        if not resolved:
            raise ValueError(
                "No model string resolved — set [models].default in config.toml "
                "or pass model_string explicitly"
            )

        self._provider_name, self._model_name = mgr.parse_model_string(resolved)
        self._provider_config = mgr.get_provider_config(self._provider_name)
        self._session_store = session_store
        self._cli_backend = None

        if self._provider_config.api == ProviderApiType.CLI:
            self._api_key = ""
            self._cli_backend = self._init_cli_backend(config_dir)
        else:
            self._api_key = mgr.resolve_api_key(self._provider_name)

            if not self._api_key:
                raise ValueError(
                    f"API key for provider '{self._provider_name}' is empty. "
                    f"Set the environment variable referenced in config.toml."
                )

        # CLI providers with no model name skip capability lookup — the CLI
        # itself decides which model to use, so registry/ModelDB lookups are
        # unnecessary and just produce noisy warnings for empty model strings.
        is_cli_no_model = (
            self._provider_config.api == ProviderApiType.CLI
            and not self._model_name
        )

        if capability_registry is not None:
            self._capability_registry = capability_registry
        else:
            self._capability_registry = ModelCapabilityRegistry()

        if is_cli_no_model:
            # CLI backends manage their own model params — this capability
            # is never consumed (call() short-circuits to _call_cli).
            self._capability = ModelCapability(
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
            if self._capability_registry.is_enriched(self._model_name):
                self._capability = self._capability_registry.get(self._model_name)
            else:
                db_client = ModelDBClient()
                try:
                    remote_data = db_client.fetch_model(self._model_name)
                except Exception:
                    logger.debug(
                        "ModelDB fetch failed, using built-in capability data",
                        exc_info=True,
                    )
                    remote_data = None
                finally:
                    db_client.close()

                if remote_data is not None:
                    cap_default = self._capability_registry.get_provider_default(
                        self._provider_name,
                    )
                    enriched_cap = ModelCapability(
                        model_id=remote_data.get("id", cap_default.model_id),
                        provider=remote_data.get("provider", cap_default.provider),
                        max_output_tokens=remote_data.get(
                            "max_output_tokens", cap_default.max_output_tokens
                        ),
                        context_window=remote_data.get(
                            "context_window", cap_default.context_window
                        ),
                        supports_vision=remote_data.get(
                            "supports_vision", cap_default.supports_vision
                        ),
                        supports_tool_use=remote_data.get(
                            "supports_tool_use", cap_default.supports_tool_use
                        ),
                        supports_temperature=remote_data.get(
                            "supports_temperature", cap_default.supports_temperature
                        ),
                        temperature_min=remote_data.get(
                            "temperature_min", cap_default.temperature_min
                        ),
                        temperature_max=remote_data.get(
                            "temperature_max", cap_default.temperature_max
                        ),
                        knowledge_cutoff=remote_data.get(
                            "knowledge_cutoff", cap_default.knowledge_cutoff
                        ),
                    )
                    self._capability_registry.set_override(
                        self._model_name, enriched_cap,
                    )
                    self._capability = enriched_cap
                else:
                    self._capability = self._capability_registry.get(self._model_name)

        logger.info(
            "LLMClient initialized: provider=%s model=%s api=%s max_output_tokens=%d",
            self._provider_name, self._model_name or "(cli)", self._provider_config.api,
            self._capability.max_output_tokens,
        )

        # Store platform config for streaming resolution
        self._platform_config = platform_config

        # Lazy-initialised persistent httpx.Client for connection reuse
        self._http_client: httpx.Client | None = None

    def _init_cli_backend(self, config_dir: Path | None) -> Any:
        """Create a GenericCLIBackend using BackendConfig from config.toml."""
        from agent_nexus.platform.agency.cli_backend.base import GenericCLIBackend
        from agent_nexus.platform.agency.cli_backend.types import BackendConfig

        loader = ConfigLoader(config_dir=config_dir)
        cli_backends = loader.load_cli_backends()
        if self._provider_name in cli_backends:
            config = cli_backends[self._provider_name]
        else:
            logger.warning(
                "CLI provider '%s' not found in config.toml, using minimal BackendConfig "
                "(command=%s). Output parsing may fail without json_paths/text_patterns config.",
                self._provider_name, self._provider_name,
            )
            config = BackendConfig(command=self._provider_name)
        return GenericCLIBackend(config)

    def _get_http_client(self) -> httpx.Client:
        """Return the persistent httpx.Client, creating it on first use."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.Client(timeout=self._TIMEOUT)
        return self._http_client

    def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._cli_backend is not None:
            pass  # GenericCLIBackend has no resources to close
        if self._http_client is not None and not self._http_client.is_closed:
            self._http_client.close()
            self._http_client = None

    def __del__(self) -> None:
        self.close()

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

    def _call_with_retry(
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
                    f"{label} API call failed (status {resp.status_code}): "
                    f"{resp.text[:500]}"
                )
                logger.warning(
                    "%s: transient error %d, retry %d/%d",
                    label, resp.status_code, attempt + 1, _MAX_RETRIES,
                )
            except httpx.TransportError as exc:
                last_exc = exc
                logger.warning(
                    "%s: transport error, retry %d/%d: %s",
                    label, attempt + 1, _MAX_RETRIES, exc,
                )
            # Exponential backoff: 1s, 2s, 4s
            delay = _RETRY_BASE_DELAY * (2 ** attempt)
            time.sleep(delay)
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
        response_format: str | None = None,
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

        if self._provider_config.api == ProviderApiType.ANTHROPIC_MESSAGES:
            text, actual_model = self._call_anthropic(
                system_prompt, user_message,
                max_tokens, temperature, top_p, timeout, response_format,
            )
        else:
            text, actual_model = self._call_openai(
                system_prompt, user_message,
                max_tokens, temperature, top_p, timeout, response_format,
            )

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
            if self._capability.supports_temperature:
                payload["top_p"] = max(0.0, min(1.0, top_p))
            else:
                logger.warning(
                    "Model '%s' does not support top_p — ignoring",
                    self._model_name,
                )

    def _should_stream(self) -> bool:
        """Resolve streaming mode: provider config -> global default -> True."""
        if self._provider_config.streaming is not None:
            return self._provider_config.streaming
        return self._platform_config.streaming_default

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
                self._model_name, actual_model, real_cap.max_output_tokens,
            )
        except Exception:
            logger.debug(
                "Failed to update capability from response model '%s'",
                actual_model, exc_info=True,
            )

    def _call_cli(
        self,
        system_prompt: str,
        user_message: str,
        session_id: str | None,
        timeout: float | None,
    ) -> LLMResponse:
        """Execute a CLI backend call and return an LLMResponse."""
        result = self._cli_backend.call(
            system_prompt, user_message,
            session_id=session_id, timeout=timeout,
        )

        if result.returncode != 0:
            raise LLMCallError(
                f"CLI backend '{self._cli_backend.name}' exited with code {result.returncode}: "
                f"{result.raw_stderr[:500]}"
            )

        status = "success" if result.text else "empty_response"

        if self._session_store is not None:
            from agent_nexus.platform.agency.cli_backend.types import CLISessionRecord

            self._session_store.record_execution(
                task_id="",
                backend_type="cli",
                backend_name=self._cli_backend.name,
                model=result.model or self._model_name,
                session_id=result.session_id,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                duration_ms=result.duration_ms,
                status=status,
            )
            if result.session_id:
                self._session_store.save_session(CLISessionRecord(
                    session_id=result.session_id,
                    backend_name=self._cli_backend.name,
                    model=result.model or self._model_name,
                ))

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

    def _call_anthropic(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: int | None,
        temperature: float | None,
        top_p: float | None,
        timeout: float | None,
        response_format: str | None = None,
    ) -> tuple[str, str]:
        base_url = self._provider_config.base_url.rstrip("/")
        url = f"{base_url}/v1/messages"

        headers: dict[str, str] = {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]

        # Prefill trick for JSON: start assistant response with "{" to
        # strongly guide the model into producing a JSON object.
        if response_format == "json":
            messages.append({"role": "assistant", "content": "{"})

        payload: dict[str, Any] = {
            "model": self._model_name,
            "max_tokens": max_tokens or self._capability.max_output_tokens,
            "system": system_prompt,
            "messages": messages,
        }
        self._apply_sampling_params(payload, temperature, top_p)

        resp = self._call_with_retry(url, headers, payload, timeout, "Anthropic")

        data = resp.json()
        actual_model: str = data.get("model", self._model_name)
        content_blocks = data.get("content", [])
        text = "".join(
            block.get("text", "") for block in content_blocks if block.get("type") == "text"
        )
        # Restore the "{" we prefilled — Anthropic strips it from the response
        if response_format == "json" and text and not text.startswith("{"):
            text = "{" + text
        return text, actual_model

    def _call_openai(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: int | None,
        temperature: float | None,
        top_p: float | None,
        timeout: float | None,
        response_format: str | None = None,
    ) -> tuple[str, str]:
        base_url = self._provider_config.base_url.rstrip("/")
        url = f"{base_url}/v1/chat/completions"

        headers: dict[str, str] = {
            "Authorization": f"Bearer {self._api_key}",
            "content-type": "application/json",
        }

        payload: dict[str, Any] = {
            "model": self._model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "max_tokens": max_tokens or self._capability.max_output_tokens,
        }
        self._apply_sampling_params(payload, temperature, top_p)

        if response_format == "json":
            payload["response_format"] = {"type": "json_object"}

        resp = self._call_with_retry(url, headers, payload, timeout, "OpenAI")

        data = resp.json()
        actual_model: str = data.get("model", self._model_name)
        choices = data.get("choices", [])
        if choices:
            return choices[0].get("message", {}).get("content", ""), actual_model
        return "", actual_model
