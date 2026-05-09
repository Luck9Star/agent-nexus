"""Entry point for running test-suite-generator as an MCP server."""

from agent_test_suite_generator.mcp_adapter import create_mcp_server

if __name__ == "__main__":
    server = create_mcp_server()
    server.run()
