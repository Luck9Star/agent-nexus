# CLI Backend Integration Design Spec

**Date**: 2026-04-28
**Status**: Draft
**Branch**: feat/agency-agents-integration

## 1. Motivation

Agent Nexus 当前通过 `LLMClient` 直接调用 Anthropic/OpenAI API（httpx）。用户希望扩展能力边界，复用已安装的 CLI Agent 工具（Claude Code、Gemini CLI、Codex CLI、OpenClaw、Hermes、Nanobot）作为 LLM 调用后端。

核心价值：
- **模型可达性**：通过 CLI 访问其专有模型（Google 模型 via Gemini CLI 等）
- **复用认证**：用户已在 CLI 中配置认证，无需额外管理 API Key
- **能力扩展**：CLI 自带工具链（文件编辑、Git 操作、多模态等）
- **专家组兼容**：Agency Pipeline 的专家提示词通过 `--system-prompt` 参数传递

## 2. Architecture

### 2.1 Data Flow

```
Agency Pipeline (LLMExecutor)
       │
       ▼
   LLMClient.call(system_prompt, user_message, session_id?)
       │
       ├─ ProviderApiType.ANTHROPIC → _call_anthropic()  (existing)
       ├─ ProviderApiType.OPENAI    → _call_openai()     (existing)
       └─ ProviderApiType.CLI ──────→ CLIRouter
                                         │
                                    routing strategy (config.toml)
                                         │
                                         ▼
                                   CLIBackendRegistry
                                         │
                              ┌──────────┼──────────┐
                              ▼          ▼          ▼
                          ClaudeCode  GeminiCLI  OpenClaw ...
                              │
                              ▼
                    subprocess.run(command, ...)
                              │
                              ▼
                    CLISessionStore → SQLite
                              │
                              ▼
                    LLMResponse(text, model, provider, metadata={session_id})
```

### 2.2 Module Structure

```
src/agent_nexus/platform/agency/
├── llm_client.py              # 扩展: ProviderApiType.CLI, _call_cli()
├── cli_backend/               # 新增模块
│   ├── __init__.py
│   ├── base.py                # CLIBackend ABC + GenericCLIBackend（config-driven，无需 per-CLI 子类）
│   ├── registry.py            # CLIBackendRegistry — 后端发现 + 健康检查
│   ├── session_store.py       # CLISessionStore — SQLite session ID 持久化
│   ├── router.py              # CLIRouter — 四策略路由
│   └── parser.py              # JSON/Text 输出解析（json_paths + text_patterns）
```

### 2.3 Key Interfaces

```python
class CLIBackend(ABC):
    name: str
    supported_models: list[str]

    @abstractmethod
    def call(self, system_prompt: str, user_message: str,
             session_id: str | None = None, **kwargs) -> CLIResult: ...

    @abstractmethod
    def is_available(self) -> bool: ...

    @abstractmethod
    def list_sessions(self) -> list[str]: ...


class CLIRouter:
    def resolve(self, model_string: str | None,
                explicit_backend: str | None,
                capabilities: list[str] | None) -> CLIBackend: ...


class CLISessionStore:
    def save(self, session_id: str, backend: str, model: str,
             task_id: str | None, name: str | None = None) -> None: ...
    def get(self, session_id: str) -> CLISessionRecord | None: ...
    def get_by_task(self, task_id: str) -> list[CLISessionRecord]: ...
    def cleanup(self, max_age_days: int = 30) -> int: ...


@dataclass
class CLIResult:
    text: str
    model: str
    session_id: str | None
    input_tokens: int | None = None
    output_tokens: int | None = None
    raw_stdout: str = ""
    raw_stderr: str = ""
    returncode: int = 0
    duration_ms: int = 0
    parse_error: bool = False
```

## 3. Config Schema

### 3.1 CLI Provider Config

每个 CLI 后端通过 config.toml 声明，无需硬编码：

```toml
[providers.claude-code]
api = "cli"
command = "claude"
args = ["-p"]
system_prompt_flag = "--system-prompt"
session_flag = "--resume"
output_format = "json"
output_format_flag = "--output-format"
model_map = { sonnet = "claude-sonnet-4-20250514", opus = "claude-opus-4-20250514" }

[providers.claude-code.json_paths]
text = "result"
session_id = "session_id"
model = "model"
input_tokens = "usage.input_tokens"
output_tokens = "usage.output_tokens"

[providers.gemini-cli]
api = "cli"
command = "gemini"
args = []
system_prompt_flag = "--system"
session_flag = "--session"
output_format = "json"
output_format_flag = "--output-format"
model_map = { flash = "gemini-2.5-flash", pro = "gemini-2.5-pro" }

[providers.gemini-cli.json_paths]
text = "response.text"
session_id = "session.id"
model = "model_version"
input_tokens = "usage_metadata.prompt_token_count"
output_tokens = "usage_metadata.candidates_token_count"

[providers.codex-cli]
api = "cli"
command = "codex"
args = []
system_prompt_flag = "--system-prompt"
session_flag = "--resume"
output_format = "json"
output_format_flag = "--format"
model_map = { default = "codex-mini" }

[providers.codex-cli.json_paths]
text = "output"
session_id = "session_id"
model = "model"
input_tokens = "usage.input_tokens"
output_tokens = "usage.output_tokens"

[providers.openclaw]
api = "cli"
command = "openclaw"
args = ["agent", "-m"]
system_prompt_flag = "--system"
session_flag = "--session"
output_format = "text"
output_format_flag = ""
model_map = { default = "openclaw-default" }

[providers.hermes]
api = "cli"
command = "hermes"
args = []
system_prompt_flag = "--system-prompt"
session_flag = "--resume"
output_format = "text"
output_format_flag = ""
model_map = { default = "hermes-default" }

[providers.nanobot]
api = "cli"
command = "nanobot"
args = []
system_prompt_flag = "--system"
session_flag = "--session"
output_format = "text"
output_format_flag = ""
model_map = { default = "nanobot-default" }
```

### 3.2 Routing Config

```toml
[cli_routing]
default = "claude-code"
fallback_enabled = true                   # 关闭后直接报错，方便调试单个 CLI
fallback_chain = ["gemini-cli", "codex-cli", "openclaw"]

[cli_routing.model_rules]
"anthropic:*" = "claude-code"
"google:*" = "gemini-cli"
"openai:*" = "codex-cli"
```

### 3.3 Database Lifecycle Config

```toml
[data_lifecycle]
hot_days = 30                # 热数据保留天数（主库）
warm_days = 90               # 温数据保留天数
archive_dir = ""             # 冷备目录，默认 ~/.agent-nexus/data/archive/
auto_archive = true          # 自动归档
```

## 4. SQLite Schema

数据库路径：`~/.agent-nexus/data/agent-nexus.db`

```sql
-- CLI 会话追踪
CREATE TABLE cli_sessions (
    session_id   TEXT PRIMARY KEY,
    name         TEXT,                     -- 可读会话名
    backend_name TEXT NOT NULL,
    model        TEXT,
    task_id      TEXT,
    created_at   TEXT DEFAULT (datetime('now')),
    last_used_at TEXT DEFAULT (datetime('now')),
    turn_count   INTEGER DEFAULT 1,
    metadata     TEXT                      -- JSON
);

-- 任务执行日志（API + CLI 统一）
CREATE TABLE task_executions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id       TEXT NOT NULL,
    backend_type  TEXT NOT NULL,           -- 'api' | 'cli'
    backend_name  TEXT NOT NULL,
    model         TEXT,
    session_id    TEXT REFERENCES cli_sessions(session_id),
    input_tokens  INTEGER,
    output_tokens INTEGER,
    duration_ms   INTEGER,
    status        TEXT DEFAULT 'success',  -- 'success' | 'error' | 'timeout'
    error         TEXT,
    created_at    TEXT DEFAULT (datetime('now'))
);

-- 后端健康记录
CREATE TABLE backend_health (
    backend_name TEXT PRIMARY KEY,
    is_available INTEGER DEFAULT 0,
    last_check   TEXT,
    version      TEXT,
    error_msg    TEXT
);

-- 每日汇总统计
CREATE TABLE daily_stats (
    date         TEXT NOT NULL,
    backend_name TEXT NOT NULL,
    total_calls  INTEGER DEFAULT 0,
    success_calls INTEGER DEFAULT 0,
    total_input_tokens  INTEGER DEFAULT 0,
    total_output_tokens INTEGER DEFAULT 0,
    avg_duration_ms     REAL DEFAULT 0,
    PRIMARY KEY (date, backend_name)
);

-- daily_stats 自动维护 Trigger
CREATE TRIGGER IF NOT EXISTS trg_update_daily_stats
AFTER INSERT ON task_executions
BEGIN
    INSERT INTO daily_stats (date, backend_name, total_calls, success_calls,
                             total_input_tokens, total_output_tokens, avg_duration_ms)
    VALUES (DATE('now'), NEW.backend_name, 1,
            CASE WHEN NEW.status = 'success' THEN 1 ELSE 0 END,
            COALESCE(NEW.input_tokens, 0), COALESCE(NEW.output_tokens, 0),
            COALESCE(NEW.duration_ms, 0))
    ON CONFLICT(date, backend_name) DO UPDATE SET
        total_calls = total_calls + 1,
        success_calls = success_calls + CASE WHEN NEW.status = 'success' THEN 1 ELSE 0 END,
        total_input_tokens = total_input_tokens + COALESCE(NEW.input_tokens, 0),
        total_output_tokens = total_output_tokens + COALESCE(NEW.output_tokens, 0),
        avg_duration_ms = (avg_duration_ms * (total_calls - 1) + COALESCE(NEW.duration_ms, 0)) / total_calls;
END;

CREATE TRIGGER IF NOT EXISTS trg_delete_daily_stats
AFTER DELETE ON task_executions
BEGIN
    UPDATE daily_stats SET
        total_calls = total_calls - 1,
        success_calls = success_calls - CASE WHEN OLD.status = 'success' THEN 1 ELSE 0 END,
        total_input_tokens = total_input_tokens - COALESCE(OLD.input_tokens, 0),
        total_output_tokens = total_output_tokens - COALESCE(OLD.output_tokens, 0)
    WHERE date = DATE(OLD.created_at) AND backend_name = OLD.backend_name;
END;
```

### 4.1 Database Lifecycle Management

**三级存储策略**：

| 层级 | 数据范围 | 存储 | 清理方式 |
|------|---------|------|---------|
| Hot | 最近 30 天 | 主库 `agent-nexus.db` | 无需操作 |
| Warm | 30-90 天 | 主库 | 可按需 vacuum |
| Cold | 90 天以上 | `archive/agent-nexus-YYYY-MM.db` | 自动归档 |

**归档流程**：

1. `CLISessionStore.cleanup()` 定期执行（可通过 CLI 命令或 cron 触发）
2. 超过 `hot_days` 的 `task_executions` 记录迁移到月度归档文件
3. 超过 `warm_days` 的 `cli_sessions` 记录迁移到归档文件
4. 归档完成后从主库 DELETE 已迁移记录
5. 归档文件为 SQLite 格式，可直接用 sqlite3 命令查询
6. 同步更新 `daily_stats` 汇总表

**归档目录结构**：

```
~/.agent-nexus/data/
├── agent-nexus.db              # 热数据主库
└── archive/
    ├── agent-nexus-2026-01.db  # 2026年1月归档
    ├── agent-nexus-2026-02.db
    └── agent-nexus-2026-03.db
```

**CLI 命令**：

```bash
agent-nexus data archive           # 手动触发归档
agent-nexus data stats             # 查看统计摘要
agent-nexus data sessions list     # 列出活跃会话
agent-nexus data sessions cleanup  # 清理过期会话
```

## 5. Routing Strategy

四策略按优先级：

1. **显式指定**：`model_string = "cli:claude-code"` 或运行时参数 `explicit_backend="claude-code"`
2. **模型映射**：`"google:*"` → `gemini-cli`（config.toml `[cli_routing.model_rules]`）
3. **能力匹配**：需要文件编辑能力 → 优先选 Claude Code（future，当前不实现）
4. **默认兜底**：`[cli_routing] default = "claude-code"`

当 `fallback_enabled = false` 时，跳过降级链，直接抛出异常。方便用户调试单个 CLI：

```python
if not fallback_enabled:
    raise CLIBackendUnavailableError(
        f"Backend '{backend.name}' failed: {error}. "
        f"Fallback disabled — enable via [cli_routing] fallback_enabled = true"
    )
```

## 6. Error Handling & Degradation

### 6.1 Degradation Chain

```
指定 backend → 健康检查 → 不可用?
  ↓ fallback_enabled = true
  按 fallback_chain 逐个尝试
  ↓ 全部不可用
  降级到原始 API 调用 (LLMClient._call_anthropic / _call_openai)
  ↓ API 也不可用
  AllBackendsUnavailableError
```

### 6.2 Error Classification

| 错误类型 | 检测方式 | 处理 |
|----------|---------|------|
| CLI 未安装 | `shutil.which(command)` 返回 None | 标记 backend_health，fallback |
| CLI 超时 | `subprocess.run(timeout=N)` | 记录 status='timeout'，fallback |
| 输出解析失败 | text/JSON 解析异常 | 返回原始 stdout，parse_error=True |
| 非零退出码 | `returncode != 0` | stderr 作为 error，fallback |
| Session ID 无效 | CLI 报错 unknown session | 丢弃 session_id，创建新会话 |

### 6.3 Timeout Tiers

| 场景 | 默认超时 | 可配置 |
|------|---------|--------|
| 简单查询 (< 1K token) | 60s | config.toml `[providers.*].timeout` |
| 标准任务 | 180s | 同上 |
| 复杂任务（代码生成） | 300s | 同上 |

## 7. Integration with LLMClient

### 7.1 Changes to Existing Code

`ProviderApiType` 枚举新增 `CLI`：

```python
class ProviderApiType(str, Enum):
    ANTHROPIC_MESSAGES = "anthropic_messages"
    OPENAI_CHAT = "openai_chat"
    CLI = "cli"                # 新增
```

`LLMClient.__init__()` 新增 CLI 分支：

```python
if self._provider_config.api == ProviderApiType.CLI:
    self._cli_backend = cli_router.resolve(
        model_string=resolved,
        explicit_backend=None,
    )
```

`LLMClient.call()` 接口变更 — 新增可选 `session_id` 参数：

```python
# 变更前
def call(self, system_prompt, user_message, max_tokens=None, temperature=None, top_p=None, timeout=None) -> LLMResponse:

# 变更后（向后兼容，新参数均为可选）
def call(self, system_prompt, user_message, max_tokens=None, temperature=None, top_p=None, timeout=None, *, session_id=None) -> LLMResponse:
```

`call()` 内部新增 `_call_cli()` 分支：

```python
if self._provider_config.api == ProviderApiType.CLI:
    result = self._call_cli(
        system_prompt, user_message, session_id,
    )
    return LLMResponse(
        text=result.text,
        model=result.model,
        provider=self._provider_name,
        metadata={"session_id": result.session_id, "input_tokens": result.input_tokens, "output_tokens": result.output_tokens},
    )
```

### 7.2 Expert Prompt Passthrough

Agency Pipeline 的 `LLMExecutor` 调用方式不变：

```python
# 上层代码零修改
response = client.call(
    system_prompt=expert_prompt,    # 专家提示词 → CLI --system-prompt
    user_message=task_description,  # 任务描述 → CLI positional arg
    session_id=previous_session_id, # 会话恢复 → CLI --resume
)
```

## 8. Output Parsing Strategy

不同 CLI 后端支持不同的输出模式，通过 config.toml 的 `output_format` 字段控制：

### 8.1 JSON Path Mapping（Config-Driven）

不同 CLI 的 JSON 输出格式不同。通过 config.toml 中的 `json_paths` 字段映射提取路径，**零代码**适配：

```toml
# Claude Code: {"result": "...", "session_id": "...", "model": "...", "usage": {"input_tokens": 100, "output_tokens": 50}}
[providers.claude-code.json_paths]
text = "result"
session_id = "session_id"
model = "model"
input_tokens = "usage.input_tokens"
output_tokens = "usage.output_tokens"

# Gemini CLI: {"response": {"text": "..."}, "session": {"id": "..."}, "usage_metadata": {"prompt_token_count": 100, ...}}
[providers.gemini-cli.json_paths]
text = "response.text"
session_id = "session.id"
model = "model_version"
input_tokens = "usage_metadata.prompt_token_count"
output_tokens = "usage_metadata.candidates_token_count"
```

**提取逻辑**（dot-separated path 递归解析）：

```rust
fn extract_json<'a>(data: &'a serde_json::Value, path: &str) -> Option<&'a serde_json::Value> {
    path.split('.')
        .try_fold(data, |current, key| current.get(key))
}

let text = extract_json(&json_data, &config.json_paths.text)
    .and_then(|v| v.as_str())
    .unwrap_or_default();
```

**设计原则**：新增 CLI = 加 config，不改代码。

### 8.2 Text Output Mode（正则提取）

当 `output_format = "text"` 时（CLI 不支持 JSON），通过可选的正则模式提取元数据：

```toml
[providers.openclaw.text_patterns]
session_id = "session[:\\s]+([a-f0-9-]+)"    # 从 stderr 匹配
model = ""                                     # 空 = 不可提取
```

无匹配规则时：`text` = stdout 全文，其他字段返回 None。

### 8.3 Parsing Fallback

JSON 解析失败自动降级为 text 模式：

```rust
match self.config.output_format.as_str() {
    "json" => match self.parse_json_output(&stdout) {
        Ok(result) => Ok(result),
        Err(_) => {
            tracing::warn!("JSON parsing failed, falling back to text mode");
            Ok(self.parse_text_output(&stdout, &stderr))
        }
    },
    _ => Ok(self.parse_text_output(&stdout, &stderr)),
}
```

### 8.4 `json_paths` 配置字段

| 字段 | 必填 | 说明 |
|------|------|------|
| `text` | 是 | 返回文本内容路径 |
| `session_id` | 否 | 会话 ID 路径 |
| `model` | 否 | 实际模型路径 |
| `input_tokens` | 否 | 输入 token 数路径 |
| `output_tokens` | 否 | 输出 token 数路径 |

所有 path 支持嵌套（`usage.input_tokens`），缺失字段返回 None。

## 9. Rust Implementation

Python 设计覆盖 1-8 节。Rust 重写使用相同的架构和 config schema，但利用 Rust 的 async runtime 和类型系统获得更好的性能。

### 9.1 为什么 Rust 更适合 CLI Backend

| 维度 | Python (asyncio) | Rust (tokio) |
|------|-----------------|--------------|
| 并发 | GIL 限制，IO-bound 才真并发 | 无 GIL，多 CLI 真并行 |
| 内存 | 50-100MB+ per process | MB 级，长时间运行稳定 |
| 字符串解析 | dict 中间层 | serde 零拷贝反序列化 |
| 进程清理 | `atexit` 不可靠 | `kill_on_drop(true)` 确定性清理 |
| 超时控制 | `asyncio.wait_for` | `tokio::time::timeout` + `select!` |
| 错误处理 | try/except 运行时 | `Result<T, E>` 编译时保证 |

### 9.2 Crate 结构

新增独立 crate `ap-cli-backend`，职责单一：

```
crates/
├── ap-core/              # 现有
├── ap-runtime/           # 现有
├── ap-gateway/           # 现有
├── ap-fetcher/           # 现有
├── ap-evolution/         # 现有
├── ap-cli/               # 现有，新增 data 子命令
└── ap-cli-backend/       # 新增 crate
    ├── Cargo.toml
    └── src/
        ├── lib.rs
        ├── types.rs          # CLIResult, CLISession, BackendConfig, RoutingConfig
        ├── backend.rs        # CLIBackend trait + GenericCLIBackend 实现
        ├── registry.rs       # CLIBackendRegistry
        ├── router.rs         # CLIRouter — 四策略路由 + fallback
        ├── session.rs        # CLISessionStore (rusqlite)
        ├── parser.rs         # JSON/Text 输出解析
        ├── archive.rs        # 数据库归档（ATTACH DATABASE）
        └── health.rs         # 后端健康检查
```

**依赖关系**：

```toml
[dependencies]
tokio = { version = "1", features = ["process", "rt-multi-thread", "macros", "time"] }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
rusqlite = { version = "0.32", features = ["bundled"] }
toml = "0.8"
thiserror = "2"
tracing = "0.1"
which = "7"          # CLI 可用性检查（替代 shutil.which）
```

### 9.3 核心类型

```rust
// types.rs
use serde::{Deserialize, Serialize};
use std::time::Duration;

/// CLI 后端调用结果
#[derive(Debug, Clone)]
pub struct CLIResult {
    pub text: String,
    pub model: String,
    pub session_id: Option<String>,
    pub input_tokens: Option<u64>,
    pub output_tokens: Option<u64>,
    pub raw_stdout: String,
    pub raw_stderr: String,
    pub returncode: i32,
    pub duration: Duration,
    pub parse_error: bool,
}

/// CLI 后端配置（从 config.toml 反序列化）
#[derive(Debug, Clone, Deserialize)]
pub struct BackendConfig {
    pub api: String,                           // "cli"
    pub command: String,                       // "claude", "gemini", ...
    #[serde(default)]
    pub args: Vec<String>,                     // ["-p"], ["agent", "-m"], ...
    pub system_prompt_flag: String,            // "--system-prompt"
    pub session_flag: String,                  // "--resume"
    #[serde(default = "default_text")]
    pub output_format: String,                 // "json" | "text"
    #[serde(default)]
    pub output_format_flag: String,            // "--output-format", ""（空=不支持）
    #[serde(default)]
    pub json_paths: JsonPathConfig,            // JSON 输出字段映射
    #[serde(default)]
    pub text_patterns: TextPatternConfig,      // Text 模式正则提取
    pub model_map: std::collections::HashMap<String, String>,
    #[serde(default = "default_timeout")]
    pub timeout_secs: u64,
}

/// JSON 输出的字段路径映射
#[derive(Debug, Clone, Deserialize, Default)]
pub struct JsonPathConfig {
    pub text: Option<String>,                  // 必填，如 "result"
    pub session_id: Option<String>,            // "session_id"
    pub model: Option<String>,                 // "model"
    pub input_tokens: Option<String>,          // "usage.input_tokens"
    pub output_tokens: Option<String>,         // "usage.output_tokens"
}

/// Text 模式的正则提取规则
#[derive(Debug, Clone, Deserialize, Default)]
pub struct TextPatternConfig {
    pub session_id: Option<String>,            // "session[:\\s]+([a-f0-9-]+)"
    pub model: Option<String>,
}

fn default_text() -> String { "text".into() }
fn default_timeout() -> u64 { 180 }

/// CLI 后端统一错误类型
#[derive(Debug, thiserror::Error)]
pub enum CLIBackendError {
    #[error("CLI '{0}' not found in PATH")]
    NotInstalled(String),

    #[error("CLI '{command}' timed out after {timeout_secs}s")]
    Timeout { command: String, timeout_secs: u64 },

    #[error("CLI '{command}' exited with code {code}: {stderr}")]
    ExitError { command: String, code: i32, stderr: String },

    #[error("Failed to parse output from '{0}': {1}")]
    ParseError(String, String),

    #[error("Session '{session_id}' not found in backend '{backend}'")]
    SessionNotFound { session_id: String, backend: String },

    #[error("No available CLI backend (fallback disabled)")]
    NoAvailableBackend,

    #[error("All backends unavailable after fallback chain exhausted")]
    AllBackendsUnavailable,

    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),

    #[error("Database error: {0}")]
    Database(#[from] rusqlite::Error),
}

/// 路由配置
#[derive(Debug, Clone, Deserialize)]
pub struct RoutingConfig {
    pub default: String,
    #[serde(default = "default_true")]
    pub fallback_enabled: bool,
    #[serde(default)]
    pub fallback_chain: Vec<String>,
    #[serde(default)]
    pub model_rules: std::collections::HashMap<String, String>,
}

fn default_true() -> bool { true }

/// 数据库生命周期配置
#[derive(Debug, Clone, Deserialize)]
pub struct DataLifecycleConfig {
    #[serde(default = "default_hot")]
    pub hot_days: u32,
    #[serde(default = "default_warm")]
    pub warm_days: u32,
    #[serde(default)]
    pub archive_dir: String,
    #[serde(default = "default_true")]
    pub auto_archive: bool,
}

fn default_hot() -> u32 { 30 }
fn default_warm() -> u32 { 90 }

/// CLI 会话记录
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CLISession {
    pub session_id: String,
    pub name: Option<String>,
    pub backend_name: String,
    pub model: Option<String>,
    pub task_id: Option<String>,
    pub created_at: String,
    pub last_used_at: String,
    pub turn_count: u32,
    pub metadata: Option<String>,
}
```

### 9.4 核心 Trait 和实现

```rust
// backend.rs
use async_trait::async_trait;

#[async_trait]
pub trait CLIBackend: Send + Sync {
    fn name(&self) -> &str;
    fn supported_models(&self) -> &[String];
    fn is_available(&self) -> bool;

    async fn call(
        &self,
        system_prompt: &str,
        user_message: &str,
        session_id: Option<&str>,
    ) -> Result<CLIResult, CLIBackendError>;

    fn list_sessions(&self) -> Result<Vec<String>, CLIBackendError>;
}

/// 通用 CLI 后端 — config-driven，无需为每个 CLI 写子类
pub struct GenericCLIBackend {
    config: BackendConfig,
    available: std::sync::atomic::AtomicBool,
}

#[async_trait]
impl CLIBackend for GenericCLIBackend {
    fn name(&self) -> &str { &self.config.command }

    fn is_available(&self) -> bool {
        self.available.load(std::sync::atomic::Ordering::Relaxed)
    }

    async fn call(
        &self,
        system_prompt: &str,
        user_message: &str,
        session_id: Option<&str>,
    ) -> Result<CLIResult, CLIBackendError> {
        let args = self.build_args(system_prompt, user_message, session_id);
        let start = std::time::Instant::now();

        let output = tokio::time::timeout(
            Duration::from_secs(self.config.timeout_secs),
            tokio::process::Command::new(&self.config.command)
                .args(&args)
                .stdin(std::process::Stdio::null())
                .stdout(std::process::Stdio::piped())
                .stderr(std::process::Stdio::piped())
                .kill_on_drop(true)
                .output(),
        ).await??;

        let duration = start.elapsed();
        let result = self.parse_output(output, duration)?;

        Ok(result)
    }
    // ...
}
```

### 9.5 Router 实现

```rust
// router.rs
pub struct CLIRouter {
    config: RoutingConfig,
    registry: CLIBackendRegistry,
}

impl CLIRouter {
    pub fn resolve(
        &self,
        model_string: Option<&str>,
        explicit_backend: Option<&str>,
        capabilities: Option<&[String]>,
    ) -> Result<Arc<dyn CLIBackend>, CLIBackendError> {
        // 1. 显式指定
        if let Some(name) = explicit_backend {
            return self.registry.get(name);
        }

        // 2. 模型映射
        if let Some(model) = model_string {
            for (pattern, backend_name) in &self.config.model_rules {
                if model_matches_pattern(model, pattern) {
                    if let Ok(backend) = self.registry.get(backend_name) {
                        return Ok(backend);
                    }
                }
            }
        }

        // 3. 默认兜底
        self.registry.get(&self.config.default)
    }

    pub async fn resolve_with_fallback(
        &self,
        model_string: Option<&str>,
        explicit_backend: Option<&str>,
    ) -> Result<Arc<dyn CLIBackend>, CLIBackendError> {
        let primary = self.resolve(model_string, explicit_backend, None);

        if primary.is_ok() || !self.config.fallback_enabled {
            return primary;
        }

        // Fallback chain
        for name in &self.config.fallback_chain {
            if let Ok(backend) = self.registry.get(name) {
                if backend.is_available() {
                    return Ok(backend);
                }
            }
        }

        primary
    }
}
```

### 9.6 Session Store (rusqlite)

```rust
// session.rs
use rusqlite::{Connection, params};

pub struct CLISessionStore {
    conn: Connection,
}

impl CLISessionStore {
    pub fn open(db_path: &Path) -> Result<Self, rusqlite::Error> {
        let conn = Connection::open(db_path)?;
        conn.execute_batch(SCHEMA)?;
        Ok(Self { conn })
    }

    pub fn save(&self, session: &CLISession) -> Result<()> {
        self.conn.execute(
            "INSERT OR REPLACE INTO cli_sessions
             (session_id, name, backend_name, model, task_id,
              created_at, last_used_at, turn_count, metadata)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
            params![
                session.session_id, session.name, session.backend_name,
                session.model, session.task_id, session.created_at,
                session.last_used_at, session.turn_count, session.metadata,
            ],
        )?;
        Ok(())
    }

    pub fn get_by_task(&self, task_id: &str) -> Result<Vec<CLISession>> {
        let mut stmt = self.conn.prepare(
            "SELECT * FROM cli_sessions WHERE task_id = ?1 ORDER BY created_at DESC"
        )?;
        let rows = stmt.query_map(params![task_id], |row| {
            Ok(CLISession { /* ... field mapping ... */ })
        })?;
        rows.collect()
    }

    pub fn archive_old_data(&self, hot_days: u32, archive_path: &Path) -> Result<u64> {
        self.conn.execute(
            &format!("ATTACH DATABASE '{}' AS archive", archive_path.display()),
            [],
        )?;
        // ... 同 Python 方案的 ATTACH + INSERT + DELETE 逻辑
    }
}
```

### 9.7 与现有 Rust 平台的集成

```
ap-cli (CLI 入口)
  ├── agent-nexus data archive    → ap-cli-backend::session::archive
  ├── agent-nexus data stats      → ap-cli-backend::session::stats
  └── agent-nexus run ...         → ap-gateway → ap-cli-backend::router::resolve

ap-gateway (MCP Gateway)
  └── 当 provider.api = "cli" 时 → ap-cli-backend::CLIBackend::call()

ap-core (核心类型)
  └── 新增 CLI provider variant 到现有的 ProviderApi 枚举
```

**Cargo.toml workspace 更新**：

```toml
[workspace]
members = [
    "crates/ap-core",
    "crates/ap-runtime",
    "crates/ap-gateway",
    "crates/ap-fetcher",
    "crates/ap-evolution",
    "crates/ap-cli",
    "crates/ap-cli-backend",     # 新增
]
```

### 9.8 Python-Rust 双端共存策略

两个实现共享同一套 config.toml 和 SQLite schema，运行时选择：

- **Python 平台**：`LLMClient` → `cli_backend/` → `subprocess.run`
- **Rust 平台**：`ap-cli-backend` → `tokio::process::Command`

过渡期两套并存，最终 CLI 调度层全部迁入 Rust（因为性能优势明显），Python 只保留 Agent Runtime。

## 10. Operational Details

### 10.1 健康检查机制

`is_available()` 执行两步检查：

1. **二进制存在检查**：`which::which(command)` — 确认 CLI 已安装
2. **版本检查**：执行 `<command> --version`，解析版本号存入 `backend_health.version`

检查时机：
- 启动时全量检查（`CLIBackendRegistry` 初始化）
- 调用失败后标记不可用（`AtomicBool`）
- 每 N 次调用后定期重新检查（可配置，默认每 10 次调用）

### 10.2 SQLite 并发访问

多进程（多个 Agent 子进程）可能同时写同一个 SQLite DB。启用 WAL mode：

```sql
PRAGMA journal_mode=WAL;       -- Write-Ahead Logging，允许并发读写
PRAGMA busy_timeout=1000;      -- 写锁冲突时等待 1s，快速失败后交由应用层 retry
PRAGMA synchronous=NORMAL;     -- 平衡性能与安全
```

### 10.3 数据库归档与统计触发时机

| 操作 | 实现方式 | 说明 |
|------|---------|------|
| `daily_stats` rollup | SQLite Trigger `AFTER INSERT/DELETE ON task_executions` | 应用层零额外代码，INSERT 即自动更新统计 |
| `auto_archive` 归档 | 应用层 `CLISessionStore.archive()` | Trigger 无法做 ATTACH DATABASE 跨库操作 |
| 手动归档 | `agent-nexus data archive` 命令 | 不受 `auto_archive` 开关限制 |

**daily_stats Trigger（自动维护）**：

```sql
-- INSERT 时自动 upsert daily_stats
CREATE TRIGGER trg_update_daily_stats
AFTER INSERT ON task_executions
BEGIN
    INSERT INTO daily_stats (date, backend_name, total_calls, success_calls,
                             total_input_tokens, total_output_tokens, avg_duration_ms)
    VALUES (DATE('now'), NEW.backend_name, 1,
            CASE WHEN NEW.status = 'success' THEN 1 ELSE 0 END,
            COALESCE(NEW.input_tokens, 0), COALESCE(NEW.output_tokens, 0),
            COALESCE(NEW.duration_ms, 0))
    ON CONFLICT(date, backend_name) DO UPDATE SET
        total_calls = total_calls + 1,
        success_calls = success_calls + CASE WHEN NEW.status = 'success' THEN 1 ELSE 0 END,
        total_input_tokens = total_input_tokens + COALESCE(NEW.input_tokens, 0),
        total_output_tokens = total_output_tokens + COALESCE(NEW.output_tokens, 0),
        avg_duration_ms = (avg_duration_ms * (total_calls - 1) + COALESCE(NEW.duration_ms, 0)) / total_calls;
END;

-- DELETE 时同步减少（归档清理场景）
CREATE TRIGGER trg_delete_daily_stats
AFTER DELETE ON task_executions
BEGIN
    UPDATE daily_stats SET
        total_calls = total_calls - 1,
        success_calls = success_calls - CASE WHEN OLD.status = 'success' THEN 1 ELSE 0 END,
        total_input_tokens = total_input_tokens - COALESCE(OLD.input_tokens, 0),
        total_output_tokens = total_output_tokens - COALESCE(OLD.output_tokens, 0)
    WHERE date = DATE(OLD.created_at) AND backend_name = OLD.backend_name;
END;
```

**归档触发时机**：`CLISessionStore.cleanup()` 中当 `auto_archive = true` 时自动执行归档。

### 10.4 与现有 TaskGraph SQLite 的关系

TaskGraph 已有独立的 SQLite 使用（`~/.agent-nexus/data/` 下）。CLI Backend 的表加入**同一个 `agent-nexus.db` 文件**：
- 共享 WAL mode 配置
- 共享连接池（Rust 侧）
- 避免 DB 文件碎片化

如果 TaskGraph 使用不同 DB 文件，CLI Backend 表创建在 `agent-nexus.db`，TaskGraph 保持不变。

## 11. Testing Strategy

| 层级 | 测试内容 | 方式 |
|------|---------|------|
| Unit | CLIRouter 路由决策 | mock config，验证四种策略优先级 |
| Unit | CLIBackend 命令拼接 | mock tokio::process::Command，验证 args 组装 |
| Unit | SessionStore CRUD + 归档 | in-memory SQLite |
| Unit | CLI 输出解析 (JSON/Text) | 预录 stdout fixture（各 CLI 格式） |
| Unit | 降级链 + fallback 开关 | mock registry，验证 fallback_enabled 行为 |
| Unit | archive 归档逻辑 | ATTACH DATABASE 集成测试 |
| Integration | 真实 CLI 调用 | `#[cfg(feature = "integration")]` feature gate |
| E2E | Agency Pipeline → CLI Backend → 结果 | 完整链路，可选依赖 |

## 12. Scope & Out of Scope

### In Scope (MVP)

- CLIBackend ABC + config-driven 命令拼接
- CLIRouter 四策略路由 + fallback 开关
- CLISessionStore SQLite 持久化
- CLIBackendRegistry 健康检查
- LLMClient 集成（ProviderApiType.CLI）
- 6 个 CLI 的 config 模板（Claude Code, Gemini CLI, Codex CLI, OpenClaw, Hermes, Nanobot）
- 数据库归档策略
- Unit tests + 可选的 Integration tests

### Out of Scope (Future)

- 能力匹配路由（需要 CLI capability registry）
- 流式输出（SSE streaming from CLI）
- CLI 特有功能暴露为 MCP tools
- Web dashboard for statistics
- 自动学习路由策略（基于历史成功率动态调整）
