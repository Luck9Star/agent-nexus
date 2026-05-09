"""Entry point for running accessibility-auditor as an MCP server."""

from agent_accessibility_auditor.mcp_adapter import create_mcp_server

if __name__ == "__main__":
    server = create_mcp_server()
    server.run()
