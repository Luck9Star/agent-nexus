"""Entry point for running contract-analyzer as an MCP server."""

from agent_contract_analyzer.mcp_adapter import create_mcp_server


if __name__ == "__main__":
    server = create_mcp_server()
    server.run()
