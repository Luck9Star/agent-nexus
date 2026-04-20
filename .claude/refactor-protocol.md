# Refactor Protocol — 重构协议

> **适用场景**: Rust 重写、模块拆分、抽象提取、API 接口变更、性能优化（改实现不改行为）。
> **不适用**: 新功能 → 用 feature-dev-protocol；bug 修复 → 用 quality-protocol。
> **核心原则**: 行为不变 = 测试不动。小步重构，每步可验证。

---

## 协议架构

```
Phase R1: 基线建立 (Baseline)
  → 输出: 现有测试全绿 + 不变量清单

Phase R2: 接口契约 (Contract)
  → 输出: 输入/输出契约定义

Phase R3: 小步重构 (Migrate)
  → 输出: 逐步替换，每步测试绿

Phase R4: 契约验证 (Verify)
  → 输出: 新实现通过原测试 + 接口兼容
```

---

## Phase R1: 基线建立

### SOP

1. **记录测试基线**:
   ```bash
   uv run pytest tests/ -x -q  # 必须全绿
   # 记录测试数量
   ```

2. **识别不变量** — 列出重构范围内必须保持的行为：
   ```markdown
   ## Invariants: [refactor scope]

   - [ ] `add_task()` 返回 task_id，status=READY 或 BLOCKED
   - [ ] `get_ready()` 只返回 READY 状态且无阻塞依赖的 task
   - [ ] `close()` 后所有 SQLite 连接已释放
   - [ ] lockfile.json 格式与现有一致
   ```

3. **影响分析**:
   ```bash
   gitnexus_impact --target [refactor_target] --direction upstream
   ```
   - d=1 的调用方有哪些？
   - d=2 的间接依赖有哪些？
   - 跨语言接口（Python↔Rust）在哪里？

4. **识别风险点**:
   - 是否涉及 Pydantic model 序列化格式？→ 不能改 JSON 结构
   - 是否涉及 SQLite schema？→ 需要 migration
   - 是否涉及 IPC 协议？→ 不能改消息格式
   - 是否涉及 lockfile.json？→ 需要 Python/Rust 兼容性测试

### R1 退出条件

- [ ] 全量测试绿
- [ ] 不变量清单已列出
- [ ] gitnexus_impact 已跑，d=1 调用方已识别
- [ ] 风险点已标注

---

## Phase R2: 接口契约

### SOP

1. **定义接口契约** — 明确输入/输出不变的部分：

   对于 Python→Python 重构（模块拆分/抽象提取）：
   ```markdown
   ## Contract: [module name]

   ### Public API (不可改)
   - `def foo(x: str) -> int` — 不变
   - `async def bar(data: dict) -> list[str]` — 不变

   ### Internal API (可改)
   - `_helper()` → 可重命名/删除/提取
   ```

   对于 Python→Rust 重写：
   ```markdown
   ## Contract: [crate name]

   ### 文件格式 (必须兼容)
   - lockfile.json: 读写格式与 Python 版一致
   - config.toml: 解析结果与 Python 版一致
   - TOML DAG templates: 解析行为与 Python 版一致

   ### IPC 消息格式 (必须兼容)
   - PlatformToAgent JSON schema
   - AgentToPlatform JSON schema
   - 错误响应格式

   ### 行为契约 (必须通过原有测试)
   - 测试文件 test_xxx.py 全部通过
   - 边界行为：空输入、超时、进程崩溃
   ```

2. **编写契约测试**（Python→Rust 场景）:
   ```python
   # 测试 Python 和 Rust 读写同一 lockfile.json
   def test_lockfile_compatibility():
       py_data = python_write_lockfile(...)
       rs_data = rust_read_lockfile(...)
       assert py_data == rs_data
   ```

3. **契约评审** — 用 code-review-expert 审契约定义

### R2 退出条件

- [ ] 契约定义已输出（Public API / 文件格式 / IPC 消息格式）
- [ ] 契约测试已编写（跨语言场景）
- [ ] 契约评审通过

---

## Phase R3: 小步重构

### SOP

1. **每次只改一个模块** — 不并行改多个模块：
   ```
   Step 1: 改 module_a → 测试 → 提交
   Step 2: 改 module_b → 测试 → 提交
   Step 3: 改 module_c → 测试 → 提交
   ```

2. **每步验证**:
   ```bash
   # 改完后立即验证
   uv run pytest tests/unit/test_changed_module.py -v
   uv run pytest tests/ -x -q
   ```

3. **测试策略**:
   - **Python→Python**: 原有测试不动，新实现必须通过原测试
   - **Python→Rust**: 先跑 Python 版测试记录预期，Rust 版必须产出相同结果
   - **API 变更**: 先加兼容层（旧 API → 新 API），下个版本再删兼容层

4. **回退策略**: 每步是一个独立 commit，失败时 `git revert` 即可回退

5. **禁止**:
   - 不同时改实现和测试（改了实现，测试应该不动就能过）
   - 不同时改多个模块（串行，不并行）
   - 不在重构中"顺手"加新功能（Feature 和 Refactor 分开）

### R3 退出条件

- [ ] 所有步骤完成
- [ ] 全量测试绿
- [ ] 每个 step 是独立 commit
- [ ] 不变量清单逐项验证通过

---

## Phase R4: 契约验证

### SOP

1. **不变量逐项验证**:
   ```markdown
   ## Invariants Check: [refactor scope]

   - [x] `add_task()` 返回 task_id，status=READY 或 BLOCKED → 测试通过
   - [x] `get_ready()` 只返回 READY 状态 → 测试通过
   - [x] `close()` 后连接释放 → 测试通过
   ```

2. **d=1 调用方回归** — 对 gitnexus_impact d=1 的每个调用方跑测试

3. **跨语言兼容性**（Python→Rust 场景）:
   - Python 写文件，Rust 读 → 一致
   - Rust 写文件，Python 读 → 一致
   - 同一输入，两边输出 → 一致

4. **变更集验证**:
   ```bash
   gitnexus_detect_changes  # 确认只改了预期范围
   ```

5. **交接给 Quality Protocol**:
   - 输出 summary：重构了什么、接口是否变了、兼容性是否验证

### R4 退出条件

- [ ] 全量测试绿
- [ ] 不变量全部通过
- [ ] d=1 调用方测试通过
- [ ] 跨语言兼容性测试通过（如有）
- [ ] gitnexus_detect_changes 范围正确
- [ ] 交接 summary 已输出

---

## Rust 重写专项约束

Phase 7 Rust 重写是一个特殊的重构场景，额外约束：

### 逐 crate 替换顺序

```
ap-core     → 核心类型（纯数据，无 I/O）
ap-fetcher  → Git 包获取（替换 installer.py）
ap-runtime  → Agent Supervisor（替换 process_manager.py + supervisor.py）
ap-gateway  → MCP 网关（替换 gateway.py + deferred_registry.py）
ap-cli      → CLI（替换 cli.py）
```

### 替换策略

1. **ap-core 先行** — 不涉及运行时，只替换类型定义
2. **ap-fetcher → ap-runtime → ap-gateway → ap-cli** — 有依赖关系，必须按序
3. **每个 crate 替换后，Python 版保留但标记 deprecated** — 共存期

### 兼容性测试清单

```markdown
- [ ] lockfile.json: Python write → Rust read
- [ ] lockfile.json: Rust write → Python read
- [ ] config.toml: Python parse == Rust parse
- [ ] IPC messages: Python→Agent == Rust→Agent
- [ ] CLI output: Python format == Rust format
```

### 不动的东西

- Agent Runtime（Python）— 永远是 Python
- IPC 消息格式 — 不改
- SKILL.md 格式 — 不改
- TOML DAG template 格式 — 不改

---

## 反模式（禁止）

| # | 反模式 | 为什么禁止 |
|---|--------|-----------|
| 1 | 改实现的同时改测试 | 失去基线，无法验证行为不变 |
| 2 | 同时重构多个模块 | 一个失败影响全部，回退困难 |
| 3 | 重构中"顺手"加功能 | Feature 和 Refactor 混做 = 变更范围失控 |
| 4 | 不做跨语言兼容性测试 | Python/Rust 共存期，格式不一致 = 数据丢失 |
| 5 | 删除旧实现前没跑兼容性测试 | 两边必须能读写同一份数据 |
| 6 | 改 IPC 消息格式 | MCP 协议边界是语言边界，不能动 |

---

## 与其他协议的关系

```
Refactor Protocol (本文件)
    │
    ├── R4 完成后
    │   └──→ Quality Protocol (quality-protocol.md)
    │
    ├── R1 发现需要新功能
    │   └──→ Feature Dev Protocol (feature-dev-protocol.md)
    │
    └── R3 过程中发现 bug
        └──→ 记录，重构完成后再修（不中途切任务）
```
