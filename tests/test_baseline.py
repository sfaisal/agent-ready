"""
Tests for baseline mode.

The behaviour that matters here is not "does it diff two lists" — it is whether
the notion of finding identity survives the edits a real team makes. Each test
below corresponds to an edit that would produce a false result under a naive
implementation.
"""

import json
from pathlib import Path

import pytest

from agent_ready.auditor import load_spec, run_audit
from agent_ready.baseline import (
    BaselineError,
    compare_to_baseline,
    fingerprint,
    load_baseline,
    write_baseline,
)
from agent_ready.cli import main


def _spec(paths):
    return {"openapi": "3.0.0", "info": {"title": "T", "version": "1"}, "paths": paths}


WEAK_ENDPOINT = {
    "get": {
        "summary": "Get things",
        "description": "",
        "parameters": [{"name": "q", "in": "query", "schema": {"type": "string"}}],
        "responses": {"200": {"description": "OK"}},
    }
}

STRONG_ENDPOINT = {
    "get": {
        "summary": "List things",
        "description": (
            "Returns every thing in the account with its identifier and current "
            "status. This is a read-only lookup and does not modify data. Use "
            "this when you need to find a thing before acting on it."
        ),
        "parameters": [
            {
                "name": "q",
                "in": "query",
                "description": "Free-text search over thing names.",
                "schema": {"type": "string"},
            }
        ],
        "responses": {
            "200": {"description": "A list of matching thing records."},
            "400": {
                "description": "The query was malformed; q must be non-empty.",
                "content": {"application/json": {"schema": {"type": "object"}}},
            },
        },
    }
}


def _baseline_for(spec, tmp_path, name="base.json"):
    path = tmp_path / name
    write_baseline(str(path), spec, run_audit(spec))
    return str(path)


# --------------------------------------------------------------------------
# Core behaviour
# --------------------------------------------------------------------------

def test_unchanged_spec_reports_no_new_findings(tmp_path):
    """The whole point: an existing backlog must not block every merge."""
    spec = _spec({"/a": WEAK_ENDPOINT, "/b": WEAK_ENDPOINT})
    path = _baseline_for(spec, tmp_path)

    result = run_audit(spec)
    assert result["fail_count"] > 0, "fixture should have pre-existing failures"

    comparison = compare_to_baseline(result, load_baseline(path))
    assert comparison["new_count"] == 0
    assert comparison["unchanged_count"] == len(result["gaps"])


def test_added_bad_endpoint_is_reported_as_new(tmp_path):
    spec = _spec({"/a": WEAK_ENDPOINT})
    path = _baseline_for(spec, tmp_path)

    worse = _spec({"/a": WEAK_ENDPOINT, "/new": WEAK_ENDPOINT})
    comparison = compare_to_baseline(run_audit(worse), load_baseline(path))

    assert comparison["new_count"] > 0
    assert all("/new" in f.endpoint for f in comparison["new_findings"]), (
        "only the added endpoint should be reported as new"
    )


def test_fixed_findings_are_reported_and_do_not_fail(tmp_path):
    spec = _spec({"/a": WEAK_ENDPOINT, "/b": WEAK_ENDPOINT})
    path = _baseline_for(spec, tmp_path)

    better = _spec({"/a": STRONG_ENDPOINT, "/b": WEAK_ENDPOINT})
    comparison = compare_to_baseline(run_audit(better), load_baseline(path))

    assert comparison["fixed_count"] > 0
    assert comparison["new_count"] == 0, "improving a spec must never look like a regression"


# --------------------------------------------------------------------------
# Fingerprint stability — each of these breaks a naive implementation
# --------------------------------------------------------------------------

def test_message_text_changes_do_not_create_false_regressions(tmp_path):
    """
    Adding a second undocumented parameter changes the finding's message from
    "1/1 parameter(s) ... (q)" to "2/2 parameter(s) ... (q, extra)". It is the
    same unfixed problem. A message-based fingerprint would report it as new.
    """
    spec = _spec({"/a": WEAK_ENDPOINT})
    path = _baseline_for(spec, tmp_path)

    changed = json.loads(json.dumps(spec))
    changed["paths"]["/a"]["get"]["parameters"].append(
        {"name": "extra", "in": "query", "schema": {"type": "string"}}
    )
    comparison = compare_to_baseline(run_audit(changed), load_baseline(path))
    assert comparison["new_count"] == 0, (
        "a reworded message for the same unfixed problem must not count as new"
    )


def test_severity_escalation_is_caught(tmp_path):
    """
    A warning becoming a fail, in the same category on the same endpoint, is a
    regression. Fingerprinting on category and endpoint alone would treat it as
    unchanged and let it through.

    The description lengths below are chosen deliberately: description_clarity
    warns between 40 and 150 characters and fails below 40, so this shortening
    changes severity without changing which categories fire. An earlier version
    of this test degraded a description that had produced no finding at all, so
    the new *category* made it pass and the severity component was never
    exercised — it stayed green with severity removed from the fingerprint.
    """
    warning_level = _spec(
        {
            "/a": {
                "get": {
                    "summary": "",
                    "description": "Returns the things in the account with status.",
                    "parameters": [],
                    "responses": {
                        "200": {"description": "OK"},
                        "default": {
                            "description": "A structured error object.",
                            "content": {"application/json": {"schema": {"type": "object"}}},
                        },
                    },
                }
            }
        }
    )
    path = _baseline_for(warning_level, tmp_path)

    before = [
        f for f in run_audit(warning_level)["gaps"] if f.category == "description_clarity"
    ]
    assert [f.severity for f in before] == ["warning"], "fixture must start at warning"

    fail_level = json.loads(json.dumps(warning_level))
    fail_level["paths"]["/a"]["get"]["description"] = "Returns things."

    after = [
        f for f in run_audit(fail_level)["gaps"] if f.category == "description_clarity"
    ]
    assert [f.severity for f in after] == ["fail"], "fixture must degrade to fail"

    comparison = compare_to_baseline(run_audit(fail_level), load_baseline(path))
    assert comparison["new_fails"] > 0, (
        "a warning escalating to a fail in the same category must be reported as new"
    )


def test_fingerprint_excludes_message():
    """Rewording a finding in a future release must not invalidate baselines."""
    from agent_ready.rubric import Finding

    a = Finding("description_clarity", "fail", "GET /x", "original wording")
    b = Finding("description_clarity", "fail", "GET /x", "completely different wording")
    assert fingerprint(a) == fingerprint(b)


def test_duplicate_findings_are_counted_not_deduplicated():
    """
    One endpoint can produce two findings in one category. Going from one to
    two is a regression, so identity must carry multiplicity.
    """
    from agent_ready.rubric import Finding

    f = Finding("parameter_explanation", "warning", "GET /x", "m")
    baseline = {"fingerprints": [fingerprint(f)]}
    result = {"gaps": [f, f], "overall_score": 50.0}

    comparison = compare_to_baseline(result, baseline)
    assert comparison["new_count"] == 1, "the second occurrence is new"


# --------------------------------------------------------------------------
# File handling
# --------------------------------------------------------------------------

def test_missing_baseline_gives_actionable_error(tmp_path):
    with pytest.raises(BaselineError, match="--write-baseline"):
        load_baseline(str(tmp_path / "nope.json"))


def test_malformed_baseline_is_rejected(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json")
    with pytest.raises(BaselineError):
        load_baseline(str(p))


def test_wrong_shaped_json_is_rejected(tmp_path):
    p = tmp_path / "other.json"
    p.write_text(json.dumps({"some": "other file"}))
    with pytest.raises(BaselineError, match="fingerprints"):
        load_baseline(str(p))


def test_newer_baseline_format_is_rejected(tmp_path):
    p = tmp_path / "future.json"
    p.write_text(json.dumps({"baseline_version": 999, "fingerprints": []}))
    with pytest.raises(BaselineError, match="newer version"):
        load_baseline(str(p))


# --------------------------------------------------------------------------
# CLI wiring and exit codes
# --------------------------------------------------------------------------

def _write_spec(tmp_path, spec, name="spec.json"):
    p = tmp_path / name
    p.write_text(json.dumps(spec))
    return str(p)


def test_cli_write_then_compare_passes(tmp_path):
    spec_path = _write_spec(tmp_path, _spec({"/a": WEAK_ENDPOINT}))
    base = str(tmp_path / "b.json")

    assert main([spec_path, "--write-baseline", base, "--quiet"]) == 0
    assert main([spec_path, "--baseline", base, "--max-new", "0", "--quiet"]) == 0


def test_cli_fails_on_new_findings(tmp_path):
    base_spec = _spec({"/a": WEAK_ENDPOINT})
    base = str(tmp_path / "b.json")
    main([_write_spec(tmp_path, base_spec), "--write-baseline", base, "--quiet"])

    worse = _write_spec(tmp_path, _spec({"/a": WEAK_ENDPOINT, "/b": WEAK_ENDPOINT}), "worse.json")
    assert main([worse, "--baseline", base, "--max-new", "0", "--quiet"]) == 1


def test_cli_missing_baseline_exits_two(tmp_path):
    spec_path = _write_spec(tmp_path, _spec({"/a": WEAK_ENDPOINT}))
    assert main([spec_path, "--baseline", str(tmp_path / "absent.json"), "--quiet"]) == 2


def test_baseline_alone_does_not_gate(tmp_path):
    """--baseline reports; only --max-new turns it into a gate."""
    base_spec = _spec({"/a": WEAK_ENDPOINT})
    base = str(tmp_path / "b.json")
    main([_write_spec(tmp_path, base_spec), "--write-baseline", base, "--quiet"])

    worse = _write_spec(tmp_path, _spec({"/a": WEAK_ENDPOINT, "/b": WEAK_ENDPOINT}), "worse.json")
    assert main([worse, "--baseline", base, "--quiet"]) == 0


def test_baseline_file_is_valid_json_with_expected_keys(tmp_path):
    spec = _spec({"/a": WEAK_ENDPOINT})
    path = _baseline_for(spec, tmp_path)
    data = json.loads(Path(path).read_text())

    assert data["baseline_version"] >= 1
    assert data["api"]["title"] == "T"
    assert isinstance(data["fingerprints"], list)
    assert data["fingerprints"] == sorted(data["fingerprints"]), (
        "fingerprints must be sorted so the file is diff-friendly in git"
    )


def test_roundtrip_through_a_real_spec_file(tmp_path):
    """End-to-end against the shipped example, not a synthetic fixture."""

    examples = Path(__file__).parent.parent / "examples"
    spec = load_spec(str(examples / "sample_booking_api.yaml"))
    path = _baseline_for(spec, tmp_path, "real.json")

    comparison = compare_to_baseline(run_audit(spec), load_baseline(path))
    assert comparison["new_count"] == 0
    assert comparison["fixed_count"] == 0
