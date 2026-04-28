"""Entry point for running code-reviewer as an MCP server."""

from agent_code_reviewer.mcp_adapter import create_mcp_server


if __name__ == "__main__":
    server = create_mcp_server()
    server.run()
