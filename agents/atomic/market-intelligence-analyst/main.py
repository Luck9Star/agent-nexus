"""Entry point for running market-intelligence-analyst as an MCP server."""

from agent_market_intelligence_analyst.mcp_adapter import create_mcp_server


if __name__ == "__main__":
    server = create_mcp_server()
    server.run()
