# Git-Based Agent 分发与本地架构

> Agent Nexus Design Doc — §12 Git-Based Agent 分发与本地架构：Git 分发模型、本地架构、安装/发布流程、Python 实现、Rust 重构路径

> **Status**: ✅ Implemented (core) | 🔧 Partial (SemVer parser)
> **Code**: `src/agent_nexus/platform/local/` (installer.py, sources.py, lockfile.py, supervisor.py, cli/), `src/agent_nexus/models/distribution.py`
> **Tests**: `tests/unit/test_local_installer.py`, `tests/unit/test_local_supervisor.py`, `tests/unit/test_local_sources.py`, `tests/unit/test_local_lockfile.py`, `tests/unit/cli/`

## §12 Git-Based Agent 分发与本地架构

### 12.1 设计动机

Agent Nexus 初期不建设云端市场（Cloud Marketplace），而是通过 **Git 仓库**作为 Agent 包的分发渠道。这一决策基于以下考量：

- **零基础设施**：无需搭建和维护云服务（FastAPI + SQLite + CAS 存储）
- **开发者友好**：Agent 开发者通过 `git push` 发布新版本，通过 PR 贡献到官方目录
- **git 原生版本管理**：git tags 即版本，git commit SHA 即内容寻址，无需自建 CAS
- **渐进式演进**：后续可无缝升级为 Cloud Registry（HTTP API 作为新的 source type）

> **参考模式**：Homebrew tap（官方 core + 用户自定义 tap）、Cargo git dependencies、deer-flow `.skill` ZIP 安装

```
┌─────────────────────────────────────────────────────────────┐
│                    用户请求（CLI / MCP）                      │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                  Local Platform（本地平台）                    │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐   │
│  │ CLI      │ │ Git      │ │ Agent    │ │ MCP Gateway  │   │
│  │ (Typer)  │ │ Installer│ │Supervisor│ │ (FastMCP)    │   │
│  └──────────┘ └──────────┘ └──────────┘ └──────────────┘   │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────────────┐    │
│  │ Config   │ │ Lockfile │ │ Provider Registry        │    │
│  │ Manager  │ │ Manager  │ │ (openai-compat/anthropic) │    │
│  └──────────┘ └──────────┘ └──────────────────────────┘    │
└───────────────────────────┬─────────────────────────────────┘
                            │ git clone / git pull
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                Git Package Sources（包源）                    │
│  ┌──────────────────┐  ┌──────────────────┐                │
│  │ Official Monorepo│  │ Private Repos    │                │
│  │ (index.yaml +    │  │ (sources.yaml    │                │
│  │  packages/*)     │  │  注册)            │                │
│  └──────────────────┘  └──────────────────┘                │
└─────────────────────────────────────────────────────────────┘
                            │ 子进程
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                Agent Subprocesses（Agent 子进程）             │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐                    │
│  │ Agent A  │ │ Agent B  │ │ Agent C  │  ...               │
│  │ (MCP Srv)│ │ (MCP Srv)│ │ (MCP Srv)│                    │
│  └──────────┘ └──────────┘ └──────────┘                    │
└─────────────────────────────────────────────────────────────┘
```

### 12.2 Git 分发模型

#### 12.2.1 包源（Package Sources）

采用 Homebrew tap 模式，支持三种包源：

| 包源类型 | 说明 | 配置方式 |
|----------|------|----------|
| **Official** | 官方 monorepo，包含 index.yaml + packages/ 目录 | 内置默认 |
| **Private** | 用户/团队私有 repo | sources.yaml 注册 |
| **Direct** | 直接指定 git URL 安装 | CLI 参数 `--git-url` |

**Official Monorepo 结构**：

```
agent-nexus-packages/           # 官方包仓库
├── index.yaml                  # 全局索引
├── packages/
│   ├── doc-filler/
│   │   ├── agent-manifest.yaml
│   │   ├── SKILL.md
│   │   ├── agent.py
│   │   ├── tools/
│   │   ├── pyproject.toml
│   │   └── ...
│   ├── code-reviewer/
│   ├── requirements-analyzer/
│   └── ...
└── README.md
```

**index.yaml 格式**：

```yaml
# 全局索引：列出所有可用 Agent 及最新版本
agents:
  - name: doc-filler
    version: 1.2.0
    type: atomic
    description: "Word 文档模板填充专家"
    tags: [document, template, docx]
  - name: code-reviewer
    version: 1.0.0
    type: atomic
    description: "代码审查专家"
    tags: [code, review, quality]
  - name: feature-delivery-pipeline
    version: 1.1.0
    type: composite
    description: "需求驱动并行生成 API 文档、测试套件和代码审查"
    tags: [pipeline, delivery]
    dependencies:
      - requirements-analyzer
      - api-doc-generator
      - test-suite-generator
      - code-reviewer
```

**sources.yaml（用户配置）**：

```yaml
# ~/.agent-nexus/sources.yaml
sources:
  - name: official
    type: git
    url: https://github.com/user/agent-nexus-packages.git
    branch: main

  - name: my-team
    type: git
    url: git@github.com:my-team/agent-packages.git
    branch: main

  # 私有 source 可配置认证
  - name: enterprise
    type: git
    url: https://gitlab.internal.com/ai/agents.git
    branch: stable
```

#### 12.2.2 版本管理

| 机制 | 说明 |
|------|------|
| **版本来源** | git tags（格式：`agent-name/v1.2.0`） |
| **版本验证** | agent-manifest.yaml 中 version 必须与 tag 一致 |
| **版本解析** | 本地 SemVer 解析，无需网络 |
| **锁定** | lockfile.json 记录 commit SHA（非 tag），确保可复现 |
| **回退** | `agent-nexus update --rollback <name>` 回退到上一版本 |

#### 12.2.3 搜索与发现

| 方式 | 说明 |
|------|------|
| **本地索引** | `agent-nexus search <query>` 搜索所有 source 的 index.yaml |
| **CLI 浏览** | `agent-nexus list --type atomic` 列出所有已安装/可用 Agent |
| **详情查看** | `agent-nexus info <name>` 显示 manifest + SKILL.md 摘要 |
| **GitHub Topics** | 官方 repo 使用 GitHub topics 标签辅助发现 |

### 12.3 本地架构

#### 12.3.1 职责划分

| 职责 | 组件 | 说明 |
|------|------|------|
| CLI 入口 | `local/cli/` | Typer 命令行（create, init, config, runtime, evolution 命令组） |
| Git 安装 | `local/installer.py` | git clone --sparse + validate + venv + lockfile |
| 包源管理 | `local/sources.py` | sources.yaml 解析 + source 刷新 |
| 进程管理 | `local/supervisor.py` | Agent 子进程启动/停止/健康检查/重启 |
| MCP 聚合 | `gateway/` | FastMCP Gateway，聚合所有 Agent 为单一 MCP Server |
| 编排调度 | `router/` | Platform Router，4-Phase Workflow + TOML DAG |
| 配置管理 | `config/` | config.toml + Provider Registry |
| 锁文件 | `local/lockfile.py` | lockfile.json 读写 |
| 模型配置 | `config/` | pydantic-ai provider:model 解析 |

#### 12.3.2 目录结构

```
~/.agent-nexus/
├── agents/                     # 已安装的 Agent Package
│   ├── doc-filler/             # 完整 Agent 目录
│   │   ├── agent-manifest.yaml
│   │   ├── SKILL.md
│   │   ├── agent.py
│   │   ├── tools/
│   │   └── pyproject.toml
│   ├── code-reviewer/
│   └── ...
├── venvs/                      # 每个 Agent 独立 venv
│   ├── doc-filler/             # uv 创建的虚拟环境
│   ├── code-reviewer/
│   └── ...
├── cache/                      # Git 仓库缓存
│   └── repos/
│       ├── official/           # 官方 repo clone
│       └── my-team/            # 私有 repo clone
├── runtimes/                   # 运行时状态
│   ├── taskgraph.db            # TaskGraph SQLite
│   └── evolution.db            # Evolution Engine SQLite
├── config.toml                 # 全局配置（模型、Provider Registry）
├── sources.yaml                # 包源配置
├── lockfile.json               # 版本锁定文件
└── logs/                       # 运行日志
```

#### 12.3.3 lockfile.json

```json
{
  "version": 1,
  "agents": {
    "doc-filler": {
      "version": "1.2.0",
      "source": "official",
      "commit_sha": "abc123def456...",
      "agent_type": "atomic",
      "installed_at": "2026-04-18T12:00:00Z",
      "venv_path": "~/.agent-nexus/venvs/doc-filler"
    },
    "feature-delivery-pipeline": {
      "version": "1.1.0",
      "source": "official",
      "commit_sha": "789abc012def...",
      "agent_type": "composite",
      "installed_at": "2026-04-18T12:05:00Z",
      "venv_path": "~/.agent-nexus/venvs/feature-delivery-pipeline",
      "dependencies": [
        "requirements-analyzer",
        "api-doc-generator",
        "test-suite-generator",
        "code-reviewer"
      ]
    }
  }
}
```

#### 12.3.4 config.toml

```toml
[runtime]
python_path = "python3"        # Python 解释器路径
uv_path = "uv"                 # uv 路径

[models]
default = "openai:gpt-4o"

# Provider Registry：自定义 base_url 的 provider
[models.providers.deepseek]
base_url = "https://api.deepseek.com/v1"
api_key_env = "DEEPSEEK_API_KEY"
api = "openai-compatible"       # 默认值，可不填

[models.providers.minimax]
base_url = "https://api.minimax.chat/v1"
api_key_env = "MINIMAX_API_KEY"
api = "anthropic-messages"      # 走 Anthropic Messages API 格式

[models.providers.qwen]
base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
api_key_env = "DASHSCOPE_API_KEY"
# api = "openai-compatible"     # 默认，可不填

[models.providers.ollama]
base_url = "http://localhost:11434/v1"
api_key_env = ""

[python]
min_version = "3.11"
```

**模型配置优先级链**：

```
AGENT_MODEL 环境变量 > agent-manifest.yaml model_config.recommended > config.toml [models].default
```

**Provider API Key 来源**：

```
os.environ[provider.api_key_env] — 从环境变量读取，不在配置文件中存储明文 key
```

### 12.4 安装与发布流程

#### 12.4.1 安装流程

```
agent-nexus install doc-filler
        │
        ▼
   ┌─ 解析 sources.yaml ─────────────────────────┐
   │  找到 doc-filler 所在 source                  │
   └─────────────────────┬───────────────────────┘
                         ▼
   ┌─ git clone --sparse ─────────────────────────┐
   │  只克隆 packages/doc-filler/ 目录             │
   │  (git sparse-checkout + depth=1)             │
   └─────────────────────┬───────────────────────┘
                         ▼
   ┌─ validate ───────────────────────────────────┐
   │  1. agent-manifest.yaml 格式检查              │
   │  2. SKILL.md 存在性检查                       │
   │  3. pyproject.toml 完整性检查                  │
   │  4. 安全审计（无敏感路径、最小权限）           │
   └─────────────────────┬───────────────────────┘
                         ▼
   ┌─ install ────────────────────────────────────┐
   │  1. 复制到 ~/.agent-nexus/agents/doc-filler/ │
   │  2. uv venv ~/.agent-nexus/venvs/doc-filler/ │
   │  3. uv pip install -r pyproject.toml         │
   │  4. 更新 lockfile.json                       │
   └─────────────────────┬───────────────────────┘
                         ▼
   ┌─ Composite Agent 额外步骤 ────────────────────┐
   │  5. 解析 composition.toml                    │
   │  6. 检查 atomic_agents 依赖是否已安装         │
   │  7. 未安装的依赖递归安装                      │
   └──────────────────────────────────────────────┘
```

#### 12.4.2 发布流程

| 场景 | 步骤 |
|------|------|
| **发布到官方目录** | 1. Fork 官方 monorepo → 2. 添加 Agent 到 packages/ → 3. 更新 index.yaml → 4. 提交 PR → 5. 质量验证通过后合并 → 6. 维护者打 tag |
| **发布到私有 repo** | 1. 添加 Agent 到 repo → 2. git tag `agent-name/v1.0.0` → 3. git push --tags |
| **直接安装（开发中）** | `agent-nexus install --git-url ./path/to/agent` 或 `agent-nexus install --git-url https://github.com/user/my-agent.git` |

**质量门禁（发布前检查）**：

- [ ] `agent-manifest.yaml` 完整且格式正确
- [ ] `SKILL.md` 包含三层渐进式内容
- [ ] 所有 tools/ 下的工具有单元测试
- [ ] hooks/ 中的钩子不会无限阻塞
- [ ] permissions 申请最小权限原则
- [ ] Composite Agent 的所有依赖已在同一 source 存在
- [ ] pyproject.toml 版本号与 manifest 一致
- [ ] 无敏感文件（.env、credentials）包含在 Agent 目录中

### 12.5 Python 实现

#### 12.5.1 实现范围

| 组件 | 实现 | 说明 |
|------|------|------|
| Git Installer | `local/installer.py` | subprocess 调用 git |
| Source Manager | `local/sources.py` | sources.yaml 解析 + repo 缓存 |
| CLI | `local/cli/` | Typer 命令行（create, init, config, runtime, evolution 命令组） |
| MCP Gateway | `gateway/` | FastMCP 聚合 |
| Agent Supervisor | `local/supervisor.py` | asyncio subprocess |
| Config | `config/` | TOML/YAML 解析 + Provider Registry |
| Lockfile | `local/lockfile.py` | JSON 读写 |

#### 12.5.2 实现代码

**PackageSource 和 SourceManager**：

```python
from dataclasses import dataclass, field

@dataclass
class PackageSource:
    """Git 包源"""
    name: str
    type: str = "git"       # git | http (future)
    url: str = ""
    branch: str = "main"
    local_cache: str = ""   # 本地 clone 路径

@dataclass
class SourceManager:
    """管理所有包源"""
    sources: list[PackageSource] = field(default_factory=list)
    cache_dir: str = "~/.agent-nexus/cache/repos"

    def load(self, path: str = "~/.agent-nexus/sources.yaml"):
        """从 sources.yaml 加载包源配置"""
        ...

    def refresh(self, source_name: str | None = None):
        """刷新 source 的 git cache（git fetch + git pull）"""
        ...

    def find_agent(self, name: str) -> tuple[PackageSource, dict] | None:
        """在所有 source 的 index.yaml 中查找 agent"""
        ...
```

**GitInstaller**：

```python
import asyncio
import shutil
from pathlib import Path

class GitInstaller:
    """通过 git 安装 Agent 包"""

    def __init__(self, base_dir: str = "~/.agent-nexus"):
        self.agents_dir = Path(base_dir) / "agents"
        self.venvs_dir = Path(base_dir) / "venvs"

    async def install(self, name: str, source: PackageSource,
                      version: str | None = None) -> dict:
        """安装 Agent：clone → validate → copy → venv → lockfile"""
        # 1. Sparse clone
        cache_path = await self._sparse_clone(source, name)

        # 2. Validate
        manifest = self._validate(cache_path)

        # 3. Copy to agents dir
        dest = self.agents_dir / name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(cache_path, dest)

        # 4. Create venv and install dependencies
        venv_path = self.venvs_dir / name
        await self._create_venv(venv_path, dest / "pyproject.toml")

        # 5. Return lockfile entry
        commit_sha = await self._get_commit_sha(source, name)
        return {
            "version": manifest["version"],
            "source": source.name,
            "commit_sha": commit_sha,
            "agent_type": manifest["type"],
        }

    async def _sparse_clone(self, source: PackageSource,
                            agent_name: str) -> Path:
        """git clone --sparse 只克隆指定 Agent 目录"""
        repo_path = Path(self.cache_dir) / source.name
        pkg_path = f"packages/{agent_name}/"

        if not repo_path.exists():
            # 初始 clone
            await asyncio.create_subprocess_shell(
                f"git clone --no-checkout --depth=1 "
                f"--filter=blob:none --sparse {source.url} {repo_path}"
            )

        # Sparse checkout
        proc = await asyncio.create_subprocess_shell(
            f"cd {repo_path} && git sparse-checkout set {pkg_path} "
            f"&& git checkout {source.branch}"
        )
        await proc.wait()
        return repo_path / pkg_path

    def _validate(self, path: Path) -> dict:
        """验证 Agent Package 完整性"""
        manifest_path = path / "agent-manifest.yaml"
        skill_path = path / "SKILL.md"
        pyproject_path = path / "pyproject.toml"

        assert manifest_path.exists(), "Missing agent-manifest.yaml"
        assert skill_path.exists(), "Missing SKILL.md"
        assert pyproject_path.exists(), "Missing pyproject.toml"

        # Parse and validate manifest
        import yaml
        manifest = yaml.safe_load(manifest_path.read_text())
        assert "name" in manifest
        assert "version" in manifest
        assert "type" in manifest
        assert manifest["type"] in ("atomic", "composite")

        return manifest

    async def _create_venv(self, venv_path: Path,
                           pyproject_path: Path):
        """uv 创建虚拟环境并安装依赖"""
        proc = await asyncio.create_subprocess_shell(
            f"uv venv {venv_path} && "
            f"uv pip install -e {pyproject_path.parent}"
        )
        await proc.wait()

    async def _get_commit_sha(self, source: PackageSource,
                              agent_name: str) -> str:
        """获取当前 commit SHA"""
        repo_path = Path(self.cache_dir) / source.name
        proc = await asyncio.create_subprocess_shell(
            f"cd {repo_path} && git rev-parse HEAD",
            stdout=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        return stdout.decode().strip()
```

**AgentSupervisor**：

```python
@dataclass
class AgentHandle:
    """运行中 Agent 的句柄"""
    name: str
    process: asyncio.subprocess.Process
    config: AgentConfig
    state: AgentState = AgentState.IDLE

class AgentSupervisor:
    """Agent 子进程管理器"""

    def __init__(self, base_dir: str = "~/.agent-nexus"):
        self.base_dir = Path(base_dir)
        self.agents: dict[str, AgentHandle] = {}

    async def start(self, name: str, config: AgentConfig) -> AgentHandle:
        """启动 Agent 子进程（INSTALLED → IDLE）"""
        venv_python = (
            self.base_dir / "venvs" / name / "bin" / "python"
        )
        agent_main = self.base_dir / "agents" / name / "main.py"

        proc = await asyncio.create_subprocess(
            str(venv_python), str(agent_main),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Drain stderr in background to prevent pipe buffer deadlock.
        # When stderr=PIPE is opened but never read, the OS pipe buffer
        # fills (~64KB) and the writing process blocks indefinitely.
        asyncio.create_task(self._drain_stderr(proc, name))

        handle = AgentHandle(name=name, process=proc, config=config)
        self.agents[name] = handle
        return handle

    async def stop(self, name: str, force: bool = False):
        """停止 Agent（IDLE/RUNNING → TERMINATED）"""
        handle = self.agents.get(name)
        if not handle:
            return
        if force:
            handle.process.kill()
        else:
            handle.process.terminate()
            try:
                await asyncio.wait_for(handle.process.wait(), timeout=10)
            except asyncio.TimeoutError:
                handle.process.kill()
        del self.agents[name]

    async def health_check(self, name: str) -> bool:
        """检查 Agent 进程是否存活"""
        handle = self.agents.get(name)
        return handle and handle.process.returncode is None

    async def restart(self, name: str) -> AgentHandle:
        """重启 Agent（STOPPED → IDLE）"""
        config = self.agents[name].config
        await self.stop(name)
        return await self.start(name, config)
```

**CLI 命令**：

```python
import typer

app = typer.Typer(name="agent-nexus", help="Agent Nexus CLI")

@app.command()
def install(name: str, version: str | None = None,
            git_url: str | None = None):
    """安装 Agent"""
    ...

@app.command()
def uninstall(name: str):
    """卸载 Agent"""
    ...

@app.command()
def update(name: str | None = None):
    """更新 Agent（不指定 name 则更新全部）"""
    ...

@app.command()
def run(name: str, task: str):
    """运行 Agent 任务"""
    ...

@app.command()
def list_agents():
    """列出已安装的 Agent"""
    ...

@app.command()
def search(query: str):
    """搜索可用的 Agent"""
    ...

@app.command()
def info(name: str):
    """查看 Agent 详情"""
    ...

@app.command()
def sources():
    """管理包源"""
    ...
```

### 12.6 Rust 重构路径

#### 12.6.1 重构策略

| Python 组件 | Rust Crate | 说明 |
|------------|-----------|------|
| `local/installer.py` | **ap-fetcher** | Git 操作（git2 crate） |
| `local/sources.py` | ap-fetcher | 包源管理 |
| `local/supervisor.py` | **ap-runtime** | tokio::process 进程管理 |
| `gateway/` | **ap-gateway** | axum HTTP MCP 网关 |
| `local/cli/` | **ap-cli** | clap 命令行 |
| `config/` | **ap-core** | 配置解析 |
| `local/lockfile.py` | ap-core | 锁文件管理 |
| Agent Runtime | **不动** | MCP 边界 = 语言边界 |

#### 12.6.2 Crate 拆分

```
crates/
├── ap-core/          # 核心类型、配置、lockfile、manifest 解析、TaskGraph、IPC、DSL
│   └── Cargo.toml    # serde, serde_json, serde_yml, toml, rusqlite, tokio, chrono, thiserror
├── ap-fetcher/       # Git-based 包获取（替代 ap-registry + ap-client + ap-store）
│   └── Cargo.toml    # ap-core, tokio
├── ap-runtime/       # Agent Supervisor（tokio::process）
│   └── Cargo.toml    # ap-core, tokio, serde, thiserror
├── ap-gateway/       # MCP 网关（axum HTTP）
│   └── Cargo.toml    # ap-core, ap-runtime, tokio, serde, serde_json, thiserror, axum
├── ap-evolution/     # 自进化引擎（SQLite store + analyzer + evolver + promotion）
│   └── Cargo.toml    # ap-core, tokio, rusqlite, serde, thiserror
├── ap-cli-backend/   # CLI 后端（session store + command routing + health check）
│   └── Cargo.toml    # ap-core, tokio, rusqlite, serde, serde_json, thiserror
└── ap-cli/           # CLI（clap）
    └── Cargo.toml    # clap, ap-core, ap-fetcher, ap-runtime, ap-gateway, ap-evolution, ap-cli-backend
```

**关键变化**：
- 删除 `ap-registry`（Cloud Registry 服务端）— 不再需要
- 删除 `ap-client`（Registry HTTP 客户端）— 被 ap-fetcher 替代
- 删除 `ap-store`（CAS 本地缓存）— Git 天然提供内容寻址
- 新增 `ap-fetcher`（Git 操作 + 版本解析）
- 新增 `ap-evolution`（自进化引擎 Rust 实现）
- 新增 `ap-cli-backend`（CLI 后端独立组件）
- 删除依赖：object_store、cacache、sqlx、reqwest、rmcp、dashmap
- 新增依赖：git2、serde_yml（注意不是 serde_yaml）、axum（替代 rmcp）

#### 12.6.3 核心 Trait 与 Struct

> **Note**: 以下为实际实现签名。Rust 端采用 struct-based 方案而非 trait-based，与 Python 端的 class 设计对齐。

```rust
// ap-core: 核心类型
pub struct AgentId(pub String);
pub struct Version(pub semver::Version);

#[derive(Deserialize)]
pub struct Manifest {
    pub name: String,
    pub version: String,
    #[serde(rename = "type")]
    pub agent_type: AgentType,
    pub description: String,
    pub model_config: Option<ModelConfig>,
    pub permissions: Option<PermissionConfig>,
    pub dependencies: Option<Vec<String>>,
    pub mcp_servers: Option<HashMap<String, McpServerConfig>>,
}

pub enum AgentType { Atomic, Composite }

// ap-fetcher: Git 包获取（Installer trait）
pub trait Installer: Send + Sync {
    fn install(
        &self,
        url: &str,
        branch: Option<&str>,
        version: Option<&str>,
    ) -> Result<PathBuf, InstallerError>;
}

pub struct GitFetcher {
    sources: Vec<GitSource>,
    cache_dir: PathBuf,
}

// ap-runtime: Agent 进程管理（AgentProcess struct）
pub struct AgentProcess {
    name: String,
    child: Option<tokio::process::Child>,
    config: ProcessSpec,
}

impl AgentProcess {
    pub async fn start(&mut self) -> Result<()>;
    pub async fn stop(&mut self, force: bool) -> Result<()>;
    pub fn health_check(&self) -> Result<bool>;
}

// ap-gateway: MCP 路由（GatewayConfig + DeferredAgentRegistry struct）
pub struct GatewayConfig {
    pub port: u16,
    pub host: String,
}

pub struct DeferredAgentRegistry {
    core_agents: HashMap<String, AgentInfo>,
    deferred_agents: HashMap<String, AgentManifest>,
}

// ap-evolution: 自进化引擎（EvolutionStore + Evolver struct）
pub struct EvolutionStore { /* SQLite-backed */ }
pub struct Evolver { /* LLM-driven FIX/DERIVED/CAPTURED */ }

// ap-cli-backend: CLI 后端（CLIRouter + SessionStore）
pub struct CLIRouter { /* command dispatch */ }
pub struct SessionStore { /* SQLite CRUD + archival */ }
```

#### 12.6.4 不变接口

Rust 重构前后完全一致的接口：

1. `lockfile.json` 格式
2. `config.toml` 格式
3. `sources.yaml` 格式
4. MCP 协议（stdio/SSE）
5. Agent Package 目录结构
6. `agent-manifest.yaml` 和 `SKILL.md` 格式
7. IPC 消息格式（stdin/stdout JSON-lines）

#### 12.6.5 依赖对比

| 依赖 | 用途 | Python 实现 | Rust 重构 |
|------|------|------------|-----------|
| MCP 通信 | Agent ↔ 平台 | FastMCP | axum（HTTP） |
| 进程管理 | Agent 子进程 | asyncio.subprocess | tokio::process |
| Git 操作 | 包获取 | subprocess(git) | git2 (libgit2) |
| 序列化 | 配置/lockfile | PyYAML/toml | serde + toml + serde_yml |
| 版本解析 | SemVer | packaging | semver |
| CLI | 命令行 | Typer | clap |
| 数据库 | TaskGraph/Evolution | SQLite (aiosqlite) | SQLite (rusqlite) |

### 12.7 配置系统

> **Code**: `src/agent_nexus/platform/config/` (loader.py, model_config.py, defaults.py, model_db.py), `src/agent_nexus/models/capability.py`

配置系统采用三层优先级链 + 可选 models.dev 丰富层的架构：

#### 12.7.1 三层配置加载

`ConfigLoader` 负责加载和合并配置，优先级从高到低：

| 层级 | 来源 | 说明 |
|------|------|------|
| **L1 环境变量** | `AGENT_MODEL`, `DEFAULT_MODEL`, `AGENT_NEXUS_HOME` | 最高优先级，覆盖一切 |
| **L2 配置文件** | `config.toml` + `agent-nexus.toml`（项目级） | 全局 + 项目合并（项目覆盖全局） |
| **L3 内置默认** | `defaults.py` 常量 | 兜底默认值 |

**ModelConfigManager 6 级模型解析链**：`AGENT_MODEL` env → manifest recommended → tier map → config default → `DEFAULT_MODEL` env → `"openai:gpt-4o"` 内置常量。

#### 12.7.2 Model Tier 映射

| Tier | 模型 | 用途 |
|------|------|------|
| lightweight | `openai:gpt-4o-mini` | 快速任务、映射阶段 |
| standard | `openai:gpt-4o` | 通用任务 |
| powerful | `anthropic:claude-sonnet-4-20250514` | 复杂推理 |
| premium | `anthropic:claude-opus-4-20250116` | 深度分析、代码审查 |

#### 12.7.3 ModelCapabilityRegistry

`ModelCapabilityRegistry` 是模型能力查询引擎，为 Agency Pipeline 和 LLMClient 提供模型限制数据：

- **数据范围**：17 个内置模型，覆盖 5 个 Provider（Anthropic 5、OpenAI 5、DeepSeek 2、Qwen 3、MiniMax 2）。Ollama 仅存在于 `PROVIDER_DEFAULTS` 占位符
- **每个模型字段**：`model_id`, `provider`, `max_output_tokens`, `context_window`, `supports_vision`, `supports_tool_use`, `supports_temperature`, `temperature_min/max`
- **4 级回退**：精确匹配 → 去日期后缀（`claude-sonnet-4-20250514` → `claude-sonnet-4`）→ 去尾部数字 → Provider 默认值
- **可选丰富**：`ModelDBClient` 从 `models.dev/api.json` 获取 116+ Provider 的完整模型目录，磁盘缓存 24 小时 TTL，trigram 模糊搜索

#### 12.7.4 Provider Fallback

`ModelConfigManager.get_provider_config()` 按以下顺序解析 Provider 配置：

1. `config.toml` 中 `[providers.<name>]` 显式配置
2. 内置 Provider 默认值（`defaults.DEFAULT_PROVIDERS`，含 openai/anthropic/deepseek/minimax/qwen/ollama）
3. 仅需 `base_url` 即可工作（最小配置）

API Key 解析：环境变量优先 → `config.toml` 中 `api_key` 字段 → Provider 特定 fallback env var。

### 12.8 技术选型对比

| 层级 | 组件 | Python 实现 | Rust 重构 |
|------|------|------------|-----------|
| **分发** | 包获取 | subprocess(git) | git2 |
| **分发** | 版本管理 | packaging | semver |
| **分发** | 包源索引 | PyYAML | serde_yml |
| **本地** | CLI | Typer | clap |
| **本地** | 进程管理 | asyncio.subprocess | tokio::process |
| **本地** | 配置 | tomli + PyYAML | toml + serde_yml |
| **本地** | 锁文件 | json | serde_json |
| **Agent** | MCP Server | FastMCP | 不动（Python） |
| **Agent** | LLM 框架 | pydantic-ai | 不动（Python） |
| **Agent** | Runtime | IPythonRuntime | 不动（Python） |
| **Agent** | 依赖管理 | uv | uv（机制不变） |

### 12.9 与早期设计文档的关系

本文档（§12）从早期的"Cloud Service + Local Client"架构重构为 Git-based 分发。主要变更：

| 变更项 | v5.1（Cloud） | v5.2（Git-based） |
|--------|--------------|-------------------|
| 分发方式 | Cloud Registry (HTTP API) | Git 仓库 (clone) |
| 版本锁定 | CAS SHA-256 digest | Git commit SHA |
| 包索引 | Sparse Index (JSON lines) | index.yaml (YAML) |
| 搜索 | Cloud API / Meilisearch | 本地 index.yaml 搜索 |
| 发布 | HTTP POST /publish | git push + PR |
| 认证 | OAuth2 | Git SSH/HTTPS |
| 本地缓存 | CAS 缓存 (cacache) | Git repo cache |
| Rust crates | 7 个 (含 ap-registry, ap-client, ap-store) | 7 个 (含 ap-fetcher, ap-evolution, ap-cli-backend) |

**不受影响的文档章节**：
- §4 自建编排层（TaskGraph, IPC, ProcessManager, OrchestrationDSL）— 不变
- §5 Python Runtime（CaveAgent 集成）— 不变
- §6 Self-Evolution Engine — 不变
- §7 Agent 体系（Atomic/Composite/运行模式）— 不变
- §8 MCP 通信（Gateway, Deferred Loading, Token 优化）— 不变
- §10 技术约束（大部分不变，Rust 重构范围缩小）

### 12.10 未来演进路径

Git-based 分发是为 MVP 设计的初始方案。当规模增长时，可按以下路径演进：

```
v5.2 (Git-based) ─────→ v6.0 (Hybrid) ─────→ v7.0 (Cloud Registry)
                           │
                           ├─ Git source (保留)
                           └─ HTTP source (新增)

具体演进步骤：
1. sources.yaml 增加 type: "http" 支持
2. ap-fetcher trait 扩展 HttpFetcher 实现
3. HTTP source 对接 Cloud Registry API（复用 v5.1 设计的 /index, /search）
4. 本地 lockfile.json 格式不变，只是 source type 从 git 变为 http
5. Agent Package 格式完全不变
```

**演进兼容性保证**：

| 接口 | Git-based | Cloud Registry | 兼容 |
|------|-----------|---------------|------|
| lockfile.json | ✅ | ✅ | 格式不变 |
| config.toml | ✅ | ✅ | 格式不变 |
| agent-manifest.yaml | ✅ | ✅ | 格式不变 |
| MCP 协议 | ✅ | ✅ | 不变 |
| Agent Package | ✅ | ✅ | 不变 |
| Installer trait | GitFetcher | HttpFetcher | 同一 trait |

#### 12.6.6 Python → Rust 模块映射

| Python 模块 | Rust Crate | 映射说明 |
|------------|-----------|---------|
| `evolution/engine.py` | ap-evolution | EvolutionEngine → Evolver |
| `evolution/store.py` | ap-evolution | EvolutionStore → SQLite rusqlite |
| `evolution/analyzer.py` | ap-evolution | ExecutionAnalyzer |
| `evolution/evolver.py` | ap-evolution | SkillEvolver (FIX/DERIVED/CAPTURED) |
| `evolution/promotion.py` | ap-evolution | AgentPromoter |
| `local/cli/` | ap-cli + ap-cli-backend | clap CLI + CLIRouter + SessionStore |
| `local/installer.py` | ap-fetcher | GitInstaller → Installer trait |
| `gateway/` | ap-gateway | axum HTTP gateway + DeferredAgentRegistry |
| `config/` | ap-core | ConfigLoader + ModelConfig |

---
