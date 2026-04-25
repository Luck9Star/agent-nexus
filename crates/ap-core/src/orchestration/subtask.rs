//! Subtask execution with timeout, retry, and bounded parallelism.
//!
//! Used by the `PlatformRouter` to run agent subtasks with configurable
//! fault tolerance. The retry pattern uses a factory closure (coroutine
//! factory pattern) because Rust futures are not `Clone` and cannot be
//! polled multiple times.

use std::future::Future;
use std::pin::Pin;
use std::sync::Arc;
use std::time::Duration;

use serde::{Deserialize, Serialize};
use tokio::sync::Semaphore;

/// Factory closure type for creating boxed, sendable async subtasks.
pub type FactoryFn<T> = Box<dyn Fn() -> Pin<Box<dyn Future<Output = Result<T, SubtaskError>> + Send>> + Send>;

/// Configuration for subtask execution.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SubtaskConfig {
    /// Per-subtask timeout in seconds. Default: 120.
    #[serde(default = "default_timeout")]
    pub timeout_seconds: u64,
    /// Maximum retry attempts per subtask. Default: 2.
    #[serde(default = "default_max_retries")]
    pub max_retries: u32,
    /// Maximum number of subtasks running concurrently. Default: 3.
    #[serde(default = "default_max_parallel")]
    pub max_parallel: usize,
}

fn default_timeout() -> u64 {
    120
}
fn default_max_retries() -> u32 {
    2
}
fn default_max_parallel() -> usize {
    3
}

impl Default for SubtaskConfig {
    fn default() -> Self {
        Self {
            timeout_seconds: default_timeout(),
            max_retries: default_max_retries(),
            max_parallel: default_max_parallel(),
        }
    }
}

/// Errors that can occur during subtask execution.
#[derive(Debug, thiserror::Error)]
pub enum SubtaskError {
    #[error("Subtask timed out after {0:?}")]
    Timeout(Duration),
    #[error("Subtask failed after {attempts} attempts: {last_error}")]
    Exhausted {
        attempts: u32,
        last_error: String,
    },
    #[error("Subtask execution error: {0}")]
    Execution(String),
    /// A parallel task panicked. Callers can distinguish panics from other
    /// execution errors using `matches!(err, SubtaskError::Panicked { .. })`.
    #[error("Subtask panicked: {message}")]
    Panicked { message: String },
}

/// Controller for executing subtasks with fault-tolerance policies.
pub struct SubtaskController {
    config: SubtaskConfig,
}

impl SubtaskController {
    pub fn new(config: SubtaskConfig) -> Self {
        Self { config }
    }

    /// Read-only access to the subtask configuration.
    pub fn config(&self) -> &SubtaskConfig {
        &self.config
    }

    /// Run a future with a timeout.
    ///
    /// # Errors
    /// Returns `SubtaskError::Timeout` if the future doesn't complete in time.
    pub async fn run_with_timeout<F, T>(&self, fut: F) -> Result<T, SubtaskError>
    where
        F: Future<Output = Result<T, SubtaskError>>,
    {
        let timeout = Duration::from_secs(self.config.timeout_seconds);
        tokio::time::timeout(timeout, fut)
            .await
            .map_err(|_| SubtaskError::Timeout(timeout))?
    }

    /// Run a future with retry. Takes a factory closure that produces a fresh
    /// future for each attempt (Rust futures are not `Clone`).
    ///
    /// Uses exponential backoff: `100ms * (attempt + 1)`.
    ///
    /// # Errors
    /// Returns `SubtaskError::Exhausted` if all attempts fail.
    pub async fn run_with_retry<F, Fut, T>(
        &self,
        factory: F,
    ) -> Result<T, SubtaskError>
    where
        F: Fn() -> Fut,
        Fut: Future<Output = Result<T, SubtaskError>>,
    {
        let mut last_err = String::new();
        for attempt in 0..=self.config.max_retries {
            let fut = factory();
            match fut.await {
                Ok(val) => return Ok(val),
                Err(e) => {
                    last_err = e.to_string();
                    if attempt < self.config.max_retries {
                        let backoff = Duration::from_millis(100 * (u64::from(attempt) + 1));
                        tokio::time::sleep(backoff).await;
                    }
                }
            }
        }
        Err(SubtaskError::Exhausted {
            attempts: self.config.max_retries + 1,
            last_error: last_err,
        })
    }

    /// Run multiple factories in parallel with bounded concurrency.
    ///
    /// Does NOT short-circuit on first failure — all tasks are attempted
    /// and individual results are returned. This matches the Python behavior
    /// where partial results are valuable for debugging.
    ///
    /// # Arguments
    /// * `factories` - Closures that produce boxed futures. Each is called exactly once.
    pub async fn run_parallel<T>(
        &self,
        factories: Vec<FactoryFn<T>>,
    ) -> Vec<Result<T, SubtaskError>>
    where
        T: Send + 'static,
    {
        let semaphore = Arc::new(Semaphore::new(self.config.max_parallel));
        let mut handles = Vec::with_capacity(factories.len());

        for factory in factories {
            let sem = semaphore.clone();
            let timeout = Duration::from_secs(self.config.timeout_seconds);
            let handle = tokio::spawn(async move {
                let _permit = sem.acquire().await.map_err(|_| SubtaskError::Panicked {
                    message: "semaphore closed".into(),
                })?;
                tokio::time::timeout(timeout, factory())
                    .await
                    .map_err(|_| SubtaskError::Timeout(timeout))?
            });
            handles.push(handle);
        }

        let mut results = Vec::with_capacity(handles.len());
        for handle in handles {
            match handle.await {
                Ok(r) => results.push(r),
                Err(e) => results.push(Err(SubtaskError::Panicked {
                    message: e.to_string(),
                })),
            }
        }
        results
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn timeout_returns_ok_when_fast() {
        let ctrl = SubtaskController::new(SubtaskConfig {
            timeout_seconds: 5,
            ..Default::default()
        });
        let result = ctrl
            .run_with_timeout(async { Ok::<_, SubtaskError>(42) })
            .await;
        assert_eq!(result.unwrap(), 42);
    }

    #[tokio::test]
    async fn timeout_returns_err_when_slow() {
        let ctrl = SubtaskController::new(SubtaskConfig {
            timeout_seconds: 0,
            ..Default::default()
        });
        let result = ctrl
            .run_with_timeout(async {
                tokio::time::sleep(Duration::from_secs(10)).await;
                Ok::<_, SubtaskError>(42)
            })
            .await;
        assert!(result.is_err());
    }

    #[tokio::test]
    async fn retry_succeeds_on_second_attempt() {
        let ctrl = SubtaskController::new(SubtaskConfig {
            max_retries: 2,
            ..Default::default()
        });
        let attempts = Arc::new(std::sync::atomic::AtomicU32::new(0));
        let attempts_clone = attempts.clone();
        let result = ctrl
            .run_with_retry(move || {
                let a = attempts_clone.clone();
                async move {
                    let n = a.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                    if n == 0 {
                        Err(SubtaskError::Execution("first fail".into()))
                    } else {
                        Ok("success")
                    }
                }
            })
            .await;
        assert_eq!(result.unwrap(), "success");
        assert_eq!(attempts.load(std::sync::atomic::Ordering::SeqCst), 2);
    }

    #[tokio::test]
    async fn retry_exhausted_when_all_fail() {
        let ctrl = SubtaskController::new(SubtaskConfig {
            max_retries: 1,
            ..Default::default()
        });
        let result = ctrl
            .run_with_retry(|| async {
                Err::<(), _>(SubtaskError::Execution("always fail".into()))
            })
            .await;
        assert!(matches!(result, Err(SubtaskError::Exhausted { .. })));
    }

    #[tokio::test]
    async fn parallel_runs_all_tasks() {
        let ctrl = SubtaskController::new(SubtaskConfig {
            max_parallel: 2,
            timeout_seconds: 5,
            ..Default::default()
        });
        let results = ctrl
            .run_parallel(vec![
                Box::new(|| Box::pin(async { Ok::<_, SubtaskError>(1) }) as Pin<Box<dyn Future<Output = Result<i32, SubtaskError>> + Send>>),
                Box::new(|| Box::pin(async { Ok::<_, SubtaskError>(2) })),
                Box::new(|| Box::pin(async { Ok::<_, SubtaskError>(3) })),
            ])
            .await;
        let values: Vec<i32> = results.into_iter().map(|r| r.unwrap()).collect();
        assert_eq!(values, vec![1, 2, 3]);
    }

    #[tokio::test]
    async fn parallel_does_not_short_circuit_on_failure() {
        let ctrl = SubtaskController::new(SubtaskConfig {
            max_parallel: 3,
            timeout_seconds: 5,
            ..Default::default()
        });
        let results = ctrl
            .run_parallel::<&str>(vec![
                Box::new(|| Box::pin(async { Ok::<_, SubtaskError>("ok") })),
                Box::new(|| Box::pin(async { Err(SubtaskError::Execution("fail".into())) })),
                Box::new(|| Box::pin(async { Ok::<_, SubtaskError>("also ok") })),
            ])
            .await;
        assert_eq!(results.len(), 3);
        assert!(results[0].is_ok());
        assert!(results[1].is_err());
        assert!(results[2].is_ok());
    }
}
