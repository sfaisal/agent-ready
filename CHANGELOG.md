# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.2.1] - 2026-07-31

Found by running the published 0.2.0 against Asana's public spec.

### Fixed
- **Generated scaffolds could contain duplicate arguments and fail to parse.**
  OpenAPI identifies a parameter by name *and* location, and an operation-level
  declaration overrides a path-level one. The extractor concatenated both lists
  without deduplicating, so a spec declaring the same parameter at each level —
  Asana's declares `project_gid` that way — produced a function with two
  identically-named arguments, a Python `SyntaxError`. Parameters declared at
  both levels are now deduplicated with operation-level winning; the same name
  in a different location is still treated as distinct.
- **`--version` reported a stale hardcoded number.** The CLI printed `0.1.0`
  from a published 0.2.0 install. The version is now read from installed package
  metadata, so `pyproject.toml` is the single source of truth and cannot drift.

## [0.1.0] - 2026-07-28

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

### Added
- **MCP conformance test suite** (`tests/test_mcp_conformance.py`). Launches the
  generated scaffold as a subprocess, completes a real MCP handshake over stdio
  using the official client, and validates the advertised tool definitions:
  name format, uniqueness, description presence, `inputSchema` validity as JSON
  Schema, required-argument correctness, and side-effect disclosure. Previously
  the only check on generated output was that the file compiled.

### Fixed
- **Request body fields were silently dropped.** The generator read only
  `parameters`, so a `POST` endpoint's payload properties never became tool
  arguments — producing, for example, a create-booking tool with no way to say
  what to book. Syntactically valid and completely uncallable, which is exactly
  the class of bug a compile check cannot catch.
- **No arguments were marked required.** Every parameter was emitted with a
  `= None` default, so the advertised `inputSchema` had an empty `required`
  array. A model could call an endpoint with a `{path_param}` and omit it,
  producing a malformed URL. Path parameters are now always required, and
  `required: true` from the spec is honoured for query, header, and body fields.

### Fixed
- **Generated scaffold failed on mcp 2.0.** FastMCP was replaced by `MCPServer`
  in the 2.x SDK, so `from mcp.server.fastmcp import FastMCP` no longer resolved
  and every generated server crashed on import. The scaffold now imports through
  a compatibility shim supporting both generations, verified against mcp 1.28.1
  and 2.0.0.

### Added
- Generated tools now carry **`ToolAnnotations`** (`readOnlyHint`,
  `destructiveHint`, `idempotentHint`, `openWorldHint`) derived from the HTTP
  method. Prose in a description helps a model reason about side effects;
  annotations let a client *enforce* — auto-approving reads while requiring
  confirmation for writes. camelCase field names are used deliberately, as they
  are accepted by both SDK generations.

### Changed
- **Generated scaffolds are now self-documenting.** The file header states its
  dependencies (`pip install mcp httpx`), how to run it, a ready-to-paste Claude
  Desktop config block, and three things to fix before shipping — notably that
  the scaffold sends no credentials, so calls against a real API will 401 until
  auth is wired in. Previously someone receiving a generated file out of context
  had no way to know what it needed.
- **Generated servers are named after their source API** (`spotify-web-api`
  rather than the shared `generated-api-server` placeholder). Two generated
  servers connected to the same client no longer present identical names.
- **The `[mcp]` extra is now also available as `[server]`,** which describes it
  accurately: these are dependencies the *generated* server needs at runtime,
  not dependencies of the auditor. `[mcp]` is retained as an alias.

### Fixed
- **Conformance tests silently skipped under mcp 2.0.** The module-level guard
  probed `mcp.server.fastmcp`, which does not exist in the 2.x SDK, so the entire
  suite skipped against a current install — a green build that verified nothing.
  The guard now probes for either server class, and CI fails explicitly if the
  suite skips rather than runs.

### Fixed
- **Conformance tests failed against mcp 2.0.** The 2.x SDK renamed result
  fields from camelCase to snake_case (`serverInfo` -> `server_info`,
  `inputSchema` -> `input_schema`, `readOnlyHint` -> `read_only_hint`), and the
  tests were pinned to the 1.x spelling. Assertions now read fields through
  version-agnostic accessors. The suite is verified against both mcp 1.28.1 and
  2.0.0.
- **Failures inside anyio task groups are now readable.** Exception groups are
  flattened to their leaf causes and `BaseException` is caught rather than
  `Exception`, so a `BaseExceptionGroup` no longer escapes the diagnostic
  wrapper and leaves the real error truncated. This is what finally surfaced
  the rename above.

### Changed
- Tool listings are fetched once per generated file and cached rather than
  spawning a server subprocess per test, cutting spawns from ~14 to 4 and
  running roughly a third faster.
