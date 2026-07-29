import json
from pathlib import Path

import pytest

from agent_ready.auditor import (
    SpecLoadError,
    endpoints_ready_for_mcp,
    extract_endpoints,
    load_spec,
    run_audit,
)
from agent_ready.cli import main
from agent_ready.rubric import (
    CATEGORY_WEIGHTS,
    EndpointInfo,
    check_ambiguity,
    check_error_responses,
    check_parameter_explanation,
    check_schema_strictness,
    check_tool_surface,
    check_usage_guidelines,
)

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

GOOD_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Good API", "version": "1.0"},
    "components": {
        "securitySchemes": {
            "OAuth2": {
                "type": "oauth2",
                "flows": {
                    "clientCredentials": {
                        "tokenUrl": "https://x/token",
                        "scopes": {"booking:read": "Read bookings"},
                    }
                },
            }
        }
    },
    "paths": {
        "/availability": {
            "get": {
                "summary": "Search availability",
                "description": (
                    "Returns bookable inventory for a date range and location. "
                    "This is a read-only lookup and does not modify any data. "
                    "Use this when you need to know what can be booked before "
                    "creating a booking. Do not use it to check an existing booking."
                ),
                "parameters": [
                    {
                        "name": "date_from",
                        "in": "query",
                        "description": "Inclusive start of the range",
                        "schema": {"type": "string", "format": "date"},
                    }
                ],
                "responses": {
                    "200": {"description": "A list of available items with price and capacity."},
                    "400": {
                        "description": "The date range was invalid; date_from must precede date_to.",
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    },
                },
            }
        }
    },
}

BAD_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Bad API", "version": "1.0"},
    "paths": {
        "/status": {
            "get": {
                "summary": "Get status",
                "description": "",
                "parameters": [{"name": "id", "in": "query", "schema": {"type": "string"}}],
                "responses": {"200": {"description": "OK"}},
            }
        }
    },
}


# --------------------------------------------------------------------------
# Spec loading
# --------------------------------------------------------------------------

def test_load_yaml_spec(tmp_path):
    p = tmp_path / "spec.yaml"
    p.write_text("openapi: 3.0.0\npaths: {}\n")
    assert load_spec(str(p))["openapi"] == "3.0.0"


def test_load_json_spec(tmp_path):
    p = tmp_path / "spec.json"
    p.write_text(json.dumps({"openapi": "3.0.0", "paths": {}}))
    assert load_spec(str(p))["openapi"] == "3.0.0"


def test_missing_file_raises():
    with pytest.raises(SpecLoadError):
        load_spec("/nonexistent/spec.yaml")


def test_malformed_spec_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not valid json")
    with pytest.raises(SpecLoadError):
        load_spec(str(p))


# --------------------------------------------------------------------------
# Endpoint extraction
# --------------------------------------------------------------------------

def test_extracts_operations():
    eps = extract_endpoints(GOOD_SPEC)
    assert len(eps) == 1
    assert eps[0].id == "GET /availability"


def test_ignores_non_operation_keys():
    spec = {
        "paths": {
            "/x": {
                "parameters": [{"name": "shared", "in": "query", "description": "d"}],
                "get": {"summary": "s", "description": "d", "responses": {}},
            }
        }
    }
    eps = extract_endpoints(spec)
    assert len(eps) == 1
    # Path-level shared parameters are inherited by the operation
    assert any(p["name"] == "shared" for p in eps[0].parameters)


def test_resolves_parameter_refs():
    spec = {
        "components": {
            "parameters": {
                "PageSize": {"name": "page_size", "in": "query", "description": "Items per page"}
            }
        },
        "paths": {
            "/x": {
                "get": {
                    "summary": "s",
                    "description": "d",
                    "parameters": [{"$ref": "#/components/parameters/PageSize"}],
                    "responses": {},
                }
            }
        },
    }
    eps = extract_endpoints(spec)
    assert eps[0].parameters[0]["name"] == "page_size"


def test_handles_empty_spec():
    assert extract_endpoints({}) == []
    assert extract_endpoints({"paths": None}) == []


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

def test_weights_sum_to_one():
    assert round(sum(CATEGORY_WEIGHTS.values()), 6) == 1.0


def test_good_spec_scores_higher_than_bad():
    good = run_audit(GOOD_SPEC)["overall_score"]
    bad = run_audit(BAD_SPEC)["overall_score"]
    assert good > bad


def test_score_in_range():
    for spec in (GOOD_SPEC, BAD_SPEC):
        score = run_audit(spec)["overall_score"]
        assert 0 <= score <= 100


def test_empty_description_is_a_fail():
    result = run_audit(BAD_SPEC)
    assert any(
        f.category == "description_clarity" and f.severity == "fail" for f in result["gaps"]
    )


def test_mcp_generation_excludes_failing_endpoints():
    result = run_audit(BAD_SPEC)
    assert endpoints_ready_for_mcp(result) == []


# --------------------------------------------------------------------------
# Regression: substring false positive
# --------------------------------------------------------------------------

def test_customer_id_not_flagged_as_temporal():
    """'customer_id' contains the substring 'to' (cus-TO-mer) but is not a date."""
    ep = EndpointInfo(
        path="/x",
        method="post",
        summary="s",
        description="d",
        parameters=[{"name": "customer_id", "in": "query", "description": "The customer"}],
        responses={},
    )
    findings = check_parameter_explanation(ep)
    assert not any("format" in f.message.lower() for f in findings if f.severity != "pass")


def test_date_param_without_format_is_flagged():
    ep = EndpointInfo(
        path="/x",
        method="get",
        summary="s",
        description="d",
        parameters=[{"name": "date_from", "in": "query", "description": "Start"}],
        responses={},
    )
    findings = check_parameter_explanation(ep)
    assert any("format" in f.message.lower() for f in findings)


# --------------------------------------------------------------------------
# Regression: usage-guidelines phrasing variants
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "description",
    [
        "Use this when you need availability.",
        "Use this only when the caller has confirmed cancellation.",
        "Use this only after confirming availability via GET /availability.",
        "Call this once the booking has been created.",
        "Do not use this to search for inventory.",
        "Intended for reconciliation jobs.",
        "Prefer GET /bookings/{id} for a single record.",
    ],
)
def test_activation_phrasing_variants_recognised(description):
    """Literal-string matching missed 'use this ONLY WHEN' and 'ONLY AFTER'."""
    ep = EndpointInfo(
        path="/x", method="get", summary="Do a thing",
        description=description, parameters=[], responses={},
    )
    findings = check_usage_guidelines(ep)
    assert all(f.severity == "pass" for f in findings), description


def test_description_without_guidance_is_flagged():
    ep = EndpointInfo(
        path="/x", method="get", summary="Get status",
        description="Returns the current status of the resource as a string value.",
        parameters=[], responses={},
    )
    findings = check_usage_guidelines(ep)
    assert any(f.severity == "warning" for f in findings)


# --------------------------------------------------------------------------
# Regression: OpenAPI `default` responses (found by auditing Stripe's spec)
# --------------------------------------------------------------------------

def test_default_response_counts_as_error_documentation():
    """
    Stripe's entire public spec documents errors via `default` rather than
    explicit 4xx/5xx codes. Counting only 4xx/5xx failed all 587 endpoints.
    """
    ep = EndpointInfo(
        path="/v1/charges", method="get", summary="List charges",
        description="Returns a list of charges you've previously created.",
        parameters=[],
        responses={
            "200": {"description": "Successful response.", "content": {"application/json": {}}},
            "default": {
                "description": "Error response with a structured error object.",
                "content": {"application/json": {}},
            },
        },
    )
    findings = check_error_responses(ep)
    assert not any(f.severity == "fail" for f in findings)


def test_no_error_responses_at_all_is_a_fail():
    ep = EndpointInfo(
        path="/x", method="get", summary="s", description="d",
        parameters=[], responses={"200": {"description": "OK"}},
    )
    findings = check_error_responses(ep)
    assert any(f.severity == "fail" for f in findings)


def test_ambiguity_findings_are_capped():
    """Large catalogues must not produce an unreadable wall of findings."""
    endpoints = [
        EndpointInfo(
            path=f"/thing{i}", method="get",
            summary="Retrieve a thing",
            description="Returns the details of an existing thing by its identifier.",
            parameters=[], responses={},
        )
        for i in range(40)
    ]
    findings = check_ambiguity(endpoints, max_reported=5)
    assert len(findings) <= 6  # 5 pairs + 1 summary line
    assert any("in total" in f.message for f in findings)


# --------------------------------------------------------------------------
# Category 8: tool surface & schema strictness (OpenAI-derived)
# --------------------------------------------------------------------------

def _dummy_endpoints(n):
    return [
        EndpointInfo(path=f"/e{i}", method="get", summary="s", description="d",
                     parameters=[], responses={})
        for i in range(n)
    ]


def test_small_tool_surface_passes():
    findings = check_tool_surface(_dummy_endpoints(10))
    assert all(f.severity == "pass" for f in findings)


def test_large_tool_surface_warns():
    findings = check_tool_surface(_dummy_endpoints(30))
    assert any(f.severity == "warning" for f in findings)


def test_very_large_tool_surface_fails():
    findings = check_tool_surface(_dummy_endpoints(200))
    assert any(f.severity == "fail" for f in findings)


def test_tool_count_findings_are_spec_scoped():
    """Spec-level findings must not be averaged away against endpoint findings."""
    findings = check_tool_surface(_dummy_endpoints(200))
    assert all(f.scope == "spec" for f in findings)


def test_spec_level_finding_is_not_drowned_out():
    """
    A 587-endpoint spec previously scored 99/100 on tool surface because one
    spec-level fail was averaged against 587 endpoint-level passes.
    """
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Huge API", "version": "1"},
        "paths": {
            f"/thing{i}": {
                "get": {
                    "summary": f"Retrieve thing {i}",
                    "description": (
                        f"Returns the full record for thing {i} by its identifier. "
                        f"This is a read-only lookup. Use this when you have a "
                        f"thing{i} id and need its current state."
                    ),
                    "parameters": [],
                    "responses": {"200": {"description": "OK"}, "default": {
                        "description": "A structured error object explaining the failure."}},
                }
            }
            for i in range(200)
        },
    }
    result = run_audit(spec)
    assert result["category_summary"]["tool_surface"]["score"] < 60


def test_missing_additional_properties_false_warns():
    ep = EndpointInfo(
        path="/x", method="post", summary="s", description="d",
        parameters=[], responses={},
        request_body={"content": {"application/json": {"schema": {
            "type": "object",
            "required": ["a"],
            "properties": {"a": {"type": "string"}},
        }}}},
    )
    findings = check_schema_strictness(ep)
    assert any("additionalProperties" in f.message for f in findings)


def test_strict_mode_compatible_body_passes():
    ep = EndpointInfo(
        path="/x", method="post", summary="s", description="d",
        parameters=[], responses={},
        request_body={"content": {"application/json": {"schema": {
            "type": "object",
            "required": ["a"],
            "additionalProperties": False,
            "properties": {"a": {"type": "string"}},
        }}}},
    )
    findings = check_schema_strictness(ep)
    assert all(f.severity == "pass" for f in findings)


def test_enum_opportunity_flagged():
    ep = EndpointInfo(
        path="/x", method="get", summary="s", description="d",
        parameters=[{"name": "status", "in": "query", "description": "The status",
                     "schema": {"type": "string"}}],
        responses={},
    )
    findings = check_schema_strictness(ep)
    assert any("enum" in f.message.lower() for f in findings)


def test_existing_enum_not_flagged():
    ep = EndpointInfo(
        path="/x", method="get", summary="s", description="d",
        parameters=[{"name": "status", "in": "query", "description": "The status",
                     "schema": {"type": "string", "enum": ["open", "closed"]}}],
        responses={},
    )
    findings = check_schema_strictness(ep)
    assert not any("enum" in f.message.lower() for f in findings if f.severity != "pass")


# --------------------------------------------------------------------------
# Example specs: the rubric must discriminate between them
# --------------------------------------------------------------------------

EXAMPLES = Path(__file__).parent.parent / "examples"


def test_example_specs_discriminate():
    bad = run_audit(load_spec(str(EXAMPLES / "sample_booking_api.yaml")))
    good = run_audit(load_spec(str(EXAMPLES / "agent_ready_booking_api.yaml")))
    assert bad["overall_score"] < 70, "flawed example should score poorly"
    assert good["overall_score"] >= 95, "well-designed example should score near-perfect"
    assert good["fail_count"] == 0


# --------------------------------------------------------------------------
# CLI exit codes (the CI-gate contract)
# --------------------------------------------------------------------------

def _write(tmp_path, spec, name="spec.json"):
    p = tmp_path / name
    p.write_text(json.dumps(spec))
    return str(p)


def test_cli_exits_zero_without_gates(tmp_path, capsys):
    assert main([_write(tmp_path, GOOD_SPEC), "--quiet"]) == 0


def test_cli_gate_fails_on_low_score(tmp_path, capsys):
    assert main([_write(tmp_path, BAD_SPEC), "--min-score", "99", "--quiet"]) == 1


def test_cli_gate_passes_on_high_threshold_met(tmp_path, capsys):
    assert main([_write(tmp_path, GOOD_SPEC), "--min-score", "1", "--quiet"]) == 0


def test_cli_max_fails_gate(tmp_path, capsys):
    assert main([_write(tmp_path, BAD_SPEC), "--max-fails", "0", "--quiet"]) == 1


def test_cli_bad_spec_exits_two(tmp_path, capsys):
    p = tmp_path / "junk.yaml"
    p.write_text("just: a string\n")
    assert main([str(p), "--quiet"]) == 2


def test_cli_json_output_is_valid(tmp_path, capsys):
    main([_write(tmp_path, GOOD_SPEC), "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert "overall_score" in payload
    assert payload["api"]["title"] == "Good API"
