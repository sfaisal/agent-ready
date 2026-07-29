# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.0] - 2026-07-29

First release, published to PyPI as `openapi-agent-ready`.

> The distribution name differs from the repo name: PyPI rejected `agent-ready`
> as too similar to the existing `agentready` package. The import name
> (`agent_ready`) and CLI command (`agent-ready`) are unaffected.


### Added
- Seven-category AI-readiness rubric for OpenAPI specs
- CLI with text, markdown, and JSON output formats
- CI gate support via `--min-score` and `--max-fails` with documented exit codes
- Remote spec loading over http(s)
- Parameter `$ref` resolution and path-level shared parameter inheritance
- MCP server scaffold generation for endpoints that pass the audit
- Python API (`load_spec`, `run_audit`)

### Fixed
- OpenAPI `default` responses now count as error documentation. Previously only
  4xx/5xx codes were recognised, which failed every endpoint in specs that use
  `default` — including Stripe's entire public API. (Found by auditing Stripe.)
- Ambiguity findings are capped at the 25 most-similar pairs with a summary
  line, so large catalogues produce a readable report.
- Usage-guidelines detection is now regex-based; literal matching missed
  phrasings like "use this only when" and "use this only after".
- CLI exits cleanly when its output is piped to `head` or `less`.

### Added (category 8)
- **Tool Surface & Schema Strictness** category, derived from OpenAI's
  function-calling guidance: flags catalogues far above the ~20-tool ceiling,
  request bodies incompatible with strict mode (`additionalProperties: false`,
  all properties required), and parameters that imply a fixed value set but
  accept free-form strings where an enum belongs.
- Findings now carry a `scope` ("endpoint" or "spec"). Spec-level findings are
  weighted at 50% of their category so a single whole-API finding isn't averaged
  away against hundreds of endpoint findings. Stripe's tool-surface score was
  99/100 before this fix and 49.6/100 after.

### Changed
- Distribution name is now `openapi-agent-ready` (PyPI collision with
  `agentready`). Import name, CLI command, and repo name unchanged.
