//! Performance benchmarks for ap-core critical paths.
//!
//! Uses std::time::Instant only — no external benchmark dependencies.
//! Targets include 5x headroom to avoid flaky failures on CI.

use std::time::Instant;

use ap_core::config::{ConfigLoader, ModelConfigManager};
use ap_core::models::common::utc_now;
use ap_core::models::config::{ModelConfig, PlatformConfig, ProviderApiType, ProviderConfig};
use ap_core::models::ipc::{
    AgentToPlatform, AgentToPlatformType, PlatformToAgent, PlatformToAgentType,
};
use ap_core::models::task::{TaskItem, TaskState};
use ap_core::orchestration::task_graph::TaskGraph;

// ── Helpers ──────────────────────────────────────────────────────────────

fn sample_toml() -> &'static str {
    r#"
[runtime]
python_path = "python3.12"

[models]
default = "openai:gpt-4o"

[models.providers.openai]
base_url = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"
api = "openai-compatible"

[models.providers.anthropic]
base_url = "https://api.anthropic.com"
api_key_env = "ANTHROPIC_API_KEY"
api = "openai-compatible"

[models.providers.deepseek]
base_url = "https://api.deepseek.com/v1"
api_key_env = "DEEPSEEK_API_KEY"
api = "openai-compatible"
"#
}

fn sample_platform_to_agent() -> PlatformToAgent {
    PlatformToAgent {
        msg_type: PlatformToAgentType::Chat,
        content: "Hello, please review this code change.".to_string(),
        task_id: Some("task-001".to_string()),
        conversation_id: Some("conv-abc123".to_string()),
        ref_id: None,
        summary: None,
    }
}

fn sample_agent_to_platform() -> AgentToPlatform {
    AgentToPlatform {
        msg_type: AgentToPlatformType::Result,
        content: String::new(),
        task_id: Some("task-001".to_string()),
        message: None,
        progress_pct: None,
        error: None,
        status: Some("completed".to_string()),
        output: Some(serde_json::json!({
            "review": "LGTM with minor suggestions",
            "files_checked": 3,
            "issues_found": []
        })),
    }
}

fn simple_task(id: &str, agent: &str, blocked_by: &[&str]) -> TaskItem {
    TaskItem {
        id: id.to_string(),
        description: format!("task {id}"),
        agent: agent.to_string(),
        blocked_by: blocked_by.iter().map(|s| s.to_string()).collect(),
        vars: serde_json::Value::Null,
        state: TaskState::Pending,
        result: None,
        created_at: utc_now(),
        updated_at: utc_now(),
    }
}

fn multi_provider_config() -> PlatformConfig {
    let mut providers = std::collections::HashMap::new();
    providers.insert(
        "openai".to_string(),
        ProviderConfig {
            base_url: "https://api.openai.com/v1".to_string(),
            api_key_env: "OPENAI_API_KEY".to_string(),
            api: ProviderApiType::OpenaiCompatible,
        },
    );
    providers.insert(
        "anthropic".to_string(),
        ProviderConfig {
            base_url: "https://api.anthropic.com".to_string(),
            api_key_env: "ANTHROPIC_API_KEY".to_string(),
            api: ProviderApiType::AnthropicMessages,
        },
    );
    providers.insert(
        "ollama".to_string(),
        ProviderConfig {
            base_url: "http://localhost:11434/v1".to_string(),
            api_key_env: String::new(),
            api: ProviderApiType::Ollama,
        },
    );
    providers.insert(
        "deepseek".to_string(),
        ProviderConfig {
            base_url: "https://api.deepseek.com/v1".to_string(),
            api_key_env: "DEEPSEEK_API_KEY".to_string(),
            api: ProviderApiType::OpenaiCompatible,
        },
    );
    PlatformConfig {
        models: ModelConfig {
            default: "openai:gpt-4o".to_string(),
            providers,
        },
        runtime: Default::default(),
    }
}

// ══════════════════════════════════════════════════════════════════════════
// Bench 1: Config loading
// ══════════════════════════════════════════════════════════════════════════

#[test]
fn bench_config_loading() {
    let toml_str = sample_toml();
    let iterations = 1000u64;

    // Warm up
    for _ in 0..10 {
        let _ = ConfigLoader::load_from_str(toml_str).unwrap();
    }

    let start = Instant::now();
    for _ in 0..iterations {
        let _config = ConfigLoader::load_from_str(toml_str).unwrap();
    }
    let elapsed = start.elapsed();
    let avg = elapsed / iterations as u32;

    println!("[bench_config_loading] {} iterations in {:?}", iterations, elapsed);
    println!("[bench_config_loading] avg per load: {:?}", avg);

    // Target: < 0.5ms = 500us. With 5x headroom: 2500us.
    assert!(
        avg.as_micros() < 2500,
        "Config load too slow: {:?} (target < 500us, headroom < 2500us)",
        avg
    );
}

// ══════════════════════════════════════════════════════════════════════════
// Bench 2: TaskGraph add_task + get_ready_tasks
// ══════════════════════════════════════════════════════════════════════════

#[test]
fn bench_task_graph_add_task() {
    let tg = TaskGraph::new_in_memory().unwrap();
    let num_tasks = 100u64;

    let start = Instant::now();
    // Build a chain: t-0 has no deps, t-N depends on t-(N-1)
    tg.add_task(&simple_task("t-0", "agent-a", &[])).unwrap();
    for i in 1..num_tasks {
        let dep = format!("t-{}", i - 1);
        tg.add_task(&simple_task(&format!("t-{i}"), "agent-a", &[&dep])).unwrap();
    }
    let elapsed = start.elapsed();
    let avg = elapsed / num_tasks as u32;

    println!("[bench_task_graph_add_task] {} tasks in {:?}", num_tasks, elapsed);
    println!("[bench_task_graph_add_task] avg per add_task: {:?}", avg);

    // Target: < 1ms per add_task (with 100 tasks). With 5x headroom: 5ms.
    assert!(
        avg.as_micros() < 5000,
        "add_task avg too slow: {:?} (target < 1ms, headroom < 5ms)",
        avg
    );
}

#[test]
fn bench_task_graph_get_ready_tasks() {
    let tg = TaskGraph::new_in_memory().unwrap();

    // Insert 100 tasks: 50 root tasks (no deps) + 50 dependent tasks
    for i in 0..50 {
        tg.add_task(&simple_task(&format!("root-{i}"), "agent-a", &[])).unwrap();
    }
    for i in 0..50 {
        let dep = format!("root-{i}");
        tg.add_task(&simple_task(&format!("child-{i}"), "agent-b", &[&dep])).unwrap();
    }

    let iterations = 1000u64;
    let start = Instant::now();
    for _ in 0..iterations {
        let _ready = tg.get_ready_tasks().unwrap();
    }
    let elapsed = start.elapsed();
    let avg = elapsed / iterations as u32;

    println!(
        "[bench_task_graph_get_ready_tasks] {} calls over 100 tasks in {:?}",
        iterations, elapsed
    );
    println!("[bench_task_graph_get_ready_tasks] avg per call: {:?}", avg);

    // Target: < 1ms per get_ready_tasks call. With 5x headroom: 5ms.
    assert!(
        avg.as_micros() < 5000,
        "get_ready_tasks avg too slow: {:?} (target < 1ms, headroom < 5ms)",
        avg
    );
}

// ══════════════════════════════════════════════════════════════════════════
// Bench 3: IPC serialization
// ══════════════════════════════════════════════════════════════════════════

#[test]
fn bench_ipc_serialize_platform_to_agent() {
    let msg = sample_platform_to_agent();
    let iterations = 10000u64;

    // Warm up
    for _ in 0..100 {
        let _ = serde_json::to_string(&msg).unwrap();
    }

    let start = Instant::now();
    for _ in 0..iterations {
        let _json = serde_json::to_string(&msg).unwrap();
    }
    let elapsed = start.elapsed();
    let avg = elapsed / iterations as u32;

    println!(
        "[bench_ipc_serialize_platform_to_agent] {} iterations in {:?}",
        iterations, elapsed
    );
    println!("[bench_ipc_serialize_platform_to_agent] avg: {:?}", avg);

    // Target: < 50us. With 5x headroom: 250us.
    assert!(
        avg.as_micros() < 250,
        "IPC serialize too slow: {:?} (target < 50us, headroom < 250us)",
        avg
    );
}

#[test]
fn bench_ipc_deserialize_platform_to_agent() {
    let msg = sample_platform_to_agent();
    let json = serde_json::to_string(&msg).unwrap();
    let iterations = 10000u64;

    // Warm up
    for _ in 0..100 {
        let _: PlatformToAgent = serde_json::from_str(&json).unwrap();
    }

    let start = Instant::now();
    for _ in 0..iterations {
        let _parsed: PlatformToAgent = serde_json::from_str(&json).unwrap();
    }
    let elapsed = start.elapsed();
    let avg = elapsed / iterations as u32;

    println!(
        "[bench_ipc_deserialize_platform_to_agent] {} iterations in {:?}",
        iterations, elapsed
    );
    println!("[bench_ipc_deserialize_platform_to_agent] avg: {:?}", avg);

    // Target: < 50us. With 5x headroom: 250us.
    assert!(
        avg.as_micros() < 250,
        "IPC deserialize too slow: {:?} (target < 50us, headroom < 250us)",
        avg
    );
}

#[test]
fn bench_ipc_serialize_agent_to_platform() {
    let msg = sample_agent_to_platform();
    let iterations = 10000u64;

    let start = Instant::now();
    for _ in 0..iterations {
        let _json = serde_json::to_string(&msg).unwrap();
    }
    let elapsed = start.elapsed();
    let avg = elapsed / iterations as u32;

    println!(
        "[bench_ipc_serialize_agent_to_platform] {} iterations in {:?}",
        iterations, elapsed
    );
    println!("[bench_ipc_serialize_agent_to_platform] avg: {:?}", avg);

    // Target: < 50us. With 5x headroom: 250us.
    assert!(
        avg.as_micros() < 250,
        "IPC serialize (agent->platform) too slow: {:?} (target < 50us, headroom < 250us)",
        avg
    );
}

#[test]
fn bench_ipc_deserialize_agent_to_platform() {
    let msg = sample_agent_to_platform();
    let json = serde_json::to_string(&msg).unwrap();
    let iterations = 10000u64;

    let start = Instant::now();
    for _ in 0..iterations {
        let _parsed: AgentToPlatform = serde_json::from_str(&json).unwrap();
    }
    let elapsed = start.elapsed();
    let avg = elapsed / iterations as u32;

    println!(
        "[bench_ipc_deserialize_agent_to_platform] {} iterations in {:?}",
        iterations, elapsed
    );
    println!("[bench_ipc_deserialize_agent_to_platform] avg: {:?}", avg);

    // Target: < 50us. With 5x headroom: 250us.
    assert!(
        avg.as_micros() < 250,
        "IPC deserialize (agent->platform) too slow: {:?} (target < 50us, headroom < 250us)",
        avg
    );
}

// ══════════════════════════════════════════════════════════════════════════
// Bench 4: Model config resolution
// ══════════════════════════════════════════════════════════════════════════

#[test]
fn bench_model_config_resolve() {
    let config = multi_provider_config();
    let mgr = ModelConfigManager::new(config);
    let iterations = 10000u64;

    // Warm up
    for _ in 0..100 {
        let _ = mgr.resolve("openai:gpt-4o").unwrap();
    }

    let start = Instant::now();
    for i in 0..iterations {
        let model_str = match i % 4 {
            0 => "openai:gpt-4o",
            1 => "anthropic:claude-sonnet-4-20250514",
            2 => "ollama:qwen2.5-coder:7b",
            _ => "deepseek:deepseek-chat",
        };
        let _resolved = mgr.resolve(model_str).unwrap();
    }
    let elapsed = start.elapsed();
    let avg = elapsed / iterations as u32;

    println!(
        "[bench_model_config_resolve] {} iterations in {:?}",
        iterations, elapsed
    );
    println!("[bench_model_config_resolve] avg per resolve: {:?}", avg);

    // Target: < 100us. With 5x headroom: 500us.
    assert!(
        avg.as_micros() < 500,
        "Model config resolve too slow: {:?} (target < 100us, headroom < 500us)",
        avg
    );
}

#[test]
fn bench_model_config_resolve_fallback() {
    // Test resolve with provider not in config (triggers fallback path).
    // Use a config with NO providers at all so the fallback chain reaches
    // the hardcoded "openai:gpt-4o" string but still fails because "openai"
    // is not in our empty providers map.
    let config = PlatformConfig::default();
    let mgr = ModelConfigManager::new(config);
    let iterations = 1000u64;

    let start = Instant::now();
    for _ in 0..iterations {
        // "unknown-provider" is not in config, triggers the full fallback chain
        // (AGENT_MODEL -> DEFAULT_MODEL -> hardcoded "openai:gpt-4o").
        // With default PlatformConfig (empty providers), all fallbacks fail too.
        let result = mgr.resolve("unknown-provider:some-model");
        // Result may succeed or fail depending on whether the hardcoded fallback
        // provider happens to be in the config. We just want to benchmark the path.
        let _ = result;
    }
    let elapsed = start.elapsed();
    let avg = elapsed / iterations as u32;

    println!(
        "[bench_model_config_resolve_fallback] {} iterations in {:?}",
        iterations, elapsed
    );
    println!("[bench_model_config_resolve_fallback] avg: {:?}", avg);

    // Target: < 100us. With 5x headroom: 500us.
    assert!(
        avg.as_micros() < 500,
        "Model config resolve (fallback) too slow: {:?} (target < 100us, headroom < 500us)",
        avg
    );
}
