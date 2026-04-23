//! IPC lock registry: per-agent Mutex with FIFO eviction.
//!
//! Python source: Python uses `dict[str, asyncio.Lock]` per agent.

use std::collections::VecDeque;
use std::sync::{Arc, Mutex};

const MAX_LOCKS: usize = 1000;

pub struct IpcLockRegistry {
    locks: dashmap::DashMap<String, Arc<Mutex<()>>>,
    order: Mutex<VecDeque<String>>,
}

impl IpcLockRegistry {
    pub fn new() -> Self {
        Self {
            locks: dashmap::DashMap::new(),
            order: Mutex::new(VecDeque::new()),
        }
    }

    /// Get or create a lock for the given agent.
    /// Evicts the oldest lock if over the limit.
    /// Uses DashMap::entry() to avoid TOCTOU race between get() and insert().
    pub fn get_or_create(&self, agent_id: &str) -> Arc<Mutex<()>> {
        use dashmap::mapref::entry::Entry;

        match self.locks.entry(agent_id.to_string()) {
            Entry::Occupied(e) => Arc::clone(e.get()),
            Entry::Vacant(e) => {
                let lock = Arc::new(Mutex::new(()));
                let cloned = Arc::clone(&lock);
                e.insert(lock);

                // Evict oldest if over limit
                let mut order = self.order.lock().unwrap_or_else(|e| e.into_inner());
                order.push_back(agent_id.to_string());
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
    pub fn len(&self) -> usize {
        self.locks.len()
    }

    /// Returns true if there are no active locks.
    pub fn is_empty(&self) -> bool {
        self.locks.is_empty()
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
        // agent-0 should have been evicted
        assert!(registry.locks.get("agent-0").is_none());
        // agent-1 should still exist
        assert!(registry.locks.get("agent-1").is_some());
    }
}
