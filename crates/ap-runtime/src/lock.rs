//! Per-agent lock registry with FIFO eviction.
//!
//! Re-exports [`ap_core::orchestration::IpcLockRegistry`] as [`LockRegistry`]
//! to eliminate code duplication. The canonical implementation lives in
//! `ap-core/src/orchestration/ipc_lock.rs`.

/// Registry of per-agent locks with bounded capacity and FIFO eviction.
///
/// Thin re-export of `ap_core::orchestration::IpcLockRegistry` under the
/// name `LockRegistry` to preserve the existing public API of this crate.
pub type LockRegistry = ap_core::orchestration::IpcLockRegistry;

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;
    use std::thread;

    #[test]
    fn same_agent_gets_same_lock() {
        let registry = LockRegistry::new();
        let lock1 = registry.get_or_create("agent-1");
        let lock2 = registry.get_or_create("agent-1");
        assert!(
            Arc::ptr_eq(&lock1, &lock2),
            "Same agent should get the same lock"
        );
    }

    #[test]
    fn different_agents_get_different_locks() {
        let registry = LockRegistry::new();
        let lock1 = registry.get_or_create("agent-1");
        let lock2 = registry.get_or_create("agent-2");
        assert!(
            !Arc::ptr_eq(&lock1, &lock2),
            "Different agents should get different locks"
        );
    }

    #[test]
    fn len_and_is_empty() {
        let registry = LockRegistry::new();
        assert!(registry.is_empty());
        assert_eq!(registry.len(), 0);

        registry.get_or_create("a");
        assert!(!registry.is_empty());
        assert_eq!(registry.len(), 1);

        registry.get_or_create("b");
        assert_eq!(registry.len(), 2);

        // Same agent doesn't increase count
        registry.get_or_create("a");
        assert_eq!(registry.len(), 2);
    }

    #[test]
    fn concurrent_access() {
        let registry = Arc::new(LockRegistry::new());
        let mut handles = Vec::new();

        for i in 0..100 {
            let reg = Arc::clone(&registry);
            handles.push(thread::spawn(move || {
                let id = format!("agent-{i}");
                let lock = reg.get_or_create(&id);
                // Acquire the mutex to verify it works
                let _guard = lock.lock().unwrap();
            }));
        }

        for handle in handles {
            handle.join().unwrap();
        }

        assert_eq!(registry.len(), 100);
    }
}
