"""Entry point for running security-scanner as an MCP server."""

from agent_security_scanner.mcp_adapter import create_mcp_server


if __name__ == "__main__":
    server = create_mcp_server()
    server.run()
