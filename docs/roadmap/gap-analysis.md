# Roadmap 缺口分析报告

> 校验日期：2026-05-11 | 基于 docs/roadmap/ 五份设计文档 vs 当前代码库实际状态

---

## 总览

| 模块 | Roadmap 声称完成度 | 实际完成度 | 主要缺口 |
|------|-------------------|-----------|----------|
| P0-1 P2P 协作 | ~40% | **~100%** | Rust 同步 |
| P0-2 Gateway 安全 | ~44% | **~100%** | 无实质缺口 |
| P1-3 Marketplace | ~56% | **~85%** | 增强搜索、部分 Rust 同步 |
| P1-4 Agent 扩展 | ~38% | **~80%** | 第二批 10 个 agent、社区流程 |
| P2-5 进化产品化 | ~33% | **~90%** | Agency hook 集成验证、Rust 同步 |

**结论：Roadmap「当前状态」表严重过时。大量标记为 ❌ 的组件已经实现。核心剩余工作集中在增量扩展（新 agent、Rust 同步）和边缘完善。**

---

## P0-1: P2P Multi-Agent 协作

### Roadmap 声称 vs 实际

| # | 组件 | Roadmap 标记 | 实际状态 | 证据 |
|---|------|-------------|---------|------|
| 1 | TaskGraph (blocked_by + cycle detection) | ✅ | ✅ 确认 | `platform/orchestration/task_graph.py` — `detect_cycles_dfs()` |
| 2 | IPC (JSON-lines, 4MB, heartbeat) | ✅ | ✅ 确认 | `platform/orchestration/ipc.py` — `IPCStream`, `IPCProtocol` |
| 3 | IPC 消息类型 | ⚠️ 有限 | ✅ 已扩展 | `models/ipc.py` — A2AMessage, AgentAddress 已存在 |
| 4 | ProcessManager | ✅ | ✅ 确认 | `platform/orchestration/process_manager.py` |
| 5 | OrchestrationDSL | ✅ | ✅ 确认 | `platform/orchestration/dsl.py` — 含 MessagingConfig |
| 6 | PlatformRouter | ✅ | ✅ 确认 | `platform/router/router.py` |
| 7 | Agent 发现 | ❌ | ✅ **已实现** | `platform/orchestration/agent_directory.py` — 按能力/角色查找 |
| 8 | A2A 消息类型 | ❌ | ✅ **已实现** | `models/ipc.py` — A2AMessage + AgentAddress 模型 |
| 9 | 消息路由/广播 | ❌ | ✅ **已实现** | `platform/orchestration/message_broker.py` — Platform-as-Broker |
| 10 | Team lifecycle | ❌ | ✅ **已实现** | `platform/orchestration/team_manager.py` — TeamManager 含 form/activate/suspend/dissolve 生命周期 + 状态机 |

### 缺口清单

| 缺口 | 严重度 | 说明 |
|------|--------|------|
| ~~Team lifecycle~~ | ~~低~~ | ~~已实现~~：`team_manager.py` 含完整生命周期管理 |
| DSL `[messaging]` 测试 | 低 | 代码中已有 MessagingConfig，但 E2E 测试覆盖待确认 |

### TODO 标记校验

- [x] Phase 1: A2AMessage + AgentAddress 已存在，8 种 IPC 类型已扩展，MessageBroker 已实现
- [x] Phase 2: AgentDirectory 已实现（register/deregister/resolve/find_by_capability/find_by_role）
- [x] Phase 3: DSL messaging 配置已有 MessagingConfig 支持
- [ ] Phase 4: Rust 同步 — 未校验 Rust 端 A2A 实现

---

## P0-2: MCP Gateway 安全增强

### Roadmap 声称 vs 实际

| # | 组件 | Roadmap 标记 | 实际状态 | 证据 |
|---|------|-------------|---------|------|
| 1 | SecurityChecker (AST, 30+ rules) | ✅ | ✅ 确认 | `platform/runtime/security_checker.py` — 29+ 危险导入检查 |
| 2 | PermissionChecker (3 modes) | ✅ | ✅ 确认 | `platform/runtime/permission_checker.py` — DEFAULT/PLAN/FULL_AUTO |
| 3 | Python Gateway (FastMCP) | ⚠️ 无认证 | ✅ **已有认证** | `platform/gateway/gateway.py` — 集成 auth + audit + access |
| 4 | Rust Gateway (axum) | ⚠️ 无认证 | ✅ 存在 | `crates/ap-gateway/src/gateway.rs` — McpGateway |
| 5 | ExternalMcpAdapter | ⚠️ 无认证 | ✅ **已有认证** | 支持 STDIO/SSE/HTTP_STREAM + auth |
| 6 | SchemaTransformer | ✅ | ✅ 确认 | `platform/gateway/schema_transformer.py` |
| 7 | DeferredAgentRegistry | ✅ | ✅ 确认 | `platform/gateway/deferred_registry.py` |
| 8 | Gateway 认证 | ❌ | ✅ **已实现** | `platform/gateway/auth.py` — GatewayAuthenticator + API Key + 角色 |
| 9 | Client identity | ❌ | ✅ **已实现** | `platform/gateway/auth.py` — AuthenticatedClient 模型 |
| 10 | 结构化审计日志 | ❌ | ✅ **已实现** | `platform/gateway/audit.py` — AuditLogger + AuditEvent + SQLite |
| 11 | 速率限制 | ❌ | ✅ **已实现** | `platform/gateway/auth.py` — ToolAccessChecker 含限流 |

### 缺口清单

**无实质缺口。** Roadmap 中所有标记为 ❌ 的组件均已实现。

### TODO 标记校验

- [x] Phase 0: FastMCP middleware 已验证可行（通过 gateway.py 集成实现）
- [x] Phase 1: AuditLogger + AuditEvent + SQLite WAL + 大小轮转
- [x] Phase 2: GatewayAuthConfig + AuthenticatedClient + SHA256 hash + 默认关闭
- [x] Phase 3: ToolAccessPolicy + 角色控制 + 限流
- [x] Phase 4: ExternalServerAuth 已扩展
- [ ] Phase 5: Rust 同步 — Rust 端 auth/audit 待确认

---

## P1-3: Agent Marketplace + Quality Gate

### Roadmap 声称 vs 实际

| # | 组件 | Roadmap 标记 | 实际状态 | 证据 |
|---|------|-------------|---------|------|
| 1 | Git 安装器 (Python) | ✅ | ✅ 确认 | `local/installer.py` — sparse-checkout |
| 2 | Git 安装器 (Rust) | ✅ | ✅ 确认 | `ap-fetcher/installer.rs` |
| 3 | Source 管理 (Python) | ✅ | ✅ 确认 | `local/sources.py` — 3 种 source |
| 4 | Source 管理 (Rust) | ✅ | ✅ 确认 | `ap-fetcher/sources.rs` |
| 5 | Lockfile (Python) | ✅ | ✅ 确认 | `local/lockfile.py` |
| 6 | Lockfile (Rust) | ✅ | ✅ 确认 | `ap-fetcher/lockfile.rs` |
| 7 | Rust 安全审计 | ✅ | ✅ 确认 | `security_audit.rs` |
| 8 | Rust manifest 检查 | ✅ | ✅ 确认 | `manifest_checker.rs` (TOML) |
| 9 | Rust skill 检查 | ✅ | ✅ 确认 | `skill_checker.rs` |
| 10 | Python manifest 检查 | ⚠️ YAML | ✅ **已扩展** | `manifest.py` — 支持 TOML + YAML 双读 |
| 11 | Python 搜索 API | ⚠️ 按名称 | ⚠️ **仍为基础** | 仅关键词匹配，无能力/领域过滤 |
| 12 | 签名验证 (Python) | ❌ | ✅ **已实现** | `signer.py` — Sigstore + GPG 双后端 |
| 13 | 签名验证 (Rust) | ❌ | ❌ 确认缺失 | Rust 端无签名验证 |
| 14 | 依赖解析 (Python) | ❌ | ✅ **已实现** | `dependency_resolver.py` — 含冲突检测 |
| 15 | 依赖解析 (Rust) | ❌ | ❌ 确认缺失 | Rust 端无依赖解析 |
| 16 | 评分系统 | ❌ | ✅ **已实现** | `platform/local/scoring.py` — ScoreManager 含 record_download/record_quality_score/record_user_rating/list_scores |

### 缺口清单

| 缺口 | 严重度 | 说明 |
|------|--------|------|
| 增强搜索 API | 中 | 缺少按能力/领域搜索、按下载量排序 |
| ~~评分系统~~ | ~~中~~ | ~~已实现~~：`scoring.py` 含下载量/质量评分/用户评分 |
| Rust 签名验证 | 低 | Python 端已实现，Rust 端未同步 |
| Rust 依赖解析 | 低 | Python 端已实现，Rust 端未同步 |
| Marketplace 集成测试 | 低 | signer/resolver 与安装流程的端到端集成待验证 |

### TODO 标记校验

- [x] Phase 1: 统一 TOML 格式 + 双读兼容（manifest.py 已支持）
- [x] Phase 2: QualityGate + 5 项检查（quality_gate.py 已实现）
- [x] Phase 3: 依赖解析 + 冲突检测（dependency_resolver.py 已实现）
- [x] Phase 4: 签名验证（signer.py 已实现 Sigstore + GPG）
- [ ] Phase 5: 搜索增强 + Rust 同步 — 搜索仍基础，Rust 部分未同步

---

## P1-4: Domain Atomic Agent 扩展

### Roadmap 声称 vs 实际

| # | 组件 | Roadmap 标记 | 实际状态 | 证据 |
|---|------|-------------|---------|------|
| 1 | Atomic Agent 模式 (12个) | ✅ | ✅ **实际 18+ 个** | `agents/atomic/` 含 18-20 个 agent |
| 2 | Composite Agent (5个) | ✅ | ✅ 确认 5 个 | `agents/composite/` |
| 3 | Agent Manifest | ✅ | ✅ 确认 | 各 agent 均有 agent-manifest.yaml |
| 4 | SKILL.md 规范 | ✅ | ✅ 确认 | 28 个 SKILL.md 文件 |
| 5 | Agent 脚手架 | ❌ | ✅ **已实现** | `local/cli/_create_agent_cmd.py` |
| 6 | 能力分类体系 | ❌ | ✅ **已实现** | `local/capabilities.toml` — 13 分类 |
| 7 | Agent 模板系统 | ❌ | ✅ **已实现** | create-agent 命令含完整模板 |

### 第一批 8 个 Agent 状态

| Agent | Roadmap | 实际 | SKILL.md | Tests | MCP |
|-------|---------|------|----------|-------|-----|
| dependency-auditor | 待实现 | ✅ 已存在 | ✅ | ✅ | ✅ |
| config-linter | 待实现 | ✅ 已存在 | ✅ | ✅ | ✅ |
| error-analyzer | 待实现 | ✅ 已存在 | ✅ | ✅ | ✅ |
| db-schema-analyzer | 待实现 | ✅ 已存在 | ✅ | ✅ | ✅ |
| api-contract-tester | 待实现 | ✅ 已存在 | ✅ | ✅ | ✅ |
| performance-profiler | 待实现 | ✅ 已存在 | ✅ | ✅ | ✅ |
| i18n-validator | 待实现 | ✅ 已存在 | ✅ | ✅ | ✅ |
| data-pipeline-validator | 待实现 | ✅ 已存在 | ✅ | ✅ | ✅ |

### 第二批 10 个 Agent 状态

| Agent | Roadmap | 实际 |
|-------|---------|------|
| terraform-reviewer | 待实现 | ❌ 未实现 |
| dockerfile-optimizer | 待实现 | ❌ 未实现 |
| graphql-schema-designer | 待实现 | ❌ 未实现 |
| ml-model-reviewer | 待实现 | ❌ 未实现 |
| prompt-engineer | 待实现 | ❌ 未实现 |
| architecture-reviewer | 待实现 | ❌ 未实现 |
| migration-planner | 待实现 | ❌ 未实现 |
| incident-analyzer | 待实现 | ❌ 未实现 |
| cost-optimizer | 待实现 | ❌ 未实现 |
| compliance-checker | 待实现 | ❌ 未实现 |

### 缺口清单

| 缺口 | 严重度 | 说明 |
|------|--------|------|
| 第二批 10 个 Agent | 高 | 全部未实现，需逐一创建 SKILL.md + 实现 + 测试 |
| 社区贡献流程 | 低 | Phase 4 内容，依赖 QG 集成 |
| agent.toml 迁移 | 低 | 当前仍为 agent-manifest.yaml，部分新 agent 已用 TOML |

### TODO 标记校验

- [x] Phase 1: 能力分类 + 脚手架 CLI（capabilities.toml + create-agent 均已实现）
- [x] Phase 2: 第一批 8 个 agent（全部已存在，含 SKILL.md + 测试 + MCP）
- [ ] Phase 3: 第二批 10 个 agent（全部未实现）
- [ ] Phase 4: 社区贡献流程 + 模板（未实现）

---

## P2-5: Self-Evolution 产品化

### Roadmap 声称 vs 实际

| # | 组件 | Roadmap 标记 | 实际状态 | 证据 |
|---|------|-------------|---------|------|
| 1 | SkillEvolver | ⚠️ 半成品 | ✅ **可用** | `evolver.py` — 支持 FIX/DERIVED/CAPTURED 三种进化 + 防循环 |
| 2 | ExecutionAnalyzer | ✅ | ✅ 确认 | `analyzer.py` — Levenshtein 模糊匹配 |
| 3 | HealthChecker | ✅ | ✅ 确认 | `health.py` — 3 条阈值规则 |
| 4 | AgentPromoter | ✅ | ✅ 确认 | `promotion.py` — 完整 agent 包生成 |
| 5 | EvolutionStore | ✅ | ✅ 确认 | `store.py` — 6 张表确认 |
| 6 | CompactionGuard | ✅ | ✅ 确认 | `compaction.py` |
| 7 | LLM 集成 | ❌ | ✅ **已实现** | `skill_patch.py` — SkillPatcher 使用 LLMClient |
| 8 | Agency 集成 | ❌ | ⚠️ **部分** | 组件存在，Pipeline hook 集成待验证 |
| 9 | 可观测性 | ❌ | ✅ **已实现** | `metrics.py` — EvolutionMetrics + EvolutionDashboard |
| 10 | A/B 测试 | ❌ | ✅ **已实现** | `experimenter.py` — EvolutionExperimenter 完整实现 |
| 11 | 回滚机制 | ❌ | ✅ **已实现** | `experimenter.py` — rollback() 方法 |
| 12 | 配置化 | ❌ | ✅ **已实现** | `evolution_config.py` + `config/evolution.toml` 已落盘，含完整配置 |

### 缺口清单

| 缺口 | 严重度 | 说明 |
|------|--------|------|
| ~~config/evolution.toml 落盘~~ | ~~中~~ | ~~已实现~~：`config/evolution.toml` 已存在，含 evolution.enabled/thresholds/llm/experiment 配置 |
| Agency Pipeline hook 集成 | 中 | SkillPatcher 组件存在，但 Agency Pipeline 中的 post_analysis hook 是否完整接入待确认 |
| Rust 同步 | 低 | Phase 5 内容，Python 完成后一次性同步 |

### TODO 标记校验

- [x] Phase 1: SkillPatcher LLM 集成（skill_patch.py 已实现）
- [?] Phase 2: Agency Pipeline 集成 hook（组件存在，集成链路待验证）
- [x] Phase 3: 配置化 + 可观测性（evolution_config.py + metrics.py + dashboard CLI）
- [x] Phase 4: A/B 测试 + 回滚（experimenter.py 完整实现）
- [ ] Phase 5: Rust 同步（未启动）

---

## 全局缺口汇总

### 按严重度排序

| # | 缺口 | 模块 | 严重度 | 工作量估计 |
|---|------|------|--------|-----------|
| G1 | 第二批 10 个 Agent 未实现 | P1-4 | 🔴 高 | 每个 2-3 天，共 ~25 天 |
| G2 | Agency Pipeline → Evolution hook 集成验证 | P2-5 | 🟠 中 | 1-2 天验证 + 修复 |
| ~~G3~~ | ~~config/evolution.toml 落盘~~ | ~~P2-5~~ | ~~✅ 已关闭~~ | config/evolution.toml 已存在 |
| G4 | 增强搜索 API（能力/领域/下载量） | P1-3 | 🟠 中 | 3-5 天 |
| ~~G5~~ | ~~评分系统~~ | ~~P1-3~~ | ~~✅ 已关闭~~ | scoring.py 已实现 |
| G6 | Rust 签名验证同步 | P1-3 | 🟡 低 | 2-3 天 |
| G7 | Rust 依赖解析同步 | P1-3 | 🟡 低 | 2-3 天 |
| G8 | Rust A2A 消息同步 | P0-1 | 🟡 低 | 3-5 天 |
| G9 | Rust Gateway auth/audit 同步 | P0-2 | 🟡 低 | 3-5 天 |
| G10 | Rust Evolution 同步 | P2-5 | 🟡 低 | 5-7 天 |
| ~~G11~~ | ~~Team lifecycle~~ | ~~P0-1~~ | ~~✅ 已关闭~~ | team_manager.py 已实现 |
| G12 | 社区贡献流程 | P1-4 | 🟡 低 | 3-5 天 |
| G13 | Marketplace 端到端集成测试 | P1-3 | 🟡 低 | 1-2 天 |

### 建议优先级

1. **立即可做**（0.5-2 天）：G2 Agency hook 验证
2. **短期**（W1-4）：G4 搜索增强
3. **中期**（W5-10）：G1 第二批 agent（可按需挑选高价值 agent 优先）
4. **长期**（W10+）：G6-G10 Rust 同步（遵循 D27 决策：Python 全部完成后一次性同步）
5. **低优先级**：G12 社区流程、G13 集成测试

### Roadmap TODO 标记建议更新

当前 README.md 中所有 TODO 均标记为 `[ ]`（未完成）。基于本次校验，建议更新为：

**P0-1**: Phase 1-3 标记为 `[x]`，Phase 4 保持 `[ ]`
**P0-2**: Phase 0-4 标记为 `[x]`，Phase 5 保持 `[ ]`
**P1-3**: Phase 1-4 标记为 `[x]`，Phase 5 保持 `[ ]`（搜索增强 + Rust 同步）
**P1-4**: Phase 1-2 标记为 `[x]`，Phase 3-4 保持 `[ ]`
**P2-5**: Phase 1, 3-4 标记为 `[x]`，Phase 2 标记为 `[~]`（部分），Phase 5 保持 `[ ]`
