"""
Baseline support: fail CI only on *new* findings.

The problem this solves: a mature API audited for the first time produces
hundreds of findings. A team cannot fix them all before merging anything, so
`--min-score` and `--max-fails` are unusable as gates and get switched off
within a week. A baseline records the current state and lets the gate fail only
when something gets worse.

The whole design rests on one question: what makes a finding "the same finding"
across two runs? Message text is the obvious answer and the wrong one — it
embeds counts and parameter names ("3/3 parameters have no description
(date_from, date_to, location)") that change when an unrelated parameter is
added, which would report a fixed finding as new.

So findings are fingerprinted on (category, endpoint, severity) instead:

- **category** — which rule fired.
- **endpoint** — where. Stable across edits to the endpoint's content.
- **severity** — a warning becoming a fail is a regression worth catching, so
  it belongs in the identity rather than being treated as the same finding.

Message is deliberately excluded. Rewording a finding's text in a future
release must not invalidate every stored baseline.
"""

import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path

BASELINE_VERSION = 1


class BaselineError(Exception):
    """Raised when a baseline file can't be read or is malformed."""


def fingerprint(finding) -> str:
    """A stable identity for a finding across runs. See module docstring."""
    return f"{finding.category}|{finding.endpoint}|{finding.severity}"


def _counted(findings) -> Counter:
    """
    Fingerprints with multiplicity.

    A single endpoint can legitimately produce two findings in one category —
    for example a parameter-explanation fail for undocumented parameters plus a
    warning for a missing date format. Counting rather than set-membership means
    going from one such finding to two is correctly reported as new.
    """
    return Counter(fingerprint(f) for f in findings)


def write_baseline(path: str, spec: dict, result: dict) -> dict:
    """Record the current findings so future runs can compare against them."""
    info = spec.get("info") or {}
    payload = {
        "baseline_version": BASELINE_VERSION,
        "api": {
            "title": info.get("title", "Untitled API"),
            "version": str(info.get("version", "unknown")),
        },
        "overall_score": result["overall_score"],
        "endpoint_count": result["endpoint_count"],
        "fingerprints": sorted(_counted(result["gaps"]).elements()),
        # Stored for human readability when reviewing a baseline diff; never
        # read back during comparison.
        "findings": [asdict(f) for f in result["gaps"]],
    }
    Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def load_baseline(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise BaselineError(
            f"Baseline file not found: {path}\n"
            f"Create one from the current state with: --write-baseline {path}"
        )
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BaselineError(f"Baseline file is not valid JSON: {exc}") from exc

    if not isinstance(data, dict) or "fingerprints" not in data:
        raise BaselineError(
            f"{path} does not look like an agent-ready baseline "
            "(no 'fingerprints' key)."
        )

    version = data.get("baseline_version", 0)
    if version > BASELINE_VERSION:
        raise BaselineError(
            f"Baseline was written by a newer version of agent-ready "
            f"(format v{version}, this build understands v{BASELINE_VERSION}). "
            "Upgrade agent-ready or regenerate the baseline."
        )
    return data


def compare_to_baseline(result: dict, baseline: dict) -> dict:
    """
    Classify current findings against a baseline.

    Returns counts plus the actual new findings, so the CLI can show a team
    exactly what their change introduced rather than the whole backlog.
    """
    baseline_counts = Counter(baseline.get("fingerprints", []))
    current_counts = _counted(result["gaps"])

    # A fingerprint appearing more often than the baseline recorded is new,
    # by the excess amount.
    new_counts = current_counts - baseline_counts
    fixed_counts = baseline_counts - current_counts

    # Map fingerprints back to findings so the report can name them. Consume
    # the allowance so duplicates beyond it are still reported.
    remaining = Counter(new_counts)
    new_findings = []
    for f in result["gaps"]:
        fp = fingerprint(f)
        if remaining.get(fp, 0) > 0:
            new_findings.append(f)
            remaining[fp] -= 1

    return {
        "new_findings": new_findings,
        "new_count": sum(new_counts.values()),
        "fixed_count": sum(fixed_counts.values()),
        "unchanged_count": sum((current_counts & baseline_counts).values()),
        "new_fails": sum(1 for f in new_findings if f.severity == "fail"),
        "baseline_score": baseline.get("overall_score"),
        "current_score": result["overall_score"],
    }
