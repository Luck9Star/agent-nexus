# Agent Nexus Design Docs

> Last updated: 2026-04-22

This directory contains the complete design documentation for Agent Nexus. All documents have been validated against the implemented codebase (Phases 1-6 complete).

## Document Index

| # | Document | Description | Status |
|---|----------|-------------|--------|
| 1 | [01-overview.md](01-overview.md) | Product positioning, competitive landscape, 4-layer core architecture | Implemented |
| 2 | [02-clawteam-integration.md](02-clawteam-integration.md) | Self-built orchestration layer: TaskGraph, IPC, ProcessManager, OrchestrationDSL | Implemented |
| 3 | [03-python-runtime.md](03-python-runtime.md) | Python Runtime execution layer: CaveAgent-based, SecurityChecker, isolation levels | Implemented |
| 4 | [04-self-evolution.md](04-self-evolution.md) | Self-Evolution Engine: Atomic Skill Evolution, Composite Orchestration Evolution, Agent Promotion | Implemented |
| 5 | [05-agent-system.md](05-agent-system.md) | Agent system: 11 Atomic Agents, 5 Composite Agents, 3 run modes, package structure | Implemented |
| 6 | [06-mcp-communication.md](06-mcp-communication.md) | MCP exposure, Gateway, communication matrix, Platform Router, SKILL.md spec | Partially implemented (Provider Adaptation pending) |
| 7 | [07-marketplace.md](07-marketplace.md) | Git-based Agent distribution, installation flow, quality gates, versioning | Partially implemented (SemVer parser, quality validation tool pending) |
| 8 | [08-constraints-decisions.md](08-constraints-decisions.md) | Technical constraints, design decisions, security model, Rust rewrite scope | Implemented |
| 9 | [09-implementation-plan.md](09-implementation-plan.md) | 7-phase implementation plan, risk matrix, timeline | Phases 1-6 complete, Phase 7 (Rust rewrite) pending |
| 10 | [10-cloud-local-architecture.md](10-cloud-local-architecture.md) | Git distribution model, local architecture, Python implementation, Rust migration path | Partially implemented (SemVer parser pending) |
| 11 | [11-agency-agents-integration.md](11-agency-agents-integration.md) | Agency Agents content-pack integration, expert profiles, dynamic Composite Agent orchestration | Proposal |
| A | [appendix.md](appendix.md) | OrchestrationDSL TOML schemas, Agent type comparison, model tier config, reference projects | Implemented |
| T | [testing.md](testing.md) | Test suite overview, coverage, conventions, run instructions | Live document |

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

1. **01-overview.md** — Understand what Agent Nexus is and why
2. **05-agent-system.md** — Learn the Agent type system
3. **02-clawteam-integration.md** — Understand orchestration internals
4. **03-python-runtime.md** — How Agents execute
5. **06-mcp-communication.md** — How Agents communicate externally
6. **04-self-evolution.md** — How the system evolves itself
7. **07-marketplace.md** — How Agents are distributed
8. **08-constraints-decisions.md** — Key constraints and trade-offs
9. **10-cloud-local-architecture.md** — Local architecture deep-dive
10. **09-implementation-plan.md** — Where we are and what's next
11. **11-agency-agents-integration.md** — How external expert profiles can power dynamic Composite Agents
12. **appendix.md** — Reference material
13. **testing.md** — Testing conventions and coverage
