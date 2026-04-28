//! Benchmarks for TaskGraph operations.
//!
//! Thresholds: < 1ms per operation

use criterion::{black_box, criterion_group, criterion_main, BenchmarkId, Criterion};

use ap_core::models::task::{TaskItem, TaskState};
use ap_core::orchestration::task_graph::TaskGraph;

fn make_task(id: &str, blocked_by: Vec<String>) -> TaskItem {
    TaskItem {
        id: id.to_string(),
        description: format!("Benchmark task {id}"),
        agent: format!("agent-{id}"),
        blocked_by,
        vars: serde_json::Value::Null,
        state: TaskState::Pending,
        result: None,
        created_at: chrono::Utc::now(),
        updated_at: chrono::Utc::now(),
    }
}

fn bench_add_task(c: &mut Criterion) {
    let mut group = c.benchmark_group("taskgraph/add_task");

    for size in [1, 10, 50, 100] {
        group.bench_with_input(BenchmarkId::from_parameter(size), &size, |b, &size| {
            b.iter(|| {
                let tg = TaskGraph::new_in_memory().unwrap();
                for i in 0..size {
                    let task = make_task(&format!("task-{i}"), vec![]);
                    let _ = tg.add_task(black_box(&task));
                }
            });
        });
    }
    group.finish();
}

fn bench_add_task_with_chain_deps(c: &mut Criterion) {
    let mut group = c.benchmark_group("taskgraph/add_task_chain_deps");

    for size in [10, 50] {
        group.bench_with_input(BenchmarkId::from_parameter(size), &size, |b, &size| {
            b.iter(|| {
                let tg = TaskGraph::new_in_memory().unwrap();
                // First task has no deps
                let t0 = make_task("task-0", vec![]);
                let _ = tg.add_task(black_box(&t0));
                // Subsequent tasks depend on previous one (chain)
                for i in 1..size {
                    let task = make_task(&format!("task-{i}"), vec![format!("task-{}", i - 1)]);
                    let _ = tg.add_task(black_box(&task));
                }
            });
        });
    }
    group.finish();
}

fn bench_detect_cycle_chain(c: &mut Criterion) {
    let mut group = c.benchmark_group("taskgraph/detect_cycle_chain");
    group.sample_size(20);

    for size in [10, 50] {
        group.bench_with_input(BenchmarkId::from_parameter(size), &size, |b, &size| {
            b.iter(|| {
                let tg = TaskGraph::new_in_memory().unwrap();
                // Build a chain: 0 -> 1 -> 2 -> ... -> (size-1)
                let t0 = make_task("task-0", vec![]);
                let _ = tg.add_task(&t0);
                for i in 1..size {
                    let task = make_task(&format!("task-{i}"), vec![format!("task-{}", i - 1)]);
                    let _ = tg.add_task(&task);
                }
                let _ = tg.detect_cycle();
            });
        });
    }
    group.finish();
}

criterion_group!(
    benches,
    bench_add_task,
    bench_add_task_with_chain_deps,
    bench_detect_cycle_chain,
);
criterion_main!(benches);
