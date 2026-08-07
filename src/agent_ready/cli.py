"""
Command-line interface for agent-ready.

Exit codes (designed for CI use):
    0  audit passed all configured gates
    1  audit ran, but a gate failed (score below --min-score, or
       --max-fails exceeded)
    2  the spec could not be loaded or parsed
"""

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .auditor import (
    SpecLoadError,
    endpoints_ready_for_mcp,
    load_spec,
    result_to_dict,
    run_audit,
)
from .baseline import (
    BaselineError,
    compare_to_baseline,
    load_baseline,
    write_baseline,
)
from .mcp_generator import generate_mcp_scaffold
from .report_generator import generate_report

EXIT_OK = 0
EXIT_GATE_FAILED = 1
EXIT_SPEC_ERROR = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-ready",
        description=(
            "Audit an OpenAPI spec for AI-agent readiness and optionally "
            "generate an MCP server scaffold."
        ),
        epilog=(
            "CI example:\n"
            "  agent-ready openapi.yaml --min-score 60 --max-fails 0 --quiet\n"
            "\n"
            "Adopting on an existing API, where fixing everything first is not\n"
            "realistic — record today's findings, then block only regressions:\n"
            "  agent-ready openapi.yaml --write-baseline .agent-ready-baseline.json\n"
            "  agent-ready openapi.yaml --baseline .agent-ready-baseline.json --max-new 0\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("spec", help="Path or http(s) URL to an OpenAPI spec (.json/.yaml)")
    parser.add_argument(
        "--format",
        choices=["markdown", "json", "text"],
        default="text",
        help="Output format (default: text summary to stdout)",
    )
    parser.add_argument("--out", help="Write the report to this file instead of stdout")
    parser.add_argument(
        "--min-score",
        type=float,
        help="Exit 1 if the overall score is below this value (CI gate)",
    )
    parser.add_argument(
        "--max-fails",
        type=int,
        help="Exit 1 if the number of FAIL findings exceeds this value (CI gate)",
    )
    parser.add_argument(
        "--write-baseline",
        metavar="PATH",
        help=(
            "Record current findings to PATH as an accepted baseline. Run this "
            "once when adopting the tool on an existing API."
        ),
    )
    parser.add_argument(
        "--baseline",
        metavar="PATH",
        help=(
            "Compare against a baseline file and report what changed. Combine "
            "with --max-new to fail only on regressions."
        ),
    )
    parser.add_argument(
        "--max-new",
        type=int,
        metavar="N",
        help=(
            "Exit 1 if more than N findings are new since --baseline. "
            "Use --max-new 0 to block any regression."
        ),
    )
    parser.add_argument(
        "--generate-mcp",
        metavar="PATH",
        nargs="?",
        const="mcp_server_scaffold.py",
        help="Generate an MCP server scaffold for the endpoints that pass",
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress non-essential output")
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    return parser



def _make_progress(endpoint_count: int, quiet: bool):
    """
    Report progress on stderr for the ambiguity check.

    Only shown when the run is large enough to be worth waiting for and stderr
    is a terminal — piping to a file or another command should not accumulate
    carriage returns. Progress goes to stderr so `--format json` stdout stays
    machine-readable.
    """
    if quiet or endpoint_count < 150 or not sys.stderr.isatty():
        return None

    def report(done: int, total: int):
        pct = 100 * done / total if total else 100
        bar_width = 24
        filled = int(bar_width * pct / 100)
        bar = "#" * filled + "-" * (bar_width - filled)
        end = "\n" if done >= total else ""
        print(
            f"\r  comparing {endpoint_count} endpoints for ambiguity "
            f"[{bar}] {pct:3.0f}%{end}",
            end=end or "",
            file=sys.stderr,
            flush=True,
        )

    return report


def _text_summary(spec: dict, result: dict) -> str:
    info = spec.get("info") or {}
    lines = [
        f"{info.get('title', 'Untitled API')} (v{info.get('version', 'unknown')})",
        f"AI-readiness score: {result['overall_score']}/100",
        (
            f"Endpoints: {result['endpoint_count']}  "
            f"Fails: {result['fail_count']}  Warnings: {result['warning_count']}"
        ),
        "",
        "Category scores:",
    ]
    for cat, summary in result["category_summary"].items():
        label = cat.replace("_", " ").title()
        lines.append(f"  {label:<28} {summary['score']:>5}/100")
    return "\n".join(lines)


def main(argv=None) -> int:
    try:
        return _run(argv)
    except BrokenPipeError:
        # Downstream command closed the pipe (e.g. `agent-ready spec.yaml | head`).
        # Suppress Python's noisy teardown warning and exit quietly.
        try:
            sys.stdout.close()
        except BrokenPipeError:
            pass
        return EXIT_OK
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


def _run(argv=None) -> int:
    args = build_parser().parse_args(argv)

    try:
        spec = load_spec(args.spec)
    except SpecLoadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_SPEC_ERROR

    if not isinstance(spec, dict) or not spec.get("paths"):
        print(
            "error: no 'paths' found — this does not look like an OpenAPI spec.",
            file=sys.stderr,
        )
        return EXIT_SPEC_ERROR

    endpoint_count = len(spec.get("paths") or {})
    result = run_audit(spec, progress=_make_progress(endpoint_count, args.quiet))

    if result["endpoint_count"] == 0:
        print("error: spec contains no operations to audit.", file=sys.stderr)
        return EXIT_SPEC_ERROR

    if args.format == "json":
        output = json.dumps(result_to_dict(spec, result), indent=2)
    elif args.format == "markdown":
        output = generate_report(spec, result)
    else:
        output = _text_summary(spec, result)

    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        if not args.quiet:
            print(f"Report written to {args.out}")
            print(f"AI-readiness score: {result['overall_score']}/100")
    else:
        print(output)

    if args.generate_mcp:
        ready = endpoints_ready_for_mcp(result)
        generate_mcp_scaffold(spec, ready, args.generate_mcp)
        if not args.quiet:
            print(
                f"MCP scaffold written to {args.generate_mcp} "
                f"({len(ready)}/{result['endpoint_count']} endpoints included)"
            )

    # Baseline: write, or compare and gate on regressions only.
    if args.write_baseline:
        write_baseline(args.write_baseline, spec, result)
        if not args.quiet:
            print(
                f"Baseline written to {args.write_baseline} "
                f"({len(result['gaps'])} finding(s) recorded as accepted)"
            )

    comparison = None
    if args.baseline:
        try:
            comparison = compare_to_baseline(result, load_baseline(args.baseline))
        except BaselineError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_SPEC_ERROR

        if not args.quiet:
            print()
            print(
                f"Compared against {args.baseline}: "
                f"{comparison['new_count']} new, "
                f"{comparison['fixed_count']} fixed, "
                f"{comparison['unchanged_count']} unchanged"
            )
            for f in comparison["new_findings"][:20]:
                marker = "FAIL" if f.severity == "fail" else "WARN"
                print(f"  NEW {marker}  {f.endpoint} — {f.message}")
            if len(comparison["new_findings"]) > 20:
                print(f"  ... and {len(comparison['new_findings']) - 20} more")

    # CI gates
    gate_failed = False
    if args.min_score is not None and result["overall_score"] < args.min_score:
        print(
            f"GATE FAILED: score {result['overall_score']} is below "
            f"minimum {args.min_score}",
            file=sys.stderr,
        )
        gate_failed = True
    if args.max_fails is not None and result["fail_count"] > args.max_fails:
        print(
            f"GATE FAILED: {result['fail_count']} FAIL finding(s) exceeds "
            f"maximum {args.max_fails}",
            file=sys.stderr,
        )
        gate_failed = True

    if (
        comparison is not None
        and args.max_new is not None
        and comparison["new_count"] > args.max_new
    ):
        print(
            f"GATE FAILED: {comparison['new_count']} new finding(s) since "
            f"baseline exceeds maximum {args.max_new}",
            file=sys.stderr,
        )
        gate_failed = True

    return EXIT_GATE_FAILED if gate_failed else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
