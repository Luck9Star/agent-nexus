//! Cross-crate integration tests — verify multiple crates work together.

use ap_core::orchestration::dsl::OrchestrationDsl;
use ap_core::orchestration::task_graph::TaskGraph;
use ap_core::models::task::TaskItem;
use ap_core::models::task::TaskState;
use ap_core::config::default_config;
use ap_core::config::ModelConfigManager;
use ap_evolution::store::EvolutionStore;
use ap_evolution::store::SkillRecord;
use ap_evolution::engine::EvolutionEngine;
use ap_evolution::analyzer::TaskResult;
use ap_evolution::thresholds::Thresholds;

// ── Test 1: Config -> Model Config resolution chain ───────────────────

#[test]
fn config_to_model_resolution() {
    let config = default_config();
    let mgr = ModelConfigManager::new(config);
    let default_model = mgr.default_model();
    assert!(!default_model.is_empty());
}

// ── Test 2: DSL -> TaskGraph pipeline ────────────────────────────────

#[test]
fn dsl_to_task_graph() {
    let toml = r#"
    [[tasks]]
    name = "a"
    agent = "x"
    phase = 1

    [[tasks]]
    name = "b"
    agent = "y"
    phase = 2
    depends_on = ["a"]
    "#;
    let _dag = OrchestrationDsl::parse(toml).unwrap();
    let graph = TaskGraph::new_in_memory().unwrap();

    // Insert task "a" first (no deps)
    let task_a = TaskItem {
        id: "t-a".to_string(),
        agent: "x".to_string(),
        description: "Execute a".to_string(),
        blocked_by: vec![],
        vars: serde_json::Value::Null,
        state: TaskState::Pending,
        result: None,
        created_at: chrono::Utc::now(),
        updated_at: chrono::Utc::now(),
    };
    graph.add_task(&task_a).unwrap();

    // Insert task "b" (depends on a)
    let task_b = TaskItem {
        id: "t-b".to_string(),
        agent: "y".to_string(),
        description: "Execute b".to_string(),
        blocked_by: vec!["t-a".to_string()],
        vars: serde_json::Value::Null,
        state: TaskState::Pending,
        result: None,
        created_at: chrono::Utc::now(),
        updated_at: chrono::Utc::now(),
    };
    graph.add_task(&task_b).unwrap();

    let ready = graph.get_ready_tasks().unwrap();
    assert_eq!(ready.len(), 1);
    assert_eq!(ready[0].id, "t-a");
}

// ── Test 3: EvolutionStore -> Analyzer -> Engine pipeline ────────────

#[test]
fn evolution_pipeline() {
    let store = EvolutionStore::new_in_memory().unwrap();
    let engine = EvolutionEngine::new(store);

    // Successful task -- no suggestions
    let result = TaskResult {
        success: true,
        error: None,
        agent_name: "test".to_string(),
        task_id: "t-1".to_string(),
    };
    let suggestions = engine.post_task_evolve(&result);
    assert!(suggestions.is_empty());
    assert_eq!(engine.get_health_score(), 1.0);

    // Failed task -- Fix suggestion
    let fail_result = TaskResult {
        success: false,
        error: Some("bad error".to_string()),
        agent_name: "test".to_string(),
        task_id: "t-2".to_string(),
    };
    let suggestions = engine.post_task_evolve(&fail_result);
    assert_eq!(suggestions.len(), 1);
}

// ── Test 4: Skill CRUD through evolution store + thresholds ──────────

#[test]
fn skill_lifecycle_with_thresholds() {
    let store = EvolutionStore::new_in_memory().unwrap();
    let thresholds = Thresholds::default();

    // Insert skill
    let skill = SkillRecord {
        id: "s-001".to_string(),
        name: "fix-imports".to_string(),
        version: "1.0.0".to_string(),
        lineage_origin: "imported".to_string(),
        lineage_generation: 0,
        lineage_content_diff: None,
        lineage_content_snapshot: None,
        directory: None,
        is_active: true,
        total_selections: 10,
        total_applied: 8,
        total_completions: 7,
        total_fallbacks: 1,
        created_at: chrono::Utc::now().to_rfc3339(),
        updated_at: chrono::Utc::now().to_rfc3339(),
    };
    store.insert_skill(&skill).unwrap();

    let loaded = store.get_skill_by_name("fix-imports").unwrap().unwrap();
    assert_eq!(loaded.name, "fix-imports");

    // Evaluate with thresholds -- 10 selections, 7/8 = 0.875 success rate, 8 applications
    let success_rate = if loaded.total_applied > 0 {
        loaded.total_completions as f64 / loaded.total_applied as f64
    } else {
        1.0
    };
    assert!(thresholds.is_viable(10, success_rate, 8));
}

// ── Test 5: Evolution store schema has all Python tables ─────────────

#[test]
fn evolution_store_schema_has_python_tables() {
    let store = EvolutionStore::new_in_memory().unwrap();
    let tables = store.list_tables().unwrap();
    assert!(tables.contains(&"skill_records".to_string()), "Missing skill_records");
    assert!(tables.contains(&"skill_lineage_parents".to_string()), "Missing skill_lineage_parents");
    assert!(tables.contains(&"execution_analyses".to_string()), "Missing execution_analyses");
    assert!(tables.contains(&"skill_judgments".to_string()), "Missing skill_judgments");
    assert!(tables.contains(&"context_budget_log".to_string()), "Missing context_budget_log");
    assert!(tables.contains(&"agent_records".to_string()), "Missing agent_records");
}

// ── Test 6: Health score tracks mixed results ─────────────────────────

#[test]
fn health_score_with_mixed_results() {
    let store = EvolutionStore::new_in_memory().unwrap();
    let engine = EvolutionEngine::new(store);

    // 3 successes, 1 failure
    for i in 0..3 {
        let result = TaskResult {
            success: true,
            error: None,
            agent_name: "test".to_string(),
            task_id: format!("t-ok-{i}"),
        };
        engine.post_task_evolve(&result);
    }
    let fail = TaskResult {
        success: false,
        error: Some("error".to_string()),
        agent_name: "test".to_string(),
        task_id: "t-fail".to_string(),
    };
    engine.post_task_evolve(&fail);

    // Health should be between 0 and 1, closer to 1 since 3/4 succeeded
    let health = engine.get_health_score();
    assert!(health > 0.5 && health < 1.0, "Expected health between 0.5 and 1.0, got {health}");
}
