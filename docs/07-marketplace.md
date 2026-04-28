# Agent 分发与质量

> Agent Nexus Design Doc — §9 Agent 分发与质量：Git-based 分发、安装流程、质量验证、发布流程、版本管理、源管理

> **Status**: ✅ Implemented (core distribution) | 🔧 Partial (SemVer parser, quality validation tool)
> **Code**: `src/agent_nexus/platform/local/installer.py`, `src/agent_nexus/platform/local/supervisor.py`, `src/agent_nexus/platform/local/sources.py`, `src/agent_nexus/platform/local/lockfile.py`, `src/agent_nexus/platform/local/cli/`
> **Tests**: `tests/unit/test_local_installer.py`, `tests/unit/test_local_supervisor.py`, `tests/unit/test_local_sources.py`, `tests/unit/test_local_lockfile.py`, `tests/unit/cli/`

## §9 Agent 分发与质量

### 9.1 分发模型

> **参考模式**: Homebrew tap（官方 core + 用户自定义 tap）、Cargo git dependencies

Agent 通过 Git 仓库分发，不依赖云基础设施。采用 Homebrew tap 模式，支持三种包源：

| 类型 | 说明 | 配置方式 |
|------|------|----------|
| **Official** | 官方 monorepo，`index.yaml` + `packages/` 目录 | 内置默认 |
| **Private** | 用户/团队私有 repo | `sources.yaml` 注册 |
| **Direct** | 直接指定 git URL 安装 | CLI `--git-url` 参数 |

**Agent Package 格式**（与 §7.6 Agent Package 结构一致）：

| 类型 | 包含内容 | 分发方式 |
|------|----------|----------|
| **Atomic Agent** | manifest + SKILL.md + agent.py + tools/ + hooks/ + mcp_servers/ + pyproject.toml | git clone --sparse |
| **Composite Agent** | manifest + SKILL.md + composition.toml + hooks/ + pyproject.toml | git clone --sparse |

> 详见 [§12 Git-Based Agent 分发与本地架构](10-cloud-local-architecture.md)。

### 9.2 安装流程

```bash
# 从官方源安装（自动解析 index.yaml → sparse clone → validate → venv）
agent-nexus install doc-filler
# 内部流程:
# 1. 解析 index.yaml 找到 doc-filler 的路径
# 2. git clone --sparse --filter=blob:none（只下载目标目录）
# 3. 验证 agent-manifest.yaml + SKILL.md 完整性
# 4. uv venv + uv pip install（创建隔离虚拟环境）
# 5. 注册到本地 Agent 目录（写入 lockfile.json）
# 6. 验证依赖可用性

# 从私有源安装
agent-nexus install my-agent --source team-tap

# 直接从 git URL 安装
agent-nexus install my-agent --git-url https://github.com/org/agent-repo --subdir agents/my-agent

# 安装 Composite Agent（自动检查 atomic_agents 依赖）
agent-nexus install feature-delivery-pipeline
# 额外步骤:
# 7. 解析 composition.toml
# 8. 检查 atomic_agents 依赖是否已安装
# 9. 缺失依赖 → 提示安装或自动安装（需确认）
```

### 9.3 质量验证

Agent 发布前必须通过以下验证（本地运行，不依赖云服务）：

1. **agent-manifest.yaml 验证**：格式检查、必填字段、type 合法性
2. **SKILL.md 验证**：三层格式检查、triggers 完整性
3. **MCP 工具测试**：所有 tools/ 下定义的工具可用性测试
4. **Hook 验证**：所有 hooks 配置的语法和执行测试
5. **MCP Server 依赖检查**：声明的 mcp_servers 可连接
6. **权限审计**：permissions 配置合理性检查（不申请不必要权限）
7. **依赖完整性**：Composite Agent 的所有 atomic_agents 已在源中存在
8. **模型配置兼容**：推荐模型可用性验证

```bash
# 本地质量验证命令
agent-nexus check ./my-agent
# 逐项检查并输出结果：
# ✅ agent-manifest.yaml 格式正确
# ✅ SKILL.md 三层内容完整
# ✅ 4 tools 通过可用性测试
# ✅ hooks 语法正确
# ⚠️  mcp_servers/docx-server 未在本地配置（非阻塞）
# ✅ permissions 遵循最小权限原则
# ✅ 依赖完整性通过
```

### 9.4 发布流程

> **参考**: Homebrew tap 的 PR 贡献模式、crates.io 的 semver 版本管理

发布采用 Git-based 贡献模式，不依赖云服务：

| 场景 | 流程 |
|------|------|
| **发布到 Official** | Fork → 开发 → 质量验证 → PR → Review → Merge |
| **发布到 Private** | 开发 → 质量验证 → git push（tag 标记版本） |
| **Direct** | 开发 → 质量验证 → git push → 用户直接 URL 安装 |

**版本管理**：

- 使用 git tags 标记版本（格式：`{agent-name}-v{semver}`，如 `doc-filler-v1.2.0`）
- SemVer 语义版本：`MAJOR.MINOR.PATCH`
- Lockfile 记录安装的 commit SHA，`agent-nexus update` 可升级到最新 tag

**PRIVATE → PUBLIC 提升**（Official 源）：

| 阶段 | 可见性 | 条件 |
|------|--------|------|
| **PR Review** | Reviewer 可见 | 提交 PR 即进入 review |
| **Published** | 所有用户 | PR 合并到 main 分支 |
| **Featured**（未来） | 推荐 | effective_rate > 0.7 且 total_selections > 20 |

### 9.5 版本解析

> 详见 [§12.4 Git Installer 实现](10-cloud-local-architecture.md#124-git-installer-实现)

| 操作 | 说明 |
|------|------|
| `install` | 默认安装最新 tag；无 tag 则安装 main 最新 commit |
| `install@version` | 安装指定版本（如 `doc-filler@1.2.0`） |
| `update` | 拉取最新 tag，更新 venv |
| `lock` | 锁定当前 commit SHA，`update` 不升级 |

### 9.6 源管理

`sources.yaml` 配置多个包源：

```yaml
# ~/.agent-nexus/sources.yaml
sources:
  official:
    type: git
    url: https://github.com/agent-nexus/official-packages
    branch: main

  team-tap:
    type: git
    url: git@github.com:my-team/agent-tap.git
    branch: main

  experimental:
    type: git
    url: https://github.com/agent-nexus/experimental
    branch: dev
```

**源发现流程**：

```
agent-nexus install <name>
  → 遍历 sources.yaml 中的源（按顺序）
  → 每个源解析 index.yaml
  → 找到第一个匹配的 name
  → 从该源安装
```

### 9.7 Agent Package 发布清单

发布前必须确保：

- [ ] `agent-manifest.yaml` 完整且格式正确
- [ ] `SKILL.md` 包含三层渐进式内容
- [ ] 所有 tools/ 下的工具有单元测试
- [ ] hooks/ 中的钩子不会无限阻塞
- [ ] mcp_servers/ 中的外部依赖可替代（有 fallback）
- [ ] permissions 申请最小权限原则
- [ ] Composite Agent 的所有依赖已发布
- [ ] pyproject.toml 版本号与 manifest 一致
- [ ] `agent-nexus check` 本地质量验证通过
- [ ] git tag 已正确标记版本

---
