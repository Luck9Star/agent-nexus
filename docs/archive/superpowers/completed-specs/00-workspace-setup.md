# Phase 0: Workspace + Cargo.toml Setup

> **Goal:** Create the Rust workspace structure with all 6 crates as skeleton libraries.

**Files:**
- Create: `Cargo.toml` (workspace root)
- Create: `crates/ap-core/Cargo.toml`
- Create: `crates/ap-core/src/lib.rs`
- Create: `crates/ap-runtime/Cargo.toml`
- Create: `crates/ap-runtime/src/lib.rs`
- Create: `crates/ap-gateway/Cargo.toml`
- Create: `crates/ap-gateway/src/lib.rs`
- Create: `crates/ap-fetcher/Cargo.toml`
- Create: `crates/ap-fetcher/src/lib.rs`
- Create: `crates/ap-evolution/Cargo.toml`
- Create: `crates/ap-evolution/src/lib.rs`
- Create: `crates/ap-cli/Cargo.toml`
- Create: `crates/ap-cli/src/main.rs`

---

- [ ] **Step 1: Create workspace root Cargo.toml**

```toml
[workspace]
members = [
    "crates/ap-core",
    "crates/ap-runtime",
    "crates/ap-gateway",
    "crates/ap-fetcher",
    "crates/ap-evolution",
    "crates/ap-cli",
]
resolver = "2"

[workspace.package]
version = "0.1.0"
edition = "2021"
license = "MIT"

[workspace.dependencies]
# Async runtime
tokio = { version = "1", features = ["full"] }

# Serialization
serde = { version = "1", features = ["derive"] }
serde_json = "1"
serde_yaml = "0.9"
toml = "0.8"

# Error handling
thiserror = "2"
anyhow = "1"

# Database
rusqlite = { version = "0.32", features = ["bundled"] }

# CLI
clap = { version = "4", features = ["derive"] }

# Git
git2 = "0.19"

# HTTP
axum = "0.8"

# MCP
rmcp = "0.1"

# Utilities
chrono = { version = "0.4", features = ["serde"] }
dashmap = "6"
semver = "1"
owo-colors = "4"
tracing = "0.1"
tracing-subscriber = { version = "0.3", features = ["env-filter"] }

# Testing
rstest = "0.22"
assert_cmd = "2"
tokio-test = "0.4"

# Internal crates
ap-core = { path = "crates/ap-core" }
ap-runtime = { path = "crates/ap-runtime" }
ap-gateway = { path = "crates/ap-gateway" }
ap-fetcher = { path = "crates/ap-fetcher" }
ap-evolution = { path = "crates/ap-evolution" }
```

- [ ] **Step 2: Create ap-core/Cargo.toml and src/lib.rs**

```toml
# crates/ap-core/Cargo.toml
[package]
name = "ap-core"
version.workspace = true
edition.workspace = true
license.workspace = true

[dependencies]
serde = { workspace = true }
serde_json = { workspace = true }
serde_yaml = { workspace = true }
toml = { workspace = true }
thiserror = { workspace = true }
chrono = { workspace = true }
rusqlite = { workspace = true }
tokio = { workspace = true }
tracing = { workspace = true }

[dev-dependencies]
rstest = { workspace = true }
tokio-test = { workspace = true }
```

```rust
// crates/ap-core/src/lib.rs
//! ap-core — Platform kernel: models, config, orchestration, router.

pub mod models;
```

- [ ] **Step 3: Create remaining crate skeletons**

```toml
# crates/ap-runtime/Cargo.toml
[package]
name = "ap-runtime"
version.workspace = true
edition.workspace = true
license.workspace = true

[dependencies]
ap-core = { workspace = true }
tokio = { workspace = true }
serde = { workspace = true }
serde_json = { workspace = true }
thiserror = { workspace = true }
tracing = { workspace = true }
dashmap = { workspace = true }

[dev-dependencies]
rstest = { workspace = true }
tokio-test = { workspace = true }
```

```rust
// crates/ap-runtime/src/lib.rs
//! ap-runtime — Python subprocess bridge: MCP client + raw IPC protocol.

pub mod ipc;
```

```toml
# crates/ap-gateway/Cargo.toml
[package]
name = "ap-gateway"
version.workspace = true
edition.workspace = true
license.workspace = true

[dependencies]
ap-core = { workspace = true }
ap-runtime = { workspace = true }
tokio = { workspace = true }
serde = { workspace = true }
serde_json = { workspace = true }
thiserror = { workspace = true }
axum = { workspace = true }
tracing = { workspace = true }

[dev-dependencies]
rstest = { workspace = true }
tokio-test = { workspace = true }
```

```rust
// crates/ap-gateway/src/lib.rs
//! ap-gateway — MCP Gateway: aggregate agent tools via MCP.
```

```toml
# crates/ap-fetcher/Cargo.toml
[package]
name = "ap-fetcher"
version.workspace = true
edition.workspace = true
license.workspace = true

[dependencies]
ap-core = { workspace = true }
tokio = { workspace = true }
serde = { workspace = true }
serde_json = { workspace = true }
serde_yaml = { workspace = true }
thiserror = { workspace = true }
git2 = { workspace = true }
semver = { workspace = true }
tracing = { workspace = true }

[dev-dependencies]
rstest = { workspace = true }
tokio-test = { workspace = true }
tempfile = "3"
```

```rust
// crates/ap-fetcher/src/lib.rs
//! ap-fetcher — Git-based agent distribution: installer, sources, lockfile, supervisor.
```

```toml
# crates/ap-evolution/Cargo.toml
[package]
name = "ap-evolution"
version.workspace = true
edition.workspace = true
license.workspace = true

[dependencies]
ap-core = { workspace = true }
ap-runtime = { workspace = true }
tokio = { workspace = true }
serde = { workspace = true }
serde_json = { workspace = true }
serde_yaml = { workspace = true }
thiserror = { workspace = true }
rusqlite = { workspace = true }
chrono = { workspace = true }
tracing = { workspace = true }

[dev-dependencies]
rstest = { workspace = true }
tokio-test = { workspace = true }
tempfile = "3"
```

```rust
// crates/ap-evolution/src/lib.rs
//! ap-evolution — Self-Evolution Engine: store, analyzer, evolver, promotion.
```

```toml
# crates/ap-cli/Cargo.toml
[package]
name = "ap-cli"
version.workspace = true
edition.workspace = true
license.workspace = true

[[bin]]
name = "agent-nexus"
path = "src/main.rs"

[dependencies]
ap-core = { workspace = true }
ap-runtime = { workspace = true }
ap-gateway = { workspace = true }
ap-fetcher = { workspace = true }
ap-evolution = { workspace = true }
tokio = { workspace = true }
clap = { workspace = true }
serde_json = { workspace = true }
anyhow = { workspace = true }
owo-colors = { workspace = true }
tracing = { workspace = true }
tracing-subscriber = { workspace = true }

[dev-dependencies]
assert_cmd = { workspace = true }
```

```rust
// crates/ap-cli/src/main.rs
fn main() {
    println!("agent-nexus CLI — Rust edition");
}
```

- [ ] **Step 4: Verify workspace compiles**

Run: `cargo build`
Expected: All 6 crates compile with no errors. Warnings about unused imports are OK at this stage.

- [ ] **Step 5: Commit**

```bash
git add Cargo.toml crates/
git commit -m "feat: scaffold Rust workspace with 6 crates (ap-core, ap-runtime, ap-gateway, ap-fetcher, ap-evolution, ap-cli)"
```
