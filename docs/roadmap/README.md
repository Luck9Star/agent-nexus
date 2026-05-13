# Agent Nexus 功能路线图 — 总控文档

> 基于飞书《Agent Nexus 需求分析最终报告》驱动。详细设计见各模块文档。
> 生成日期：2026-05-10 | 决策确认日期：2026-05-10

---

## 模块文档索引

| 文档 | 优先级 | 核心内容 | 预估工期 |
|------|--------|---------|----------|
| [p0-1-p2p-collaboration.md](p0-1-p2p-collaboration.md) | P0 🔴 | Platform-as-Broker A2A 消息 | W1-6 |
| [p0-2-gateway-security.md](p0-2-gateway-security.md) | P0 🔴 | 三层安全（Auth + Policy + Audit） | W1-7 |
| [p1-3-marketplace.md](p1-3-marketplace.md) | P1 🟠 | TOML 统一 + QG Pipeline + Sigstore + 依赖解析 | W5-9 |
| [p1-4-agent-extension.md](p1-4-agent-extension.md) | P1 🟠 | 分类体系 + 脚手架 CLI + 30→50 agents | W7-10 |
| [p2-5-evolution.md](p2-5-evolution.md) | P2 🟡 | LLM SkillPatcher + A/B 测试 + 可观测性 | W9-14 |

---

## TODO 清单

### P0-1: P2P Multi-Agent 协作

- [x] **Phase 1 (W1-2)**: A2A 消息模型 + MessageBroker + IPC 类型扩展
  - [x] 新增 `A2AMessage`、`AgentAddress` 数据模型 (`models/ipc.py`)
  - [x] 新增 8 种 IPC 消息类型（4 Agent→Platform + 4 Platform→Agent）
  - [x] 实现 `MessageBroker`（send/request/broadcast/reply）
  - [x] 内存队列 + 请求超时 + 嵌套禁令
  - [x] 单元测试覆盖 4 种消息类型
- [x] **Phase 2 (W3)**: AgentDirectory + agent 发现协议
  - [x] 新增 `AgentDirectory`（register/deregister/resolve/find_by_capability/find_by_role）
  - [x] Agent 启动时通过 IPC 注册能力和角色
  - [x] 集成测试：动态注册、按能力查找、自动注销
- [x] **Phase 3 (W4)**: DSL messaging 配置 + 路由规则
  - [x] OrchestrationDSL 新增 `[messaging]` 段
  - [x] 路由权限限制（仅同一 composition 内）
  - [x] E2E 测试：两个 agent 通过 broker 完成请求-回复
- [ ] **Phase 4 (W5-6)**: Rust 平台同步实现
  - [ ] `crates/ap-core/` 新增 A2A 消息类型和 MessageBroker
  - [ ] Rust 端 E2E 测试通过

### P0-2: MCP Gateway 安全增强

- [x] **前置验证 (Phase 0)**: FastMCP middleware 可行性验证
  - [x] 确认 FastMCP 2.x 是否支持 ASGI middleware
  - [x] 如不支持，评估反向代理方案（nginx/caddy）
- [x] **Phase 1 (W1-2)**: AuditLogger + 结构化审计事件
  - [x] 新增 `AuditEvent` 模型和 `AuditLogger` (`platform/gateway/audit.py`)
  - [x] SQLite WAL 存储，异步写入
  - [x] 按大小轮转 500MB
  - [x] 工具调用产生审计记录，可查询导出
- [x] **Phase 2 (W3)**: GatewayAuth API Key 模式
  - [x] 新增 `GatewayAuthConfig` + `AuthenticatedClient` (`platform/gateway/auth.py`)
  - [x] 环境变量 + SHA256 hash 校验
  - [x] 默认关闭，显式启用（`gateway-auth.toml: enabled = false`）
  - [x] 未认证客户端返回 MCP error `-32001`
- [x] **Phase 3 (W4)**: Tool Access Policy + 角色模型
  - [x] 新增 `ToolAccessPolicy`，按 client roles 控制工具集
  - [x] client_id 全局限流 100/min
  - [x] 热重载配置（SIGHUP / CLI reload）
- [x] **Phase 4 (W5)**: 外部 Server 认证 + TLS
  - [x] 扩展 `ExternalServerConfig` 增加 auth 字段和 tls_verify
  - [x] 支持 bearer/api_key 认证连接外部 server
- [ ] **Phase 5 (W6-7)**: Rust 平台同步实现
  - [ ] axum tower layer auth guard
  - [ ] Rust 端审计日志

### P1-3: Agent Marketplace + Quality Gate

- [x] **Phase 1 (W5-6)**: 统一 Manifest TOML + 双读兼容
  - [x] 定义 `agent.toml` 统一格式
  - [x] Python 端 TOML + YAML 双读兼容
  - [x] 自动迁移脚本 `agent-manifest.yaml → agent.toml`
- [x] **Phase 2 (W7-8)**: Quality Gate Pipeline
  - [x] 新增 `QualityGate` + 5 项检查（Manifest/Skill/Security/Dependency/TestCoverage）
  - [x] 评分算法：Critical fail + Warning -0.1 + 可配置最低线（默认 0.6）
  - [x] Python 端 `platform/local/quality_gate.py`
- [x] **Phase 3 (W8)**: 依赖解析 + 冲突检测
  - [x] 新增 `DependencyResolver`（仅 direct deps）
  - [x] Composite 安装自动拉取 Atomic 依赖
- [x] **Phase 4 (W9)**: Sigstore 签名验证
  - [x] 新增 `AgentSigner`（Sigstore 优先）
  - [x] 签名/验签流程端到端
- [ ] **Phase 5 (W9)**: 搜索 API 增强 + Rust 同步
  - [ ] 按能力/领域搜索
  - [ ] 按下载量排序
  - [ ] Rust 端 QG + 依赖解析同步

### P1-4: Domain Atomic Agent 扩展

- [x] **Phase 1 (W7-8)**: 能力分类体系 + 脚手架 CLI
  - [x] 新增 `capabilities.toml` 受控词汇表
  - [x] 新增 `agent-nexus create-agent` CLI 命令
  - [x] 生成 agent 骨架（agent.toml + SKILL.md + pyproject.toml + src/ + tests/）
- [x] **Phase 2 (W8-10)**: 第一批 8 个 agent
  - [x] dependency-auditor（Top 1）
  - [x] config-linter（Top 2）
  - [x] error-analyzer（Top 3）
  - [x] db-schema-analyzer
  - [x] api-contract-tester
  - [x] performance-profiler
  - [x] i18n-validator
  - [x] data-pipeline-validator
  - [x] 每个 agent：SKILL.md（AI 生成 + 用户审核）+ 测试 + MCP 可调用
- [ ] **Phase 3 (W10)**: 第二批 10 个 agent
  - [ ] terraform-reviewer / dockerfile-optimizer / graphql-schema-designer
  - [ ] ml-model-reviewer / prompt-engineer / architecture-reviewer
  - [ ] migration-planner / incident-analyzer / cost-optimizer / compliance-checker
- [ ] **Phase 4**: 社区贡献流程 + 模板
  - [ ] 外部贡献 PR 流程
  - [ ] Quality Gate 集成

### P2-5: Self-Evolution 产品化

- [x] **Phase 1 (W9-11)**: SkillPatcher LLM 集成
  - [x] 新增 `SkillPatcher` (`platform/evolution/skill_patch.py`)
  - [x] LLM 生成 FIX/DERIVED 内容（默认 sonnet）
  - [x] Validation pipeline（语法 + 安全 + 测试 + confidence 阈值）
  - [x] 能自动修复一个已知问题的 skill
- [?] **Phase 2 (W11)**: Agency Pipeline 集成
  - [ ] 任务完成后自动触发进化分析 hook
  - [ ] 反馈闭环：task result → skill quality counters → evolution trigger
- [x] **Phase 3 (W12-13)**: 配置化 + 可观测性
  - [x] `config/evolution.toml` 可配置阈值和 LLM 参数
  - [x] EvolutionMetrics 指标导出
  - [x] CLI dashboard (`evolution summary` / `evolution health`)
- [x] **Phase 4 (W13)**: A/B 测试 + 回滚
  - [x] EvolutionExperimenter（可配置，默认 A/B 测试后替代）
  - [x] 仅回滚到上一版本
  - [x] 进化质量综合加权（0.5 effective + 0.3 fallback + 0.2 usage）
- [ ] **Phase 5 (W13-14)**: Rust 同步
  - [ ] Python 全部完成后一次性同步到 Rust

---

## 功能依赖图

```
P0-1 (P2P Collaboration) ──── 独立路径，无强依赖

P0-2 (Gateway Security)
  ↓ auth 机制被 Marketplace 签名验证复用
P1-3 (Marketplace + QG)
  ↓ QG 被 Agent 扩展使用
P1-4 (Agent Extension)
  ↓ 更多 agent 为 Evolution 提供数据
P2-5 (Self-Evolution)
```

---

## 时间线

```
W1-2  ┃ P0-1 Phase 1 (A2A 消息模型)  ┃ P0-2 Phase 0-1 (验证+AuditLogger)
W3-4  ┃ P0-1 Phase 2-3 (Directory+DSL) ┃ P0-2 Phase 2-3 (Auth+Policy)
W5-6  ┃ P0-1 Phase 4 (Rust sync)     ┃ P0-2 Phase 4-5 (External+Rust)
       ┃                              ┃ P1-3 Phase 1-2 (TOML+QG)
W7-8  ┃ P1-3 Phase 3-4 (Deps+Sign)   ┃ P1-4 Phase 1-2 (Taxonomy+CLI+8 agents)
W9-10 ┃ P1-3 Phase 5 (Rust+Search)   ┃ P1-4 Phase 3 (10 agents)
       ┃                              ┃ P2-5 Phase 1-2 (SkillPatcher+Agency)
W11-12┃ P1-4 Phase 4 (Community)     ┃ P2-5 Phase 3-4 (Config+A/B)
W13-14┃                               ┃ P2-5 Phase 5 (Rust sync)
```

---

## 里程碑

| # | 里程碑 | 时间 | 交付物 |
|---|--------|------|--------|
| M1 | A2A MVP | W4 | Agent 间可通过 platform broker 互相发送消息 |
| M2 | Secure Gateway | W6 | Gateway 认证 + 审计日志上线 |
| M3 | Marketplace Alpha | W8 | Quality Gate + 依赖解析 + 签名验证 |
| M4 | 30 Agents | W10 | 达到 30 个 atomic agent |
| M5 | Evolution Live | W14 | LLM 驱动的 skill 进化上线 |

---

## 风险矩阵

| 风险 | 概率 | 影响 | 总评 | 缓解 |
|------|------|------|------|------|
| FastMCP auth 不支持 | 中 | 高 | 🔴 | Phase 0 验证，备选反向代理 |
| P2P 消息死锁 | 中 | 高 | 🔴 | 超时 + 禁止嵌套请求 |
| YAML→TOML 迁移 | 中 | 高 | 🔴 | 3 个月双读 + 自动迁移脚本 |
| LLM 进化质量不稳 | 高 | 中 | 🟠 | validation + confidence + 人工审核 |
| 50 agent 维护成本 | 中 | 中 | 🟠 | 脚手架 + 自动测试 + 社区 |
| 双语言同步延迟 | 高 | 低 | 🟡 | Python 先行，全部完成后同步 Rust |
| 审计日志性能 | 低 | 中 | 🟡 | 异步写入 + 批量提交 |

---

## 跨功能决策（D26-D28）

| # | 决策 | 理由 |
|---|------|------|
| D26 | 配置文件统一放 `config/` 目录 | 符合现有约定 |
| D27 | Python 全部完成后，再启动 Rust 同步 | 避免 context switch，Python 是 source of truth |
| D28 | 维持 80% 基线，新增模块不低于 85% | 新代码无历史包袱 |

---

## 参考文档

| 文档 | 路径 |
|------|------|
| 竞品分析（需求来源） | 飞书 `UzXEdpCizofVD9xuUQWcLwi3n3b` |
| 原子 Agent 改进计划 | `docs/12-atomic-agents-improvement-plan.md` |
| MCP 通信矩阵 | `docs/06-mcp-communication.md` |
| Agent 系统 | `docs/05-agent-system.md` |
| Self-Evolution | `docs/04-self-evolution.md` |
| 约束与决策 | `docs/08-constraints-decisions.md` |
| 实施计划 | `docs/09-implementation-plan.md` |
| ClawTeam 参考 | `/Users/yangyitian/Documents/dev/Agents/ClawTeam/` |
| OpenSpace 参考 | `/Users/yangyitian/Documents/dev/Agents/OpenSpace/` |
