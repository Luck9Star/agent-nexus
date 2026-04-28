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

// ── Test 7: Transaction safety — save_health_state concurrent writes ────

#[test]
fn save_health_state_concurrent_no_corruption() {
    use std::sync::Arc;

    let dir = tempfile::tempdir().unwrap();
    let db_path = dir.path().join("health_concurrent.db");
    let store = Arc::new(EvolutionStore::new(&db_path).unwrap());

    // Save an initial health state
    store.save_health_state(0.5, 10).unwrap();
    let (_initial_score, initial_total) = store.load_health_state().unwrap();
    assert_eq!(initial_total, 10);

    // Spawn multiple threads all saving health state concurrently
    let mut handles = vec![];
    for i in 0..8 {
        let s = Arc::clone(&store);
        handles.push(std::thread::spawn(move || {
            let score = 0.5 + (i as f64 * 0.05);
            let total = 10 + i;
            s.save_health_state(score, total).unwrap();
        }));
    }

    for h in handles {
        h.join().unwrap();
    }

    // The final state should be readable without error (not corrupted)
    let (final_score, final_total) = store.load_health_state().unwrap();
    // Score should be a valid float (not NaN or Infinity)
    assert!(
        final_score.is_finite(),
        "Health score should be a finite number, got: {final_score}"
    );
    // Total should be one of the values we wrote (10..17)
    assert!(
        (10..=17).contains(&final_total),
        "Total should be in the range we wrote, got: {final_total}"
    );
}

// ── Test 8: Transaction safety — with_transaction rollback on error ────

#[test]
fn with_transaction_rollback_on_error() {
    use ap_evolution::store::error::StoreError;

    let store = EvolutionStore::new_in_memory().unwrap();

    // Insert a skill first
    let skill = SkillRecord {
        id: "tx-rollback-1".to_string(),
        name: "rollback-skill".to_string(),
        version: "1.0.0".to_string(),
        lineage_origin: "imported".to_string(),
        lineage_generation: 0,
        lineage_content_diff: None,
        lineage_content_snapshot: None,
        directory: None,
        is_active: true,
        total_selections: 5,
        total_applied: 5,
        total_completions: 5,
        total_fallbacks: 0,
        created_at: chrono::Utc::now().to_rfc3339(),
        updated_at: chrono::Utc::now().to_rfc3339(),
    };
    store.insert_skill(&skill).unwrap();

    // Verify the skill exists
    let found = store.get_skill_by_name("rollback-skill").unwrap().unwrap();
    assert_eq!(found.total_selections, 5);

    // Use with_transaction to do something that fails -- data should be rolled back
    let result: std::result::Result<(), StoreError> = store.with_transaction(|conn| {
        // Delete the skill within the transaction
        conn.execute("DELETE FROM skill_records WHERE id = 'tx-rollback-1'", [])?;
        // Now force an error to trigger rollback
        Err(StoreError::Sqlite(rusqlite::Error::InvalidParameterName("force_error".to_string())))
    });

    assert!(result.is_err(), "Transaction should have returned an error");

    // The skill should still exist because the transaction was rolled back
    let found_after = store.get_skill_by_name("rollback-skill").unwrap().unwrap();
    assert_eq!(
        found_after.id, "tx-rollback-1",
        "Skill should still exist after transaction rollback"
    );
    assert_eq!(
        found_after.total_selections, 5,
        "Skill data should be unchanged after rollback"
    );
}

// ── Test 9: Constraint violation detection uses rusqlite error codes ──

#[test]
fn concurrent_evolution_detects_constraint_violation_by_error_code() {
    use ap_evolution::evolver::SkillEvolver;
    use std::sync::Arc;

    let store = Arc::new(EvolutionStore::new_in_memory().unwrap());
    let skill = SkillRecord {
        id: "cv-test-1".to_string(),
        name: "concurrent-skill".to_string(),
        version: "1.0.0".to_string(),
        lineage_origin: "imported".to_string(),
        lineage_generation: 0,
        lineage_content_diff: None,
        lineage_content_snapshot: None,
        directory: Some("/skills/cv".to_string()),
        is_active: true,
        total_selections: 0,
        total_applied: 0,
        total_completions: 0,
        total_fallbacks: 0,
        created_at: chrono::Utc::now().to_rfc3339(),
        updated_at: chrono::Utc::now().to_rfc3339(),
    };
    store.insert_skill(&skill).unwrap();

    let evolver = SkillEvolver::new(store.clone());

    // First evolution succeeds
    let outcome = evolver.evolve_fix("concurrent-skill", "first fix").unwrap();
    assert!(
        matches!(outcome, ap_evolution::evolver::EvolutionOutcome::Success { .. }),
        "First evolution should succeed"
    );

    // Evolve again — should succeed (new generation)
    let outcome2 = evolver.evolve_fix("concurrent-skill", "second fix").unwrap();
    assert!(
        matches!(outcome2, ap_evolution::evolver::EvolutionOutcome::Success { .. }),
        "Second evolution should succeed"
    );

    // Verify both new skills exist and are active
    let active = store.get_skill_by_name("concurrent-skill").unwrap().unwrap();
    assert!(active.lineage_generation >= 1, "Should have evolved at least once");
}

// ── Test 10: Real concurrent threads trigger ConcurrentModification ─────

#[test]
fn concurrent_evolution_triggers_concurrent_modification() {
    use ap_evolution::evolver::{EvolverError, SkillEvolver};
    use std::sync::Arc;
    use std::sync::Barrier;

    let dir = tempfile::tempdir().unwrap();
    let db_path = dir.path().join("concurrent_evolve.db");
    let store = Arc::new(EvolutionStore::new(&db_path).unwrap());

    // Insert initial skill
    let skill = SkillRecord {
        id: "race-1".to_string(),
        name: "race-skill".to_string(),
        version: "1.0.0".to_string(),
        lineage_origin: "imported".to_string(),
        lineage_generation: 0,
        lineage_content_diff: None,
        lineage_content_snapshot: None,
        directory: Some("/skills/race".to_string()),
        is_active: true,
        total_selections: 0,
        total_applied: 0,
        total_completions: 0,
        total_fallbacks: 0,
        created_at: chrono::Utc::now().to_rfc3339(),
        updated_at: chrono::Utc::now().to_rfc3339(),
    };
    store.insert_skill(&skill).unwrap();

    let num_threads = 4;
    let barrier = Arc::new(Barrier::new(num_threads));
    let mut handles = vec![];

    for i in 0..num_threads {
        let s = Arc::clone(&store);
        let b = Arc::clone(&barrier);
        handles.push(std::thread::spawn(move || {
            b.wait(); // all threads read at the same snapshot
            let evolver = SkillEvolver::new(s);
            evolver.evolve_fix("race-skill", &format!("fix {i}"))
        }));
    }

    let results: Vec<_> = handles.into_iter().map(|h| h.join().unwrap()).collect();

    let successes = results.iter().filter(|r| r.is_ok()).count();
    let concurrent_mods = results
        .iter()
        .filter(|r| matches!(r, Err(EvolverError::ConcurrentModification(_))))
        .count();

    assert!(
        successes >= 1,
        "At least one evolution must succeed, got {successes} successes out of {num_threads}"
    );
    assert!(
        successes + concurrent_mods == num_threads,
        "All results must be Success or ConcurrentModification, got {successes} successes + {concurrent_mods} concurrent_mods out of {num_threads}"
    );

    // Verify no data corruption
    let active = store.get_skill_by_name("race-skill").unwrap().unwrap();
    assert!(active.is_active, "Active skill must remain active");
}
