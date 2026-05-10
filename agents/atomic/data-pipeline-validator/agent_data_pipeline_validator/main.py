"""Entry point for data-pipeline-validator agent.

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
        from agent_data_pipeline_validator.local_adapter import run_local_adapter

        run_local_adapter()

    elif mode == "cli":
        _run_cli()

    else:
        # MCP mode (default)
        try:
            from agent_data_pipeline_validator.mcp_adapter import create_mcp_server

            server = create_mcp_server()
            server.run()
        except ImportError as e:
            print(f"Error: {e}", file=sys.stderr)
            print(
                "Install full dependencies with: pip install agent-data-pipeline-validator[full]",
                file=sys.stderr,
            )
            sys.exit(1)


def _run_cli() -> None:
    """Simple CLI interface for development and testing."""
    from agent_data_pipeline_validator.agent import DataPipelineValidatorAgent

    parser = argparse.ArgumentParser(
        description="data-pipeline-validator — ETL pipeline validation agent"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # validate command
    validate_parser = subparsers.add_parser("validate", help="Validate a pipeline config")
    validate_parser.add_argument("config", help="Pipeline config JSON string or file path")

    # report command
    report_parser = subparsers.add_parser("report", help="Generate validation report")
    report_parser.add_argument(
        "--findings", required=True, help="JSON file or string with findings"
    )

    args = parser.parse_args()
    agent = DataPipelineValidatorAgent()

    if args.command == "validate":
        try:
            # Try to load as file first, fall back to raw string
            try:
                with open(args.config) as f:
                    config = f.read()
            except (FileNotFoundError, IsADirectoryError):
                config = args.config
            result = agent.validate_pipeline(config)
            print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == "report":
        try:
            findings = json.loads(args.findings)
        except json.JSONDecodeError as e:
            print(f"Invalid JSON for --findings: {e}", file=sys.stderr)
            sys.exit(1)
        result = agent.generate_report(findings)
        print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
