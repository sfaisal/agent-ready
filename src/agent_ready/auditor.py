"""
Core audit engine: parse an OpenAPI spec, run the rubric, produce findings.
"""

import json
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from urllib.request import urlopen

import yaml

from .rubric import (
    CATEGORY_WEIGHTS,
    EndpointInfo,
    Finding,
    check_ambiguity,
    check_auth_clarity,
    check_description_clarity,
    check_error_responses,
    check_parameter_explanation,
    check_schema_strictness,
    check_side_effects,
    check_tool_surface,
    check_usage_guidelines,
)

SEVERITY_VALUE = {"pass": 1.0, "warning": 0.5, "fail": 0.0}


class SpecLoadError(Exception):
    """Raised when a spec can't be loaded or parsed."""


def load_spec(source: str, timeout: int = 30) -> dict:
    """Load an OpenAPI spec from a local path or an http(s) URL."""
    if source.startswith(("http://", "https://")):
        try:
            with urlopen(source, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
        except Exception as exc:
            raise SpecLoadError(f"Could not fetch spec from {source}: {exc}") from exc
    else:
        path = Path(source)
        if not path.exists():
            raise SpecLoadError(f"Spec file not found: {source}")
        raw = path.read_text(encoding="utf-8")

    # YAML is a superset of JSON, so safe_load handles both. Try JSON first for
    # a clearer error on malformed JSON.
    try:
        if source.endswith(".json") or raw.lstrip().startswith("{"):
            return json.loads(raw)
        return yaml.safe_load(raw)
    except Exception as exc:
        raise SpecLoadError(f"Could not parse spec as JSON or YAML: {exc}") from exc


def _resolve_ref(spec: dict, ref: str):
    """Resolve a local $ref like '#/components/parameters/PageSize'."""
    if not ref.startswith("#/"):
        return None
    node = spec
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def extract_endpoints(spec: dict) -> list[EndpointInfo]:
    """Pull every operation out of the spec, resolving parameter $refs."""
    endpoints = []
    paths = spec.get("paths") or {}
    if not isinstance(paths, dict):
        return endpoints

    http_methods = {"get", "post", "put", "patch", "delete", "head", "options"}

    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        # Parameters can be declared once at the path level and shared.
        shared_params = methods.get("parameters", []) or []

        for method, op in methods.items():
            if method.lower() not in http_methods or not isinstance(op, dict):
                continue

            raw_params = list(shared_params) + list(op.get("parameters", []) or [])
            resolved = []
            for p in raw_params:
                if isinstance(p, dict) and "$ref" in p:
                    target = _resolve_ref(spec, p["$ref"])
                    if isinstance(target, dict):
                        resolved.append(target)
                elif isinstance(p, dict):
                    resolved.append(p)

            # Deduplicate. OpenAPI defines a parameter's identity as the
            # combination of `name` and `in`, and an operation-level parameter
            # overrides a path-level one with the same identity. Real specs
            # routinely declare both — Asana's declares `project_gid` at each
            # level — and concatenating them produced generated functions with
            # duplicate argument names, which are a Python SyntaxError.
            deduped = {}
            for p in resolved:
                key = (p.get("name"), p.get("in", "query"))
                deduped[key] = p  # later entries (operation-level) win
            resolved = list(deduped.values())

            endpoints.append(
                EndpointInfo(
                    path=path,
                    method=method,
                    summary=op.get("summary") or "",
                    description=op.get("description") or "",
                    parameters=resolved,
                    responses=op.get("responses") or {},
                    request_body=op.get("requestBody") or {},
                )
            )
    return endpoints


def run_audit(spec: dict) -> dict:
    """Run every rubric check and aggregate into a scored result."""
    endpoints = extract_endpoints(spec)
    security_schemes = (spec.get("components") or {}).get("securitySchemes") or {}

    findings: list[Finding] = []
    for ep in endpoints:
        findings += check_description_clarity(ep)
        findings += check_side_effects(ep)
        findings += check_error_responses(ep)
        findings += check_auth_clarity(ep, security_schemes)
        findings += check_parameter_explanation(ep)
        findings += check_usage_guidelines(ep)
        findings += check_schema_strictness(ep)
    findings += check_ambiguity(endpoints)
    findings += check_tool_surface(endpoints)

    # Score per category. Spec-level findings (e.g. "this catalogue has 587
    # tools") describe the whole API, so averaging them in alongside hundreds of
    # endpoint-level findings would drown them out entirely. Where both exist,
    # the category score is a 50/50 blend of the two scopes.
    endpoint_vals = defaultdict(list)
    spec_vals = defaultdict(list)
    for f in findings:
        bucket = spec_vals if f.scope == "spec" else endpoint_vals
        bucket[f.category].append(SEVERITY_VALUE[f.severity])

    category_summary = {}
    for cat, weight in CATEGORY_WEIGHTS.items():
        ep_scores = endpoint_vals.get(cat, [])
        sp_scores = spec_vals.get(cat, [])
        ep_avg = sum(ep_scores) / len(ep_scores) if ep_scores else None
        sp_avg = sum(sp_scores) / len(sp_scores) if sp_scores else None

        if ep_avg is not None and sp_avg is not None:
            avg = 0.5 * ep_avg + 0.5 * sp_avg
        elif ep_avg is not None:
            avg = ep_avg
        elif sp_avg is not None:
            avg = sp_avg
        else:
            avg = 0.0

        category_summary[cat] = {"score": round(avg * 100, 1), "weight": weight}

    overall = round(
        sum(category_summary[c]["score"] * w for c, w in CATEGORY_WEIGHTS.items()), 1
    )

    return {
        "endpoints": endpoints,
        "endpoint_count": len(endpoints),
        "overall_score": overall,
        "category_summary": category_summary,
        "gaps": [f for f in findings if f.severity in ("fail", "warning")],
        "passes": [f for f in findings if f.severity == "pass"],
        "fail_count": sum(1 for f in findings if f.severity == "fail"),
        "warning_count": sum(1 for f in findings if f.severity == "warning"),
    }


def result_to_dict(spec: dict, result: dict) -> dict:
    """Serialise an audit result to plain JSON-safe data (for --format json)."""
    info = spec.get("info") or {}
    return {
        "api": {
            "title": info.get("title", "Untitled API"),
            "version": str(info.get("version", "unknown")),
        },
        "overall_score": result["overall_score"],
        "endpoint_count": result["endpoint_count"],
        "fail_count": result["fail_count"],
        "warning_count": result["warning_count"],
        "categories": result["category_summary"],
        "findings": [asdict(f) for f in result["gaps"]],
    }


def endpoints_ready_for_mcp(
    result: dict,
    blocking_categories=("description_clarity", "parameter_explanation"),
) -> list[EndpointInfo]:
    """
    Endpoints with no FAIL findings in the blocking categories — i.e. safe to
    wrap as MCP tools. Description clarity and parameter explanation are the
    blockers because they most directly cause wrong tool selection and
    malformed arguments.
    """
    blocked = {
        f.endpoint
        for f in result["gaps"]
        if f.severity == "fail" and f.category in blocking_categories
    }
    return [ep for ep in result["endpoints"] if ep.id not in blocked]
