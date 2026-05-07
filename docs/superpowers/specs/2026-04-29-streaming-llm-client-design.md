# Streaming LLM Client Design

## Summary

Add streaming/non-streaming configuration to LLMClient, defaulting to streaming. Both OpenAI and Anthropic format APIs use official SDKs as the primary call path (streaming and non-streaming). httpx direct calls are preserved as fallback when SDK is unavailable.

## Requirements

- Default to streaming for all LLM API calls
- Per-provider configurable via `config.toml`
- Transparent to callers — `LLMResponse` interface unchanged
- SDK-first: `openai` and `anthropic` SDKs handle all API calls
- Fallback: httpx path retained when SDK initialization fails

## Section 1: Dependencies & Architecture Boundary

**New dependencies** (`pyproject.toml`):
- `openai` — OpenAI SDK
- `anthropic` — Anthropic SDK

**Architecture**: LLMClient internal call paths become:

| Provider API Type | SDK Path | Fallback Path |
|---|---|---|
| `openai-compatible` | `openai.OpenAI` SDK | `_call_openai_raw()` (httpx) |
| `anthropic-messages` | `anthropic.Anthropic` SDK | `_call_anthropic_raw()` (httpx) |
| `ollama` | `openai.OpenAI` SDK (Ollama is OpenAI-compatible) | `_call_openai_raw()` (httpx) |
| `cli` | unchanged (`_call_cli()`) | N/A |

**Boundary**: Only use SDK's low-level streaming completion API. No SDK agent loops, tool loops, or higher-level abstractions.

**SDK Client Lifecycle**:
- Lazy initialization: `_get_openai_sdk()` / `_get_anthropic_sdk()` create on first call
- Cache SDK client instance on `self`
- Close in `close()` / `__exit__()` alongside existing httpx client
- Pass provider's `base_url` and resolved `api_key` to SDK constructor

## Section 2: Configuration System

**config.toml additions**:

```toml
[models]
default = "openai:gpt-4o"
streaming_default = true  # Global streaming default

[models.providers.openai]
base_url = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"
streaming = true  # Per-provider override

[models.providers.api]
base_url = "http://192.168.3.10:3006"
api_key_env = "API_API_KEY"
api = "anthropic-messages"
streaming = false  # This provider uses non-streaming

[models.providers.deepseek]
base_url = "https://api.deepseek.com"
api_key_env = "DEEPSEEK_API_KEY"
streaming = false
```

**Pydantic Schema changes** (`models/config.py`):

- `ModelConfig`: add `streaming_default: bool = True`
- `ProviderConfig`: add `streaming: bool | None = None` (None = use global default)

**Resolution chain** (at call time in LLMClient):

1. `ProviderConfig.streaming` — if not None, use it
2. `ModelConfig.streaming_default` — global default
3. Hardcoded `True` — final fallback

Streaming is a user preference, not a model capability — `ModelCapability` dataclass is not modified.

## Section 3: Call Flow

**Top-level dispatch** (`call()` method):

```
call()
  ├── Resolve streaming config (provider → global → True)
  ├── CLI provider → _call_cli() (unchanged)
  ├── Anthropic format
  │   ├── streaming=True  → SDK client.messages.create(stream=True) → concatenate text
  │   └── streaming=False → SDK client.messages.create()             → extract text
  └── OpenAI format
      ├── streaming=True  → SDK client.chat.completions.create(stream=True) → concatenate
      └── streaming=False → SDK client.chat.completions.create()            → extract text
```

**Streaming concatenation**:

- **OpenAI**: Iterate stream chunks, collect `chunk.choices[0].delta.content`, concatenate to full str
- **Anthropic**: Handle event stream (`message_start` → `content_block_delta` → `message_stop`), collect `delta.text`, concatenate to full str

**JSON mode compatibility**:
- OpenAI: pass `response_format={"type": "json_object"}` to SDK as before
- Anthropic: preserve existing assistant message prefill `{"` trick

**Error handling**:
- SDK import/init failure → log warning, fall back to httpx path
- Stream mid-disconnect → return concatenated partial content, set `finish_reason="error"`

**Retry**: SDK has built-in retry. Non-streaming httpx fallback retains existing `_call_with_retry()`.

## Section 4: Testing Strategy

**Unit tests** (mock SDK):
- `test_streaming_openai` — mock `openai.OpenAI().chat.completions.create(stream=True)`, verify chunk concatenation
- `test_streaming_anthropic` — mock `anthropic.Anthropic().messages.create(stream=True)`, verify event stream concatenation
- `test_non_streaming_sdk` — verify streaming=False uses SDK non-streaming path
- `test_streaming_config_resolution` — verify 3-tier resolution: provider config → global default → hardcoded
- `test_sdk_fallback_to_httpx` — simulate SDK import failure, verify httpx fallback

**Integration tests** (mock HTTP):
- Use `respx` or httpx mock server to verify SDK sends correct payload (including `stream=True`)

**Unchanged tests**:
- All existing caller tests remain valid — `LLMResponse` interface is unchanged

## Files to Modify

| File | Change |
|------|--------|
| `pyproject.toml` | Add `openai`, `anthropic` dependencies |
| `src/agent_nexus/models/config.py` | Add `streaming_default` to `ModelConfig`, `streaming` to `ProviderConfig` |
| `src/agent_nexus/platform/agency/llm_client.py` | Add SDK lazy init, streaming/non-streaming call paths, config resolution |
| `config.toml` | Add `streaming_default` and per-provider `streaming` fields |
| `tests/unit/` | New unit tests for streaming logic |
