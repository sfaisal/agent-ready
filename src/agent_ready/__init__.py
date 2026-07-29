"""agent-ready: audit OpenAPI specs for AI-agent and MCP readiness."""

__version__ = "0.1.0"

from .auditor import SpecLoadError, extract_endpoints, load_spec, run_audit
from .rubric import CATEGORY_WEIGHTS, EndpointInfo, Finding

__all__ = [
    "CATEGORY_WEIGHTS",
    "EndpointInfo",
    "Finding",
    "SpecLoadError",
    "extract_endpoints",
    "load_spec",
    "run_audit",
]
