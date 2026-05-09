# Agent Nexus Design Docs

> Last updated: 2026-05-08

This directory contains the complete design documentation for Agent Nexus. All documents have been validated against the implemented codebase (Rust Rewrite Phase 0-10 complete).

## Document Index

| # | Document | Description | Status |
|---|----------|-------------|--------|
| 1 | [01-overview.md](01-overview.md) | Product positioning, competitive landscape, 4-layer core architecture | ✅ Implemented |
| 2 | [02-clawteam-integration.md](02-clawteam-integration.md) | Self-built orchestration layer: TaskGraph, IPC, ProcessManager, OrchestrationDSL | ✅ Implemented |
| 3 | [03-python-runtime.md](03-python-runtime.md) | Python Runtime execution layer: CaveAgent-based, SecurityChecker, isolation levels | ✅ Implemented |
| 4 | [04-self-evolution.md](04-self-evolution.md) | Self-Evolution Engine: Atomic Skill Evolution, Composite Orchestration Evolution, Agent Promotion | ✅ Implemented |
| 5 | [05-agent-system.md](05-agent-system.md) | Agent system: 11 Atomic Agents, 5 Composite Agents, 3 run modes, package structure | ⚠️ Design Goals |
| 6 | [06-mcp-communication.md](06-mcp-communication.md) | MCP exposure, Gateway, communication matrix, Platform Router, SKILL.md spec | ⚠️ Partially implemented |
| 7 | [07-marketplace.md](07-marketplace.md) | Git-based Agent distribution, installation flow, quality gates, versioning | ⚠️ Partially implemented |
| 8 | [08-constraints-decisions.md](08-constraints-decisions.md) | Technical constraints, design decisions, security model, Rust rewrite scope | ✅ Implemented |
| 9 | [09-implementation-plan.md](09-implementation-plan.md) | 7-phase implementation plan, risk matrix, timeline | ✅ Phase 0-10 Complete, Phase 11 TODO |
| 10 | [10-cloud-local-architecture.md](10-cloud-local-architecture.md) | Git distribution model, local architecture, Python implementation, Rust migration path | ✅ Implemented |
| 12 | [12-atomic-agents-improvement-plan.md](12-atomic-agents-improvement-plan.md) | Atomic Agents 借鉴改进方案 P0-P4（Schema/Hook/Token/Context/Reflect） | ✅ Implemented |
| A | [appendix.md](appendix.md) | OrchestrationDSL TOML schemas, Agent type comparison, model tier config, reference projects | ✅ Implemented |
| T | [testing.md](testing.md) | Test suite overview, coverage, conventions, run instructions | ✅ Live document |
| C | [capability-testing.md](capability-testing.md) | Contract-driven capability tests: 80 tests, 3 agent tiers × 2 modes × 2 validation levels | ⚠️ ~40% Implemented |
|   | [configuration.md](configuration.md) | Full config schema, environment variables, priority chain, migration | ✅ Live document |
|   | [cli.md](cli.md) | Complete CLI reference — 24 commands with usage examples | ✅ Live document |
|   | [quick-start.md](quick-start.md) | 5-minute setup guide: install → init → config → run | ✅ Live document |

## Architecture at a Glance

```
┌─────────────────────────────────────────────────┐
│  MCP Exposure Layer                             │  FastMCP Server + Gateway
├─────────────────────────────────────────────────┤
│  Orchestration Layer (self-built)               │  TaskGraph + IPC + ProcessManager
├─────────────────────────────────────────────────┤
│  Python Runtime Layer                           │  CaveAgent-based, SecurityChecker
├─────────────────────────────────────────────────┤
│  Self-Evolution Engine                          │  Skill Evolution + Agent Promotion
└─────────────────────────────────────────────────┘
```

## Reading Order

For new contributors, the recommended reading order is:

1. **quick-start.md** — 5-minute setup and first agent run
2. **01-overview.md** — Understand what Agent Nexus is and why
3. **configuration.md** — How config works (env vars, providers, priority chain)
4. **cli.md** — All CLI commands and how to use them
5. **02-clawteam-integration.md** — Understand orchestration internals
6. **03-python-runtime.md** — How Agents execute
7. **06-mcp-communication.md** — How Agents communicate externally
8. **04-self-evolution.md** — How the system evolves itself
9. **07-marketplace.md** — How Agents are distributed
10. **08-constraints-decisions.md** — Key constraints and trade-offs
11. **10-cloud-local-architecture.md** — Local architecture deep-dive
12. **09-implementation-plan.md** — Where we are and what's next
13. **appendix.md** — Reference material
14. **testing.md** — Testing conventions and coverage
15. **capability-testing.md** — Agent capability contract testing framework

## Archived Documents

Documents that have been completed or superseded are moved to [archive/](archive/):

- [archive/superpowers/completed-specs/](archive/superpowers/completed-specs/) — Completed Rust Rewrite specs and implementation plans
- [archive/superpowers/deprecated/](archive/superpowers/deprecated/) — Deprecated documents
- [archive/superpowers/superseded-designs/](archive/superpowers/superseded-designs/) — Superseded designs (e.g., LiteLLM plan never implemented)
