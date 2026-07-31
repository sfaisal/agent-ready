"""agent-ready: audit OpenAPI specs for AI-agent and MCP readiness."""

# Single source of truth is pyproject.toml; read it back from installed
# metadata so the CLI's --version can never drift from the packaged version.
try:
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _pkg_version

    __version__ = _pkg_version("openapi-agent-ready")
except PackageNotFoundError:  # pragma: no cover - running from a source tree
    __version__ = "unknown"

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
