# P1-4: Domain Atomic Agent 扩展

> 优先级：P1 🟠 | 预估工期：W7-10 | 依赖 P1-3（Quality Gate + TOML 格式）

## 需求

- **来源**：市场趋势从泛化→专精，Agent Nexus Atomic/Composite 分层优势明显
- **目标**：将现有 12 个 Atomic Agent 扩展到 30-50 个，覆盖主流开发领域，并建立社区贡献机制
- **需求强度**：⭐⭐⭐⭐ | **差异化**：⭐⭐⭐⭐ | **实现复杂度**：低

## 当前状态

| 组件 | 状态 | 文件 |
|------|------|------|
| Atomic Agent 模式 | ✅ 成熟 | `agents/atomic/`（12 个） |
| Composite Agent 组合 | ✅ 成熟 | `agents/composite/`（5 个） |
| Agent Manifest | ✅ 成熟 | `agent-manifest.yaml` |
| SKILL.md 规范 | ✅ 成熟 | 三段式渐进加载 |
| Agent 脚手架 | ❌ 缺失 | 手动创建 4-5 个文件 |
| 能力分类体系 | ❌ 缺失 | 无受控词汇表 |
| Agent 模板系统 | ❌ 缺失 | 无 cookiecutter 模板 |
| 测试基础设施 | ⚠️ 基础 | 多数 agent 无测试文件 |

### 关键约束

- Agent 必须有 SKILL.md 才能实现
- 新 agent 使用统一 agent.toml 格式（依赖 P1-3）
- 三层加载模型（core/activated/dormant）已验证，50 agent 不需要第四层

## 设计方案

### 能力分类体系

```toml
# capabilities.toml — 受控能力词汇表
[categories]
"software-engineering" = ["code-review", "testing", "refactoring", "debugging"]
"documentation" = ["api-docs", "technical-writing", "localization", "compliance"]
"devops" = ["ci-cd", "deployment", "monitoring", "infrastructure"]
"data-engineering" = ["etl", "data-quality", "schema-design", "analytics"]
"security" = ["vulnerability-scan", "compliance-check", "dependency-audit"]
"ai-ml" = ["model-review", "data-validation", "experiment-tracking"]
"product" = ["requirements", "market-analysis", "competitive-intelligence"]
```

### Agent 脚手架 CLI

新增 `agent-nexus create-agent` 命令：

```bash
# 交互式创建
agent-nexus create-agent --name "db-migration-reviewer" --category "software-engineering" --interactive

# 模板创建
agent-nexus create-agent --template code-reviewer --name "custom-reviewer"
```

**生成文件**：
- `agent.toml`（预填分类和能力标签）
- `SKILL.md`（三段式模板，带 TODO 占位符）
- `pyproject.toml`（标准依赖和 entry point）
- `src/{agent_name}/__init__.py`
- `src/{agent_name}/agent.py`（最小实现骨架）
- `tests/test_{agent_name}.py`（测试模板）

### 扩展路线图（30-50 agents）

**第一批（+8，达 20 个）** — 高需求、低实现成本：

| Agent | 领域 | 核心能力 | 复杂度 |
|-------|------|----------|--------|
| dependency-auditor | security | 依赖漏洞扫描（Top 1） | 低 |
| config-linter | devops | 配置文件规范检查（Top 2） | 低 |
| error-analyzer | debugging | 错误模式分析与建议（Top 3） | 低 |
| db-schema-analyzer | data | 数据库 schema 设计审查 | 低 |
| api-contract-tester | qa | API 契约测试生成 | 中 |
| performance-profiler | perf | 性能瓶颈分析 | 中 |
| i18n-validator | i18n | 国际化完整性检查 | 低 |
| data-pipeline-validator | data | ETL pipeline 验证 | 中 |

**第二批（+10，达 30 个）** — 中等需求、中实现成本：

| Agent | 领域 |
|-------|------|
| terraform-reviewer | devops |
| dockerfile-optimizer | devops |
| graphql-schema-designer | api |
| ml-model-reviewer | ai-ml |
| prompt-engineer | ai-ml |
| architecture-reviewer | software |
| migration-planner | data |
| incident-analyzer | sre |
| cost-optimizer | cloud |
| compliance-checker | governance |

**第三批（+10-20，达 40-50 个）** — 长尾领域、社区驱动：

按社区贡献为主，平台提供模板和质量检查。方向包括：mobile、game-dev、embedded、blockchain 等。

## 已确认决策

| # | 决策 | 理由 |
|---|------|------|
| D19 | 第一批优先级：dependency-auditor → config-linter → error-analyzer | 需求频率 × 实现简单度排序 |
| D20 | SKILL.md 编写：AI 生成初稿 + 用户审核 | 结构固定可自动生成，领域准确性靠人工 |
| D21 | Agent 并发加载：延续现有三层（core/activated/dormant） | 已验证的模型，50 agent 不需要第四层 |

## 实施阶段

| Phase | 内容 | 工期 | 验证标准 |
|-------|------|------|----------|
| 1 | 能力分类体系 + 脚手架 CLI | W7-8 | `create-agent` 生成可运行的 agent 骨架 |
| 2 | 第一批 8 个 agent | W8-10 | 每个：SKILL.md + 测试 + MCP 可调用 |
| 3 | 第二批 10 个 agent | W10 | 同上 |
| 4 | 社区贡献流程 + 模板 | W10+ | 外部贡献者 PR 通过 Quality Gate |

## 依赖

- P1-3 的 Quality Gate Pipeline（agent 质量检查）
- P1-3 的统一 TOML 格式

## 风险

| 风险 | 缓解 |
|------|------|
| Agent 质量参差不齐 | Quality Gate + 最低覆盖率要求 |
| 维护 50 agent 成本 | 社区贡献 + 自动化测试 + 脚手架 |
| LLM 提示词调优 | SKILL.md 标准化减少调优量 |
