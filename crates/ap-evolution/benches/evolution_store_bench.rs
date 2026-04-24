//! Benchmarks for EvolutionStore queries.
//!
//! Thresholds: < 5ms per query

use criterion::{black_box, criterion_group, criterion_main, BenchmarkId, Criterion};

use ap_evolution::store::EvolutionStore;
use ap_evolution::store::SkillRecord;

fn setup_store_with_skills(n: usize) -> EvolutionStore {
    let store = EvolutionStore::new_in_memory().unwrap();
    let now = chrono::Utc::now().to_rfc3339();
    for i in 0..n {
        store
            .insert_skill(&SkillRecord {
                id: format!("skill-{i}"),
                name: format!("bench-skill-{i}"),
                version: format!("{}.0.0", i % 10),
                lineage_origin: "bench".to_string(),
                lineage_generation: 1,
                lineage_content_diff: None,
                lineage_content_snapshot: Some(format!("snapshot-{i}")),
                directory: Some(format!("/tmp/skill-{i}")),
                is_active: i % 3 != 0,
                total_selections: (i as i64) * 10,
                total_applied: (i as i64) * 8,
                total_completions: (i as i64) * 7,
                total_fallbacks: (i as i64) * 1,
                created_at: now.clone(),
                updated_at: now.clone(),
            })
            .unwrap();
    }
    store
}

fn bench_get_active_skills(c: &mut Criterion) {
    let mut group = c.benchmark_group("evolution/get_active_skills");

    for size in [10, 50, 100, 500] {
        let store = setup_store_with_skills(size);
        group.bench_with_input(BenchmarkId::from_parameter(size), &size, |b, _| {
            b.iter(|| {
                let _ = store.get_active_skills();
            });
        });
    }
    group.finish();
}

fn bench_get_skill_by_name(c: &mut Criterion) {
    let store = setup_store_with_skills(100);

    let mut group = c.benchmark_group("evolution/get_skill_by_name");

    group.bench_function("existing_skill", |b| {
        b.iter(|| {
            let _ = store.get_skill_by_name(black_box("bench-skill-50"));
        });
    });

    group.bench_function("nonexistent_skill", |b| {
        b.iter(|| {
            let _ = store.get_skill_by_name(black_box("no-such-skill"));
        });
    });
    group.finish();
}

criterion_group!(benches, bench_get_active_skills, bench_get_skill_by_name);
criterion_main!(benches);
