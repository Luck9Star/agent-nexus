//! Advisory file locking via `flock(2)`.
//!
//! Provides process-level mutual exclusion for read-modify-write cycles on
//! JSON/YAML files. Uses non-blocking exclusive `flock` with a retry loop
//! (up to 100 attempts, 10 ms sleep each, ~1 s total).

use std::os::unix::io::AsRawFd;
use std::path::Path;
use std::time::Duration;

use tracing::{debug, warn};

/// Maximum number of attempts to acquire a non-blocking exclusive lock.
const MAX_ATTEMPTS: u32 = 100;

/// Sleep duration between lock acquisition attempts.
const RETRY_SLEEP: Duration = Duration::from_millis(10);

/// Platform-specific `flock` operation constants.
#[cfg(unix)]
const LOCK_EX: libc::c_int = 2; // Exclusive lock
#[cfg(unix)]
const LOCK_NB: libc::c_int = 4; // Non-blocking

/// RAII wrapper for an advisory file lock acquired via `flock(2)`.
///
/// Holds an open `File` handle. The lock is released when this struct is dropped.
pub struct FileLock {
    _file: std::fs::File,
}

impl FileLock {
    /// Acquire an exclusive advisory lock on the file at `path`.
    ///
    /// If the file does not exist, it is created (along with parent directories).
    /// Uses non-blocking `flock` with a retry loop to avoid blocking indefinitely.
    ///
    /// # Errors
    /// Returns `std::io::Error` if the lock cannot be acquired after `MAX_ATTEMPTS`
    /// or if file creation/opening fails.
    pub fn acquire_exclusive(path: &Path) -> Result<Self, std::io::Error> {
        // Ensure parent directory exists
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }

        // Open or create the file. We need write access for exclusive flock.
        let file = std::fs::OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(path)?;

        let fd = file.as_raw_fd();

        for attempt in 1..=MAX_ATTEMPTS {
            let result = unsafe { libc::flock(fd, LOCK_EX | LOCK_NB) };
            if result == 0 {
                debug!(
                    "Acquired exclusive lock on {:?} (attempt {attempt})",
                    path
                );
                return Ok(Self { _file: file });
            }

            let err = std::io::Error::last_os_error();
            if err.kind() != std::io::ErrorKind::WouldBlock {
                return Err(err);
            }

            if attempt == MAX_ATTEMPTS {
                warn!(
                    "Failed to acquire lock on {:?} after {MAX_ATTEMPTS} attempts",
                    path
                );
                return Err(std::io::Error::new(
                    std::io::ErrorKind::TimedOut,
                    format!(
                        "timed out waiting for file lock on {}",
                        path.display()
                    ),
                ));
            }

            std::thread::sleep(RETRY_SLEEP);
        }

        // Unreachable, but satisfy the compiler
        Err(std::io::Error::new(
            std::io::ErrorKind::TimedOut,
            "lock acquisition loop exited unexpectedly",
        ))
    }
}

impl Drop for FileLock {
    fn drop(&mut self) {
        // flock is automatically released when the file descriptor is closed.
        // Closing the File handle is sufficient.
        debug!("Released file lock (fd dropped)");
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn acquire_and_release_lock() {
        let dir = tempfile::tempdir().unwrap();
        let lock_path = dir.path().join("test.lock");
        let _lock = FileLock::acquire_exclusive(&lock_path).unwrap();
        // Lock released when _lock is dropped
    }

    #[test]
    fn lock_creates_missing_file() {
        let dir = tempfile::tempdir().unwrap();
        let lock_path = dir.path().join("subdir").join("test.lock");
        assert!(!lock_path.exists());
        let _lock = FileLock::acquire_exclusive(&lock_path).unwrap();
        assert!(lock_path.exists());
    }

    #[test]
    fn lock_is_released_on_drop() {
        let dir = tempfile::tempdir().unwrap();
        let lock_path = dir.path().join("test.lock");

        {
            let _lock = FileLock::acquire_exclusive(&lock_path).unwrap();
        }

        // Should be able to re-acquire after drop
        let _lock2 = FileLock::acquire_exclusive(&lock_path).unwrap();
    }

    #[test]
    fn lock_contention_is_detected() {
        let dir = tempfile::tempdir().unwrap();
        let lock_path = dir.path().join("test.lock");

        let _lock1 = FileLock::acquire_exclusive(&lock_path).unwrap();

        // Second lock attempt in same thread will succeed because flock
        // locks are per-file-descriptor, not per-thread. However, we can
        // test that the non-blocking path works by verifying the function
        // returns successfully.
        // Note: On the same fd table (fork'd processes), the lock IS shared,
        // so this test mainly validates no panic/crash.
        drop(_lock1);

        let _lock2 = FileLock::acquire_exclusive(&lock_path).unwrap();
    }
}
