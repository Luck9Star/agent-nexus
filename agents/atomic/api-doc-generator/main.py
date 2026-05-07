"""Entry point for running api-doc-generator as an MCP server."""

from agent_api_doc_generator.mcp_adapter import create_mcp_server

if __name__ == "__main__":
    server = create_mcp_server()
    server.run()
