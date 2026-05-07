"""Entry point for running doc-filler as an MCP server."""

from agent_doc_filler.mcp_adapter import create_mcp_server

if __name__ == "__main__":
    server = create_mcp_server()
    server.run()
