# Streaming LLM Client Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add SDK-based streaming/non-streaming LLM calls with per-provider configuration.

**Architecture:** OpenAI and Anthropic SDKs replace httpx as the primary call path. Streaming is transparent — SSE chunks are concatenated into a full string before returning. `LLMResponse` interface is unchanged. httpx calls preserved as fallback when SDK init fails.

**Tech Stack:** `openai` SDK, `anthropic` SDK, `httpx` (fallback), Pydantic config models.

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `pyproject.toml` | Modify | Add `openai`, `anthropic` dependencies |
| `src/agent_nexus/models/config.py` | Modify | Add `streaming_default` to `ModelConfig`, `streaming` to `ProviderConfig` |
| `src/agent_nexus/platform/agency/llm_client.py` | Modify | Add SDK lazy init, streaming/non-streaming paths, config resolution |
| `config.toml` | Modify | Add `streaming_default` and per-provider `streaming` fields |
| `tests/unit/test_streaming_config.py` | Create | Unit tests for streaming config resolution |
| `tests/unit/test_streaming_llm_client.py` | Create | Unit tests for SDK streaming/non-streaming call paths |

---

### Task 1: Add streaming fields to Pydantic config models

**Files:**
- Modify: `src/agent_nexus/models/config.py:21-31` (ProviderConfig)
- Modify: `src/agent_nexus/models/config.py:34-61` (ModelConfig)
- Test: `tests/unit/test_streaming_config.py`

- [ ] **Step 1: Write failing tests for config schema**

Create `tests/unit/test_streaming_config.py`:

```python
"""Tests for streaming config fields on Pydantic models."""
from agent_nexus.models.config import ModelConfig, ProviderConfig


def test_provider_config_streaming_default_is_none():
    """ProviderConfig.streaming defaults to None (use global default)."""
    cfg = ProviderConfig()
    assert cfg.streaming is None


def test_provider_config_streaming_can_be_set():
    cfg = ProviderConfig(streaming=True)
    assert cfg.streaming is True


def test_model_config_streaming_default_is_true():
    """ModelConfig.streaming_default defaults to True."""
    cfg = ModelConfig()
    assert cfg.streaming_default is True


def test_model_config_streaming_default_can_be_set():
    cfg = ModelConfig(streaming_default=False)
    assert cfg.streaming_default is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_streaming_config.py -v`
Expected: FAIL — `ProviderConfig` and `ModelConfig` don't have the new fields yet.

- [ ] **Step 3: Add fields to config models**

In `src/agent_nexus/models/config.py`, modify `ProviderConfig` (line 21-31):

```python
class ProviderConfig(FrozenModel):
    """A single model provider configuration.

    Maps to a [models.providers.<name>] section in config.toml.
    API keys are read from the environment variable named in api_key_env,
    never stored directly in the config file.
    """

    base_url: str = ""
    api_key_env: str = ""
    api: ProviderApiType = ProviderApiType.OPENAI_COMPATIBLE
    streaming: bool | None = None
```

Modify `ModelConfig` (line 34-61), add after `stages` field:

```python
    default: str = "openai:gpt-4o"
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    stages: dict[str, str] = Field(default_factory=dict)
    """Per-stage model overrides..."""
    streaming_default: bool = True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_streaming_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_nexus/models/config.py tests/unit/test_streaming_config.py
git commit -m "feat(config): add streaming_default and per-provider streaming fields"
```

---

### Task 2: Add streaming resolution method to LLMClient

**Files:**
- Modify: `src/agent_nexus/platform/agency/llm_client.py`
- Test: `tests/unit/test_streaming_config.py` (extend)

- [ ] **Step 1: Write failing test for resolution chain**

Add to `tests/unit/test_streaming_config.py`:

```python
"""Tests for streaming config fields on Pydantic models."""
import pytest

from agent_nexus.models.config import ModelConfig, ProviderConfig


def test_provider_config_streaming_default_is_none():
    cfg = ProviderConfig()
    assert cfg.streaming is None


def test_provider_config_streaming_can_be_set():
    cfg = ProviderConfig(streaming=True)
    assert cfg.streaming is True


def test_model_config_streaming_default_is_true():
    cfg = ModelConfig()
    assert cfg.streaming_default is True


def test_model_config_streaming_default_can_be_set():
    cfg = ModelConfig(streaming_default=False)
    assert cfg.streaming_default is False


# --- Streaming resolution tests ---

class TestStreamingResolution:
    """Test the 3-tier streaming resolution: provider → global → True."""

    def test_provider_streaming_true(self):
        pc = ProviderConfig(streaming=True)
        mc = ModelConfig(streaming_default=False)
        assert _resolve_streaming(pc, mc) is True

    def test_provider_streaming_false(self):
        pc = ProviderConfig(streaming=False)
        mc = ModelConfig(streaming_default=True)
        assert _resolve_streaming(pc, mc) is False

    def test_provider_none_uses_global_true(self):
        pc = ProviderConfig(streaming=None)
        mc = ModelConfig(streaming_default=True)
        assert _resolve_streaming(pc, mc) is True

    def test_provider_none_uses_global_false(self):
        pc = ProviderConfig(streaming=None)
        mc = ModelConfig(streaming_default=False)
        assert _resolve_streaming(pc, mc) is False


def _resolve_streaming(provider_config: ProviderConfig, model_config: ModelConfig) -> bool:
    """Extract resolution logic for testing — mirrors LLMClient._should_stream."""
    if provider_config.streaming is not None:
        return provider_config.streaming
    return model_config.streaming_default
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_streaming_config.py -v`
Expected: PASS (logic is simple enough to write alongside test)

- [ ] **Step 3: Add `_should_stream` and streaming config to LLMClient.__init__**

In `src/agent_nexus/platform/agency/llm_client.py`, add a new method after `_apply_sampling_params`:

```python
def _should_stream(self) -> bool:
    """Resolve streaming mode: provider config → global default → True."""
    if self._provider_config.streaming is not None:
        return self._provider_config.streaming
    return self._platform_config.models.streaming_default
```

In `__init__`, store the platform config and add streaming flag after the httpx client init (around line 207):

```python
        # Store platform config for streaming resolution
        self._platform_config = platform_config
        # Lazy-initialised persistent httpx.Client for connection reuse
        self._http_client: httpx.Client | None = None
```

Note: `platform_config` is already available as a local variable in `__init__` (from `loader.load_config()`).

- [ ] **Step 4: Run all existing tests to verify no regressions**

Run: `uv run pytest tests/ -x --timeout=30 -q`
Expected: All pass — no behavioral change yet.

- [ ] **Step 5: Commit**

```bash
git add src/agent_nexus/platform/agency/llm_client.py tests/unit/test_streaming_config.py
git commit -m "feat(llm-client): add _should_stream method and streaming config resolution"
```

---

### Task 3: Add SDK dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add openai and anthropic to dependencies**

In `pyproject.toml`, add to the `dependencies` list:

```toml
dependencies = [
    "pydantic-ai>=0.2",
    "fastmcp>=2.0",
    "typer>=0.12",
    "pydantic>=2.0",
    "toml>=0.10",
    "pyyaml>=6.0",
    "httpx>=0.27",
    "ipython>=8.0",
    "questionary>=2.0",
    "openai>=1.0",
    "anthropic>=0.40",
]
```

- [ ] **Step 2: Install dependencies**

Run: `uv sync`
Expected: `openai` and `anthropic` packages installed.

- [ ] **Step 3: Verify import works**

Run: `uv run python -c "import openai; import anthropic; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "feat(deps): add openai and anthropic SDK dependencies"
```

---

### Task 4: Add SDK lazy initialization to LLMClient

**Files:**
- Modify: `src/agent_nexus/platform/agency/llm_client.py`

- [ ] **Step 1: Add SDK client instance variables to __init__**

In `__init__`, after the `self._http_client` line (around line 207):

```python
        self._http_client: httpx.Client | None = None
        # Lazy-initialised SDK clients (created on first use)
        self._openai_sdk: openai.OpenAI | None = None
        self._anthropic_sdk: anthropic.Anthropic | None = None
```

- [ ] **Step 2: Add `_get_openai_sdk` method**

Add after `_get_http_client`:

```python
def _get_openai_sdk(self) -> openai.OpenAI:
    """Lazy-initialise and cache the OpenAI SDK client."""
    if self._openai_sdk is None:
        base_url = self._provider_config.base_url or None
        self._openai_sdk = openai.OpenAI(
            api_key=self._api_key,
            base_url=base_url,
        )
    return self._openai_sdk

def _get_anthropic_sdk(self) -> anthropic.Anthropic:
    """Lazy-initialise and cache the Anthropic SDK client."""
    if self._anthropic_sdk is None:
        base_url = self._provider_config.base_url or None
        self._anthropic_sdk = anthropic.Anthropic(
            api_key=self._api_key,
            base_url=base_url,
        )
    return self._anthropic_sdk
```

- [ ] **Step 3: Update `close()` to clean up SDK clients**

Replace the `close` method:

```python
def close(self) -> None:
    """Close the underlying HTTP client and SDK clients."""
    if self._cli_backend is not None:
        pass
    if self._http_client is not None and not self._http_client.is_closed:
        self._http_client.close()
        self._http_client = None
    if self._openai_sdk is not None:
        self._openai_sdk.close()
        self._openai_sdk = None
    if self._anthropic_sdk is not None:
        self._anthropic_sdk.close()
        self._anthropic_sdk = None
```

- [ ] **Step 4: Run existing tests**

Run: `uv run pytest tests/ -x --timeout=30 -q`
Expected: PASS — SDK clients are lazy, not created until first call.

- [ ] **Step 5: Commit**

```bash
git add src/agent_nexus/platform/agency/llm_client.py
git commit -m "feat(llm-client): add SDK client lazy initialization and cleanup"
```

---

### Task 5: Rewrite `_call_openai` to use SDK

**Files:**
- Modify: `src/agent_nexus/platform/agency/llm_client.py` (lines 517-555)
- Create: `tests/unit/test_streaming_llm_client.py`

- [ ] **Step 1: Write failing tests for SDK-based OpenAI calls**

Create `tests/unit/test_streaming_llm_client.py`:

```python
"""Tests for LLMClient SDK-based streaming and non-streaming calls."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from agent_nexus.models.config import (
    ModelConfig,
    PlatformConfig,
    ProviderApiType,
    ProviderConfig,
    RuntimeConfig,
)


def _make_llm_client(provider_api=ProviderApiType.OPENAI_COMPATIBLE,
                      streaming=True,
                      provider_base_url="https://api.test.com/v1"):
    """Create an LLMClient with mocked config for testing."""
    with patch("agent_nexus.platform.agency.llm_client.ConfigLoader") as MockLoader, \
         patch("agent_nexus.platform.agency.llm_client.ModelDBClient"):
        platform_cfg = PlatformConfig(
            runtime=RuntimeConfig(),
            models=ModelConfig(
                default="openai:test-model",
                providers={
                    "openai": ProviderConfig(
                        base_url=provider_base_url,
                        api_key_env="TEST_API_KEY",
                        api=provider_api,
                        streaming=streaming,
                    ),
                },
            ),
        )
        mock_loader = MagicMock()
        mock_loader.load_config.return_value = platform_cfg
        MockLoader.return_value = mock_loader

        import os
        os.environ["TEST_API_KEY"] = "test-key-123"

        from agent_nexus.platform.agency.llm_client import LLMClient
        client = LLMClient(model_string="openai:test-model")
        return client


class TestCallOpenaiSDK:
    """Test OpenAI SDK path (streaming and non-streaming)."""

    def test_non_streaming_uses_sdk(self):
        """Non-streaming call uses OpenAI SDK .create() without stream=True."""
        client = _make_llm_client(streaming=False)

        mock_response = MagicMock()
        mock_response.model = "test-model"
        mock_choice = MagicMock()
        mock_choice.message.content = "Hello from test"
        mock_response.choices = [mock_choice]

        with patch.object(client, "_get_openai_sdk") as mock_get_sdk:
            mock_sdk = MagicMock()
            mock_sdk.chat.completions.create.return_value = mock_response
            mock_get_sdk.return_value = mock_sdk

            result = client.call("You are helpful", "Say hi")

            assert result.text == "Hello from test"
            mock_sdk.chat.completions.create.assert_called_once()
            call_kwargs = mock_sdk.chat.completions.create.call_args
            assert call_kwargs.kwargs.get("stream") is None or call_kwargs.kwargs.get("stream") is False

    def test_streaming_uses_sdk_with_stream_true(self):
        """Streaming call uses OpenAI SDK .create(stream=True)."""
        client = _make_llm_client(streaming=True)

        # Build mock stream chunks
        chunks = []
        for text in ["Hello", " from", " stream"]:
            chunk = MagicMock()
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta.content = text
            chunks.append(chunk)

        with patch.object(client, "_get_openai_sdk") as mock_get_sdk:
            mock_sdk = MagicMock()
            mock_sdk.chat.completions.create.return_value = iter(chunks)
            mock_get_sdk.return_value = mock_sdk

            result = client.call("You are helpful", "Say hi")

            assert result.text == "Hello from stream"
            call_kwargs = mock_sdk.chat.completions.create.call_args
            assert call_kwargs.kwargs.get("stream") is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_streaming_llm_client.py -v`
Expected: FAIL — `_call_openai` still uses httpx.

- [ ] **Step 3: Rewrite `_call_openai` to use SDK**

Replace the `_call_openai` method in `src/agent_nexus/platform/agency/llm_client.py`:

```python
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
        use_stream = self._should_stream()

        try:
            sdk = self._get_openai_sdk()
        except Exception:
            logger.warning("OpenAI SDK init failed, falling back to httpx", exc_info=True)
            return self._call_openai_raw(
                system_prompt, user_message,
                max_tokens, temperature, top_p, timeout, response_format,
            )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        kwargs: dict[str, Any] = {
            "model": self._model_name,
            "messages": messages,
            "max_tokens": max_tokens or self._capability.max_output_tokens,
        }
        if temperature is not None and self._capability.supports_temperature:
            kwargs["temperature"] = max(
                self._capability.temperature_min,
                min(self._capability.temperature_max, temperature),
            )
        if top_p is not None and self._capability.supports_temperature:
            kwargs["top_p"] = max(0.0, min(1.0, top_p))
        if response_format == "json":
            kwargs["response_format"] = {"type": "json_object"}
        if timeout is not None:
            kwargs["timeout"] = timeout

        if use_stream:
            text_parts: list[str] = []
            actual_model = self._model_name
            with sdk.chat.completions.create(stream=True, **kwargs) as stream:
                for chunk in stream:
                    if chunk.model:
                        actual_model = chunk.model
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if delta and delta.content:
                        text_parts.append(delta.content)
            return "".join(text_parts), actual_model
        else:
            resp = sdk.chat.completions.create(**kwargs)
            actual_model = resp.model or self._model_name
            content = resp.choices[0].message.content if resp.choices else ""
            return content or "", actual_model
```

Add the old httpx logic as `_call_openai_raw` (copy of the original `_call_openai` method):

```python
def _call_openai_raw(
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_streaming_llm_client.py -v`
Expected: PASS

- [ ] **Step 5: Run all tests for regression check**

Run: `uv run pytest tests/ -x --timeout=30 -q`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add src/agent_nexus/platform/agency/llm_client.py tests/unit/test_streaming_llm_client.py
git commit -m "feat(llm-client): rewrite _call_openai to use SDK with streaming support"
```

---

### Task 6: Rewrite `_call_anthropic` to use SDK

**Files:**
- Modify: `src/agent_nexus/platform/agency/llm_client.py`
- Modify: `tests/unit/test_streaming_llm_client.py`

- [ ] **Step 1: Write failing tests for SDK-based Anthropic calls**

Add to `tests/unit/test_streaming_llm_client.py`:

```python
class TestCallAnthropicSDK:
    """Test Anthropic SDK path (streaming and non-streaming)."""

    def _make_anthropic_client(self, streaming=True):
        with patch("agent_nexus.platform.agency.llm_client.ConfigLoader") as MockLoader, \
             patch("agent_nexus.platform.agency.llm_client.ModelDBClient"):
            platform_cfg = PlatformConfig(
                runtime=RuntimeConfig(),
                models=ModelConfig(
                    default="anthropic:test-model",
                    providers={
                        "anthropic": ProviderConfig(
                            base_url="https://api.anthropic.com",
                            api_key_env="TEST_API_KEY",
                            api=ProviderApiType.ANTHROPIC_MESSAGES,
                            streaming=streaming,
                        ),
                    },
                ),
            )
            mock_loader = MagicMock()
            mock_loader.load_config.return_value = platform_cfg
            MockLoader.return_value = mock_loader

            import os
            os.environ["TEST_API_KEY"] = "test-key-123"

            from agent_nexus.platform.agency.llm_client import LLMClient
            return LLMClient(model_string="anthropic:test-model")

    def test_non_streaming_uses_sdk(self):
        client = self._make_anthropic_client(streaming=False)

        mock_response = MagicMock()
        mock_response.model = "test-model"
        mock_block = MagicMock()
        mock_block.type = "text"
        mock_block.text = "Hello from Claude"
        mock_response.content = [mock_block]

        with patch.object(client, "_get_anthropic_sdk") as mock_get_sdk:
            mock_sdk = MagicMock()
            mock_sdk.messages.create.return_value = mock_response
            mock_get_sdk.return_value = mock_sdk

            result = client.call("You are helpful", "Say hi")

            assert result.text == "Hello from Claude"
            mock_sdk.messages.create.assert_called_once()
            call_kwargs = mock_sdk.messages.create.call_args
            assert call_kwargs.kwargs.get("stream") is None or call_kwargs.kwargs.get("stream") is False

    def test_streaming_uses_sdk_with_stream_true(self):
        client = self._make_anthropic_client(streaming=True)

        # Anthropic stream events
        events = []
        # message_start
        start_event = MagicMock()
        start_event.type = "message_start"
        start_event.message = MagicMock()
        start_event.message.model = "test-model"
        events.append(start_event)
        # content_block_delta
        for text in ["Hello", " from", " Claude"]:
            delta_event = MagicMock()
            delta_event.type = "content_block_delta"
            delta_event.delta = MagicMock()
            delta_event.delta.text = text
            events.append(delta_event)
        # message_stop
        stop_event = MagicMock()
        stop_event.type = "message_stop"
        events.append(stop_event)

        with patch.object(client, "_get_anthropic_sdk") as mock_get_sdk:
            mock_sdk = MagicMock()
            mock_sdk.messages.create.return_value = iter(events)
            mock_get_sdk.return_value = mock_sdk

            result = client.call("You are helpful", "Say hi")

            assert result.text == "Hello from Claude"
            call_kwargs = mock_sdk.messages.create.call_args
            assert call_kwargs.kwargs.get("stream") is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_streaming_llm_client.py::TestCallAnthropicSDK -v`
Expected: FAIL — `_call_anthropic` still uses httpx.

- [ ] **Step 3: Rewrite `_call_anthropic` to use SDK**

Replace the `_call_anthropic` method:

```python
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
        use_stream = self._should_stream()

        try:
            sdk = self._get_anthropic_sdk()
        except Exception:
            logger.warning("Anthropic SDK init failed, falling back to httpx", exc_info=True)
            return self._call_anthropic_raw(
                system_prompt, user_message,
                max_tokens, temperature, top_p, timeout, response_format,
            )

        messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
        if response_format == "json":
            messages.append({"role": "assistant", "content": "{"})

        kwargs: dict[str, Any] = {
            "model": self._model_name,
            "max_tokens": max_tokens or self._capability.max_output_tokens,
            "system": system_prompt,
            "messages": messages,
        }
        if temperature is not None and self._capability.supports_temperature:
            kwargs["temperature"] = max(
                self._capability.temperature_min,
                min(self._capability.temperature_max, temperature),
            )
        if top_p is not None and self._capability.supports_temperature:
            kwargs["top_p"] = max(0.0, min(1.0, top_p))
        if timeout is not None:
            kwargs["timeout"] = timeout

        if use_stream:
            text_parts: list[str] = []
            actual_model = self._model_name
            with sdk.messages.create(stream=True, **kwargs) as stream:
                for event in stream:
                    if event.type == "message_start" and hasattr(event, "message"):
                        actual_model = getattr(event.message, "model", actual_model)
                    elif event.type == "content_block_delta":
                        if hasattr(event, "delta") and hasattr(event.delta, "text"):
                            text_parts.append(event.delta.text)
            text = "".join(text_parts)
            if response_format == "json" and text and not text.startswith("{"):
                text = "{" + text
            return text, actual_model
        else:
            resp = sdk.messages.create(**kwargs)
            actual_model = resp.model or self._model_name
            text = "".join(
                block.text for block in resp.content if block.type == "text"
            )
            if response_format == "json" and text and not text.startswith("{"):
                text = "{" + text
            return text, actual_model
```

Add the old httpx logic as `_call_anthropic_raw` (copy of the original `_call_anthropic` method):

```python
def _call_anthropic_raw(
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
        if response_format == "json" and text and not text.startswith("{"):
            text = "{" + text
        return text, actual_model
```

- [ ] **Step 4: Run all streaming tests**

Run: `uv run pytest tests/unit/test_streaming_llm_client.py -v`
Expected: All PASS

- [ ] **Step 5: Run full test suite for regression check**

Run: `uv run pytest tests/ -x --timeout=30 -q`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add src/agent_nexus/platform/agency/llm_client.py tests/unit/test_streaming_llm_client.py
git commit -m "feat(llm-client): rewrite _call_anthropic to use SDK with streaming support"
```

---

### Task 7: Update config.toml with streaming defaults

**Files:**
- Modify: `config.toml`

- [ ] **Step 1: Add streaming_default and per-provider streaming fields**

In `config.toml`, update the `[models]` section:

```toml
[models]
default = "openai:gpt-4o"
streaming_default = true
```

Add `streaming` to each provider:

```toml
[models.providers.anthropic]
base_url = ""
api_key_env = "ANTHROPIC_API_KEY"
api = "anthropic-messages"
streaming = true

[models.providers.minimax]
base_url = ""
api_key_env = "MINIMAX_API_KEY"
api = "openai-compatible"
streaming = true

[models.providers.openai]
base_url = ""
api_key_env = "OPENAI_API_KEY"
api = "openai-compatible"
streaming = true

[models.providers.deepseek]
base_url = "https://api.deepseek.com/v1"
api_key_env = "DEEPSEEK_API_KEY"
api = "openai-compatible"
streaming = false

[models.providers.qwen]
base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
api_key_env = "DASHSCOPE_API_KEY"
api = "openai-compatible"
streaming = true
```

Leave `ollama` and CLI providers without explicit `streaming` (they'll use `streaming_default = true`).

- [ ] **Step 2: Verify config loads**

Run: `uv run python -c "from agent_nexus.platform.config.loader import ConfigLoader; cfg = ConfigLoader().load_config(); print(f'streaming_default={cfg.models.streaming_default}'); print(f'openai.streaming={cfg.models.providers[\"openai\"].streaming}'); print(f'deepseek.streaming={cfg.models.providers[\"deepseek\"].streaming}')"`
Expected: `streaming_default=True`, `openai.streaming=True`, `deepseek.streaming=False`

- [ ] **Step 3: Commit**

```bash
git add config.toml
git commit -m "feat(config): add streaming_default and per-provider streaming settings"
```

---

### Task 8: Lint and final verification

**Files:**
- All modified files

- [ ] **Step 1: Run ruff lint**

Run: `uv run ruff check src/ tests/`
Expected: No errors. Fix any issues found.

- [ ] **Step 2: Run ruff format**

Run: `uv run ruff format src/ tests/`
Expected: Files already formatted.

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest tests/ -x --timeout=30 -q`
Expected: All pass.

- [ ] **Step 4: Final commit if formatting changes needed**

```bash
git add -A
git commit -m "chore: lint and format streaming LLM client changes"
```
