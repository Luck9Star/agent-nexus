# 未实现功能缺口清单

> Last updated: 2026-05-08
> 状态: 🔴 高优先级 | 🟡 中优先级 | 🟢 低优先级

本文档跟踪所有已规划但尚未实现的功能缺口。

---

## 🔴 高优先级

### 1. Phase 11: Integration Tests

**描述**: Rust 集成测试套件
**计划**: `docs/superpowers/plans/rust-rewrite/11-integration-tests.md`

| 测试类型 | 状态 | 说明 |
|---------|------|------|
| ap-cli-backend integration_tests.rs | ✅ | 已有基础测试 |
| Backward Compatibility Tests | ✅ | compat_tests.rs: 12 tests pass |
| Cross-crate Integration Tests | ✅ | ap-evolution/cross_crate.rs: 10 tests pass |
| E2E Tests | ✅ | ap-cli/e2e_tests.rs: 37 tests pass |

**状态**: ✅ 已完成

---

### 2. Agent Capability Tests

**描述**: 契约驱动的 Agent 能力测试
**计划**: `docs/superpowers/plans/2026-04-29-agent-capability-test-plan.md`

| 测试类型 | 状态 |
|---------|------|
| schema + base types | ✅ |
| StructureValidator | ✅ |
| OrchestrationValidator | ✅ |
| CLIProvider | ✅ |
| Atomic contracts | ✅ |
| Composite contracts | ✅ |
| Agency contracts | ✅ |
| SemanticValidator | ✅ |
| APIProvider | ✅ |
| test_atomic_cli.py | ✅ |
| test_composite_cli.py | ✅ |
| test_agency_cli.py | ✅ |
| test_atomic_api.py | ✅ |
| test_composite_api.py | ✅ |
| test_agency_api.py | ✅ |

**状态**: ✅ 已完成（51 passed, 29 skipped）

---

### 3. Streaming LLM Client SDK 迁移

**描述**: 当前使用 litellm（符合设计决策）
**设计**: `docs/superpowers/specs/2026-04-29-streaming-llm-client-design.md`

> ⚠️ **Note**: 原设计要求使用官方 OpenAI/Anthropic SDK，但实际实现选择了 litellm 作为统一调用层。

| 任务 | 状态 |
|------|------|
| streaming_default 配置 | ✅ |
| _should_stream 方法 | ✅ |
| SDK lazy 初始化 | ✅ |
| litellm streaming 集成 | ✅ |
| 测试套件 | ✅ |

**状态**: ✅ 已完成（使用 litellm）

---

## 🟡 中优先级

### 4. CLI Shell Completion

**描述**: CLI 命令补全功能
**位置**: ap-cli

| 任务 | 状态 |
|------|------|
| init 命令 | ✅ |
| sources 命令 | ✅ |
| install 命令 | ✅ |
| run 命令 | ✅ |
| check 命令 | ✅ |
| config 命令 | ✅ |
| evolution 命令 | ✅ |
| runtime 命令 | ✅ |
| shell completion | ✅ |

**状态**: ✅ 已完成（bash/zsh/fish completion, 4 tests pass）

---

### 5. SemVer 版本解析器

**描述**: 从 git tags 解析版本号
**位置**: ap-fetcher

| 任务 | 状态 |
|------|------|
| GitInstaller | ✅ |
| Lockfile 管理 | ✅ |
| SemVer 解析器 | ✅ |

**状态**: ✅ 已完成（使用 semver crate, checkout_semver_tag_with_v_prefix 等 tests pass）

---

### 6. 质量验证工具

**描述**: manifest 检查、SKILL.md 检查、安全审计
**位置**: ap-fetcher

| 任务 | 状态 |
|------|------|
| SourceManager | ✅ |
| GitInstaller | ✅ |
| manifest 检查 | ✅ |
| SKILL.md 检查 | ✅ |
| 安全审计 | ✅ |

**状态**: ✅ 已完成（18 tests: manifest_checker 6 + skill_checker 5 + security_audit 7）

---

### 7. Cross-Agent Data Reference（Mailbox）

**描述**: 引用传递格式减少 token 开销
**位置**: ap-core/orchestration

| 任务 | 状态 |
|------|------|
| ~50 tokens 引用格式 | ✅ |
| Mailbox 实现 | ✅ |

**状态**: ✅ 已完成（11 tests: store/resolve/list/purge/URI format）

---

### 8. Provider Adaptation（MCP）

**描述**: MCP Gateway 的 Provider 适配层
**位置**: ap-gateway

| 任务 | 状态 |
|------|------|
| MCP Gateway | ✅ |
| ToolAdapter | ✅ |
| DeferredRegistry | ✅ |
| Provider Adaptation | ✅ |

**状态**: ✅ 已完成（5 tests: ProviderAwareStrategy with Eager/Lazy/AnthropicDeferred）

---

### 9. 端到端 MCP 测试

**描述**: 完整的 MCP 通信测试
**位置**: ap-gateway

| 任务 | 状态 |
|------|------|
| MCP Gateway | ✅ |
| ToolAdapter | ✅ |
| E2E MCP 测试 | ✅ |

**状态**: ✅ 已完成（5 E2E tests: registration, invocation, multi-agent, strategy, lifecycle）

---

## 🟢 低优先级

### 11. Agent 仓库隔离

**描述**: Agent 仓库隔离级别配置
**状态**: ✅ 已完成（IsolationLevel enum: None/Process/Container, 3 tests pass）

### 12. CLI Backend 配置加载测试

**描述**: config.toml schema 解析正确性验证
**状态**: ✅ 已完成（4 tests pass）

---

## 已完成的功能

以下功能已从本清单移除并标记为已完成：

- ✅ Phase 7: Rust Rewrite (Phase 0-10)
- ✅ CLI 命令系统 (24/27 命令)
- ✅ 配置整合 (config.toml)
- ✅ Agency LLM 集成
- ✅ CLI Backend 集成
- ✅ Evolution Engine
- ✅ MCP Gateway

---

## 更新日志

- 2026-05-08: 创建本文档，综合 3 个子代理分析结果
- 2026-05-08: 全面验证后更新 — Items 1-9, 11-12 全部 ✅ 已完成
