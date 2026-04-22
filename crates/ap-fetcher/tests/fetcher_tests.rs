//! Fetcher integration tests — source YAML roundtrip and lockfile parsing.

use ap_fetcher::sources::SourceManager;
use ap_core::models::distribution::SourceEntry;

// ── Test: Source YAML -> SourceManager pipeline ──────────────────────

#[test]
fn source_yaml_roundtrip() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("sources.yaml");
    std::fs::write(&path, "sources: []\n").unwrap();

    let mgr = SourceManager::new(path.clone());
    assert!(mgr.list().is_empty());

    let entry = SourceEntry {
        name: "test".to_string(),
        source_type: "git".to_string(),
        url: "https://github.com/example/repo".to_string(),
        branch: "main".to_string(),
    };
    mgr.add(entry).unwrap();

    let mgr2 = SourceManager::new(path);
    let sources = mgr2.list();
    assert_eq!(sources.len(), 1);
    assert_eq!(sources[0].name, "test");
}

#[test]
fn source_parse_from_python_yaml() {
    let yaml = r#"
sources:
  - name: official
    type: git
    url: https://github.com/anthropics/agent-nexus-agents
    branch: main
  - name: community
    type: git
    url: https://github.com/community/agent-nexus-contrib
    branch: stable
"#;
    let entries = SourceManager::parse(yaml).unwrap();
    assert_eq!(entries.len(), 2);
    assert_eq!(entries[0].name, "official");
    assert_eq!(entries[1].name, "community");
}

#[test]
fn source_add_remove_roundtrip() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("sources.yaml");
    std::fs::write(&path, "sources: []\n").unwrap();

    let mgr = SourceManager::new(path.clone());
    let entry = SourceEntry {
        name: "to-remove".to_string(),
        source_type: "git".to_string(),
        url: "https://github.com/example/repo".to_string(),
        branch: "main".to_string(),
    };
    mgr.add(entry).unwrap();
    assert_eq!(mgr.list().len(), 1);

    mgr.remove("to-remove").unwrap();
    assert!(mgr.list().is_empty());
}
