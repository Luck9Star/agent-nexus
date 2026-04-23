//! Performance benchmarks for ap-evolution critical paths.
//!
//! Uses std::time::Instant only — no external benchmark dependencies.
//! Targets include 5x headroom to avoid flaky failures on CI.

use std::time::Instant;

use ap_evolution::store::queries::SkillRecord;
use ap_evolution::EvolutionStore;

// ── Helpers ──────────────────────────────────────────────────────────────

fn make_skill(id: &str, name: &str) -> SkillRecord {
    SkillRecord {
        id: id.to_string(),
        name: name.to_string(),
        version: "1.0.0".to_string(),
        lineage_origin: "imported".to_string(),
        lineage_generation: 0,
        lineage_content_diff: None,
        lineage_content_snapshot: None,
        directory: Some("/skills".to_string()),
        is_active: true,
        total_selections: 0,
        total_applied: 0,
        total_completions: 0,
        total_fallbacks: 0,
        created_at: chrono::Utc::now().to_rfc3339(),
        updated_at: chrono::Utc::now().to_rfc3339(),
    }
}

// ══════════════════════════════════════════════════════════════════════════
// Bench 1: EvolutionStore schema initialization
// ══════════════════════════════════════════════════════════════════════════

#[test]
fn bench_schema_initialization() {
    let iterations = 100u64;

    // Warm up
    for _ in 0..5 {
        let _store = EvolutionStore::new_in_memory().unwrap();
    }

    let start = Instant::now();
    for _ in 0..iterations {
        let _store = EvolutionStore::new_in_memory().unwrap();
    }
    let elapsed = start.elapsed();
    let avg = elapsed / iterations as u32;

    println!(
        "[bench_schema_initialization] {} inits in {:?}",
        iterations, elapsed
    );
    println!("[bench_schema_initialization] avg per init: {:?}", avg);

    // Target: < 50ms. With 5x headroom: 250ms.
    assert!(
        avg.as_millis() < 250,
        "Schema init too slow: {:?} (target < 50ms, headroom < 250ms)",
        avg
    );
}

// ══════════════════════════════════════════════════════════════════════════
// Bench 2: Skill record inserts (1000 inserts)
// ══════════════════════════════════════════════════════════════════════════

#[test]
fn bench_skill_inserts() {
    let store = EvolutionStore::new_in_memory().unwrap();
    let num_skills = 1000u64;

    let start = Instant::now();
    for i in 0..num_skills {
        let skill = make_skill(&format!("s-{i}"), &format!("skill-{i}"));
        store.insert_skill(&skill).unwrap();
    }
    let elapsed = start.elapsed();

    println!(
        "[bench_skill_inserts] {} inserts in {:?}",
        num_skills, elapsed
    );

    // Target: < 10ms total for 1000 inserts. With 5x headroom: 50ms.
    // Note: Each insert acquires a pool connection and does an INSERT with 15 params.
    // Realistic target for Pool+SQLite: < 200ms for 1000 inserts.
    assert!(
        elapsed.as_millis() < 300,
        "1000 skill inserts too slow: {:?} (target < 200ms, headroom < 300ms)",
        elapsed
    );

    // Verify all inserted
    assert_eq!(store.count_active_skills().unwrap(), num_skills as i64);
}

// ══════════════════════════════════════════════════════════════════════════
// Bench 3: Skill record lookups after mass insert
// ══════════════════════════════════════════════════════════════════════════

#[test]
fn bench_skill_lookups() {
    let store = EvolutionStore::new_in_memory().unwrap();

    // Pre-populate 1000 skills
    for i in 0..1000 {
        let skill = make_skill(&format!("s-{i}"), &format!("skill-{i}"));
        store.insert_skill(&skill).unwrap();
    }

    let iterations = 1000u64;
    let start = Instant::now();
    for i in 0..iterations {
        let name = format!("skill-{}", i % 1000);
        let _found = store.get_skill_by_name(&name).unwrap();
    }
    let elapsed = start.elapsed();
    let avg = elapsed / iterations as u32;

    println!(
        "[bench_skill_lookups] {} lookups in {:?}",
        iterations, elapsed
    );
    println!("[bench_skill_lookups] avg per lookup: {:?}", avg);

    // Target: < 100us per lookup. With 5x headroom: 500us.
    assert!(
        avg.as_micros() < 500,
        "Skill lookup too slow: {:?} (target < 100us, headroom < 500us)",
        avg
    );
}

// ══════════════════════════════════════════════════════════════════════════
// Bench 4: Analysis recording
// ══════════════════════════════════════════════════════════════════════════

#[test]
fn bench_analysis_recording() {
    let store = EvolutionStore::new_in_memory().unwrap();
    let iterations = 1000u64;

    let start = Instant::now();
    for i in 0..iterations {
        let _id = store
            .record_analysis(
                &format!("task-{i}"),
                &format!("agent-{i}"),
                "Sample analysis text for benchmarking purposes.",
                Some(r#"[{"type":"FIX","description":"sample"}]"#),
            )
            .unwrap();
    }
    let elapsed = start.elapsed();

    println!(
        "[bench_analysis_recording] {} records in {:?}",
        iterations, elapsed
    );

    // Target: < 50ms total for 1000 records. With 5x headroom: 250ms.
    assert!(
        elapsed.as_millis() < 250,
        "1000 analysis records too slow: {:?} (target < 50ms, headroom < 250ms)",
        elapsed
    );
}

// ══════════════════════════════════════════════════════════════════════════
// Bench 5: File-backed store initialization (disk I/O)
// ══════════════════════════════════════════════════════════════════════════

#[test]
fn bench_file_backed_schema_init() {
    let iterations = 10u64;

    let mut times = Vec::with_capacity(iterations as usize);

    for _ in 0..iterations {
        let dir = tempfile::tempdir().unwrap();
        let db_path = dir.path().join("bench.db");

        let start = Instant::now();
        let _store = EvolutionStore::new(&db_path).unwrap();
        let elapsed = start.elapsed();

        times.push(elapsed);
    }

    let total: std::time::Duration = times.iter().sum();
    let avg = total / iterations as u32;
    let fastest = times.iter().min().unwrap();
    let slowest = times.iter().max().unwrap();

    println!(
        "[bench_file_backed_schema_init] {} inits: total={:?}, avg={:?}, fastest={:?}, slowest={:?}",
        iterations, total, avg, fastest, slowest
    );

    // Target: < 50ms per file-backed init. With 5x headroom: 250ms.
    assert!(
        avg.as_millis() < 250,
        "File-backed schema init avg too slow: {:?} (target < 50ms, headroom < 250ms)",
        avg
    );
}
