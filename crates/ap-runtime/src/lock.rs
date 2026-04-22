//! Per-agent lock registry with FIFO eviction.
//!
//! Uses DashMap for concurrent access and a VecDeque to track insertion
//! order for FIFO eviction when the lock count exceeds MAX_LOCKS.

use dashmap::mapref::entry::Entry;
use std::collections::VecDeque;
use std::sync::{Arc, Mutex};

/// Maximum number of concurrent agent locks before eviction kicks in.
const MAX_LOCKS: usize = 1000;

// ---------------------------------------------------------------------------
// LockRegistry
// ---------------------------------------------------------------------------

/// Registry of per-agent locks with bounded capacity and FIFO eviction.
pub struct LockRegistry {
    locks: dashmap::DashMap<String, Arc<Mutex<()>>>,
    order: Mutex<VecDeque<String>>,
}

impl LockRegistry {
    /// Create a new empty registry.
    pub fn new() -> Self {
        Self {
            locks: dashmap::DashMap::new(),
            order: Mutex::new(VecDeque::new()),
        }
    }

    /// Get or create a lock for the given agent ID.
    ///
    /// If the agent already has a lock, returns a clone of the existing Arc.
    /// If the agent is new and the registry is at capacity, evicts the oldest entry.
    ///
    /// Uses DashMap::entry() to avoid TOCTOU race between get() and insert().
    pub fn get_or_create(&self, agent_id: &str) -> Arc<Mutex<()>> {
        match self.locks.entry(agent_id.to_string()) {
            Entry::Occupied(e) => Arc::clone(e.get()),
            Entry::Vacant(e) => {
                let lock = Arc::new(Mutex::new(()));
                let cloned = Arc::clone(&lock);
                e.insert(lock);

                // Track insertion order
                let mut order = self.order.lock().unwrap();
                order.push_back(agent_id.to_string());

                // Evict oldest if over limit
                if order.len() > MAX_LOCKS {
                    if let Some(old_id) = order.pop_front() {
                        self.locks.remove(&old_id);
                    }
                }

                cloned
            }
        }
    }

    /// Returns the current number of active locks.
    #[allow(dead_code)]
    pub fn len(&self) -> usize {
        self.locks.len()
    }

    /// Returns true if there are no active locks.
    #[allow(dead_code)]
    pub fn is_empty(&self) -> bool {
        self.locks.is_empty()
    }
}

impl Default for LockRegistry {
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
    fn eviction_removes_oldest() {
        let registry = LockRegistry::new();
        // Fill up to MAX_LOCKS + 1
        for i in 0..=MAX_LOCKS {
            registry.get_or_create(&format!("agent-{i}"));
        }
        // agent-0 should have been evicted
        assert!(registry.locks.get("agent-0").is_none());
        // agent-1 should still exist
        assert!(registry.locks.get("agent-1").is_some());
        // Latest agent should still exist
        assert!(registry.locks.get(&format!("agent-{MAX_LOCKS}")).is_some());
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
