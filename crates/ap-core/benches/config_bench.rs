//! Benchmarks for config loading and model resolution.
//!
//! Thresholds: < 10ms cold start for config load, < 100us for model resolve

use criterion::{black_box, criterion_group, criterion_main, BenchmarkId, Criterion};

use ap_core::config::loader::ConfigLoader;
use ap_core::config::model_config::ModelConfigManager;

const CONFIG_TOML: &str = r#"
[models]
default = "openai:gpt-4o"

[models.providers.openai]
base_url = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"

[models.providers.anthropic]
base_url = "https://api.anthropic.com"
api_key_env = "ANTHROPIC_API_KEY"

[models.providers.ollama]
base_url = "http://localhost:11434"
"#;

fn bench_config_load_from_str(c: &mut Criterion) {
    c.bench_function("config/load_from_str", |b| {
        b.iter(|| {
            let _ = ConfigLoader::load_from_str(black_box(CONFIG_TOML));
        });
    });
}

fn bench_model_resolve(c: &mut Criterion) {
    let config = ConfigLoader::load_from_str(CONFIG_TOML).expect("valid config");
    let mgr = ModelConfigManager::new(config);

    let mut group = c.benchmark_group("config/model_resolve");

    let models = ["openai:gpt-4o", "anthropic:claude-3-opus", "ollama:llama3"];
    for model_str in models {
        group.bench_with_input(
            BenchmarkId::new("resolve", model_str.replace(':', "_")),
            model_str,
            |b, model| {
                b.iter(|| {
                    let _ = mgr.resolve(black_box(model));
                });
            },
        );
    }
    group.finish();
}

criterion_group!(benches, bench_config_load_from_str, bench_model_resolve);
criterion_main!(benches);
