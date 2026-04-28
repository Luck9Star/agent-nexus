"""Output contract validation for Expert Profile artifacts."""

import re
from typing import Any


def _extract_sections(output: str) -> set[str]:
    """Extract markdown header section names from output text.

    Looks for lines matching the pattern ``## section_name`` (ATX heading
    level 2).  The section name is lowercased and stripped of surrounding
    whitespace to enable case-insensitive matching.
    """
    sections: set[str] = set()
    for line in output.splitlines():
        match = re.match(r"^##\s+(.+)$", line.strip())
        if match:
            sections.add(match.group(1).strip().lower())
    return sections


def validate_output_contract(output: str, contract: dict[str, Any]) -> dict[str, Any]:
    """Validate that *output* satisfies the *contract*'s required sections.

    The function parses *output* looking for Markdown ``##`` headers and
    checks that every section listed in ``contract["required_sections"]``
    is present.  Matching is case-insensitive.

    Args:
        output: The raw text output from the expert agent.
        contract: A dict with at least a ``required_sections`` key containing
            a list of section name strings.

    Returns:
        A dict with keys:
        - ``valid`` (bool): True if all required sections are present.
        - ``missing_sections`` (list[str]): Sections that were not found.
    """
    required = [s.lower() for s in contract.get("required_sections", [])]
    present = _extract_sections(output)

    missing = [s for s in required if s not in present]

    return {
        "valid": len(missing) == 0,
        "missing_sections": missing,
    }
