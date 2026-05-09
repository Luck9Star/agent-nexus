"""Entry point for contract-analyzer agent.

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
        from agent_contract_analyzer.local_adapter import run_local_adapter

        run_local_adapter()

    elif mode == "cli":
        _run_cli()

    else:
        # MCP mode (default)
        try:
            from agent_contract_analyzer.mcp_adapter import create_mcp_server

            server = create_mcp_server()
            server.run()
        except ImportError as e:
            print(f"Error: {e}", file=sys.stderr)
            print(
                "Install full dependencies with: pip install agent-contract-analyzer[full]",
                file=sys.stderr,
            )
            sys.exit(1)


def _run_cli() -> None:
    """Simple CLI interface for development and testing."""
    from agent_contract_analyzer.agent import ContractAnalyzerAgent

    parser = argparse.ArgumentParser(
        description="contract-analyzer — Contract clause analysis specialist"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # extract command
    extract_parser = subparsers.add_parser("extract", help="Extract clauses from contract text")
    extract_parser.add_argument("file_path", help="Path to contract text file")

    # risks command
    risks_parser = subparsers.add_parser("risks", help="Analyze risks in contract")
    risks_parser.add_argument("file_path", help="Path to contract text file")

    # compliance command
    compliance_parser = subparsers.add_parser(
        "compliance", help="Check compliance against jurisdiction"
    )
    compliance_parser.add_argument("file_path", help="Path to contract text file")
    compliance_parser.add_argument(
        "--jurisdiction", required=True, help="Jurisdiction code (CN, US, UK, EU, HK, SG)"
    )

    args = parser.parse_args()
    agent = ContractAnalyzerAgent()

    if args.command == "extract":
        try:
            with open(args.file_path) as f:
                text = f.read()
            clauses = agent.extract_clauses(text)
            print(json.dumps([c.model_dump() for c in clauses], indent=2, ensure_ascii=False))
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == "risks":
        try:
            with open(args.file_path) as f:
                text = f.read()
            clauses = agent.extract_clauses(text)
            result = agent.analyze_risks(clauses)
            print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == "compliance":
        try:
            with open(args.file_path) as f:
                text = f.read()
            clauses = agent.extract_clauses(text)
            result = agent.check_compliance(clauses, args.jurisdiction)
            print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
