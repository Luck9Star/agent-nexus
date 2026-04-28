//! Fetcher integration tests — source TOML roundtrip and lockfile parsing.

use ap_fetcher::sources::SourceManager;
use ap_core::models::distribution::SourceEntry;

// ── Test: Source TOML -> SourceManager pipeline ──────────────────────

#[test]
fn source_toml_roundtrip() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("config.toml");
    std::fs::write(&path, "[models]\ndefault = \"openai:gpt-4o\"\n").unwrap();

    let mgr = SourceManager::new_toml(path.clone());
    assert!(mgr.list().is_empty());

    let entry = SourceEntry {
        name: "test".to_string(),
        source_type: "git".to_string(),
        url: "https://github.com/example/repo".to_string(),
        branch: "main".to_string(),
    };
    mgr.add(entry).unwrap();

    let mgr2 = SourceManager::new_toml(path);
    let sources = mgr2.list();
    assert_eq!(sources.len(), 1);
    assert_eq!(sources[0].name, "test");
}

#[test]
fn source_parse_yaml_migration() {
    let yaml = r#"
sources:
  - name: legacy
    type: git
    url: https://github.com/example/old-repo
    branch: main
"#;
    let entries = SourceManager::parse(yaml).unwrap();
    assert_eq!(entries.len(), 1);
    assert_eq!(entries[0].name, "legacy");
}
