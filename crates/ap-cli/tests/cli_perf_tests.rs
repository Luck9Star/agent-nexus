//! Performance benchmarks for ap-cli critical paths.
//!
//! Uses std::time::Instant only — no external benchmark dependencies.
//! Measures CLI cold/warm start wall time using assert_cmd.

use std::time::Instant;

use assert_cmd::Command;

// ══════════════════════════════════════════════════════════════════════════
// Bench 1: CLI cold start (single invocation)
// ══════════════════════════════════════════════════════════════════════════

#[test]
fn bench_cli_version_cold_start() {
    let iterations = 10u64;

    // Warm up: run once first
    Command::cargo_bin("agent-nexus")
        .unwrap()
        .arg("--version")
        .assert()
        .success();

    let start = Instant::now();
    for _ in 0..iterations {
        let output = Command::cargo_bin("agent-nexus")
            .unwrap()
            .arg("--version")
            .output()
            .expect("failed to run agent-nexus --version");
        assert!(output.status.success());
    }
    let elapsed = start.elapsed();
    let avg = elapsed / iterations as u32;

    println!(
        "[bench_cli_version_cold_start] {} runs in {:?}",
        iterations, elapsed
    );
    println!("[bench_cli_version_cold_start] avg per run: {:?}", avg);

    // Target: < 100ms per run. With 5x headroom: 500ms.
    assert!(
        avg.as_millis() < 500,
        "CLI --version too slow: {:?} (target < 100ms, headroom < 500ms)",
        avg
    );
}

// ══════════════════════════════════════════════════════════════════════════
// Bench 2: CLI warm start — multiple sequential runs
// ══════════════════════════════════════════════════════════════════════════

#[test]
fn bench_cli_help_warm_start() {
    let iterations = 10u64;

    let mut times = Vec::with_capacity(iterations as usize);

    // Warm up
    Command::cargo_bin("agent-nexus")
        .unwrap()
        .arg("--help")
        .assert()
        .success();

    for _ in 0..iterations {
        let start = Instant::now();
        let output = Command::cargo_bin("agent-nexus")
            .unwrap()
            .arg("--help")
            .output()
            .expect("failed to run agent-nexus --help");
        let elapsed = start.elapsed();
        assert!(output.status.success());
        times.push(elapsed);
    }

    let total: std::time::Duration = times.iter().sum();
    let avg = total / iterations as u32;
    let fastest = times.iter().min().unwrap();
    let slowest = times.iter().max().unwrap();

    println!(
        "[bench_cli_help_warm_start] {} runs: total={:?}, avg={:?}, fastest={:?}, slowest={:?}",
        iterations, total, avg, fastest, slowest
    );

    // Target: < 100ms per run. With 5x headroom: 500ms.
    assert!(
        avg.as_millis() < 500,
        "CLI --help avg too slow: {:?} (target < 100ms, headroom < 500ms)",
        avg
    );
}

// ══════════════════════════════════════════════════════════════════════════
// Bench 3: CLI init command (disk I/O involved)
// ══════════════════════════════════════════════════════════════════════════

#[test]
fn bench_cli_init_command() {
    let iterations = 10u64;

    let mut times = Vec::with_capacity(iterations as usize);

    for _ in 0..iterations {
        let dir = tempfile::tempdir().unwrap();

        let start = Instant::now();
        let output = Command::cargo_bin("agent-nexus")
            .unwrap()
            .args(["init", "--dir", dir.path().to_str().unwrap()])
            .output()
            .expect("failed to run agent-nexus init");
        let elapsed = start.elapsed();

        assert!(output.status.success());
        assert!(dir.path().join("config.toml").exists());
        times.push(elapsed);
    }

    let total: std::time::Duration = times.iter().sum();
    let avg = total / iterations as u32;
    let fastest = times.iter().min().unwrap();
    let slowest = times.iter().max().unwrap();

    println!(
        "[bench_cli_init_command] {} runs: total={:?}, avg={:?}, fastest={:?}, slowest={:?}",
        iterations, total, avg, fastest, slowest
    );

    // Target: < 200ms per init. With 5x headroom: 1000ms.
    assert!(
        avg.as_millis() < 1000,
        "CLI init avg too slow: {:?} (target < 200ms, headroom < 1000ms)",
        avg
    );
}
