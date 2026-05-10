"""Entry point for running data-pipeline-validator as an MCP server."""

from agent_data_pipeline_validator.mcp_adapter import create_mcp_server

if __name__ == "__main__":
    server = create_mcp_server()
    server.run()
