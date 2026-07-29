# agent-ready

> The practical implementation of [*The Silent Revolution: How AI Agents Are Rewriting the Rules of API Growth*](https://medium.com/@shahfaisaldarwaish/the-silent-revolution-how-ai-agents-are-rewriting-the-rules-of-api-growth-1bef8e3e0e80) — turning "audit your agent readiness" from advice into a command you can run.

[![CI](https://github.com/sfaisal/agent-ready/actions/workflows/ci.yml/badge.svg)](https://github.com/sfaisal/agent-ready/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**Audit any OpenAPI spec for AI-agent readiness — then generate an MCP server scaffold for the endpoints that pass.**

Your API works fine for human developers. That doesn't mean an AI agent can use
it reliably. `agent-ready` scores a spec against seven categories of
agent-specific failure, tells you exactly which endpoints will cause trouble and
why, and can gate a CI pipeline so specs don't regress.

```bash
pip install openapi-agent-ready
agent-ready https://example.com/openapi.yaml
```

```
Generic Booking Platform API (v1.0)
AI-readiness score: 58.4/100
Endpoints: 4  Fails: 7  Warnings: 14

Category scores:
  Description Clarity           37.5/100
  Side Effects                  70.0/100
  Error Responses               62.5/100
  Auth Clarity                  50.0/100
  Ambiguity                    100.0/100
  Parameter Explanation         10.0/100
  Usage Guidelines              50.0/100
  Tool Surface                 100.0/100
```

## Why this exists

A human developer who hits a vague `404` shrugs and checks the docs. An agent
dead-ends. A human who sees two similar endpoints picks the right one from
context. An agent picks whichever description sounds closer.

This tool is the practical implementation of an argument I made in
[**The Silent Revolution: How AI Agents Are Rewriting the Rules of API
Growth**](https://medium.com/@shahfaisaldarwaish/the-silent-revolution-how-ai-agents-are-rewriting-the-rules-of-api-growth-1bef8e3e0e80).
That article's first recommendation was to *audit your agent readiness — check
OpenAPI spec completeness, authentication friction, and error message
machine-readability, and score yourself honestly.* This is that audit, made
runnable.

The three characteristics of an agent-ready API identified in the article map
directly onto the rubric:

| Article | Rubric categories |
|---|---|
| **Machine-readable schemas** | 1 (description clarity), 6 (parameters), 7 (usage guidelines) |
| **Frictionless authentication** | 4 (auth/scope clarity) |
| **Structured error handling** | 3 (actionable errors) |

Categories 2 (side effects) and 5 (ambiguity) were added during implementation
— they turned out to matter as much as the original three, which is the usual
reward for actually building the thing you wrote about.

The gap is measurable, and enormous. A 2026 study of 856 tools across 103 MCP
servers found that **97.1% of tool descriptions contained at least one quality
defect**, and **56% failed to state their purpose clearly** — with no
significant difference between official and community-maintained servers.

`agent-ready` catches those defects at the spec level, before they become
agent failures in production.

## Install

```bash
pip install openapi-agent-ready             # core
pip install "openapi-agent-ready[mcp]"      # + MCP scaffold generation
```

From source:

```bash
git clone https://github.com/sfaisal/agent-ready
cd agent-ready
pip install -e ".[dev]"
```

## Usage

```bash
# Audit a local spec
agent-ready openapi.yaml

# Audit a remote spec
agent-ready https://api.example.com/openapi.json

# Full markdown report
agent-ready openapi.yaml --format markdown --out report.md

# Machine-readable output
agent-ready openapi.yaml --format json

# Generate an MCP server for the endpoints that pass
agent-ready openapi.yaml --generate-mcp server.py
```

### Use it as a CI gate

This is the point. Add it to your pipeline so agent-readiness can't silently
regress when someone adds an endpoint:

```yaml
- name: Check API is agent-ready
  run: |
    pip install openapi-agent-ready
    agent-ready openapi.yaml --min-score 60 --max-fails 0
```

`60` is a reasonable starting threshold — below it, an API is likely to lose
agent-driven integrations to a better-documented competitor. Raise it as you
close gaps; a spec that scores 60 has real problems, it just doesn't have
catastrophic ones.

Exit codes:

| Code | Meaning |
|---|---|
| `0` | All configured gates passed |
| `1` | A gate failed (`--min-score` / `--max-fails`) |
| `2` | The spec could not be loaded or parsed |

## Real-world results

Running it against Stripe's production spec (587 endpoints), as of v0.1.0.
These figures move whenever the rubric changes — rerun the tool for current
numbers:

```
Stripe API (v2026-06-24.dahlia)
AI-readiness score: 61.3/100
Endpoints: 587  Fails: 393  Warnings: 2133

Category scores:
  Description Clarity           68.3/100
  Side Effects                  64.7/100
  Error Responses              100.0/100
  Auth Clarity                  50.0/100
  Ambiguity                     50.0/100
  Parameter Explanation         43.0/100
  Usage Guidelines              51.2/100
  Tool Surface                  49.6/100
```

Two things worth drawing out.

**The first run scored 47.9 with error handling at 0/100 — and that was a bug
in this tool, not in Stripe.** Stripe documents errors via OpenAPI's `default`
response rather than enumerating explicit 4xx/5xx codes. Perfectly valid, and
arguably cleaner. The check only looked for `4`/`5` prefixes, so it failed all
587 endpoints. Fixed, regression-tested, and the reason this README says to run it against a
real spec before trusting it.

**Stripe is widely and correctly regarded as agent-ready, yet its raw OpenAPI
spec scores 61.3.** That isn't a contradiction — it's the whole point. Stripe's
agent-readiness comes from the *Agent Toolkit* layered on top: curated tool
definitions, an MCP server, managed OAuth. The raw spec is the input to that
layer, not the layer itself. Which is exactly why this tool ships with
`--generate-mcp`: the audit tells you what to fix, and the scaffold is where the
curation happens.

Genuine findings that survived scrutiny: `GET /v1/account` and
`GET /v1/accounts/{account}` have **100% identical descriptions**, and the spec
uses bearer/basic API keys rather than scoped OAuth2, so there's no way to
express "this agent may only read."

### Python API

```python
from agent_ready import load_spec, run_audit

spec = load_spec("openapi.yaml")
result = run_audit(spec)

print(result["overall_score"])
for finding in result["gaps"]:
    print(finding.severity, finding.endpoint, finding.message)
```

## The eight categories

| # | Category | What it catches | Published anchor |
|---|---|---|---|
| 1 | **Description clarity** | Endpoints a model can't decide whether to call | "Unclear Purpose" smell — 56% prevalence |
| 2 | **Side-effect signaling** | Write endpoints that don't say they're writes; missing idempotency | MCP tool annotations (destructive / open-world) |
| 3 | **Actionable errors** | Failures an agent can't recover from | Adjacent to "Limitations" — 89.8% prevalence |
| 4 | **Auth/scope clarity** | No way to tell delegated from autonomous agent access | *Not covered by existing rubrics* |
| 5 | **Endpoint ambiguity** | Two endpoints a model could confuse | 73% of servers had repeated tool names |
| 6 | **Parameter explanation** | Undocumented or format-ambiguous parameters | "Opaque Parameters" — 84.3% prevalence |
| 7 | **Usage guidelines** | States what it does, but not *when to call it* | "Missing Usage Guidelines" — 89.3% prevalence |
| 8 | **Tool surface & schema strictness** | Too many tools to select from; schemas incompatible with strict mode | OpenAI function-calling guidance |

### A worked example of why category 6 matters

From the Hasan et al. paper: a Yahoo Finance MCP tool referred to "start" and
"end" without naming them as explicit parameters or specifying a date format.
The model couldn't construct a bounded range, so it fell back to a broad
`period` parameter and pulled multi-year windows to answer narrow questions —
inflating payload size, latency, and token cost. As the authors put it, that is
a specification problem in the description, not a model bug.

`agent-ready` flags exactly this: a date-like parameter with no `format`,
`pattern`, `enum`, or format hint in its description.

## Provenance

Categories **1–5** were derived independently, from how MCP tool selection
works plus standard API design principles reframed for a non-human caller.
Categories **6–7** were added *after* validating the rubric against published
empirical research:

- Hasan, Li, Rajbahadur, Adams & Hassan (Queen's University), *"Model Context
  Protocol (MCP) Tool Descriptions Are Smelly! Towards Improving AI Agent
  Efficiency with Augmented MCP Tool Descriptions"* —
  [arXiv:2602.14878](https://arxiv.org/abs/2602.14878)
- Wang et al., *"From Docs to Descriptions: Smell-Aware Evaluation of MCP
  Server Descriptions"* — [arXiv:2602.18914](https://arxiv.org/abs/2602.18914)
- Anthropic, [*Writing effective tools for agents*](https://www.anthropic.com/engineering/writing-tools-for-agents)
- [OpenAI's function-calling guidance](https://developers.openai.com/api/docs/guides/function-calling)
  — source for category 8. OpenAI recommends keeping the initially-available
  tool count small (they suggest under 20) and notes that function definitions
  count against the model's context limit and are billed as input tokens.

## The score is a diagnostic, not a target

Hasan et al. also found that enriching *all* description components improved
task success by a median **5.85 percentage points** — but increased execution
steps by **67.46%** and *regressed* performance in **16.67%** of cases, because
richer descriptions consume context window and raise cost. Their ablation study
found that shorter, targeted descriptions often performed equivalently.

**Fix the fails. Don't gold-plate the passes.** The right operating point
depends on your domain and your model, and choosing it is a product decision
rather than an engineering one.

## Known limitations

Named up front, because a tool that hides its weaknesses is worse than one that
doesn't:

- **The ambiguity check uses string similarity**, so it misses endpoints that
  are semantically similar but differently worded. Embedding-based similarity
  would be stronger. ([#1](https://github.com/sfaisal/agent-ready/issues))
- **Category weights are judgement calls, not measured effect sizes.**
  Description clarity is weighted highest on the prior that wrong tool selection
  is the costliest failure — that prior is untested here.
- **Static analysis can't catch descriptions that are fluent but wrong.** Only
  running a real agent against the real API catches semantic inaccuracy.
- **OpenAPI 3.x only.** Swagger 2.0 and AsyncAPI aren't supported yet.

## Related writing

- [The Silent Revolution: How AI Agents Are Rewriting the Rules of API Growth](https://medium.com/@shahfaisaldarwaish/the-silent-revolution-how-ai-agents-are-rewriting-the-rules-of-api-growth-1bef8e3e0e80)
  — the argument this tool implements: why agent discoverability is displacing
  documentation quality as the competitive moat for API platforms, and what to
  measure instead of DAU and NPS.

## Roadmap

- [ ] Embedding-based ambiguity detection
- [ ] Swagger 2.0 support
- [ ] Pagination and rate-limit documentation checks
- [ ] LLM-assisted rewriting of flagged descriptions (`--fix`)
- [ ] Live tool-selection testing against a generated MCP server
- [ ] GitHub Action wrapper
- [ ] Baseline mode (`--baseline`) so CI only fails on *new* regressions

## Contributing

Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). New rubric
categories should come with a rationale for why the gap causes agent-specific
failure, ideally with a citation or a reproducible example.

## License

MIT — see [LICENSE](LICENSE).
