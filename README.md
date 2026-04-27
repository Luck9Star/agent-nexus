# Agent Nexus

MCP 原生 Agent 平台，自建编排引擎，Git 分发体系。提供 Agent 生命周期管理、专家能力编排、Python Runtime 执行层和自演化引擎。

## 架构概览

四层架构（自上而下）：

```
┌─────────────────────────────────────────────┐
│           MCP Exposure Layer                │  FastMCP Server + Gateway 路由
├─────────────────────────────────────────────┤
│           Orchestration Layer               │  TaskGraph · IPC · ProcessManager · DSL
├─────────────────────────────────────────────┤
│           Runtime Layer                     │  Python Runtime (CaveAgent IPython)
├─────────────────────────────────────────────┤
│           Evolution Engine                  │  Atomic Skill → Composite → Agent Promotion
└─────────────────────────────────────────────┘
```

- **Agent 类型**：Atomic（11 个）+ Composite（5 个）
- **运行模式**：MCP 独立 / Platform Router / CLI 独立
- **双重实现**：Python 平台（生产）+ Rust 平台重写（6 crate，进行中）

## Agency 专家编排

Agent Nexus 集成 [agency-agents](https://github.com/nicepkg/agency-agents) 专家池，支持动态能力拆解、专家选择、并发执行：

```
用户任务 → 能力推断 → 专家选择 → DAG 构建 → LLM 并发执行 → 结果整合 → QA 门禁
```

### 快速开始

**1. 配置 LLM API**

编辑 `~/.agent-nexus/config.toml`：

```toml
[models]
default = "api:MiniMax-M2.7-highspeed"

[models.providers.api]
base_url = "http://your-api-endpoint:3006"
api_key_env = "API_API_KEY"
api = "anthropic-messages"   # 或 "openai-compatible"
```

在 `~/.agent-nexus/.env` 中设置 API Key：

```
API_API_KEY="sk-your-api-key"
```

**2. 准备专家仓库**

```bash
# agency-agents 作为 vendor 引入
git submodule add https://github.com/nicepkg/agency-agents.git vendor/agency-agents
```

**3. 执行专家编排**

```bash
# 查看可用专家
uv run python -m agent_nexus.platform.agency.cli list-experts \
  --vendor-path vendor/agency-agents \
  --allowlist config/agency-agents.allowlist.yaml

# 规划 DAG（不执行 LLM）
uv run python -m agent_nexus.platform.agency.cli plan-composition \
  --task "评审支付系统的安全性和架构设计" \
  --vendor-path vendor/agency-agents \
  --allowlist config/agency-agents.allowlist.yaml

# 完整执行：编排 → LLM 调用 → 整合 → QA
uv run python -m agent_nexus.platform.agency.cli run-composition \
  --task "评审支付系统的安全性和架构设计" \
  --vendor-path vendor/agency-agents \
  --allowlist config/agency-agents.allowlist.yaml \
  --max-parallel 3
```

### 编排流水线

| 阶段 | 模块 | 说明 |
|------|------|------|
| 导入 | `AgencyImporter` | 从 vendor 仓库导入专家 profile，支持 allowlist 过滤 |
| 注册 | `ExpertRegistry` | 专家能力索引，支持 set-cover 选择 |
| 推断 | `infer_capabilities()` | 自然语言 → 能力标签（中英文） |
| 选择 | `SpecialistSelector` | 贪心集合覆盖，选出最优专家组合 |
| 规划 | `DynamicCompositePlanner` | 基于能力子集关系构建 DAG |
| 执行 | `LLMExecutor` + `DAGDispatcher` | 并发 LLM 调用，ThreadPoolExecutor |
| 整合 | `Integrator` | 多专家结果合并 |
| 验证 | `QAGate` | 输出合规性检查 |

### CLI 命令

| 命令 | 说明 |
|------|------|
| `import-experts` | 导入专家 profile（支持 dry-run） |
| `list-experts` | 预览可用专家 |
| `plan-composition` | 规划编排 DAG（不执行） |
| `run-composition` | 完整编排执行（LLM + 并发 + QA） |
| `check-profiles` | 校验已导入的 profile |
| `validate-output` | 校验专家输出合规性 |

## 安装

```bash
# Python 平台
git clone https://github.com/Luck9Star/agent-nexus.git
cd agent-nexus
uv sync

# Rust 平台（可选）
cargo build --workspace
cargo test --workspace
```

## 开发

```bash
# 测试
uv run pytest tests/               # 全部
uv run pytest tests/ -m unit       # 单元测试
uv run pytest tests/ -m e2e        # E2E 测试

# Lint & 格式化
uv run ruff check src/ agents/
uv run ruff format src/ agents/

# Rust
cargo test          # 全部 crate
cargo clippy        # Lint
```

## 项目结构

```
agent-nexus/
├── src/agent_nexus/          # 平台核心
│   ├── platform/
│   │   ├── agency/           # 专家编排流水线
│   │   ├── orchestration/    # TaskGraph · ProcessManager · IPC · DSL
│   │   ├── gateway/          # MCP Gateway
│   │   ├── config/           # 模型配置 + Provider 注册
│   │   ├── runtime/          # Python Runtime
│   │   └── evolution/        # 自演化引擎
│   └── models/               # 共享数据模型
├── agents/                   # Agent 包（每个独立 pyproject.toml）
│   ├── atomic/               # 11 个 Atomic Agent
│   └── composite/            # 5 个 Composite Agent
├── crates/                   # Rust 平台重写
│   ├── ap-core/              # TaskGraph · ProcessManager · StateMachine · DSL
│   ├── ap-cli/               # CLI（clap derive）
│   ├── ap-gateway/           # MCP Gateway
│   ├── ap-fetcher/           # Git Agent 分发
│   ├── ap-evolution/         # 自演化引擎
│   └── ap-runtime/           # Python 子进程桥接
├── tests/                    # 测试
├── docs/                     # 设计文档
├── config/                   # 配置样例
└── vendor/agency-agents/     # 专家仓库（submodule）
```

## 配置

- **模型优先级**：环境变量 > Agent 配置 > 默认值
- **环境变量**：`AGENT_MODEL`、`DEFAULT_MODEL`、`OPENAI_API_KEY`、`ANTHROPIC_API_KEY`、`OLLAMA_BASE_URL`
- **配置文件**：`~/.agent-nexus/config.toml`
- **支持 API 格式**：`anthropic-messages`、`openai-compatible`、`ollama`

## 关键设计决策

- **自建编排**：参考 ClawTeam 验证过的模式（TaskStore、MailboxManager、SpawnBackend），无外部 pip 依赖
- **MCP 边界 = 语言边界**：Rust 平台通过 MCP stdio/SSE 与 Python Agent 子进程通信
- **Git 分发**：Homebrew tap 模型，无需云端基础设施
- **Rust 重写范围**：仅上层（Gateway、Fetcher、Evolution、CLI），Agent Runtime 保持 Python

## License

MIT
