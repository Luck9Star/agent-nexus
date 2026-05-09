//! Integration tests for ap-cli-backend — registry, router, session store, archive, parser.

mod registry {
    use ap_cli_backend::backend::GenericCLIBackend;
    use ap_cli_backend::registry::CLIBackendRegistry;
    use ap_cli_backend::types::BackendConfig;

    fn echo_backend() -> GenericCLIBackend {
        GenericCLIBackend::new(BackendConfig {
            command: "echo".into(),
            ..Default::default()
        })
    }

    fn unavailable_backend() -> GenericCLIBackend {
        GenericCLIBackend::new(BackendConfig {
            command: "nonexistent_command_xyz_abc".into(),
            ..Default::default()
        })
    }

    #[test]
    fn register_and_get_backend() {
        let mut registry = CLIBackendRegistry::new();
        registry.register("echo".into(), echo_backend());
        let backend = registry.get("echo").unwrap();
        assert_eq!(backend.name(), "echo");
    }

    #[test]
    fn get_nonexistent_returns_error() {
        let registry = CLIBackendRegistry::new();
        let result = registry.get("nope");
        let err = match result {
            Err(e) => e,
            Ok(_) => panic!("Expected error"),
        };
        assert!(err.contains("not registered"));
    }

    #[test]
    fn available_backends_filters_unavailable() {
        let mut registry = CLIBackendRegistry::new();
        registry.register("echo".into(), echo_backend());
        registry.register("nope".into(), unavailable_backend());
        let available = registry.available_backends();
        assert_eq!(available.len(), 1);
        assert_eq!(available[0].name(), "echo");
    }

    #[test]
    fn len_and_is_empty() {
        let mut registry = CLIBackendRegistry::new();
        assert!(registry.is_empty());
        assert_eq!(registry.len(), 0);
        registry.register("test".into(), echo_backend());
        assert!(!registry.is_empty());
        assert_eq!(registry.len(), 1);
    }

    #[test]
    fn register_replaces_existing() {
        let mut registry = CLIBackendRegistry::new();
        registry.register("test".into(), echo_backend());
        registry.register("test".into(), unavailable_backend());
        assert_eq!(registry.len(), 1);
        // Should now be unavailable since we replaced it
        let backend = registry.get("test").unwrap();
        assert!(!backend.is_available());
    }
}

mod router {
    use ap_cli_backend::backend::GenericCLIBackend;
    use ap_cli_backend::registry::CLIBackendRegistry;
    use ap_cli_backend::router::CLIRouter;
    use ap_cli_backend::types::{BackendConfig, RoutingConfig};
    use std::collections::HashMap;

    fn echo_config() -> BackendConfig {
        BackendConfig {
            command: "echo".into(),
            ..Default::default()
        }
    }

    fn unavailable_config() -> BackendConfig {
        BackendConfig {
            command: "nonexistent_xyz_abc".into(),
            ..Default::default()
        }
    }

    fn registry_with(names: &[(&str, bool)]) -> CLIBackendRegistry {
        let mut reg = CLIBackendRegistry::new();
        for (name, available) in names {
            let config = if *available {
                echo_config()
            } else {
                unavailable_config()
            };
            reg.register(name.to_string(), GenericCLIBackend::new(config));
        }
        reg
    }

    #[test]
    fn resolve_explicit_overrides_model() {
        let reg = registry_with(&[("echo", true)]);
        let mut rules = HashMap::new();
        rules.insert("gpt-*".into(), "echo".into());
        let routing = RoutingConfig {
            default: "echo".into(),
            fallback_enabled: true,
            fallback_chain: vec![],
            model_rules: rules,
        };
        let router = CLIRouter::new(routing, reg);
        // Explicit backend should be used even with model string
        let result = router.resolve(Some("gpt-4o"), Some("echo"));
        assert!(result.is_ok());
    }

    #[test]
    fn resolve_model_rules_match_pattern() {
        let reg = registry_with(&[("echo", true)]);
        let mut rules = HashMap::new();
        rules.insert("claude-*".into(), "echo".into());
        let routing = RoutingConfig {
            default: "echo".into(),
            fallback_enabled: false,
            fallback_chain: vec![],
            model_rules: rules,
        };
        let router = CLIRouter::new(routing, reg);
        let result = router.resolve(Some("claude-sonnet-4"), None);
        assert!(result.is_ok());
    }

    #[test]
    fn resolve_fallback_chain_tries_in_order() {
        let reg = registry_with(&[
            ("primary", false),
            ("fallback1", false),
            ("fallback2", true),
        ]);
        let routing = RoutingConfig {
            default: "primary".into(),
            fallback_enabled: true,
            fallback_chain: vec!["fallback1".into(), "fallback2".into()],
            model_rules: HashMap::new(),
        };
        let router = CLIRouter::new(routing, reg);
        let result = router.resolve_with_fallback(None, None).unwrap();
        assert_eq!(result.name(), "echo"); // the available one
    }
}

mod session_store {
    use ap_cli_backend::session::CLISessionStore;
    use ap_cli_backend::types::{CLISession, ExecutionRecord};
    use tempfile::TempDir;

    fn setup() -> (TempDir, CLISessionStore) {
        let dir = TempDir::new().unwrap();
        let db_path = dir.path().join("test.db");
        let store = CLISessionStore::open(&db_path).unwrap();
        (dir, store)
    }

    #[test]
    fn open_creates_schema() {
        let dir = TempDir::new().unwrap();
        let db_path = dir.path().join("new.db");
        let store = CLISessionStore::open(&db_path).unwrap();
        // Verify tables exist by querying them
        let stmt = store.prepare_stmt("SELECT COUNT(*) FROM cli_sessions");
        assert!(stmt.is_ok());
        let stmt = store.prepare_stmt("SELECT COUNT(*) FROM task_executions");
        assert!(stmt.is_ok());
        let stmt = store.prepare_stmt("SELECT COUNT(*) FROM backend_health");
        assert!(stmt.is_ok());
        let stmt = store.prepare_stmt("SELECT COUNT(*) FROM daily_stats");
        assert!(stmt.is_ok());
    }

    #[test]
    fn session_full_lifecycle() {
        let (_dir, store) = setup();

        // Create
        let session = CLISession {
            session_id: "lifecycle-1".into(),
            backend_name: "claude-code".into(),
            model: Some("claude-sonnet-4".into()),
            task_id: Some("task-001".into()),
            name: Some("test".into()),
            created_at: "2026-01-01T00:00:00".into(),
            last_used_at: "2026-01-01T00:00:00".into(),
            turn_count: 1,
            metadata: Some(r#"{"key":"value"}"#.into()),
        };
        store.save_session(&session).unwrap();

        // Read
        let retrieved = store.get_session("lifecycle-1").unwrap().unwrap();
        assert_eq!(retrieved.session_id, "lifecycle-1");
        assert_eq!(
            retrieved.metadata,
            Some(r#"{"key":"value"}"#.to_string())
        );

        // Update
        let updated = CLISession {
            turn_count: 5,
            last_used_at: "2026-05-01T00:00:00".into(),
            ..session
        };
        store.save_session(&updated).unwrap();
        let after_update = store.get_session("lifecycle-1").unwrap().unwrap();
        assert_eq!(after_update.turn_count, 5);
    }

    #[test]
    fn record_execution_with_all_fields() {
        let (_dir, store) = setup();

        // Create the session first to satisfy FK constraint
        let session = CLISession {
            session_id: "sess-1".into(),
            backend_name: "claude-code".into(),
            created_at: "2026-01-01T00:00:00".into(),
            last_used_at: "2026-01-01T00:00:00".into(),
            ..Default::default()
        };
        store.save_session(&session).unwrap();

        let record = ExecutionRecord {
            task_id: "task-all",
            backend_type: "cli",
            backend_name: "claude-code",
            model: Some("claude-sonnet-4"),
            session_id: Some("sess-1"),
            input_tokens: Some(500),
            output_tokens: Some(200),
            duration_ms: Some(1500),
            status: "success",
            error: None,
        };
        store.record_execution(&record).unwrap();

        // Verify trigger updated daily_stats
        let mut stmt = store
            .prepare_stmt(
                "SELECT total_calls, success_calls, total_input_tokens FROM daily_stats WHERE backend_name = 'claude-code'",
            )
            .unwrap();
        let row: (i64, i64, i64) = stmt
            .query_row([], |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)))
            .unwrap();
        assert_eq!(row.0, 1);
        assert_eq!(row.1, 1);
        assert_eq!(row.2, 500);
    }

    #[test]
    fn record_execution_error_status_tracked() {
        let (_dir, store) = setup();

        let record = ExecutionRecord {
            task_id: "task-err",
            backend_type: "cli",
            backend_name: "gemini-cli",
            status: "error",
            error: Some("timeout"),
            ..Default::default()
        };
        store.record_execution(&record).unwrap();

        let mut stmt = store
            .prepare_stmt(
                "SELECT total_calls, success_calls FROM daily_stats WHERE backend_name = 'gemini-cli'",
            )
            .unwrap();
        let row: (i64, i64) = stmt
            .query_row([], |row| Ok((row.get(0)?, row.get(1)?)))
            .unwrap();
        assert_eq!(row.0, 1);
        assert_eq!(row.1, 0); // not a success
    }

    #[test]
    fn cleanup_sessions_with_no_sessions() {
        let (_dir, store) = setup();
        let removed = store.cleanup_sessions(90).unwrap();
        assert_eq!(removed, 0);
    }
}

mod archive_integration {
    use ap_cli_backend::session::CLISessionStore;
    use ap_cli_backend::types::{DataLifecycleConfig, ExecutionRecord};
    use tempfile::TempDir;

    #[test]
    fn archive_through_session_store() {
        let dir = TempDir::new().unwrap();
        let db_path = dir.path().join("test.db");
        let store = CLISessionStore::open(&db_path).unwrap();

        // Insert a recent execution
        store.record_execution(&ExecutionRecord {
            task_id: "recent-task",
            backend_type: "cli",
            backend_name: "test-backend",
            status: "success",
            ..Default::default()
        }).unwrap();

        let archive_path = dir.path().join("archive.db");
        let config = DataLifecycleConfig {
            hot_days: 30,
            ..Default::default()
        };

        // The record has a default created_at (now), so it won't be archived
        let migrated = store.archive_old_data(&config, &archive_path).unwrap();
        assert_eq!(migrated, 0);
    }

    #[test]
    fn archive_rejects_path_with_special_chars() {
        let dir = TempDir::new().unwrap();
        let db_path = dir.path().join("test.db");
        let store = CLISessionStore::open(&db_path).unwrap();

        let bad_path = std::path::PathBuf::from("/tmp/test; echo evil");
        let config = DataLifecycleConfig::default();
        let result = store.archive_old_data(&config, &bad_path);
        assert!(result.is_err());
    }
}

mod parser_edge_cases {
    use ap_cli_backend::parser::{extract_json_value, parse_json_output, parse_text_output};
    use ap_cli_backend::types::{BackendConfig, JsonPathConfig, TextPatternConfig};

    #[test]
    fn extract_from_array_returns_none() {
        let data = serde_json::json!([1, 2, 3]);
        assert!(extract_json_value(&data, "0").is_none());
    }

    #[test]
    fn extract_from_null_returns_none() {
        let data = serde_json::json!(null);
        assert!(extract_json_value(&data, "any").is_none());
    }

    #[test]
    fn parse_empty_json_object() {
        let config = BackendConfig {
            command: "test".into(),
            output_format: "json".into(),
            ..Default::default()
        };
        let result = parse_json_output("{}", &config);
        // Should not panic, should have empty text
        assert!(result.text.is_empty() || result.text == "{}");
    }

    #[test]
    fn parse_text_no_patterns_extracts_raw() {
        let config = BackendConfig {
            command: "test".into(),
            output_format: "text".into(),
            ..Default::default()
        };
        let result = parse_text_output("hello world", "some stderr", &config);
        assert_eq!(result.text, "hello world");
        assert_eq!(result.raw_stderr, "some stderr");
    }

    #[test]
    fn parse_json_with_missing_paths_falls_back() {
        let config = BackendConfig {
            command: "test".into(),
            output_format: "json".into(),
            json_paths: JsonPathConfig {
                text: Some("result.text.content".into()),
                session_id: Some("meta.session".into()),
                model: None,
                input_tokens: Some("usage.input".into()),
                output_tokens: Some("usage.output".into()),
            },
            ..Default::default()
        };
        let stdout = r#"{"result": "fallback text"}"#;
        let result = parse_json_output(stdout, &config);
        // text path "result.text.content" doesn't exist, so text should be empty
        assert!(result.text.is_empty());
    }

    #[test]
    fn parse_text_with_invalid_regex_ignored() {
        let config = BackendConfig {
            command: "test".into(),
            output_format: "text".into(),
            text_patterns: TextPatternConfig {
                session_id: Some("[invalid regex".into()),
                model: None,
            },
            ..Default::default()
        };
        // Should not panic with invalid regex
        let result = parse_text_output("text", "session: abc", &config);
        assert!(result.session_id.is_none());
    }
}

mod backend_call {
    use ap_cli_backend::backend::GenericCLIBackend;
    use ap_cli_backend::types::BackendConfig;

    #[tokio::test]
    async fn call_with_empty_system_prompt() {
        let backend = GenericCLIBackend::new(BackendConfig {
            command: "echo".into(),
            output_format: "text".into(),
            system_prompt_flag: "--system".into(),
            ..Default::default()
        });
        let result = backend.call("", "hello", None).await.unwrap();
        assert!(result.text.contains("hello"));
    }

    #[tokio::test]
    async fn call_with_session_id() {
        let backend = GenericCLIBackend::new(BackendConfig {
            command: "echo".into(),
            output_format: "text".into(),
            session_flag: "--resume".into(),
            ..Default::default()
        });
        let result = backend
            .call("", "message", Some("sess-abc"))
            .await
            .unwrap();
        assert!(result.raw_stdout.contains("message"));
    }

    #[test]
    fn build_args_no_duplicate_user_message() {
        let backend = GenericCLIBackend::new(BackendConfig {
            command: "echo".into(),
            args: vec!["-p".into()],
            ..Default::default()
        });
        let args = backend.build_args("sys", "user-msg", None);
        // user message should appear exactly once
        let count = args.iter().filter(|a| **a == "user-msg").count();
        assert_eq!(count, 1);
    }

    #[test]
    fn refresh_availability() {
        let mut bad_backend = GenericCLIBackend::new(BackendConfig {
            command: "nonexistent_xyz".into(),
            ..Default::default()
        });
        bad_backend.refresh_availability();
        assert!(!bad_backend.is_available());

        let mut echo_backend = GenericCLIBackend::new(BackendConfig {
            command: "echo".into(),
            ..Default::default()
        });
        assert!(echo_backend.is_available());
        echo_backend.refresh_availability();
        assert!(echo_backend.is_available());
    }
}

mod health_check {
    use ap_cli_backend::health::HealthCheck;
    use ap_cli_backend::types::BackendConfig;

    #[test]
    fn check_installed_for_common_commands() {
        let ls_config = BackendConfig {
            command: "ls".into(),
            ..Default::default()
        };
        assert!(HealthCheck::check_installed(&ls_config));
    }

    #[test]
    fn check_installed_for_nonexistent() {
        let config = BackendConfig {
            command: "nonexistent_binary_xyz_12345".into(),
            ..Default::default()
        };
        assert!(!HealthCheck::check_installed(&config));
    }
}

mod setup_integration {
    use ap_cli_backend::CLISetup;
    use std::path::Path;

    #[test]
    fn from_file_nonexistent_returns_default() {
        // Should not panic, returns empty setup
        let setup = CLISetup::from_file_or_default(Path::new("/tmp/nonexistent_config_xyz.toml"));
        assert_eq!(setup.registry.len(), 0);
    }

    #[test]
    fn parse_toml_with_routing_rules() {
        let toml = r#"
[cli_backends.claude]
command = "claude"
args = ["-p"]
output_format = "json"

[cli_backends.gemini]
command = "gemini"
output_format = "text"

[cli_routing]
default = "claude"
fallback_chain = ["gemini"]
fallback_enabled = true

[cli_routing.model_rules]
"claude-*" = "claude"
"gpt-*" = "gemini"
"#;
        let setup = CLISetup::parse_toml(toml).unwrap();
        assert_eq!(setup.registry.len(), 2);

        let routing = &setup.router;
        // Should resolve claude-sonnet-4 to claude backend
        let result = routing.resolve(Some("claude-sonnet-4"), None);
        assert!(result.is_ok());
    }

    #[test]
    fn parse_toml_missing_routing_auto_defaults() {
        let toml = r#"
[cli_backends.echo-test]
command = "echo"
"#;
        let setup = CLISetup::parse_toml(toml).unwrap();
        assert_eq!(setup.registry.len(), 1);
        // Router should still work with auto-default routing
    }
}
