# 技术约束与设计决策

> Agent Nexus Design Doc — §10 技术约束与设计决策：设计约束、技术约束、质量约束、模型配置、许可证

> **Status**: ✅ Implemented
> **Code**: `src/agent_nexus/platform/runtime/security_checker.py`, `src/agent_nexus/platform/runtime/permission_checker.py`, `src/agent_nexus/platform/config/`
> **Tests**: `tests/unit/test_security_checker.py`, `tests/unit/test_security_rules.py`, `tests/unit/test_permission_checker.py`, `tests/unit/test_config_models.py`, `tests/unit/test_config_model_config.py`, `tests/unit/test_config_defaults.py`

## §10 技术约束与设计决策

### 10.1 设计约束

| # | 约束 | 说明 |
|---|------|------|
| 1 | ClawTeam 是参考实现不是依赖 | 参考 ClawTeam 经过验证的编排模式按需自建，不作为 pip 依赖引入 |
| 2 | 用户本地运行 | Agent 运行在本地，用户自配模型。初期通过 Git 仓库分发 Agent 包，无需云基础设施 |
| 3 | 无计费功能 | 平台免费使用，不包含计费/订阅功能 |
| 4 | Composite Agent = OrchestrationDSL TOML | 使用自建 OrchestrationDSL（TOML DAG）定义编排 |
| 5 | Atomic Agent = PydanticAI + Runtime + MCP | 三合一实现 |
| 6 | Python Runtime 优先 | Agent 内部使用 IPythonRuntime，MCP 用于外部通信 |
| 7 | Agent 是子进程 | 使用 ProcessManager（asyncio.subprocess）管理 |
| 8 | Skill 可晋升为 Agent | effective_rate 超阈值且覆盖完整工作流时提升 |
| 9 | Rust 上层重构 | Python 做 MVP 验证，上层（Gateway、Supervisor、Fetcher、CLI）用 Rust 重构，Agent Runtime 保持 Python 不变 |
| 10 | MCP 协议边界 = 语言边界 | Rust 平台通过 MCP stdio/SSE 与 Python Agent 子进程通信，Agent 内部代码完全不受 Rust 重构影响 |
| 11 | 接口格式不变 | Rust 重构后 config.toml、lockfile.json、sources.yaml、MCP 协议等接口格式保持不变 |
| 12 | 配置格式不变 | config.toml、lockfile.json、agent-manifest.yaml 格式不随语言切换而改变 |
| 13 | Agent Package 格式不变 | pyproject.toml + agent-manifest.yaml + src/ 结构不随平台语言切换而改变 |
| 14 | 逐模块替换 | Rust 重构采用逐模块替换策略，不搞 big bang 重写 |

### 10.2 技术约束

| # | 约束 | 说明 |
|---|------|------|
| 1 | 零外部依赖（内部通信） | Router → Agent 只用 stdin/stdout，不用 HTTP 服务器 |
| 2 | 编排层自建 | 参考 ClawTeam 实现，按需精简自建 |
| 3 | MCP 为基 | 所有 Agent 暴露为 MCP Server |
| 4 | SKILL.md 先行 | 每个 Agent 必须先有 SKILL.md，再实现代码 |
| 5 | Runtime-First Hybrid | Python Runtime 优先，不完全抛弃 Tool Call |
| 6 | Python Agent Runtime 不动 | Agent 内部代码（pydantic-ai、业务逻辑、Python 依赖）保持 Python，通过 MCP 与 Rust 平台通信 |
| 7 | uv 管理不变 | venv 创建和依赖安装始终通过 uv，无论平台用 Python 还是 Rust |
| 8 | rmcp 官方 SDK | Rust MCP 通信使用 rmcp crate（官方 Rust SDK），支持 stdio/SSE/StreamableHTTP |
| 9 | axum HTTP 框架 | Rust HTTP 服务使用 axum（与 crates.io 同款） |
| 10 | git2 Git 操作 | Rust Git 操作使用 git2 crate（libgit2 绑定），支持 clone、tag、sparse checkout |
| 11 | semver 版本解析 | Rust 版本解析使用 semver crate，与 Python packaging 版本规范兼容 |

### 10.3 Token 优化约束

> **参考来源**: nanobot `docs/proposals/tool-skill-deferred-loading.md` — Deferred Tool Loading 行业调研与实现方案; OpenClaw/SoulClaw 社区验证方案; Anthropic Tool Search API 实测数据

#### 10.3.1 Context Budget 硬限制

| 参数 | 上限 | 说明 |
|------|------|------|
| Layer 0（身份核心） | ≤ 800 tokens | 每轮注入，Agent 名称 + 描述 + 可用 Tools 名称 + Type 名称 + Evolution Metrics 摘要 |
| Layer 1（执行上下文） | ≤ 3,000 tokens | 首轮注入，Tool Schema + Function 签名 + 相关 Type Schema + 当前任务进化建议 |
| Bootstrap 总量 | ≤ 5,000 tokens | L0 + L1 合计不超过 5K tokens |
| 单文件大小上限 | ≤ 8,000 chars | 单个 bootstrap 文件（SKILL.md、manifest 等）超过则截断（参考 OpenClaw `bootstrapMaxChars: 20,000`，我们更保守） |
| Compaction 触发阈值 | 80% | Context 使用率超过 80% 触发压缩 |
| Compaction 目标 | 40% | 压缩后 Context 使用率降至 40% |
| Session 硬上限 | context_window × 95% | 超过时强制截断历史消息 + 写入告警日志，防止 session 冻结（nanobot #3029, #2638 教训） |

#### 10.3.2 Tiered Context 分层规则

> **Continuation-Skip 语义**：L0 每轮注入，L1 仅首轮注入（后续轮跳过，参考 OpenClaw #9157 修复）。Compaction 后重注入 L0 + L1 摘要（不全量），避免正反馈死循环。

| 组件 | L0（每轮） | L1（首轮） | L2（按需） | L3（运行时） |
|------|----------|----------|----------|------------|
| Agent Identity | 名称 + 一句话描述 | — | — | — |
| Tool Schema | Tool 名称列表 | 完整 JSON Schema | — | — |
| Runtime Variables | name + description | — | — | Variable 当前值 |
| Runtime Functions | — | name + description + 签名 | — | — |
| Runtime Types | Type 名称列表 | 当前任务相关 Type Schema | 完整 JSON Schema | — |
| Evolution Data | Metrics 摘要 | 当前任务进化建议 | 历史建议 + FIX/DERIVED/CAPTURED | — |
| Cross-Agent Data | — | — | — | Data Reference（~50 tokens） |

#### 10.3.3 Compaction 防死循环

| 规则 | 值 | 来源 |
|------|------|------|
| `min_turns_between_compactions` | 5 | OpenClaw #68032 教训 |
| 重注入范围 | L0 + L1 摘要 | 避免 Compaction 后立即再次溢出 |
| 强制截断阈值 | 90% context window | 兜底保护，截断最早历史消息 |
| 连续 Compaction 告警 | 3 次 | 写入 `context_budget_log`，触发人工介入 |

#### 10.3.4 Agent 级 Deferred Loading

- MCP Gateway 以 **Agent 为粒度**（非 Tool 级）做 deferred loading
- LLM 先看 Agent manifest（名称 + 描述），按需激活 Agent 的完整 tool schema
- 基础方案：`search_and_activate()` 标准 function calling（Provider-Agnostic）
- 加速方案：Anthropic 用户可选用原生 `defer_loading` 零 round-trip 加速
- 激活 Agent 同时启动子进程（信息优化 + 资源优化合一）

#### 10.3.5 Token 开销估算

> **数据来源**: nanobot 社区实测 + Anthropic Tool Search 基准测试 + OpenClaw/SoulClaw 对比

**Agent Nexus Bootstrap Token 开销（每 Agent）**:

| 组件 | 估算 Token | 说明 |
|------|-----------|------|
| SKILL.md Metadata（L0） | ~100-200 | 名称 + 描述 + triggers |
| Tool 名称列表（L0） | ~50-100 | 每个 Agent 3-5 个工具 |
| Type 名称列表（L0） | ~30-50 | Runtime Type 名称 |
| Evolution Metrics 摘要（L0） | ~50-100 | effective_rate + 3 指标 |
| **L0 小计** | **~230-450** | **≤ 800 预算** |
| SKILL.md Body（L1） | ~200-500 | 三层内容 |
| Tool Schema 完整（L1） | ~600-2,000 | 每个 ~200-400 × 3-5 tools |
| Runtime Functions/Variables（L1） | ~100-300 | 签名描述 |
| 相关 Type Schema（L1） | ~100-300 | 当前任务相关 |
| 进化建议（L1） | ~100-200 | 当前任务 FIX/DERIVED |
| **L1 小计** | **~1,100-3,300** | **≤ 3,000 预算** |

**平台级 Token 开销（MCP Gateway 聚合后）**:

| 场景 | 全量 Token | Deferred 后 Token | 节省 |
|------|-----------|-----------------|------|
| 15 Agents (Router 模式) | 30,000-60,000 | 3,000-5,000 (manifest) | **83-92%** |
| 单 Composite (4 Atomic) | 6,000-12,000 | 1,000-2,000 | **75-83%** |
| MCP Standalone (单 Agent) | 600-4,000 | 600-4,000 (无需优化) | 0% |
| 后续轮次 (Tiered Loading) | 全量重复 | L0 only (~800) | **~60%** |

**行业基准参考**:

| 方案 | 来源 | Token 节省 | 适用规模 |
|------|------|-----------|---------|
| Agent 级 Deferred (本项目) | 自研 | 83-92% | Agent 10+ |
| Tool 级 Deferred | nanobot 提案 | 56-83% | Tool 20+ |
| Anthropic 原生 Tool Search | Anthropic API | 85% (实测) | Tool ≤ 10,000 |
| Tiered Bootstrap (SoulClaw) | 社区验证 | 52-62% | 通用 |
| ToolRAG (向量检索) | @antl3x/toolrag | ~89% | Tool 200+ (需 embedding) |

#### 10.3.6 Provider 兼容性矩阵

Deferred Loading 的实现依赖 Provider 能力。Gateway 根据 `config.toml` 自动选择策略。

| Provider | Tool Search | `defer_loading` | 搜索方式 | Agent Nexus 策略 |
|----------|------------|----------------|---------|-----------------|
| Anthropic Claude (Sonnet 4.5+, Opus 4.5+) | ✅ GA | ✅ | Server BM25/Regex | **AnthropicNativeStrategy**（零 round-trip） |
| OpenAI GPT-5.4+ | ✅ GA | ✅ | Server + Client | **AnthropicNativeStrategy** 兼容（API 格式微调） |
| Google Gemini | ❌ 未支持 | ❌ | — | **ToolSearchFallbackStrategy**（function calling） |
| DeepSeek / Qwen / GLM | ❌ | ❌ | — | **ToolSearchFallbackStrategy**（function calling） |
| Ollama (本地模型) | ❌ | ❌ | — | **ToolSearchFallbackStrategy**（function calling） |

> **关键原则**: Tool Search 是 Provider 专有能力，不是通用协议。Agent Nexus 必须同时维护两套策略（原生加速 + function calling fallback），保证所有 Provider 可用。

#### 10.3.7 Prompt Cache 稳定性

| 规则 | 说明 | 来源 |
|------|------|------|
| Builtins 排在 MCP 之前 | Core Agent tools 在前，deferred agents 在后 | nanobot #2723 — MCP 变更破坏 prefix cache |
| Agent manifest 顺序稳定 | deferred agents 按 name 排序注入 | 避免 manifest 顺序变化导致 cache miss |
| 激活后不取消 | Agent 一旦激活，整个 session 保持激活状态 | 工具列表不变 = cache prefix 稳定 |

> **Anthropic Prompt Cache 机制**: 前缀不变的部分可复用 cache，减少重复计费。工具排序稳定性直接影响 cache 命中率。

#### 10.3.8 Token 用量追踪

| 层级 | 追踪内容 | 存储位置 |
|------|---------|---------|
| Session 内 | 每轮 prompt/completion tokens | `AgentContext.token_usage`（内存） |
| 持久化 | 每次 Compaction 事件 + token 量 + 触发原因 | `context_budget_log`（SQLite） |
| 聚合 | 每 Agent 生命周期总 token 消耗 | Evolution Engine `execution_analyses` |

```python
# Session 内实时追踪（注入 AgentContext）
@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    compaction_count: int = 0
    last_compaction_turn: int = 0

    def check_budget(self, context_window: int) -> str | None:
        """返回告警级别或 None"""
        ratio = self.total_tokens / context_window
        if ratio > 0.95:
            return "hard_ceiling"  # 强制截断
        if ratio > 0.9:
            return "forced_truncate"  # 截断最早消息
        if ratio > 0.8:
            return "compaction"  # 触发压缩
        return None
```

### 10.4 质量约束

| # | 约束 | 说明 |
|---|------|------|
| 1 | 原子性原则 | Atomic Agent 只做一件事，但做到极致 |
| 2 | 交叉验证 | Composite Agent 中的 Code Reviewer 验证其他 Agent 输出 |
| 3 | 独立测试 | 每个 Atomic Agent 有独立测试套件 |

### 10.5 模型配置

用户本地运行，自配模型。支持 OpenAI、Anthropic、Ollama（本地）等。

**优先级**：`环境变量` > `Agent 配置` > `默认配置`

**环境变量覆盖**：

| 环境变量 | 覆盖内容 |
|----------|----------|
| `AGENT_MODEL` | 覆盖单个 Agent 的模型 |
| `DEFAULT_MODEL` | 覆盖默认模型 |
| `OPENAI_API_KEY` | OpenAI API Key |
| `ANTHROPIC_API_KEY` | Anthropic API Key |
| `OLLAMA_BASE_URL` | Ollama 本地地址 |

### 10.6 许可证

所有参考项目均为 MIT 或 Apache-2.0，无商业限制：

| 项目 | 许可证 | 版权方 | 本地路径 | 关键模块 |
|------|--------|--------|---------|---------|
| **ClawTeam** | MIT | HKUDS (2025) | `/Users/yangyitian/Documents/dev/Agents/ClawTeam/` | 编排层参考（TaskStore, MailboxManager, SpawnBackend, TOML Template） |
| **OpenSpace** | MIT | Data Intelligence Lab@HKU (2026) | `/Users/yangyitian/Documents/dev/Agents/OpenSpace/` | `openspace/skill_engine/` |
| **CaveAgent** | MIT | Ram (2025) | `/Users/yangyitian/Documents/dev/Agents/cave-agent/` | `src/cave_agent/runtime/`, `src/cave_agent/security/` |
| **deer-flow** | Apache-2.0 | ByteDance | `/Users/yangyitian/Documents/dev/Agents/deer-flow/` | `packages/harness/deerflow/skills/`, `packages/harness/deerflow/subagents/` |
| **nanobot** | MIT | icemachined | `/Users/yangyitian/Documents/dev/Agents/nanobot/` | `nanobot/agent/tools/mcp.py` |

**MIT 义务**：保留原始版权声明和许可声明。
**Apache-2.0 义务**（deer-flow）：同 MIT，额外保留 NOTICE 文件（如有）。

#### Rust 依赖许可证

| Crate | 许可证 | 用途 |
|-------|--------|------|
| **rmcp** (modelcontextprotocol/rust-sdk) | MIT | MCP 官方 Rust SDK (3.2k⭐) |
| **tokio** | MIT | 异步运行时 |
| **git2** | MIT/Apache-2.0 | Git 操作（libgit2 绑定） |
| **serde** | MIT/Apache-2.0 | 序列化框架 |
| **semver** | MIT/Apache-2.0 | 版本解析 |
| **clap** | MIT/Apache-2.0 | CLI 框架 |
| **dashmap** | MIT | 并发 HashMap |
| **toml** | MIT/Apache-2.0 | TOML 解析 |
| **yaml-rust2** | MIT/Apache-2.0 | YAML 解析 |

### 10.7 安全架构（Permission System）

#### 10.7.1 设计理念

采用 Defense-in-depth（纵深防御）策略，在多个层面建立安全防护：

| 层级 | 组件 | 作用 |
|------|------|------|
| 第一层 | 进程边界 | Agent 运行在独立子进程中，与宿主隔离 |
| 第二层 | PermissionChecker | 执行前权限检查，管控工具、路径、命令 |
| 第三层 | SecurityChecker | Python Runtime AST 级代码安全检查 |

参考 OpenHarness (HKUDS/OpenHarness) 的 PermissionChecker 设计，支持三种权限模式。

> **参考模块**: OpenHarness `src/openharness/permissions/checker.py` — `PermissionChecker` 类, `src/openharness/permissions/modes.py` — `PermissionMode` 枚举

#### 10.7.2 三模式权限

| 模式 | 说明 | 适用场景 |
|------|------|---------|
| DEFAULT | 可变操作需用户确认 | 日常使用 |
| PLAN | 只读，禁止所有修改操作 | 调研/分析场景 |
| FULL_AUTO | 全自动，无需确认 | CI/CD、测试环境 |

#### 10.7.3 权限评估流程

按顺序评估，后续步骤可覆盖前置规则（路径规则除外）：

1. **内置敏感路径保护**（不可覆盖）：检测 SSH/AWS/GCP/Azure/GPG/Docker/K8s 凭证文件
   - Pattern list: `*/.ssh/*`, `*/.aws/credentials`, `*/.aws/config`, `*/.config/gcloud/*`, `*/.azure/*`, `*/.gnupg/*`, `*/.docker/config.json`, `*/.kube/config`
2. **工具黑名单**（denied_tools）：禁止特定工具调用
3. **工具白名单**（allowed_tools）：显式允许的工具列表
4. **路径规则**（path_rules）：glob 模式路径访问控制
5. **命令模式拒绝**（denied_commands）：危险 shell 命令模式匹配
6. **模式级别**：DEFAULT/PLAN/FULL_AUTO 决定基础权限
7. **只读工具豁免**：只读工具（如 file_read、grep）在非 FULL_AUTO 模式下始终允许

#### 10.7.4 Agent 级别权限配置

通过 YAML frontmatter 定义各 Agent 权限：

```yaml
---
name: doc-filler
permission_mode: default
allowed_tools: [file_read, file_write, mcp__*]
denied_tools: [bash]
path_rules:
  - pattern: "*.docx"
    access: read-write
  - pattern: "*.env"
    access: denied
---
```

#### 10.7.5 与 SecurityChecker 的协作

三层安全体系协同工作：

- **Layer 1 — 进程边界**：Agent 作为子进程运行，通过进程隔离限制权限范围
- **Layer 2 — PermissionChecker**：执行前检查，验证工具调用、路径访问、命令执行权限
- **Layer 3 — SecurityChecker**：运行时 AST 分析，检测代码级安全风险（如代码注入、危险 API 调用）

PermissionChecker 侧重**静态权限控制**（配置层面），SecurityChecker 侧重**动态代码分析**（执行层面），两者互补构成完整安全防护。

### 10.8 Rust 重构约束

#### 10.8.1 重构范围

| 组件 | Python Implementation | Rust 重构 | 不迁移原因 |
|------|----------------------|-----------|-----------|
| Git Installer (subprocess→git) | ✅ Implemented | ✅ 重构（ap-fetcher） | 性能关键 |
| MCP Gateway (FastMCP → rmcp) | ✅ Implemented | ✅ 重构 | 官方 SDK |
| Agent Supervisor (asyncio → tokio) | ✅ Implemented | ✅ 重构 | 进程管理 |
| CLI (Typer → clap) | ✅ Implemented | ✅ 重构 | 用户体验 |
| Agent Runtime | Python | **不动** | MCP 边界 |
| Agent Business Logic | Python | **不动** | MCP 边界 |
| uv venv 管理 | subprocess(uv) | tokio::process(uv) | 机制不变 |

#### 10.8.2 不变接口清单

以下接口在 Rust 重构前后保持完全一致：

1. lockfile.json 格式
2. config.toml 格式
3. sources.yaml 格式
4. MCP 协议（stdio/SSE）
5. Agent Package 目录结构
6. agent-manifest.yaml 和 SKILL.md 格式
7. IPC 消息格式（stdin/stdout JSON-lines）

#### 10.8.3 迁移兼容性约束

1. **锁文件兼容**：Rust 和 Python 版本读写同一个 lockfile.json
2. **配置文件兼容**：config.toml 和 sources.yaml 格式不变
3. **Agent Package 兼容**：安装后的 Agent 目录结构不变
4. **渐进式迁移**：Python 平台和 Rust 平台可同时运行，共享同一个配置和 Agent 存储

> **参考**: 完整 Rust Crate 拆分和迁移路径见 [§12.6 Rust 重构路径](10-cloud-local-architecture.md#126-rust-重构路径)

---
