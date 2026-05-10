"""Entry point for running db-schema-analyzer as an MCP server."""

from agent_db_schema_analyzer.mcp_adapter import create_mcp_server

if __name__ == "__main__":
    server = create_mcp_server()
    server.run()
