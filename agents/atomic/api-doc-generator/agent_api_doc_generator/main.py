"""Entry point for api-doc-generator agent.

Detects run mode from AGENT_MODE environment variable:
- "mcp"    -> Start MCP Server (default)
- "local"  -> Start stdin/stdout JSON-lines adapter for Platform Router
- "cli"    -> Simple CLI interface for development and testing
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def main() -> None:
    """Route to the appropriate adapter based on AGENT_MODE."""
    mode = os.getenv("AGENT_MODE", "mcp").lower()

    if mode == "local":
        from agent_api_doc_generator.local_adapter import run_local_adapter

        run_local_adapter()

    elif mode == "cli":
        _run_cli()

    else:
        # MCP mode (default)
        try:
            from agent_api_doc_generator.mcp_adapter import create_mcp_server

            server = create_mcp_server()
            server.run()
        except ImportError as e:
            print(f"Error: {e}", file=sys.stderr)
            print(
                "Install full dependencies with: pip install agent-api-doc-generator[full]",
                file=sys.stderr,
            )
            sys.exit(1)


def _run_cli() -> None:
    """Simple CLI interface for development and testing."""
    from agent_api_doc_generator.agent import APIDocGeneratorAgent
    from agent_api_doc_generator.models import EndpointInfo

    parser = argparse.ArgumentParser(description="api-doc-generator -- API 文档生成专家")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # extract command
    extract_parser = subparsers.add_parser("extract", help="Extract API endpoints")
    extract_parser.add_argument("file_path", help="Path to source code file")

    # infer command
    infer_parser = subparsers.add_parser("infer", help="Infer JSON Schema")
    infer_parser.add_argument("type_info", help="Type annotation text")

    # generate command
    gen_parser = subparsers.add_parser("generate", help="Generate OpenAPI spec")
    gen_parser.add_argument("--endpoints", required=True, help="JSON string of endpoints")
    gen_parser.add_argument("--title", default="API Documentation", help="API title")
    gen_parser.add_argument("--version", default="1.0.0", help="API version")

    args = parser.parse_args()
    agent = APIDocGeneratorAgent()

    if args.command == "extract":
        endpoints = agent.extract(args.file_path)
        print(
            json.dumps(
                {"endpoints": [e.model_dump() for e in endpoints]},
                indent=2,
                ensure_ascii=False,
            )
        )

    elif args.command == "infer":
        schema = agent.infer(args.type_info)
        print(json.dumps(schema.model_dump(), indent=2, ensure_ascii=False))

    elif args.command == "generate":
        try:
            endpoints_data = json.loads(args.endpoints)
        except json.JSONDecodeError as e:
            print(f"Invalid JSON for --endpoints: {e}", file=sys.stderr)
            sys.exit(1)

        endpoints = [EndpointInfo.model_validate(e) for e in endpoints_data]
        spec = agent.generate(
            endpoints,
            info={"title": args.title, "version": args.version},
        )
        print(json.dumps(spec.model_dump(), indent=2, ensure_ascii=False))

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
