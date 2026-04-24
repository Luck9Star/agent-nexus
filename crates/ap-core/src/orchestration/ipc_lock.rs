//! IPC lock registry: per-agent Mutex with reference-count-aware eviction.
//!
//! Python source: Python uses `dict[str, asyncio.Lock]` per agent.

use std::sync::{Arc, Mutex};

const MAX_LOCKS: usize = 1000;

pub struct IpcLockRegistry {
    locks: Mutex<Vec<(String, Arc<Mutex<()>>)>>,
}

impl IpcLockRegistry {
    #[must_use]
    pub fn new() -> Self {
        Self {
            locks: Mutex::new(Vec::new()),
        }
    }

    /// Get or create a lock for the given agent.
    /// Evicts entries whose Arc has no external references (strong_count == 1).
    pub fn get_or_create(&self, agent_id: &str) -> Arc<Mutex<()>> {
        let mut locks = self.locks.lock().unwrap_or_else(std::sync::PoisonError::into_inner);

        // Fast path: find existing
        if let Some(idx) = locks.iter().position(|(id, _)| id == agent_id) {
            return Arc::clone(&locks[idx].1);
        }

        // Enforce limit: evict oldest unreferenced entries one at a time
        while locks.len() >= MAX_LOCKS {
            let idx = locks.iter().position(|(_, arc)| Arc::strong_count(arc) <= 1);
            match idx {
                Some(i) => { locks.remove(i); }
                None => break, // All entries are actively referenced
            }
        }

        let lock = Arc::new(Mutex::new(()));
        let cloned = Arc::clone(&lock);
        locks.push((agent_id.to_string(), lock));
        cloned
    }

    /// Returns the current number of active locks.
    pub fn len(&self) -> usize {
        self.locks.lock().unwrap_or_else(std::sync::PoisonError::into_inner).len()
    }

    /// Returns true if there are no active locks.
    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
}

impl Default for IpcLockRegistry {
    fn default() -> Self {
        Self::new()
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn get_or_create_returns_same_lock() {
        let registry = IpcLockRegistry::new();
        let lock1 = registry.get_or_create("agent-1");
        let lock2 = registry.get_or_create("agent-1");
        // Both should point to the same Mutex
        assert!(
            Arc::ptr_eq(&lock1, &lock2),
            "Same agent should get the same lock"
        );
    }

    #[test]
    fn different_agents_get_different_locks() {
        let registry = IpcLockRegistry::new();
        let lock1 = registry.get_or_create("agent-1");
        let lock2 = registry.get_or_create("agent-2");
        assert!(
            !Arc::ptr_eq(&lock1, &lock2),
            "Different agents should get different locks"
        );
    }

    #[test]
    fn eviction_removes_oldest() {
        let registry = IpcLockRegistry::new();
        // Fill up to MAX_LOCKS + 1
        for i in 0..=MAX_LOCKS {
            registry.get_or_create(&format!("agent-{i}"));
        }
        // agent-0 should have been evicted (no external reference held)
        let locks = registry.locks.lock().unwrap();
        assert!(locks.iter().all(|(id, _)| id != "agent-0"));
        // agent-1 should still exist
        assert!(locks.iter().any(|(id, _)| id == "agent-1"));
    }
}
