# 迭代协议

本文件定义每一轮代码质量迭代的 SOP。所有迭代必须严格遵循。
需要加载与使用Skill： 
- /pua:p9
- /superpowers:using-superpowers 
- /code-review-expert
- 在下午 14 点至 18 点之间，需要使用/orch 2技能限制并发上限，其余时间可以尝试/orch 3或者4。

## 迭代方向

每一轮迭代的核心流程：**研究项目缺陷 → 审核发现的问题 → 迭代修复**。

### 缺陷来源
- 代码评审（使用 /code-review-expert）
- Bug 模式扫描（按本文件的优先级队列逐个扫描）
- 测试失败分析（回归测试暴露的问题）
- Pyright / 类型检查诊断
- 安全审计（import 绕过、eval/exec 绕过、sandbox escape）

### 每轮迭代目标
1. **发现问题**：通过扫描或评审识别一类缺陷
2. **评估问题**：分类严重程度（P0-P3），确认影响范围
3. **修复问题**：批量修复该类别的所有命中项（不是只修当前文件）
4. **验证修复**：全量测试 + 回归测试确认修复有效且无副作用

## 退出条件（满足 ALL 即停，不需要跑满 200 轮）

- [x] 全库 `grep -r "except.*:\s*pass" src/` 无静默异常（bare `pass` 无日志）
- [x] 全库 `grep -rn "TODO\|FIXME\|HACK" src/` 清零
- [x] POC 文档中每个 Phase 的模块实现对照无遗漏（对照 `docs/` 目录）
- [x] `uv run pytest tests/ -x -q` 全绿
- [x] 无 HIGH/CRITICAL Pyright 诊断
- [x] 全库无已知安全绕过向量（import / function / attribute / regex 四规则覆盖）

## 每轮迭代 SOP（严格遵守顺序）

### Phase 1: 缺陷研究（按模式逐个扫描，禁止随机审计）

1. 从优先级队列选一个未清的模式：
   - P0: 安全（import 绕过、eval/exec 绕过、sandbox escape、命令注入）
   - P1: 数据一致性（FK 约束、counter race、TOCTOU）
   - P2: 竞态条件（lock 缺失、async race、event-loop 生命周期）
   - P3: 静默失败（bare `except: pass`、吞错误）
   - P4: 类型安全（Pyright 诊断、Optional 解引用）
   - P5: 代码卫生（TODO/FIXME、dead code、unused imports、重复模式）
   - P6: 性能（N+1 查询、O(n²) 算法、无界内存）
   - P7: API 设计（私有方法暴露、不可测试的硬编码、缺少路径守卫）
2. 全库 grep 该模式的所有出现位置
3. 输出完整命中列表，标注已处理 / 待处理
4. 评估每个命中的严重程度和影响范围

### Phase 2: 批量修复

5. 一次修复 ALL 命中项（不是只修当前看到的那个文件）
6. 每个修复必须有对应回归测试
7. 如果修改了 Pydantic model 的 field 约束（如 min_length、ge、pattern），必须 grep 所有构造该 model 的代码，确认构造参数兼容

### Phase 3: 回归验证

8. `uv run pytest tests/ -x -q` 全绿才能继续
9. 如果修复引入新测试失败：
   - 先分析失败原因
   - 如果是修复本身有问题 → 回退修复，重新设计
   - 如果是调用方依赖旧行为 → 更新调用方 + 加回归测试
   - 禁止跳过失败继续下一轮

### Phase 4: 标记完成

10. 该模式标记为 "已清"，记录在下方进度区
11. 下一轮选择新的未清模式

## 反模式（禁止）

- 修一个模块的 silent except，不 grep 其他模块的同模式
- 给 Pydantic field 加约束（min_length、ge、pattern）但不检查所有构造该 model 的代码
- 一轮改 5 个不相关的 bug（应该一轮只做一种模式的全库修复）
- 修完不跑全量测试就 declare done
- 在测试文件里按迭代编号命名（test_iter88_xxx → 禁止）
- 重复修复同一模式而不标记已清（导致 iter80/81/83 TOCTOU 三轮重复）
- 随机审计不相关的代码（必须按优先级队列逐个模式扫）

## 测试规则

- 测试文件按模块命名：`test_{module_name}.py` 或 `test_{module_name}_models.py`
- 新增测试放在对应模块测试文件的 `# iter{N} regression` 注释块内
- Mock read/readline 返回值必须用 `b""`，防止无限循环
- IPython InteractiveShell 用 session-scoped fixture，不 per-test 创建
- 测试内存限制：单测文件执行不超过 30 秒，全量测试不超过 180 秒
- 测试时候需要注意 Python 进程内存使用量，内存占用过大可以认为有内存溢出风险。

## 进度追踪

在下方记录已清的模式。每清一个模式，更新日期和范围。

### 已清模式

| 模式 | 范围 | 清完日期 | 轮次 |
|------|------|---------|------|
| ImportRule 相对导入绕过 | security_rules.py | 2026-04-19 | iter88 |
| SecurityViolation field 约束 | runtime.py + security_checker.py | 2026-04-19 | iter88 |
| Dead-agent 工具名残留 | gateway.py | 2026-04-19 | iter88 |
| Deferred registry 空 list falsy | deferred_registry.py | 2026-04-19 | iter88 |
| LockfileEntry hex 大小写 | distribution.py | 2026-04-19 | iter88 |
| silent except 全库扫描 | src/ 全部（0 命中，含multiline清理路径） | 2026-04-19 | iter89 |
| TODO/FIXME/HACK 全库清理 | src/ 全部（0 命中） | 2026-04-19 | iter89 |
| Pyright HIGH 诊断清零 | src/ 全部（0 errors） | 2026-04-19 | iter89 |
| POC 文档对齐检查 | docs/09 vs src/，checkbox+项目结构同步 | 2026-04-19 | iter89 |
| 全量代码评审修复（18项） | P0:2 + P1:6 + P2:6 + P3:4 | 2026-04-19 | code-review |
| IPC exception handler _registered_tool_names 泄漏 | gateway.py _invoke | 2026-04-19 | iter90 |
| subprocess FD 泄漏（4处 communicate() 无 cleanup） | installer.py _run_git/_run_git_capture/_create_venv | 2026-04-19 | iter90 |
| P2 并发安全性分析（ipc/router/task_graph） | 确认 asyncio 单线程模型下安全，无需修复 | 2026-04-19 | iter90 |
| compaction char/token 单位混淆 | compaction.py tokens_after 改为 chars//4 估计值 | 2026-04-19 | iter91 |
| N+1 查询 store.py _row_to_record + get_ancestry | _batch_load_parents 批量加载，3个调用方+get_ancestry更新 | 2026-04-19 | iter91 |
| N+1 查询 task_graph.py get_ready/blocked + parallel_groups | SQL NOT EXISTS + _rows_to_tasks 批量加载 | 2026-04-19 | iter91 |
| Symlink 目录逃逸 + status 大小写敏感 | installer.py _create_venv abort + router/tool_adapter .lower() | 2026-04-19 | iter92 |
| 路径遍历 promotion.py + IPC 超时提取 | _AGENT_NAME_RE 校验 + DEFAULT_IPC_EXECUTE_TIMEOUT 常量 | 2026-04-19 | iter93 |
| P7 API 设计全量扫描（蓝军 2 agent） | 13 命中: P1:2 已修, P2:6(路径修1+其余可接受), P3:3 | 2026-04-19 | iter93 |
| 跨模块集成缺陷扫描（蓝军 2 agent） | 0 缺陷: 接口一致/性能可接受/错误传播完整 | 2026-04-19 | iter93 |
| 未覆盖错误分支回归（蓝军扫描 4 处） | executor race(4) + pip-install FD(1) + invalid SkillOrigin(3) = 8 tests | 2026-04-19 | iter94 |
| 资源生命周期 — missing close() | TaskGraph :memory: leak(P0) + EvolutionStore API + Router已安全 = 6 tests | 2026-04-19 | iter95 |
| installer install() rollback 回归 | venv失败清理(+) + validation失败前无copy(+) = 2 tests | 2026-04-19 | iter95b |
| supervisor auto_restart_dead 成功/失败路径 | start成功返回name(+) + build_command失败跳过(+) = 2 tests | 2026-04-19 | iter96 |
| 协程泄漏 — 未 close() 的跳过协程 | subtask.py _guarded coro.close() + regression test | 2026-04-19 | iter97 |
| P3 测试缺口全量验证 | 6项中5项已有覆盖，补1项: _parse_frontmatter YAML error | 2026-04-19 | iter97 |
| get_judgments_batch SQL LIMIT | store.py LIMIT ? + limit_per_skill 参数推入 SQL | 2026-04-19 | iter97 |
| get_parallel_groups O(n²) → O(V+E) | Kahn's algorithm with in-degree tracking | 2026-04-19 | iter97 |
| context_describer N+1 ancestry → get_ancestry_batch | store.py batch method + context_describer single-call | 2026-04-19 | iter97 |
| ipc.py 冗余 except (TimeoutError, Exception) | 简化为 except Exception | 2026-04-19 | iter97 |
| fail_task PENDING→FAILED 状态转换 | task_graph.py 允许 PENDING→FAILED + 3 regression tests | 2026-04-19 | iter98 |
| get_judgments_batch 全局 LIMIT 不均匀 | ROW_NUMBER() window function 替换全局 LIMIT + 2 tests | 2026-04-19 | iter98 |
| HookExecutor 空 allowlist 逻辑反转 | deny-by-default + 14 test updates + 2 regression tests | 2026-04-19 | iter98 |
| 错误传播全量扫描（蓝军 2 agent） | 2 P0 + 6 P1 + 10 P2 + 19 P3 发现 | 2026-04-19 | iter99 |
| _get_commit_sha 返回伪造 SHA | installer.py raise InstallationError + 2 test updates | 2026-04-19 | iter99 |
| _read_manifest 静默返回 {} | installer.py raise InstallationError + 1 test update | 2026-04-19 | iter99 |
| _file_lock FD 泄漏（flock 异常时） | lockfile.py try/except 关闭 FD + 2 regression tests | 2026-04-19 | iter99 |
| P7 API 前向兼容性（3处） | router mapping.get + health CAPTURED branch + TaskState.BLOCKED 移除 + 5 tests | 2026-04-19 | iter100 |
| 异常上下文丢失（7 P1 + 2 P2 sites） | WorkflowResult/HookExecution 新增 error_type 字段 + 9 sites 传 type(exc).__name__ + 4 tests | 2026-04-19 | iter101 |
| 外部 dict 裸下标 KeyError（3 sites） | tool_adapter name校验 + sources.py .get() validation + 6 tests | 2026-04-19 | iter102 |
| __del__ 安全网（TaskGraph + ProcessManager） | task_graph.py + process_manager.py 添加 __del__ 防止资源泄漏 | 2026-04-19 | iter103 |
| 覆盖率缺口 iter104: store.py + sources.py | get_ancestry_batch BFS(8 tests) + search_agents(7 tests) + 边界 | 2026-04-19 | iter104 |
| 覆盖率缺口 iter105: 5 模块 34 行 | deferred_registry schema验证 + router stale-lock + promotion atomic-write + task_graph corrupt row = 9 tests | 2026-04-19 | iter105 |
| 覆盖率缺口 iter106: 3 模块 10 行 → 2 miss | ipc non-dict + executor whitespace + context_describer no-ancestry + mock fix = 3 tests | 2026-04-20 | iter106 |
| 蓝队积压 iter106: 测试基础设施 | pytest markers(unit/integration/e2e) + 移位分类集成测试 + e2e conftest + cli.py env var覆盖 | 2026-04-20 | iter106 |
| 蓝军 adversarial audit iter107: P0 数据一致性 | evolve_skill IntegrityError rollback + _generate_manifest flat YAML + AgentManifest兼容 = 4 tests | 2026-04-20 | iter107 |
| 蓝军 adversarial audit iter107: P1 FK约束 | skill_judgments.skill_id FK + ghost-skill rejection + atomic rollback = 2 tests | 2026-04-20 | iter107 |
| 蓝军深度扫描 iter108: 资源泄漏 | store._conn() PRAGMA/BEGIN移入try/finally + proxy-based regression test × 2 = 3 agents 14 findings cross-verified | 2026-04-20 | iter108 |
| 蓝军积压清零 iter109: 错误处理韧性 | store._rows_to_records批量行恢复 + lockfile腐化ERROR日志 + router send_chat IPC异常包装 = 6 tests | 2026-04-20 | iter109 |
| 蓝军3-agent深度扫描 iter110: 5模式全量修复 | SQL LIMIT clamp(2) + ERROR响应类型检查(2) + asyncio.TimeoutError catch(2) + BaseException细化(1) + 计数器不变量修正(1) = 11 tests | 2026-04-20 | iter110 |
| iter110 changeset regression: 死代码+测试缺口 | 移除 process_manager/supervisor 死代码 TimeoutError catch(2) + get_judgments_batch clamp test(1) + test名修正 = net -2 tests | 2026-04-20 | iter111 |
| 蓝军3-agent退出条件验证 iter112 | get_snapshot N+1→_rows_to_tasks批量(1) + unused imports(3) + decode errors=replace(5) + ctypes/open安全回归(2) = 2491 tests | 2026-04-20 | iter112 |
| code-review-expert全量评审 iter113 | evolve_skill双重rollback→外部catch IntegrityError(P0) + executor _exec_done异常恢复(P1) + 回归test(1) = 2492 tests | 2026-04-20 | iter113 |
| composite workflow整体超时 iter114 | route_composite phase loop添加asyncio.wait_for(1200s) + 2 regression tests = 2496 tests | 2026-04-20 | iter114 |
| 蓝军sandbox escape type()+__bases__/__mro__ iter115 | type加入forbidden_functions + __bases__/__mro__加入forbidden_attributes + 3 regression tests = 2500 tests | 2026-04-20 | iter115 |
| run_with_retry MemoryError retry | subtask.py MemoryError加入immediate-rethrow + 1 regression test + route_locks asyncio安全确认 + lockfile save()设计确认 = 2500 tests | 2026-04-20 | iter116 |
| status=None默认success + evolve_skill sqlite3.Error | tool_adapter+router status=None→success=True + 2 stale tests更新 = 2507 tests | 2026-04-20 | iter117 |
| executescript→individual execute + rmtree best-effort | task_graph+store DDL transactional + installer uninstall onexc cleanup = 2515 tests | 2026-04-20 | iter118 |
| 蓝军3-agent深度扫描 iter119: P0腐化锁文件备份 | lockfile.py _corrupt_detected + _save备份到.json.corrupt + 4 regression tests = 2519 tests | 2026-04-20 | iter119 |
| 蓝军P1修复 iter120: silent UPDATE + is_active保留 | increment_counters rowcount warn + evolve_skill parent validation + save_skill_record is_active保留 = 2522 tests | 2026-04-20 | iter120 |

### 待清模式

- ~~P2: get_parallel_groups O(n²) → 可用 in-degree 优化~~ FIXED iter97: Kahn's algorithm with in-degree, O(V+E)
- ~~P2: _would_create_cycle 全表扫描~~ EVALUATED iter99: startup-only call, O(100) rows negligible, caching adds complexity with no benefit
- ~~P2: get_judgments_batch 无 SQL LIMIT → 当前数据量可接受~~ FIXED iter97: LIMIT pushed to SQL
- ~~P2: context_describer per-skill ancestry~~ FIXED iter97: get_ancestry_batch single-connection batch
