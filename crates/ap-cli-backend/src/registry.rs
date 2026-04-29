//! CLIBackendRegistry — backend discovery and health check.

use crate::backend::GenericCLIBackend;
use std::collections::HashMap;
use std::sync::Arc;

#[derive(Clone)]
pub struct CLIBackendRegistry {
    backends: HashMap<String, Arc<GenericCLIBackend>>,
}

impl Default for CLIBackendRegistry {
    fn default() -> Self {
        Self::new()
    }
}

impl CLIBackendRegistry {
    pub fn new() -> Self {
        Self { backends: HashMap::new() }
    }

    pub fn register(&mut self, name: String, backend: GenericCLIBackend) {
        self.backends.insert(name, Arc::new(backend));
    }

    pub fn get(&self, name: &str) -> Result<Arc<GenericCLIBackend>, String> {
        self.backends.get(name)
            .cloned()
            .ok_or_else(|| format!("CLI backend '{}' not registered", name))
    }

    pub fn available_backends(&self) -> Vec<Arc<GenericCLIBackend>> {
        self.backends.values()
            .filter(|b| b.is_available())
            .cloned()
            .collect()
    }

    pub fn len(&self) -> usize {
        self.backends.len()
    }
}
