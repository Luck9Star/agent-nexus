//! CLI-specific error types for structured error reporting.

use std::fmt;

/// Error category for machine-consumable output.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ErrorKind {
    Init,
    Config,
    Runtime,
    Install,
    Source,
    Evolution,
    Io,
    Other,
}

/// Typed CLI errors with structured context.
#[derive(Debug)]
pub enum CliError {
    Init(String),
    Config(String),
    Runtime(String),
    Install(String),
    Source(String),
    Evolution(String),
    Io(std::io::Error),
    Other(anyhow::Error),
}

impl CliError {
    /// Returns the error category for structured output.
    #[must_use] 
    pub fn kind(&self) -> ErrorKind {
        match self {
            Self::Init(_) => ErrorKind::Init,
            Self::Config(_) => ErrorKind::Config,
            Self::Runtime(_) => ErrorKind::Runtime,
            Self::Install(_) => ErrorKind::Install,
            Self::Source(_) => ErrorKind::Source,
            Self::Evolution(_) => ErrorKind::Evolution,
            Self::Io(_) => ErrorKind::Io,
            Self::Other(_) => ErrorKind::Other,
        }
    }

    /// Create an init error from a message.
    pub fn init(msg: impl fmt::Display) -> Self {
        Self::Init(msg.to_string())
    }

    /// Create a runtime error from a message.
    pub fn runtime(msg: impl fmt::Display) -> Self {
        Self::Runtime(msg.to_string())
    }

    /// Create a config error from a message.
    pub fn config(msg: impl fmt::Display) -> Self {
        Self::Config(msg.to_string())
    }

    /// Create an install error from a message.
    pub fn install(msg: impl fmt::Display) -> Self {
        Self::Install(msg.to_string())
    }
}

impl fmt::Display for CliError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Init(msg) => write!(f, "Initialization failed: {msg}"),
            Self::Config(msg) => write!(f, "Configuration error: {msg}"),
            Self::Runtime(msg) => write!(f, "Runtime error: {msg}"),
            Self::Install(msg) => write!(f, "Install failed: {msg}"),
            Self::Source(msg) => write!(f, "Source error: {msg}"),
            Self::Evolution(msg) => write!(f, "Evolution error: {msg}"),
            Self::Io(err) => write!(f, "IO error: {err}"),
            Self::Other(err) => write!(f, "{err}"),
        }
    }
}

impl std::error::Error for CliError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Io(err) => Some(err),
            Self::Other(err) => Some(err.as_ref()),
            _ => None,
        }
    }
}

impl From<std::io::Error> for CliError {
    fn from(err: std::io::Error) -> Self {
        Self::Io(err)
    }
}

impl From<anyhow::Error> for CliError {
    fn from(err: anyhow::Error) -> Self {
        Self::Other(err)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn error_variants_display_correctly() {
        assert_eq!(CliError::Init("no config".into()).to_string(), "Initialization failed: no config");
        assert_eq!(CliError::Runtime("not running".into()).to_string(), "Runtime error: not running");
        assert_eq!(CliError::Install("not found".into()).to_string(), "Install failed: not found");
    }

    #[test]
    fn kind_matches_variant() {
        assert_eq!(CliError::init("x").kind(), ErrorKind::Init);
        assert_eq!(CliError::config("x").kind(), ErrorKind::Config);
        assert_eq!(CliError::runtime("x").kind(), ErrorKind::Runtime);
        assert_eq!(CliError::install("x").kind(), ErrorKind::Install);
        assert_eq!(CliError::from(std::io::Error::new(std::io::ErrorKind::NotFound, "x")).kind(), ErrorKind::Io);
        assert_eq!(CliError::from(anyhow::anyhow!("x")).kind(), ErrorKind::Other);
    }

    #[test]
    fn io_error_converts() {
        let err = CliError::from(std::io::Error::new(std::io::ErrorKind::NotFound, "missing"));
        assert!(err.to_string().contains("missing"));
    }

    #[test]
    fn anyhow_error_converts() {
        let err = CliError::from(anyhow::anyhow!("something went wrong"));
        assert!(err.to_string().contains("something went wrong"));
    }

    #[test]
    fn helper_constructors() {
        assert!(matches!(CliError::init("bad dir"), CliError::Init(_)));
        assert!(matches!(CliError::runtime("dead"), CliError::Runtime(_)));
        assert!(matches!(CliError::config("bad"), CliError::Config(_)));
        assert!(matches!(CliError::install("fail"), CliError::Install(_)));
    }
}
