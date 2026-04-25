//! Benchmarks for Gateway tool adapter and schema operations.
//!
//! Thresholds: < 100us per operation (tool name parsing, schema merging)

use criterion::{black_box, criterion_group, criterion_main, BenchmarkId, Criterion};

use ap_gateway::schema::{extract_tool_call, merge_tool_schemas};
use ap_gateway::tool_adapter::McpToolAdapter;
use ap_runtime::mcp_client::ToolInfo;

fn sample_tools(n: usize) -> Vec<ToolInfo> {
    (0..n)
        .map(|i| ToolInfo {
            name: format!("tool-{i}"),
            description: Some(format!("Description for tool {i}")),
            input_schema: Some(serde_json::json!({
                "type": "object",
                "properties": {
                    "input": {"type": "string"}
                }
            })),
        })
        .collect()
}

fn bench_namespace_tool(c: &mut Criterion) {
    let adapter = McpToolAdapter::new();
    c.bench_function("gateway/namespace_tool", |b| {
        b.iter(|| adapter.namespace_tool(black_box("code-reviewer"), black_box("review")))
    });
}

fn bench_parse_namespaced(c: &mut Criterion) {
    let adapter = McpToolAdapter::new();
    c.bench_function("gateway/parse_namespaced", |b| {
        b.iter(|| adapter.parse_namespaced(black_box("code-reviewer___review")))
    });
}

fn bench_merge_tool_schemas(c: &mut Criterion) {
    let mut group = c.benchmark_group("gateway/merge_tool_schemas");

    for size in [1, 5, 20, 50] {
        let tools = sample_tools(size);
        group.bench_with_input(BenchmarkId::from_parameter(size), &tools, |b, tools| {
            b.iter(|| merge_tool_schemas(black_box("test-agent"), black_box(tools)))
        });
    }
    group.finish();
}

fn bench_extract_tool_call(c: &mut Criterion) {
    let request = serde_json::json!({
        "name": "code-reviewer___review",
        "arguments": {"path": "/src/main.rs", "verbose": true}
    });

    c.bench_function("gateway/extract_tool_call", |b| {
        b.iter(|| extract_tool_call(black_box(&request)))
    });
}

criterion_group!(
    benches,
    bench_namespace_tool,
    bench_parse_namespaced,
    bench_merge_tool_schemas,
    bench_extract_tool_call,
);
criterion_main!(benches);
