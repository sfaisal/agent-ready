"""Generates a markdown AI-readiness report from an audit result."""

CATEGORY_LABELS = {
    "description_clarity": "1. Capability Description Clarity",
    "side_effects": "2. Side-Effect Signaling",
    "error_responses": "3. Actionable Error Responses",
    "auth_clarity": "4. Auth/Scope Clarity for Agents",
    "ambiguity": "5. Endpoint Ambiguity",
    "parameter_explanation": "6. Parameter Explanation",
    "usage_guidelines": "7. Usage Guidelines (When to Call)",
    "tool_surface": "8. Tool Surface & Schema Strictness",
}

SEVERITY_ICON = {"fail": "🔴", "warning": "🟡", "pass": "🟢"}


def generate_report(spec: dict, result: dict) -> str:
    title = spec.get("info", {}).get("title", "Untitled API")
    version = spec.get("info", {}).get("version", "unknown")

    lines = []
    lines.append(f"# AI-Readiness Report: {title} (v{version})")
    lines.append("")
    lines.append(f"**Overall score: {result['overall_score']}/100**  ")
    lines.append(f"**Endpoints analyzed:** {result['endpoint_count']}")
    lines.append("")
    lines.append("## Category Scores")
    lines.append("")
    lines.append("| Category | Score | Weight |")
    lines.append("|---|---|---|")
    for cat, label in CATEGORY_LABELS.items():
        summary = result["category_summary"].get(cat, {"score": 0, "weight": 0})
        lines.append(f"| {label} | {summary['score']}/100 | {int(summary['weight']*100)}% |")
    lines.append("")

    lines.append("## Gaps Found")
    lines.append("")
    if not result["gaps"]:
        lines.append("No gaps found — every endpoint passed every category.")
    else:
        by_category = {}
        for f in result["gaps"]:
            by_category.setdefault(f.category, []).append(f)
        for cat, label in CATEGORY_LABELS.items():
            findings = by_category.get(cat, [])
            if not findings:
                continue
            lines.append(f"### {label}")
            lines.append("")
            for f in findings:
                icon = SEVERITY_ICON[f.severity]
                lines.append(f"- {icon} **{f.endpoint}** — {f.message}")
            lines.append("")

    lines.append("## What Passed")
    lines.append("")
    pass_counts = {}
    for f in result["passes"]:
        pass_counts[f.category] = pass_counts.get(f.category, 0) + 1
    for cat, label in CATEGORY_LABELS.items():
        count = pass_counts.get(cat, 0)
        lines.append(f"- {label}: {count} endpoint(s) passed cleanly")
    lines.append("")

    lines.append("## Recommended Next Step")
    lines.append("")
    lines.append(
        "Don't try to fix every gap at once. Prioritize the endpoints with 🔴 fails in "
        "**Capability Description Clarity** and **Parameter Explanation** first — these "
        "most directly cause wrong tool selection and malformed arguments, which matters "
        "more early on than auth polish or ambiguity cleanup."
    )
    lines.append("")
    lines.append("### A caveat on maximising this score")
    lines.append("")
    lines.append(
        "This score is a diagnostic, not a target. Hasan et al. (arXiv:2602.14878) found "
        "that augmenting *all* description components raised task success by a median 5.85 "
        "percentage points — but increased execution steps by 67.46% and regressed "
        "performance in 16.67% of cases, because richer descriptions consume context window "
        "and raise cost. Their ablations found shorter, targeted descriptions often performed "
        "equivalently. Fix the fails; don't gold-plate the passes."
    )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*Rubric categories 1–5 derived independently from MCP tool-selection mechanics "
                 "and API design principles; categories 6–7 added after validation against "
                 "arXiv:2602.14878 (856 tools / 103 servers) and arXiv:2602.18914 (10,831 servers). "
                 "Category weights are judgement calls, not measured effect sizes.*")
    lines.append("")

    return "\n".join(lines)
