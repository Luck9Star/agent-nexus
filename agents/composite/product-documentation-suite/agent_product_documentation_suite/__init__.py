"""agent-product-documentation-suite -- Product documentation suite.

Orchestrates three Atomic Agents in a parallel-then-sequential pattern:
  1. api-doc-generator      -- (parallel) generate OpenAPI specification
  2. code-reviewer          -- (parallel) review code quality
  3. localization-specialist -- (sequential) localize combined output
"""

from agent_product_documentation_suite.coordinator import (
    DocumentationSuiteCoordinator,
)
from agent_product_documentation_suite.models import (
    DocArtifact,
    DocumentationResult,
)

__all__ = [
    "DocumentationSuiteCoordinator",
    "DocArtifact",
    "DocumentationResult",
]
