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

from agent_nexus.models.config import ProviderApiType
from agent_nexus.platform.config.loader import ConfigLoader
from agent_nexus.platform.config.model_config import ModelConfigManager

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
    _MAX_TOKENS = 4096

    def __init__(
        self,
        model_string: str | None = None,
        stage: str | None = None,
        config_dir: Path | None = None,
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

        # Lazy-initialised persistent httpx.Client for connection reuse
        self._http_client: httpx.Client | None = None

        logger.info(
            "LLMClient initialized: provider=%s model=%s api=%s",
            self._provider_name, self._model_name, self._provider_config.api,
        )

    def _get_http_client(self, timeout: float | None = None) -> httpx.Client:
        """Return the persistent httpx.Client, creating it on first use."""
        effective_timeout = timeout or self._TIMEOUT
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.Client(timeout=effective_timeout)
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
        client = self._get_http_client(timeout)
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = client.post(url, json=payload, headers=headers)
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
            Override max output tokens.
        timeout:
            Override request timeout in seconds.

        Returns
        -------
        LLMResponse
        """
        if self._provider_config.api == ProviderApiType.ANTHROPIC_MESSAGES:
            text = self._call_anthropic(system_prompt, user_message, max_tokens, timeout)
        else:
            text = self._call_openai(system_prompt, user_message, max_tokens, timeout)

        return LLMResponse(
            text=text,
            model=self._model_name,
            provider=self._provider_name,
        )

    def _call_anthropic(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: int | None,
        timeout: float | None,
    ) -> str:
        base_url = self._provider_config.base_url.rstrip("/")
        url = f"{base_url}/v1/messages"

        headers: dict[str, str] = {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        payload: dict[str, Any] = {
            "model": self._model_name,
            "max_tokens": max_tokens or self._MAX_TOKENS,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_message}],
        }

        resp = self._call_with_retry(url, headers, payload, timeout, "Anthropic")

        data = resp.json()
        content_blocks = data.get("content", [])
        return "".join(
            block.get("text", "") for block in content_blocks if block.get("type") == "text"
        )

    def _call_openai(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: int | None,
        timeout: float | None,
    ) -> str:
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
            "max_tokens": max_tokens or self._MAX_TOKENS,
        }

        resp = self._call_with_retry(url, headers, payload, timeout, "OpenAI")

        data = resp.json()
        choices = data.get("choices", [])
        if choices:
            return choices[0].get("message", {}).get("content", "")
        return ""
