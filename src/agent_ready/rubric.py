"""
AI-Readiness Rubric
-------------------
Seven categories used to score how ready an API (from an OpenAPI spec) is
for reliable use by AI agents / MCP servers. Each endpoint gets checked
against every category; failures become concrete, cite-able gaps.

Categories:
1. Capability description clarity   -- maps to "Purpose" in Hasan et al. (2026)
2. Side-effect signaling            -- maps to MCP tool annotations (destructive/open-world)
3. Actionable error responses       -- adjacent to "Limitations" in Hasan et al.
4. Auth/scope clarity for agents    -- platform-level; not covered by description-quality rubrics
5. Ambiguity between endpoints      -- Wang et al. (2026) found 73% repeated tool names
6. Parameter explanation            -- "Opaque Parameters" smell: 84.3% prevalence
7. Usage guidelines (when to call)  -- "Missing Usage Guidelines" smell: 89.3% prevalence

Provenance note (be honest about this in interviews):
Categories 1-5 were derived independently from how MCP tool selection works
plus standard API design principles. Categories 6-7 were added after checking
the rubric against published empirical work:

  - Hasan, Li, Rajbahadur, Adams & Hassan (Queen's University), "Model Context
    Protocol (MCP) Tool Descriptions Are Smelly!" arXiv:2602.14878.
    856 tools / 103 servers. Six components: Purpose, Guidelines, Limitations,
    Parameter Explanation, Length & Completeness, Examples.
    97.1% of descriptions had >=1 smell; 56% had an Unclear Purpose smell.

  - Wang et al., "From Docs to Descriptions: Smell-Aware Evaluation of MCP
    Server Descriptions," arXiv:2602.18914. 10,831 servers, 18 smell
    categories across accuracy / functionality / completeness / conciseness.

IMPORTANT TRADE-OFF (cite this, it's the senior-level point):
Hasan et al. found that augmenting ALL description components improved task
success by a median 5.85pp but increased execution steps by 67.46% and
REGRESSED performance in 16.67% of cases. More completeness is not strictly
better -- richer descriptions consume context window and raise cost. Their
ablations found shorter, targeted descriptions often performed equivalently.
Treat this rubric's score as a diagnostic, not a target to maximise.

CAVEAT: the weights in CATEGORY_WEIGHTS are judgement calls, not empirical.
"""

import difflib
import re
from dataclasses import dataclass, field


@dataclass
class Finding:
    category: str
    severity: str  # "fail", "warning", "pass"
    endpoint: str
    message: str
    scope: str = "endpoint"  # "endpoint" or "spec"


@dataclass
class EndpointInfo:
    path: str
    method: str
    summary: str
    description: str
    parameters: list
    responses: dict
    request_body: dict = field(default_factory=dict)

    @property
    def full_text(self):
        return f"{self.summary} {self.description}".strip()

    @property
    def id(self):
        return f"{self.method.upper()} {self.path}"


def check_description_clarity(ep: EndpointInfo) -> list[Finding]:
    """
    Maps to the 'Purpose' component. Hasan et al. found 56% of real MCP tools
    fail to state their purpose clearly — the single most consequential gap.

    Length threshold follows the published guidance that a tool description
    should run to roughly 3-4 sentences (~150 chars) rather than a bare label.
    """
    findings = []
    text = ep.full_text
    sentence_count = len([s for s in re.split(r"[.!?]+", text) if s.strip()])

    if not text:
        findings.append(Finding(
            "description_clarity", "fail", ep.id,
            "No summary or description at all — a model has nothing to decide whether to call this."
        ))
    elif len(text) < 40 or sentence_count < 1:
        findings.append(Finding(
            "description_clarity", "fail", ep.id,
            f"Description is a bare label ({len(text)} chars) — this is the 'Unclear Purpose' smell, "
            "found in 56% of real MCP tools and the strongest predictor of wrong tool selection."
        ))
    elif len(text) < 150 or sentence_count < 3:
        findings.append(Finding(
            "description_clarity", "warning", ep.id,
            f"Description is thin ({len(text)} chars, ~{sentence_count} sentence(s)) — published guidance "
            "suggests 3-4 sentences covering function, behaviour, and what is returned."
        ))
    else:
        findings.append(Finding("description_clarity", "pass", ep.id, "Has a substantive description."))
    return findings


def check_side_effects(ep: EndpointInfo) -> list[Finding]:
    findings = []
    write_methods = {"post", "put", "patch", "delete"}
    is_write = ep.method.lower() in write_methods
    text = ep.full_text.lower()
    mentions_effect = any(k in text for k in [
        "creates", "deletes", "updates", "modifies", "cancels", "read-only",
        "does not modify", "no side effect", "idempotent"
    ])
    if is_write and not mentions_effect:
        findings.append(Finding(
            "side_effects", "fail", ep.id,
            f"{ep.method.upper()} endpoint doesn't state its side effect in the description — "
            "an agent can't tell if this is safe to call speculatively."
        ))
    elif is_write:
        findings.append(Finding("side_effects", "pass", ep.id, "Write endpoint documents its effect."))
    else:
        findings.append(Finding("side_effects", "pass", ep.id, "Read-only endpoint (low risk by method)."))

    # Idempotency signal for write endpoints
    if is_write:
        has_idempotency_key = any(
            "idempot" in str(p).lower() for p in ep.parameters
        ) or "idempot" in text
        if not has_idempotency_key:
            findings.append(Finding(
                "side_effects", "warning", ep.id,
                "No idempotency key or note found — a retrying agent could create duplicate side effects."
            ))
    return findings


def check_error_responses(ep: EndpointInfo) -> list[Finding]:
    """
    An agent needs to know what a failure means so it can decide what to try
    next. A bare status code dead-ends it.

    NOTE: OpenAPI's `default` response is a legitimate — and common — way to
    document errors (Stripe's entire public spec uses it). Counting only 4xx/5xx
    codes here previously failed every endpoint in specs that use `default`.
    """
    findings = []
    error_codes = [
        c for c in ep.responses
        if str(c).startswith(("4", "5")) or str(c).lower() == "default"
    ]
    if not error_codes:
        findings.append(Finding(
            "error_responses", "fail", ep.id,
            "No error responses documented (no 4xx/5xx codes and no `default` response) — "
            "an agent has no way to distinguish or recover from failures."
        ))
        return findings

    vague_count = 0
    for code in error_codes:
        resp = ep.responses[code]
        desc = str(resp.get("description", "")) if isinstance(resp, dict) else ""
        has_schema = isinstance(resp, dict) and bool(resp.get("content"))
        if len(desc) < 15 and not has_schema:
            vague_count += 1

    if vague_count == len(error_codes):
        findings.append(Finding(
            "error_responses", "warning", ep.id,
            f"{len(error_codes)} error code(s) documented but all vague (generic descriptions, no schema) — "
            "an agent gets a status code but no actionable next step."
        ))
    else:
        findings.append(Finding("error_responses", "pass", ep.id, "Error responses have useful detail."))
    return findings


def check_auth_clarity(ep: EndpointInfo, security_schemes: dict) -> list[Finding]:
    findings = []
    if not security_schemes:
        findings.append(Finding(
            "auth_clarity", "warning", ep.id,
            "No security schemes defined in the spec at all — agents have no way to know what scope to request."
        ))
        return findings

    oauth_schemes = {
        k: v for k, v in security_schemes.items()
        if isinstance(v, dict) and v.get("type") == "oauth2"
    }
    if not oauth_schemes:
        findings.append(Finding(
            "auth_clarity", "warning", ep.id,
            "No OAuth2 scheme found — scoped, delegated-authority tokens for agents aren't possible as specified."
        ))
    else:
        has_scopes = any(
            v.get("flows", {}).get("clientCredentials", {}).get("scopes")
            or v.get("flows", {}).get("authorizationCode", {}).get("scopes")
            for v in oauth_schemes.values()
        )
        if has_scopes:
            findings.append(Finding("auth_clarity", "pass", ep.id, "OAuth2 with defined scopes present."))
        else:
            findings.append(Finding(
                "auth_clarity", "warning", ep.id,
                "OAuth2 present but no scopes defined — can't distinguish delegated vs autonomous agent access."
            ))
    return findings


def check_ambiguity(endpoints: list[EndpointInfo], max_reported: int = 25) -> list[Finding]:
    """
    Pairwise-compare endpoint descriptions to flag near-duplicates that could
    confuse a model.

    On large specs this is O(n^2) in comparisons and can produce thousands of
    findings, so we score against all of them but only report the worst
    `max_reported` pairs — an unreadable report gets ignored, which helps nobody.
    """
    scored = []
    for i in range(len(endpoints)):
        for j in range(i + 1, len(endpoints)):
            a, b = endpoints[i], endpoints[j]
            if a.path == b.path:
                continue
            text_a, text_b = a.full_text.lower(), b.full_text.lower()
            if not text_a or not text_b:
                continue
            similarity = difflib.SequenceMatcher(None, text_a, text_b).ratio()
            if similarity > 0.7:
                scored.append((similarity, a, b))

    if not scored:
        return [Finding("ambiguity", "pass", "all", "No significant description overlap detected.")]

    scored.sort(key=lambda t: t[0], reverse=True)

    findings = [
        Finding(
            "ambiguity", "warning", f"{a.id} vs {b.id}",
            f"Descriptions are {sim:.0%} similar — a model may confuse these two endpoints."
        )
        for sim, a, b in scored[:max_reported]
    ]

    if len(scored) > max_reported:
        findings.append(Finding(
            "ambiguity", "warning", "(summary)",
            f"{len(scored)} similar-description pairs found in total; showing the "
            f"{max_reported} most similar. Large catalogues usually need naming and "
            "description conventions rather than pair-by-pair fixes."
        ))
    return findings


def check_parameter_explanation(ep: EndpointInfo) -> list[Finding]:
    """
    Maps to the 'Opaque Parameters' smell (84.3% prevalence in Hasan et al.).

    The paper's motivating case: a Yahoo Finance tool referred to "start" and
    "end" without naming them as explicit parameters or specifying a date
    format. The model couldn't construct a bounded range, so it fell back to a
    broad `period` parameter and pulled multi-year windows for narrow questions
    — inflating payload size, latency and token cost. As the authors put it,
    that is a specification problem in the description, not a model bug.

    So: a parameter needs a description AND, for anything format-sensitive,
    an explicit format hint.
    """
    findings = []
    params = [p for p in ep.parameters if isinstance(p, dict) and p.get("name")]

    if not params:
        findings.append(Finding(
            "parameter_explanation", "pass", ep.id, "No parameters to document."
        ))
        return findings

    undocumented = [p["name"] for p in params if not str(p.get("description", "")).strip()]
    if undocumented:
        severity = "fail" if len(undocumented) == len(params) else "warning"
        findings.append(Finding(
            "parameter_explanation", severity, ep.id,
            f"{len(undocumented)}/{len(params)} parameter(s) have no description "
            f"({', '.join(undocumented[:4])}) — the agent must guess their meaning."
        ))

    # Format-sensitive params (dates, times, enums) need an explicit format hint
    format_sensitive = []
    for p in params:
        name = p["name"].lower()
        schema = p.get("schema", {}) if isinstance(p.get("schema"), dict) else {}
        desc = str(p.get("description", "")).lower()
        # Split on non-alphanumerics so we match whole tokens, not substrings.
        # (Naive substring matching flags "customer_id" because "to" is inside "custo-mer".)
        name_tokens = set(re.split(r"[^a-z0-9]+", name))
        temporal_tokens = {"date", "time", "datetime", "timestamp", "from", "to",
                           "start", "end", "since", "until", "before", "after"}
        looks_temporal = bool(name_tokens & temporal_tokens)
        has_format_hint = (
            schema.get("format")
            or schema.get("enum")
            or schema.get("pattern")
            or any(hint in desc for hint in ["yyyy", "iso", "format", "utc", "epoch", "rfc"])
        )
        if looks_temporal and not has_format_hint:
            format_sensitive.append(p["name"])

    if format_sensitive:
        findings.append(Finding(
            "parameter_explanation", "warning", ep.id,
            f"Date/time parameter(s) with no format specified ({', '.join(format_sensitive[:4])}) — "
            "agents commonly pick the wrong format or fall back to a broader default range."
        ))

    if not undocumented and not format_sensitive:
        findings.append(Finding(
            "parameter_explanation", "pass", ep.id,
            "All parameters documented with adequate format detail."
        ))
    return findings


def check_usage_guidelines(ep: EndpointInfo) -> list[Finding]:
    """
    Maps to the 'Missing Usage Guidelines' smell (89.3% prevalence).

    A description says what a tool does. Guidelines say WHEN to reach for it —
    activation criteria — and when to reach for something else instead. This is
    what stops an agent picking a plausible-but-wrong endpoint.
    """
    findings = []
    text = ep.full_text.lower()

    if not text:
        findings.append(Finding(
            "usage_guidelines", "fail", ep.id,
            "No description, so no usage guidance either — nothing tells the agent when to call this."
        ))
        return findings

    # Regex rather than literal substrings: real descriptions insert words into
    # these phrases ("use this ONLY when...", "call this ONLY IF..."), which a
    # fixed-string list silently misses.
    activation_patterns = [
        r"\buse (this|it|these)?\s*\w{0,8}\s*(when|if|for|to|after|before|once)\b",
        r"\bcall (this|it)?\s*\w{0,8}\s*(when|if|after|before|once)\b",
        r"\b(when|if) you need\b",
        r"\bfor (cases|situations|scenarios) where\b",
        r"\bintended for\b",
        r"\b(do not|don't|never) (use|call)\b",
        r"\b(instead|rather than) (use|call|using|calling)\b",
        r"\b(prefer|preferred over)\b",
        r"\bshould (only )?be (used|called)\b",
    ]
    has_guidance = any(re.search(p, text) for p in activation_patterns)

    if has_guidance:
        findings.append(Finding(
            "usage_guidelines", "pass", ep.id,
            "Description includes activation criteria (when to use / when not to)."
        ))
    else:
        findings.append(Finding(
            "usage_guidelines", "warning", ep.id,
            "No activation criteria — description states what the endpoint does but not when an agent "
            "should choose it over a similar one. This smell appears in 89.3% of real MCP tools."
        ))
    return findings


# --------------------------------------------------------------------------
# Category 8: Tool surface & schema strictness
# Derived from OpenAI's function-calling guidance, which is explicit that
# function definitions count against the model's context limit and are billed
# as input tokens, and recommends keeping the initially-available tool count
# small (they suggest under 20) for higher accuracy.
#
# This converges with two independent sources:
#   - MCP client best practices (progressive discovery; loading every tool
#     definition upfront wastes tokens and degrades model performance)
#   - Hasan et al., who measured a 67.46% increase in execution steps when all
#     description components were enriched
# Three sources, one conclusion: fewer, tighter tools beat more, richer ones.
# --------------------------------------------------------------------------

# OpenAI describes this as a soft suggestion rather than a hard limit.
RECOMMENDED_MAX_TOOLS = 20


def check_tool_surface(endpoints: list[EndpointInfo]) -> list[Finding]:
    """Spec-level check: is the catalogue small enough for reliable selection?"""
    n = len(endpoints)
    if n == 0:
        return []
    if n <= RECOMMENDED_MAX_TOOLS:
        return [Finding(
            "tool_surface", "pass", "(spec)",
            f"{n} operation(s) — within the recommended ceiling of "
            f"~{RECOMMENDED_MAX_TOOLS} tools available at the start of a turn.",
            scope="spec",
        )]

    severity = "fail" if n > RECOMMENDED_MAX_TOOLS * 3 else "warning"
    return [Finding(
        "tool_surface", severity, "(spec)",
        f"{n} operations — well above the ~{RECOMMENDED_MAX_TOOLS} tools that "
        "should be available to a model at the start of a turn. Exposing all of "
        "them as tools inflates context cost and degrades selection accuracy. "
        "Expose a curated subset, group by namespace, or use progressive "
        "discovery / tool search to load the rest on demand.",
        scope="spec",
    )]


def check_schema_strictness(ep: EndpointInfo) -> list[Finding]:
    """
    Endpoint-level checks derived from OpenAI's schema guidance:
      - strict mode requires `additionalProperties: false` and every property
        listed in `required`
      - enums and object structure should make invalid states unrepresentable
    """
    findings = []
    body_schema = _request_body_schema(ep)

    if body_schema:
        props = body_schema.get("properties") or {}
        required = set(body_schema.get("required") or [])
        additional = body_schema.get("additionalProperties")

        if props:
            if additional is not False:
                findings.append(Finding(
                    "tool_surface", "warning", ep.id,
                    "Request body schema doesn't set `additionalProperties: false` — "
                    "incompatible with strict mode, so argument generation is "
                    "best-effort rather than schema-guaranteed."
                ))
            missing_required = [p for p in props if p not in required]
            if missing_required and len(missing_required) == len(props):
                findings.append(Finding(
                    "tool_surface", "warning", ep.id,
                    f"No request-body properties are marked required "
                    f"({len(props)} optional) — strict mode requires all fields in "
                    "`properties` to be listed in `required` (use a null type for "
                    "genuinely optional ones)."
                ))

    # Enum opportunity: params whose name implies a closed set but which accept free strings.
    enum_candidates = []
    for p in ep.parameters:
        if not isinstance(p, dict) or not p.get("name"):
            continue
        schema = p.get("schema") if isinstance(p.get("schema"), dict) else {}
        if not schema or schema.get("enum"):
            continue
        if schema.get("type") != "string":
            continue
        tokens = set(re.split(r"[^a-z0-9]+", p["name"].lower()))
        closed_set_hints = {"status", "state", "type", "kind", "mode", "currency",
                            "direction", "sort", "order", "level", "category"}
        if tokens & closed_set_hints:
            enum_candidates.append(p["name"])

    if enum_candidates:
        findings.append(Finding(
            "tool_surface", "warning", ep.id,
            f"Parameter(s) suggesting a fixed set of values accept free-form strings "
            f"({', '.join(enum_candidates[:4])}) — an enum would make invalid states "
            "unrepresentable instead of leaving the model to guess valid values."
        ))

    if not findings:
        findings.append(Finding(
            "tool_surface", "pass", ep.id, "Schema is strict-mode friendly."
        ))
    return findings


def _request_body_schema(ep: EndpointInfo) -> dict:
    """Best-effort extraction of the JSON request body schema."""
    body = ep.request_body if isinstance(ep.request_body, dict) else {}
    content = body.get("content") or {}
    for media_type, media in content.items():
        if "json" in str(media_type).lower() and isinstance(media, dict):
            schema = media.get("schema")
            if isinstance(schema, dict):
                return schema
    return {}


# Weights are judgement calls, NOT empirical. Description clarity is weighted
# highest because 'Unclear Purpose' most directly drives wrong tool selection;
# be upfront that this is a prior, not a measured effect size.
CATEGORY_WEIGHTS = {
    "description_clarity": 0.20,
    "side_effects": 0.13,
    "error_responses": 0.13,
    "auth_clarity": 0.09,
    "ambiguity": 0.13,
    "parameter_explanation": 0.12,
    "usage_guidelines": 0.10,
    "tool_surface": 0.10,
}
