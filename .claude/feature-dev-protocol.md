# Feature Dev Protocol

> **When to use**: New Agent, new API, new module, new DSL, cross-module features.
> **Not for**: Bug fixes → use quality-protocol; structural refactoring → use refactor-protocol.
> **Core principle**: SKILL.md before code, interface before implementation, tests before filling.

---

## Protocol Architecture

```
Phase D1: Requirements Clarification
  → Output: SKILL.md + interface contract + impact scope

Phase D2: Interface Design
  → Output: Data models + public API + file structure

Phase D3: Test-Driven Implementation
  → Output: Tests passing + code complete

Phase D4: Integration Handoff
  → Output: Full suite green + docs updated → hand off to Quality Protocol
```

---

## Phase D1: Requirements Clarification

### SOP

1. **Write SKILL.md** — Project convention (see CLAUDE.md): all Agents must have SKILL.md before any code. Contents:
   - Feature summary (1 paragraph)
   - Input/output specification
   - Dependencies (modules and interfaces)
   - Error handling strategy
   - Performance expectations

2. **Impact Analysis** — Use `gitnexus_impact` to confirm change scope:
   - Which existing modules will be affected?
   - Do we need new files/directories?
   - Does it involve Pydantic model changes?
   - Does it involve database schema changes?

3. **Dependency Check** — Confirm external dependencies:
   - New pip package needed? → Add to pyproject.toml
   - New reference project? → Clone to reference path
   - New TOML template? → Add to templates/

4. **Scope Lock** — Output a checklist:

```markdown
## Feature Scope: [feature name]

### New Files
- [ ] src/agent_nexus/.../new_module.py
- [ ] tests/unit/test_new_module.py

### Modified Files (impact analysis confirmed)
- [ ] file_a.py — what changes
- [ ] file_b.py — what changes

### Unchanged Files (impact analysis excluded)
- file_c.py — unrelated

### External Dependencies
- None / or list them
```

### D1 Exit Conditions

- [ ] SKILL.md created (or in corresponding agents/ directory)
- [ ] gitnexus_impact run, blast radius recorded
- [ ] Scope checklist output, file list explicit

---

## Phase D2: Interface Design

### SOP

1. **Define Data Models** — Write Pydantic model / dataclass first:
   ```python
   # Model first, implementation later
   class NewFeatureInput(BaseModel):
       query: str = Field(min_length=1, max_length=500)
       ...

   class NewFeatureOutput(BaseModel):
       result: str
       ...
   ```

2. **Define Public API** — Method signatures + docstrings, no implementation:
   ```python
   async def new_feature(input: NewFeatureInput) -> NewFeatureOutput:
       """One-line summary.

       Args:
           input: ...

       Returns:
           ...

       Raises:
           FeatureError: ...
       """
       ...
   ```

3. **Define Error Types** — Error hierarchy for the new feature:
   ```python
   class FeatureError(Exception): ...
   class FeatureValidationError(FeatureError): ...
   class FeatureExecutionError(FeatureError): ...
   ```

4. **Interface Review** — Use `/code-review-expert` to review the interface design (not the implementation)

### D2 Exit Conditions

- [ ] Data models defined (Pydantic models with constraints)
- [ ] Public API signatures defined
- [ ] Error types defined
- [ ] Interface review passed

---

## Phase D3: Test-Driven Implementation

### SOP

1. **Write Test Stubs** — Based on interface definition, cover:
   - Happy path
   - Boundary conditions (empty input, extremely long input, special characters)
   - Error paths (dependency failure, timeout, abnormal input)
   - Integration points with existing modules

2. **Implement Code** — Fill in implementation to pass tests:
   - Implement one method/feature point at a time
   - Run corresponding test after each implementation to confirm it passes
   - Do not "opportunistically" refactor other code during implementation

3. **Implementation Constraints**:
   - New code uses only stdlib + existing project dependencies
   - No new global state
   - Every `async def` must have a corresponding `async` test
   - Every `close()`/`cleanup` must have a corresponding teardown test
   - Mock read/readline must return `b""` (prevents infinite loops)

4. **Post-Implementation Checks**:
   ```bash
   # Run new feature tests
   uv run pytest tests/unit/test_new_module.py -v
   # Run full suite to confirm no regression
   uv run pytest tests/ -x -q
   # Pyright check
   uv run pyright src/agent_nexus/.../new_module.py
   ```

### D3 Exit Conditions

- [ ] All new feature tests pass
- [ ] Full suite shows no regression
- [ ] Pyright zero new HIGH/CRITICAL
- [ ] No `TODO`/`FIXME`/`HACK` remaining

---

## Phase D4: Integration Handoff

### SOP

1. **Integration Point Verification** — Confirm new feature integrates with existing system:
   - Callee side: code calling the new feature passes parameters correctly?
   - Caller side: new feature calling old code behaves consistently?
   - Registration/Discovery: Agent correctly registered to Gateway/Router?

2. **Documentation Updates**:
   - `docs/09-implementation-plan.md` checkbox updated
   - `CLAUDE.md` project structure update if needed
   - SKILL.md consistent with implementation

3. **Changeset Verification**:
   ```bash
   gitnexus_detect_changes  # Confirm only expected scope changed
   gitnexus_impact          # Confirm d=1 dependencies updated
   ```

4. **Hand Off to Quality Protocol**:
   - Output a summary: what was changed, what was added, what was tested
   - Hand off to quality-protocol for Phase A exhaustive scan

### D4 Exit Conditions

- [ ] Full suite green
- [ ] Integration points verified
- [ ] Documentation updated
- [ ] gitnexus_detect_changes confirms correct scope
- [ ] Handoff summary output

---

## Anti-Patterns (Forbidden)

| # | Anti-Pattern | Why Forbidden |
|---|-------------|---------------|
| 1 | Writing code without SKILL.md | Project convention. SKILL.md is the requirement alignment artifact |
| 2 | Writing implementation before tests | Tests will unconsciously verify implementation rather than requirements |
| 3 | "Opportunistically" refactoring other code during implementation | Feature and Refactor are different protocols; mixing them blurs change scope |
| 4 | Adding global state/singletons | Increases test difficulty, introduces hidden dependencies |
| 5 | Editing code without running gitnexus_impact | Unknown blast radius = unknown correctness |
| 6 | Changing Pydantic models without checking constructors | v1 lesson: field constraint changes break callers |

---

## Relationship to Other Protocols

```
Feature Dev Protocol (this file)
    |
    +-- D1-D4 complete
    |   +--> Quality Protocol (quality-protocol.md)
    |         Phase A scan new code + Phase B adversarial audit
    |
    +-- D2 discovers refactoring needed
    |   +--> Refactor Protocol (refactor-protocol.md)
    |
    +-- D3 finds bugs during implementation
        +--> Fix directly (small bug) / Escalate to Quality Protocol (large bug)
```

---

## Test Rules

- Test files named by module: `test_{module_name}.py`
- Mock read/readline must use `b""`
- IPython InteractiveShell: session-scoped fixtures only
- Single test file execution ≤ 30s, full suite ≤ 180s
- Agent tests prefer AsyncMock for IPC simulation, no real subprocess launches
