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
        self._api_key = mgr.resolve_api_key(self._provider_name)

        if not self._api_key:
            raise ValueError(
                f"API key for provider '{self._provider_name}' is empty. "
                f"Set the environment variable referenced in config.toml."
            )

        # Load model capability data (replaces hardcoded 4096 max_tokens)
        if capability_registry is not None:
            self._capability_registry = capability_registry
        else:
            self._capability_registry = ModelCapabilityRegistry()
        self._capability = self._capability_registry.get(self._model_name)

        logger.info(
            "LLMClient initialized: provider=%s model=%s api=%s max_output_tokens=%d",
            self._provider_name, self._model_name, self._provider_config.api,
            self._capability.max_output_tokens,
        )

        # Enrich from models.dev — when a shared registry is in use, update it
        # in-place so other clients see the enriched data without re-fetching.
        if self._capability_registry.is_enriched(self._model_name):
            logger.debug(
                "Model '%s' already enriched in shared registry, skipping fetch",
                self._model_name,
            )
            self._capability = self._capability_registry.get(self._model_name)
        else:
            db_client = ModelDBClient()
            try:
                remote_data = db_client.fetch_model(self._model_name)
                if remote_data is not None:
                    cap = self._capability
                    enriched_cap = ModelCapability(
                        model_id=remote_data.get("id", cap.model_id),
                        provider=remote_data.get("provider", cap.provider),
                        max_output_tokens=remote_data.get(
                            "max_output_tokens", cap.max_output_tokens
                        ),
                        context_window=remote_data.get(
                            "context_window", cap.context_window
                        ),
                        supports_vision=remote_data.get(
                            "supports_vision", cap.supports_vision
                        ),
                        supports_tool_use=remote_data.get(
                            "supports_tool_use", cap.supports_tool_use
                        ),
                        supports_temperature=remote_data.get(
                            "supports_temperature", cap.supports_temperature
                        ),
                        temperature_min=remote_data.get(
                            "temperature_min", cap.temperature_min
                        ),
                        temperature_max=remote_data.get(
                            "temperature_max", cap.temperature_max
                        ),
                        knowledge_cutoff=remote_data.get(
                            "knowledge_cutoff", cap.knowledge_cutoff
                        ),
                    )
                    self._capability_registry.set_override(
                        self._model_name, enriched_cap,
                    )
                    self._capability = enriched_cap
            except Exception:
                logger.debug(
                    "ModelDB enrichment failed, using built-in capability data",
                    exc_info=True,
                )
            finally:
                db_client.close()

        # Lazy-initialised persistent httpx.Client for connection reuse
        self._http_client: httpx.Client | None = None

    def _get_http_client(self) -> httpx.Client:
        """Return the persistent httpx.Client, creating it on first use."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.Client(timeout=self._TIMEOUT)
        return self._http_client

    def close(self) -> None:
        """Close the underlying HTTP client."""
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

        Returns
        -------
        LLMResponse
        """
        if self._provider_config.api == ProviderApiType.ANTHROPIC_MESSAGES:
            text, actual_model = self._call_anthropic(
                system_prompt, user_message,
                max_tokens, temperature, top_p, timeout,
            )
        else:
            text, actual_model = self._call_openai(
                system_prompt, user_message,
                max_tokens, temperature, top_p, timeout,
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

    def _call_anthropic(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: int | None,
        temperature: float | None,
        top_p: float | None,
        timeout: float | None,
    ) -> tuple[str, str]:
        base_url = self._provider_config.base_url.rstrip("/")
        url = f"{base_url}/v1/messages"

        headers: dict[str, str] = {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        payload: dict[str, Any] = {
            "model": self._model_name,
            "max_tokens": max_tokens or self._capability.max_output_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_message}],
        }
        self._apply_sampling_params(payload, temperature, top_p)

        resp = self._call_with_retry(url, headers, payload, timeout, "Anthropic")

        data = resp.json()
        actual_model: str = data.get("model", self._model_name)
        content_blocks = data.get("content", [])
        text = "".join(
            block.get("text", "") for block in content_blocks if block.get("type") == "text"
        )
        return text, actual_model

    def _call_openai(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: int | None,
        temperature: float | None,
        top_p: float | None,
        timeout: float | None,
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

        resp = self._call_with_retry(url, headers, payload, timeout, "OpenAI")

        data = resp.json()
        actual_model: str = data.get("model", self._model_name)
        choices = data.get("choices", [])
        if choices:
            return choices[0].get("message", {}).get("content", ""), actual_model
        return "", actual_model
