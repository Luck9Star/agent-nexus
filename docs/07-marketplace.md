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

Agent 发布前必须通过以下验证（本地运行，不依赖云服务）。检查由 `quality_gate.py` 中 5 个 Check 类 + SecurityCheck 组成的管线执行：

1. **ManifestCheck**：验证 agent.toml / agent-manifest.yaml 存在且包含必填字段（name, version, type）
2. **SkillFileCheck**：验证 SKILL.md 存在且非空
3. **SecurityCheck**：AST 级安全扫描，检测 eval/exec/subprocess 等危险调用
4. **DependencyCheck**：验证 pip_dependencies 声明的依赖名称符合 PEP 508 格式
5. **TestCoverageCheck**：检查 tests/ 目录存在且包含至少一个测试文件

```bash
# 本地质量验证命令
agent-nexus check ./my-agent
# 逐项检查并输出结果：
# ✅ manifest: Manifest valid
# ✅ skill_file: SKILL.md present and non-empty
# ✅ security: No dangerous patterns detected
# ✅ dependency: All 3 dependencies valid
# ⚠️  test_coverage: No tests/ directory found（WARNING，不阻塞）
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

> 详见 [§12.4 安装与发布流程](10-cloud-local-architecture.md#124-安装与发布流程)

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

### 9.8 Local Module 扩展

> **Code**: `src/agent_nexus/platform/local/` (12 模块文件 + `cli/` 子目录 11 个命令文件 + `capabilities.toml` 能力分类)

除核心分发模块外，local/ 包含以下扩展模块，支撑质量验证、安全签名和脚手架功能：

#### 9.8.1 签名验证（signer.py）

Agent 包的加密签名和验证，支持两种后端：

| 后端 | 说明 | 优先级 |
|------|------|--------|
| **Sigstore** | Keyless 签名（零证书管理） | 首选 |
| **GPG** | 传统公钥签名 | 备选 |

签名基于 Merkle-tree 式目录哈希：遍历所有文件（排除 `.git`/`__pycache__`），按路径排序后逐文件 SHA-256，最终合成目录哈希。Sigstore 依赖按需导入（非硬依赖）。

#### 9.8.2 评分系统（scoring.py）

`ScoreManager` 持久化 Agent 质量评分到 JSON 文件：

| 指标 | 类型 | 说明 |
|------|------|------|
| download_count | 计数 | 累计安装次数 |
| quality_score | 0-1 浮点 | Quality Gate 评估分数 |
| user_rating | 1-5 加权平均 | 用户评价（增量更新均值） |

所有修改即时持久化（无批量缓冲），用于 `agent-nexus search` 结果排序。

#### 9.8.3 Agent 脚手架（create_cmd.py）

`agent-nexus create agent` 命令，支持交互式（`--wizard`）和非交互式两种模式。生成 8 个文件的完整 Agent 包：

| 文件 | 说明 |
|------|------|
| `agent-manifest.yaml` | Agent 元数据 |
| `SKILL.md` | 三层渐进式能力描述 |
| `pyproject.toml` | 包依赖和入口 |
| `main.py` | 独立运行入口 |
| `<pkg>/__init__.py` | Python 包 |
| `<pkg>/agent.py` | PydanticAI Agent 定义 |
| `<pkg>/mcp_adapter.py` | FastMCP 适配器（stdio + SSE） |
| `agent.py` | 顶层入口 |

两种工具模式：`simple`（单一 `run` 工具）和 `pipeline`（analyze/execute/report 三阶段）。

#### 9.8.4 依赖解析（dependency_resolver.py）

`DependencyResolver` 解析和验证 Agent 间依赖关系：

- **pip 依赖**：解析 PEP 508 格式的 `pip_dependencies`，检测跨 Agent 版本冲突
- **Agent 依赖**：将 `atomic_agents` 引用解析为已安装路径（Composite Agent 必需）
- **冲突检测**：成对比较版本规格符（字符串级），报告 `ConflictReport`

#### 9.8.5 统一 Manifest 加载（manifest.py）

`find_manifest()` → `load_manifest()` 的统一入口，支持两种格式：

| 格式 | 优先级 | 说明 |
|------|--------|------|
| `agent.toml` | 首选 | TOML 格式，字段包裹在 `[agent]` section 下 |
| `agent-manifest.yaml` | 兼容 | YAML 格式（扁平结构），加载时发出 DeprecationWarning |

提供 `migrate_yaml_to_toml()` 迁移工具。使用 `tomllib`（Python 3.11+ 标准库），内置最小 TOML 序列化器避免外部依赖。

#### 9.8.6 质量验证管线（quality_gate.py）

> 详见 §9.3 质量验证。此处补充管线架构。

`QualityGate` 执行可配置的检查管线，由 `BaseCheck` 子类组成：

| 检查 | 严重级别 | 阻塞行为 |
|------|----------|----------|
| ManifestCheck | CRITICAL | 必需字段缺失 → FAIL |
| SkillFileCheck | CRITICAL | SKILL.md 缺失/空 → FAIL |
| SecurityCheck | CRITICAL | AST 检测 eval/exec/subprocess → FAIL |
| DependencyCheck | WARNING | PEP 508 格式错误 → 扣 0.1 分 |
| TestCoverageCheck | WARNING | 无 tests/ → 扣 0.1 分 |

**两级判定**：CRITICAL 失败直接 FAIL（忽略分数）；仅 WARNING 失败则按 `score >= floor`（默认 0.6）判定。管线完全可定制——调用方可传入自定义 `list[BaseCheck]`。

#### 9.8.7 CLI 命令模块（cli/）

`cli/` 子目录包含 11 个命令实现文件，覆盖全部平台操作：

| 命令文件 | 对应命令 | 说明 |
|---------|---------|------|
| `__init__.py` | — | Typer app 入口，注册所有命令组 |
| `_lifecycle.py` | install/uninstall/update/run/list/search/info | Agent 生命周期管理 |
| `check_cmd.py` | check | 质量验证（agent 包完整性检查） |
| `_create_agent_cmd.py` | create-agent | 能力分类感知的 Agent 脚手架 |
| `create_cmd.py` | create agent | 传统向导式脚手架 |
| `init_cmd.py` | init/version/doctor/env | 初始化与诊断 |
| `config_cmd.py` | config show/get/edit/validate/providers/path | 配置管理 |
| `runtime_cmd.py` | runtime start/stop/restart/status/ps/logs | 运行时进程管理 |
| `evolution_cmd.py` | evolution status/health/list/history/metrics/fix/promote | 自进化引擎管理 |
| `sources_cmd.py` | sources list/add/remove | 包源管理 |
| `_shared.py` | — | 共享工具函数和选项定义 |

#### 9.8.8 能力分类（capabilities.toml）

`capabilities.toml` 定义 Agent 能力分类体系，供 `create-agent` 命令使用。每个能力类别包含名称、描述和推荐的工具模板，用于指导 Agent 脚手架生成合理的工具集合。

---
