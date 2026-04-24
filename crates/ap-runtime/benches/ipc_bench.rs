//! Benchmarks for IPC message serialization/deserialization.
//!
//! Thresholds: < 100us per message

use criterion::{black_box, criterion_group, criterion_main, Criterion};

use ap_core::models::ipc::{
    AgentToPlatform, AgentToPlatformType, IPCMessage, MessageDirection, PlatformToAgent,
    PlatformToAgentType,
};

fn make_platform_to_agent() -> PlatformToAgent {
    PlatformToAgent {
        msg_type: PlatformToAgentType::Task,
        content: "Review the following code changes".to_string(),
        task_id: Some("task-12345".to_string()),
        conversation_id: Some("conv-abc".to_string()),
        ref_id: None,
        summary: Some("Code review request".to_string()),
    }
}

fn make_agent_to_platform() -> AgentToPlatform {
    AgentToPlatform {
        msg_type: AgentToPlatformType::Result,
        content: String::new(),
        task_id: Some("task-12345".to_string()),
        message: None,
        progress_pct: None,
        error: None,
        status: Some("completed".to_string()),
        output: Some(serde_json::json!({
            "findings": [
                {"severity": "high", "message": "Missing error handling in line 42"},
                {"severity": "medium", "message": "Consider using Arc<Mutex> for shared state"}
            ],
            "summary": "Found 2 issues",
            "score": 85.5
        })),
    }
}

fn bench_ipc_serialize(c: &mut Criterion) {
    let p2a = make_platform_to_agent();
    let a2p = make_agent_to_platform();

    let mut group = c.benchmark_group("ipc/serialize");

    group.bench_function("platform_to_agent", |b| {
        b.iter(|| {
            let _ = serde_json::to_string(black_box(&p2a));
        });
    });

    group.bench_function("agent_to_platform", |b| {
        b.iter(|| {
            let _ = serde_json::to_string(black_box(&a2p));
        });
    });

    group.finish();
}

fn bench_ipc_deserialize(c: &mut Criterion) {
    let p2a_json = serde_json::to_string(&make_platform_to_agent()).unwrap();
    let a2p_json = serde_json::to_string(&make_agent_to_platform()).unwrap();

    let mut group = c.benchmark_group("ipc/deserialize");

    group.bench_function("platform_to_agent", |b| {
        b.iter(|| {
            let _: PlatformToAgent = serde_json::from_str(black_box(&p2a_json)).unwrap();
        });
    });

    group.bench_function("agent_to_platform", |b| {
        b.iter(|| {
            let _: AgentToPlatform = serde_json::from_str(black_box(&a2p_json)).unwrap();
        });
    });

    group.finish();
}

fn bench_ipc_message_roundtrip(c: &mut Criterion) {
    let msg = IPCMessage {
        direction: MessageDirection::PlatformToAgent,
        payload: serde_json::to_value(&make_platform_to_agent()).unwrap(),
    };

    c.bench_function("ipc/message_roundtrip", |b| {
        b.iter(|| {
            let json = serde_json::to_string(black_box(&msg)).unwrap();
            let _: IPCMessage = serde_json::from_str(&json).unwrap();
        });
    });
}

criterion_group!(
    benches,
    bench_ipc_serialize,
    bench_ipc_deserialize,
    bench_ipc_message_roundtrip,
);
criterion_main!(benches);
