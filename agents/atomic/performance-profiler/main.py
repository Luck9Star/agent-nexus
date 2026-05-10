"""Entry point for running performance-profiler as an MCP server."""

from agent_performance_profiler.mcp_adapter import create_mcp_server

if __name__ == "__main__":
    server = create_mcp_server()
    server.run()
