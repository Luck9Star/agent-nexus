//! Backward compatibility tests — verify Rust can read Python-written artifacts.

use ap_core::orchestration::dsl::OrchestrationDsl;
use ap_core::models::ipc::PlatformToAgent;
use ap_core::models::ipc::PlatformToAgentType;
use ap_core::models::agent::AgentType;
use ap_core::models::distribution::Lockfile;
use ap_core::models::config::PlatformConfig;

// ── Test 1: Read Python lockfile ──────────────────────────────────────

#[test]
fn read_python_lockfile_json() {
    let json = include_str!("compat/fixtures/lockfile_python.json");
    let parsed: serde_json::Value = serde_json::from_str(json).unwrap();
    assert_eq!(parsed["version"], 1);
    assert!(parsed["agents"]["code-reviewer"].is_object());
    assert_eq!(parsed["agents"]["code-reviewer"]["version"], "1.2.0");
}

#[test]
fn read_python_lockfile_typed() {
    let json = include_str!("compat/fixtures/lockfile_python.json");
    let lockfile: Lockfile = serde_json::from_str(json).unwrap();
    assert_eq!(lockfile.version, 1);
    assert!(lockfile.agents.contains_key("code-reviewer"));
    let cr = &lockfile.agents["code-reviewer"];
    assert_eq!(cr.version, "1.2.0");
    assert_eq!(cr.agent_type, AgentType::Atomic);
    assert!(cr.validate_commit_sha().is_ok());
}

#[test]
fn read_python_lockfile_composite_agent() {
    let json = include_str!("compat/fixtures/lockfile_python.json");
    let lockfile: Lockfile = serde_json::from_str(json).unwrap();
    let fd = &lockfile.agents["feature-delivery"];
    assert_eq!(fd.version, "0.5.0");
    assert_eq!(fd.agent_type, AgentType::Composite);
    assert_eq!(fd.dependencies, vec!["code-reviewer"]);
}

// ── Test 2: Read Python config ───────────────────────────────────────

#[test]
fn read_python_config_raw_toml() {
    let toml_str = include_str!("compat/fixtures/config_python.toml");
    let config: toml::Value = toml::from_str(toml_str).unwrap();
    assert_eq!(config["models"]["default"].as_str(), Some("openai:gpt-4o"));
    assert!(config["models"]["providers"]["deepseek"].is_table());
    assert!(config["models"]["providers"]["ollama"].is_table());
}

#[test]
fn read_python_config_typed() {
    let toml_str = include_str!("compat/fixtures/config_python.toml");
    let config: PlatformConfig = toml::from_str(toml_str).unwrap();
    assert_eq!(config.models.default, "openai:gpt-4o");
    assert!(config.models.providers.contains_key("deepseek"));
    assert!(config.models.providers.contains_key("ollama"));
    // Verify deepseek provider details
    let ds = &config.models.providers["deepseek"];
    assert_eq!(ds.base_url, "https://api.deepseek.com/v1");
    assert_eq!(ds.api_key_env, "DEEPSEEK_API_KEY");
}

// ── Test 3: Read Python sources YAML (raw) ───────────────────────────

#[test]
fn read_python_sources_yaml() {
    let yaml = include_str!("compat/fixtures/sources_python.yaml");
    let sources: serde_yaml::Value = serde_yaml::from_str(yaml).unwrap();
    let list = sources["sources"].as_sequence().unwrap();
    assert_eq!(list.len(), 2);
    assert_eq!(list[0]["name"].as_str(), Some("official"));
    assert_eq!(list[1]["name"].as_str(), Some("community"));
}

// ── Test 4: Read Python DSL TOML ──────────────────────────────────────

#[test]
fn read_python_dsl_toml() {
    let toml_str = include_str!("compat/fixtures/dsl_python.toml");
    let dag = OrchestrationDsl::parse(toml_str).unwrap();
    assert_eq!(dag.tasks.len(), 3);
    assert_eq!(dag.tasks[0].name, "research");
    assert_eq!(dag.tasks[1].depends_on, vec!["research"]);
    assert_eq!(dag.tasks[2].depends_on, vec!["implement"]);

    // Verify topological order
    let order: Vec<&str> = dag.get_execution_order().iter().map(|t| t.name.as_str()).collect();
    assert!(order.iter().position(|&n| n == "research").unwrap()
          < order.iter().position(|&n| n == "implement").unwrap());
    assert!(order.iter().position(|&n| n == "implement").unwrap()
          < order.iter().position(|&n| n == "test").unwrap());
}

// ── Test 5: IPC wire format matches Python ────────────────────────────

#[test]
fn ipc_wire_format_matches_python() {
    let json = r#"{"type":"chat","content":"hello","conversation_id":null,"task_id":null,"ref_id":null,"summary":null}"#;
    let msg: PlatformToAgent = serde_json::from_str(json).unwrap();
    assert_eq!(msg.msg_type, PlatformToAgentType::Chat);
    assert_eq!(msg.content, "hello");
}

#[test]
fn ipc_chat_minimal_python_format() {
    // Python often omits null fields
    let json = r#"{"type":"chat","content":"hello from python"}"#;
    let msg: PlatformToAgent = serde_json::from_str(json).unwrap();
    assert_eq!(msg.msg_type, PlatformToAgentType::Chat);
    assert_eq!(msg.content, "hello from python");
    assert!(msg.task_id.is_none());
    assert!(msg.conversation_id.is_none());
}

// ── Test 6: AgentType serialization (snake_case) ─────────────────────

#[test]
fn agent_type_serializes_as_snake_case() {
    let json = serde_json::to_string(&AgentType::Atomic).unwrap();
    assert_eq!(json, r#""atomic""#);
    let json = serde_json::to_string(&AgentType::Composite).unwrap();
    assert_eq!(json, r#""composite""#);
}

#[test]
fn agent_type_deserialize_from_snake_case() {
    let at: AgentType = serde_json::from_str(r#""atomic""#).unwrap();
    assert_eq!(at, AgentType::Atomic);
    let at: AgentType = serde_json::from_str(r#""composite""#).unwrap();
    assert_eq!(at, AgentType::Composite);
}

// ── Test 7: TaskGraph SQLite schema matches Python ────────────────────

#[test]
fn task_graph_schema_has_tasks_table() {
    use ap_core::models::task::TaskItem;
    use ap_core::models::task::TaskState;

    let graph = ap_core::orchestration::task_graph::TaskGraph::new_in_memory().unwrap();
    // Empty graph should be valid
    assert!(graph.is_empty().unwrap());

    // Add a task to verify the schema works
    let task = TaskItem {
        id: "t-1".to_string(),
        agent: "test-agent".to_string(),
        description: "test task".to_string(),
        blocked_by: vec![],
        vars: serde_json::Value::Null,
        state: TaskState::Pending,
        result: None,
        created_at: chrono::Utc::now(),
        updated_at: chrono::Utc::now(),
    };
    graph.add_task(&task).unwrap();
    assert!(!graph.is_empty().unwrap());

    let fetched = graph.get_task("t-1").unwrap().unwrap();
    assert_eq!(fetched.id, "t-1");
    assert_eq!(fetched.agent, "test-agent");
}
