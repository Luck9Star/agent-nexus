//! MCP Client trait definition.
//!
//! Defines the interface for MCP tool invocation without depending on rmcp
//! (which is immature). Consumers should implement this trait for their
//! specific MCP transport (stdio, SSE, etc.).

use serde_json::Value as JsonValue;
use std::future::Future;
use std::pin::Pin;

// ---------------------------------------------------------------------------
// ToolInfo
// ---------------------------------------------------------------------------

/// Metadata about an MCP tool offered by an agent.
#[derive(Debug, Clone, PartialEq)]
pub struct ToolInfo {
    pub name: String,
    pub description: Option<String>,
    pub input_schema: Option<JsonValue>,
}

// ---------------------------------------------------------------------------
// McpError
// ---------------------------------------------------------------------------

/// Errors that can occur during MCP client operations.
#[derive(Debug, thiserror::Error)]
pub enum McpError {
    /// Failed to connect to the MCP server.
    #[error("MCP connection failed: {0}")]
    ConnectionFailed(String),
    /// The requested tool was not found.
    #[error("Tool not found: {0}")]
    ToolNotFound(String),
    /// Tool invocation returned an error.
    #[error("MCP tool execution failed: {0}")]
    ExecutionFailed(String),
    /// Serialization/deserialization error.
    #[error("MCP serialization error: {0}")]
    Serde(#[from] serde_json::Error),
    /// IO error during transport.
    #[error("MCP IO error: {0}")]
    Io(#[from] std::io::Error),
}

// ---------------------------------------------------------------------------
// McpClient trait (async via Pin<Box<dyn Future>>)
// ---------------------------------------------------------------------------

/// Trait for MCP client implementations.
///
/// Implementations handle the transport layer (stdio, SSE, etc.) while
/// the trait provides a uniform interface for listing and calling tools.
///
/// Uses `Pin<Box<dyn Future>>` return types to remain dyn-compatible
/// (object-safe) for use as `Box<dyn McpClient>`.
pub trait McpClient: Send + Sync {
    /// List all tools available on the MCP server.
    fn list_tools(&self) -> Pin<Box<dyn Future<Output = Result<Vec<ToolInfo>, McpError>> + Send + '_>>;

    /// Call a tool by name with the given arguments.
    fn call_tool(
        &self,
        name: &str,
        arguments: JsonValue,
    ) -> Pin<Box<dyn Future<Output = Result<JsonValue, McpError>> + Send + '_>>;

    /// Shut down the client and release any held resources (subprocesses, connections, etc.).
    ///
    /// Default implementation is a no-op so that existing implementors are not broken.
    fn shutdown(&mut self) -> Pin<Box<dyn Future<Output = ()> + Send + '_>> {
        Box::pin(async {})
    }
}

// ---------------------------------------------------------------------------
// NoopMcpClient (testing stub)
// ---------------------------------------------------------------------------

/// A no-op MCP client for testing that returns empty results.
pub struct NoopMcpClient;

impl McpClient for NoopMcpClient {
    fn list_tools(&self) -> Pin<Box<dyn Future<Output = Result<Vec<ToolInfo>, McpError>> + Send + '_>> {
        Box::pin(async { Ok(Vec::new()) })
    }

    fn call_tool(
        &self,
        name: &str,
        _arguments: JsonValue,
    ) -> Pin<Box<dyn Future<Output = Result<JsonValue, McpError>> + Send + '_>> {
        let name = name.to_string();
        Box::pin(async { Err(McpError::ToolNotFound(name)) })
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn noop_client_lists_no_tools() {
        let client = NoopMcpClient;
        let tools = client.list_tools().await.unwrap();
        assert!(tools.is_empty());
    }

    #[tokio::test]
    async fn noop_client_returns_tool_not_found() {
        let client = NoopMcpClient;
        let result = client.call_tool("any_tool", serde_json::json!({})).await;
        assert!(result.is_err());
        match result.unwrap_err() {
            McpError::ToolNotFound(name) => assert_eq!(name, "any_tool"),
            other => panic!("expected ToolNotFound, got: {other}"),
        }
    }

    #[test]
    fn mcp_error_display() {
        let err = McpError::ConnectionFailed("timeout".to_string());
        assert_eq!(format!("{err}"), "MCP connection failed: timeout");

        let err = McpError::ToolNotFound("my_tool".to_string());
        assert_eq!(format!("{err}"), "Tool not found: my_tool");

        let err = McpError::ExecutionFailed("crash".to_string());
        assert_eq!(format!("{err}"), "MCP tool execution failed: crash");
    }

    #[test]
    fn tool_info_equality() {
        let t1 = ToolInfo {
            name: "review".to_string(),
            description: Some("reviews code".to_string()),
            input_schema: Some(serde_json::json!({"type": "object"})),
        };
        let t2 = t1.clone();
        assert_eq!(t1, t2);
    }

    #[test]
    fn mcp_error_from_json_error() {
        let json_err = serde_json::from_str::<serde_json::Value>("not json");
        let mcp_err: McpError = json_err.unwrap_err().into();
        match mcp_err {
            McpError::Serde(_) => {} // expected
            other => panic!("expected Serde, got: {other}"),
        }
    }

    #[test]
    fn mcp_error_source_chain() {
        use std::error::Error;
        let io_err = std::io::Error::new(std::io::ErrorKind::BrokenPipe, "pipe broke");
        let mcp_err: McpError = io_err.into();
        assert!(mcp_err.source().is_some());
    }

    #[tokio::test]
    async fn dyn_mcp_client_works() {
        let client: Box<dyn McpClient> = Box::new(NoopMcpClient);
        let tools = client.list_tools().await.unwrap();
        assert!(tools.is_empty());
    }

    // --- McpError thiserror migration tests ---

    #[test]
    fn mcp_error_serde_displays_correctly() {
        let json_err = serde_json::from_str::<serde_json::Value>("not valid json");
        let mcp_err = McpError::Serde(json_err.unwrap_err());
        let msg = format!("{mcp_err}");
        assert!(
            msg.starts_with("MCP serialization error:"),
            "Serde variant should display with 'MCP serialization error:' prefix, got: {msg}"
        );
    }

    #[test]
    fn mcp_error_io_displays_correctly() {
        let io_err = std::io::Error::new(std::io::ErrorKind::BrokenPipe, "pipe broke");
        let mcp_err = McpError::Io(io_err);
        let msg = format!("{mcp_err}");
        assert!(
            msg.starts_with("MCP IO error:"),
            "Io variant should display with 'MCP IO error:' prefix, got: {msg}"
        );
    }

    #[test]
    fn mcp_error_from_serde_json_error() {
        let json_err = serde_json::from_str::<serde_json::Value>("{bad").unwrap_err();
        let mcp_err: McpError = json_err.into();
        match mcp_err {
            McpError::Serde(_) => {} // expected
            other => panic!("expected Serde variant, got: {other}"),
        }
    }

    #[test]
    fn mcp_error_from_std_io_error() {
        let io_err = std::io::Error::new(std::io::ErrorKind::UnexpectedEof, "unexpected eof");
        let mcp_err: McpError = io_err.into();
        match mcp_err {
            McpError::Io(e) => assert_eq!(e.kind(), std::io::ErrorKind::UnexpectedEof),
            other => panic!("expected Io variant, got: {other}"),
        }
    }

    #[test]
    fn mcp_error_source_chain_for_io() {
        use std::error::Error;
        let io_err = std::io::Error::new(std::io::ErrorKind::BrokenPipe, "pipe broke");
        let mcp_err: McpError = io_err.into();
        assert!(mcp_err.source().is_some(), "Io variant should have a source");
    }

    #[test]
    fn mcp_error_source_chain_for_serde() {
        use std::error::Error;
        let json_err = serde_json::from_str::<serde_json::Value>("bad").unwrap_err();
        let mcp_err: McpError = json_err.into();
        assert!(mcp_err.source().is_some(), "Serde variant should have a source");
    }
}
