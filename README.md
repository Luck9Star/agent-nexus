# Agent Nexus

<p align="center">
  <strong>MCP-native Agent Platform | Self-built Multi-Agent Orchestration | Git-based Distribution</strong><br>
  <strong>MCP 原生 Agent 平台 | 自建多 Agent 编排 | Git 分发体系</strong>
</p>

---

Agent Nexus is an MCP-native Agent platform providing self-built multi-agent orchestration infrastructure, a Python runtime execution layer, a self-evolution engine, and expert capability composition. Agents are distributed via Git repositories (Homebrew tap model), run locally, and use user-configured models.

Agent Nexus 是一个 MCP 原生的 Agent 平台，提供自建多 Agent 编排基础设施、Python Runtime 执行层、自进化引擎和专家能力编排。Agent 通过 Git 仓库分发（类 Homebrew Tap 模型），本地运行，使用用户自配模型。

---

## Core Features | 核心特性

- **Self-built Orchestration | 自建编排层** — References ClawTeam's proven patterns (TaskStore, Mailbox, SpawnBackend), simplified and self-built. TaskGraph (SQLite + DAG + cycle detection), IPC (JSON-lines), ProcessManager (async subprocess + health check).
- **MCP-native | MCP 原生** — Each Agent ships a FastMCP Server; MCP Gateway handles unified routing and discovery. MCP protocol boundary = language boundary.
- **Git-based Distribution | Git 分发** — Official monorepo + private repos + direct URLs. No cloud infrastructure needed. Homebrew Tap model.
- **Dual Language | 双语言实现** — Python platform complete (Phases 1-6), Rust platform rewrite in progress (6 crates, ~18K LOC).
- **Self-Evolution Engine | 自进化引擎** — OpenSpace-inspired design, three-level progression: Atomic Skill Evolution → Composite Orchestration Evolution → Agent Promotion.
- **User-configured Models | 用户自配模型** — Supports OpenAI, Anthropic, Ollama, and custom API backends. Bring your own API key.

---

## Architecture | 架构概览

Four-layer architecture (top-down) | 四层架构（自上而下）：

```
┌─────────────────────────────────────────────────┐
│ Layer 1: MCP Exposure | 曝光层                    │
│ FastMCP Server per Agent + Gateway routing       │
├─────────────────────────────────────────────────┤
│ Layer 2: Orchestration | 编排层（自建）            │
│ TaskGraph (SQLite DAG) + IPC (JSON-lines)        │
│ ProcessManager + OrchestrationDSL (TOML)         │
├─────────────────────────────────────────────────┤
│ Layer 3: Python Runtime | 运行时                  │
│ IPythonRuntime (CaveAgent-based) + SecurityChecker│
├─────────────────────────────────────────────────┤
│ Layer 4: Self-Evolution | 自进化引擎              │
│ Skill → Orchestration → Agent Promotion          │
└─────────────────────────────────────────────────┘
```

## Agent System | Agent 体系

| Type | Count | Examples | 示例 |
|------|-------|----------|------|
| **Atomic** | 11 | doc-filler, code-reviewer, security-scanner, test-suite-generator |
| **Composite** | 5 | feature-delivery-pipeline, product-documentation-suite |

Four run modes: **MCP standalone** / **Platform Router** / **CLI standalone** / **Agency Pipeline**
四种运行模式：**MCP 独立运行** / **Platform Router 调度** / **CLI 独立运行** / **Agency Pipeline**

### Model Capability System | 模型能力系统

Three-layer model capability resolution | 三层模型能力解析：

```
Built-in Data (17 models)  →  optional models.dev enrichment  →  LLMClient consumption
内置数据（17 个模型）        →  可选 models.dev 增强            →  LLMClient 消费
```

- **Dynamic max_tokens**: Reads from capability data instead of hardcoded 4096 | 从能力数据动态获取，不再硬编码 4096
- **Temperature clamping**: Clamps to model's `[temperature_min, temperature_max]` range | 钳位到模型支持的温度范围
- **`supports_temperature` gate**: Skips temperature/top_p for models that don't support them | 对不支持温度的模型自动跳过
- **models.dev enrichment**: Auto-fetched on init, silent fallback to built-in data on failure | 初始化时自动拉取，失败静默回退
- **Auto model inference**: Extracts real model name from API response to self-correct capability data | 从 API 返回中提取真实模型名，自校正能力数据

---

## Agency Expert Orchestration | Agency 专家编排

Agent Nexus integrates the [agency-agents](https://github.com/nicepkg/agency-agents) expert pool, supporting dynamic capability decomposition, expert selection, and concurrent execution.

Agent Nexus 集成 [agency-agents](https://github.com/nicepkg/agency-agents) 专家池，支持动态能力拆解、专家选择、并发执行：

```
User Task → Capability Inference → Expert Selection → DAG Build → LLM Concurrent Execution → Result Integration → QA Gate
用户任务   → 能力推断           → 专家选择        → DAG 构建  → LLM 并发执行            → 结果整合         → QA 门禁
```

### Pipeline | 编排流水线

| Stage | Module | Description | 说明 |
|-------|--------|-------------|------|
| Import | `AgencyImporter` | Import expert profiles from vendor repo, with allowlist filtering | 从 vendor 仓库导入专家 profile，支持 allowlist 过滤 |
| Register | `ExpertRegistry` | Expert capability indexing, set-cover selection | 专家能力索引，支持 set-cover 选择 |
| Infer | `LLMPlanner` / `infer_capabilities()` | Semantic task decomposition → capabilities (LLM + 关键词回退) | 语义任务拆解 → 能力标签（LLM + 关键词回退） |
| Select | `SpecialistSelector` | Greedy set-cover, optimal expert combination | 贪心集合覆盖，选出最优专家组合 |
| Plan | `DynamicCompositePlanner` | Build DAG based on capability subset relations | 基于能力子集关系构建 DAG |
| Execute | `LLMExecutor` + `DAGDispatcher` | Concurrent LLM calls via ThreadPoolExecutor, per-expert model override | 并发 LLM 调用，支持按专家覆盖模型 |
| Integrate | `LLMIntegrator` / `Integrator` | Semantic multi-expert synthesis (LLM + 规则回退) | 多专家结果语义合成（LLM + 规则回退） |
| Validate | `LLMQualityGate` / `QAGate` | Semantic quality evaluation + structural compliance (LLM 双层门禁) | 语义质量评估 + 结构合规（LLM 双层门禁） |
| **Capability** | `ModelCapabilityRegistry` | Dynamic max_tokens / temperature / vision from built-in data + models.dev enrichment | 动态模型能力数据（内置17模型 + models.dev 增强） |

### CLI Commands | CLI 命令

See [CLI Reference](docs/cli.md) for the complete command documentation (17 commands). | 完整命令行文档（17 个命令）见 [CLI 参考](docs/cli.md)。

---

## Quick Start | 快速开始

See the [Quick Start Guide](docs/quick-start.md) for a 5-minute setup walkthrough. | 5 分钟上手指南见 [快速开始](docs/quick-start.md)。

For full configuration options, see [Configuration Reference](docs/configuration.md). | 完整配置选项见 [配置参考](docs/configuration.md)。

Model string format | 模型字符串格式：`provider:model_name`（例如 `anthropic:claude-sonnet-4-20250514`、`api:MiniMax-M2.7-highspeed`）

```bash
export AGENT_MODEL=gpt-4o           # Default agent model | 默认 Agent 模型
export DEFAULT_MODEL=gpt-4o         # Global default model | 全局默认模型
export OPENAI_API_KEY=sk-...        # OpenAI
export ANTHROPIC_API_KEY=sk-ant-... # Anthropic
export OLLAMA_BASE_URL=http://...   # Ollama local models
```

Supported API formats | 支持的 API 格式：`anthropic-messages`, `openai-compatible`, `ollama`
Lint & Format | 代码检查与格式化：`ruff check --fix src/` · `ruff format src/` · Type Check | 类型检查：`ty check src/`

---

## Project Structure | 项目结构

```
agent-nexus/
├── src/agent_nexus/              # Platform core | 平台核心
│   ├── platform/
│   │   ├── agency/               # Expert orchestration pipeline | 专家编排流水线
│   │   ├── orchestration/        # TaskGraph, ProcessManager, IPC, DSL
│   │   ├── router/               # Platform Router (4-Phase Workflow)
│   │   ├── gateway/              # MCP Gateway
│   │   ├── config/               # Model config + Provider registry
│   │   ├── runtime/              # Python Runtime (CaveAgent-based)
│   │   ├── skills/               # Skill Loader
│   │   ├── evolution/            # Self-Evolution Engine
│   │   └── local/                # CLI + Git Installer + Supervisor
│   └── models/                   # Shared data models | 共享数据模型
├── agents/                       # Official Agent packages | 官方 Agent 包
│   ├── atomic/                   # 20 Atomic Agents
│   └── composite/                # 5 Composite Agents
├── crates/                       # Rust platform rewrite | Rust 平台重写
│   ├── ap-core/                  # Core: TaskGraph, StateMachine, IPC, Hooks, DSL
│   ├── ap-cli/                   # CLI: clap derive, 9 commands
│   ├── ap-gateway/               # MCP Gateway: deferred loading, tool aggregation
│   ├── ap-fetcher/               # Git-based agent distribution
│   ├── ap-evolution/             # Self-Evolution Engine (SQLite)
│   └── ap-runtime/               # Python subprocess bridge
├── tests/                        # Tests | 测试
│   ├── unit/                     # Unit tests | 单元测试
│   ├── integration/              # Integration tests | 集成测试
│   └── e2e/                      # E2E tests | 端到端测试
├── templates/                    # OrchestrationDSL TOML templates
├── docs/                         # Design documents | 设计文档
├── config/                       # Sample configs | 配置样例
├── vendor/agency-agents/         # Expert repo (submodule) | 专家仓库
├── Cargo.toml                    # Rust workspace
├── pyproject.toml                # Python package config
└── uv.lock                       # Python lock file
```

---

## Tech Stack | 技术栈

| Layer | Technology | 技术 |
|-------|------------|------|
| Python Platform | Python 3.11+, Pydantic, FastMCP, Typer, asyncio |
| Rust Platform | Rust 2021, Tokio, Axum, Rusqlite, Clap, Git2 |
| Protocol | MCP (stdio/SSE), JSON-lines IPC, TOML DSL |
| Storage | SQLite (TaskGraph + Evolution), TOML (config) |
| Distribution | Git (Homebrew Tap model) |

---

## Security | 安全架构

Defense-in-depth | 纵深防御：

1. **Process Boundary | 进程边界** — Agents run as independent subprocesses / Agent 以独立子进程运行
2. **PermissionChecker** — Pre-execution permission check (DEFAULT / PLAN / FULL_AUTO) / 执行前权限检查
3. **SecurityChecker** — Runtime AST-level code safety analysis / 运行时 AST 级别代码安全分析

---

## Key Design Decisions | 关键设计决策

- **Self-built Orchestration | 自建编排** — References ClawTeam's proven patterns (TaskStore, MailboxManager, SpawnBackend). No external pip dependency.
- **MCP Boundary = Language Boundary | MCP 边界 = 语言边界** — Rust platform communicates with Python Agent subprocesses via MCP stdio/SSE. Agent internals stay Python forever.
- **Git-based Distribution | Git 分发** — Homebrew tap model, no cloud infrastructure needed.
- **Rust Rewrite Scope | Rust 重写范围** — Upper layers only (Gateway, Fetcher, Evolution, CLI). Agent Runtime stays Python.

---

## Development | 开发

```bash
# Python tests | Python 测试
uv run pytest tests/               # All tests | 全部
uv run pytest tests/ -m unit       # Unit tests only | 单元测试
uv run pytest tests/ -m e2e        # E2E tests only | E2E 测试

# Lint & Format | 代码检查与格式化
uv run ruff check src/ agents/
uv run ruff check --fix src/ agents/
uv run ruff format src/ agents/

# Type Check | 类型检查
uv run ty check src/              # ty v0.0.32+ (brew install ty)

# Rust
cargo test --workspace             # All crates | 全部 crate
cargo clippy --workspace           # Lint
cargo test -p ap-core              # Single crate | 单个 crate
```

---

## Documentation | 文档

Full design docs in `docs/`. See `docs/README.md` for the navigation index.
完整设计文档位于 `docs/` 目录，详见 `docs/README.md`。

| Document | Content | 内容 |
|----------|---------|------|
| `docs/01-overview.md` | Product positioning & core architecture | 产品定位与核心架构 |
| `docs/02-clawteam-integration.md` | Orchestration layer design | 编排层设计 |
| `docs/03-python-runtime.md` | Python Runtime | Python 运行时 |
| `docs/04-self-evolution.md` | Self-Evolution Engine | 自进化引擎 |
| `docs/05-agent-system.md` | Agent system (Atomic/Composite) | Agent 体系 |
| `docs/06-mcp-communication.md` | MCP communication matrix | MCP 通信矩阵 |
| `docs/07-marketplace.md` | Git distribution & quality gates | Git 分发与质量门禁 |
| `docs/08-constraints-decisions.md` | Constraints & decisions | 约束与决策 |
| `docs/09-implementation-plan.md` | Implementation plan (7 phases) | 实施计划 |
| `docs/testing.md` | Testing overview & conventions | 测试概览与规范 |

---

## License | 许可证

MIT License
