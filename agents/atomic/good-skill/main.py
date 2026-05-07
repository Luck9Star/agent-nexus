"""Entry point for running good-skill as an MCP server."""

from agent_good_skill.mcp_adapter import create_mcp_server

if __name__ == "__main__":
    server = create_mcp_server()
    server.run()
