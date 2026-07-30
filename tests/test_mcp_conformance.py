"""
Conformance tests for the generated MCP scaffold.

These are the tests that actually matter for `--generate-mcp`. Checking that the
generated file compiles proves almost nothing: a syntactically valid Python file
can still fail to speak MCP, expose malformed tool names, or hand a model an
inputSchema it can't fill.

So instead of inspecting the generated source, these tests:

  1. generate a scaffold from a real spec
  2. launch it as a subprocess, over stdio, using the official MCP client
  3. complete the protocol handshake
  4. call tools/list and validate the returned definitions against the
     constraints the MCP spec and the major model providers actually impose

If the scaffold can't complete a handshake, `--generate-mcp` is producing
something no agent can use, and these tests fail.
"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from agent_ready.auditor import endpoints_ready_for_mcp, load_spec, run_audit
from agent_ready.mcp_generator import generate_mcp_scaffold

# The official client. If MCP isn't installed, these tests are skipped rather
# than failed — scaffold generation is an optional extra.
#
# Guard on `mcp.server.fastmcp` specifically, not just `mcp`. The generated
# server imports FastMCP, so a top-level `mcp` that lacks that submodule (an
# unrelated package of the same name, or a pre-FastMCP release) would let these
# tests run and then fail confusingly in a subprocess rather than skip cleanly.
# The generated server needs a usable MCP server class. That class moved in
# mcp 2.0 (FastMCP -> MCPServer), so probe for either: guarding on the 1.x path
# alone would silently skip this entire module against a 2.x install, and CI
# would go green having verified nothing.
try:
    from mcp.server import MCPServer as _ServerClass  # mcp >= 2.0
except ImportError:  # pragma: no cover - depends on installed SDK
    try:
        from mcp.server.fastmcp import FastMCP as _ServerClass  # mcp 1.x
    except ImportError:
        _ServerClass = None

if _ServerClass is None:
    pytest.skip(
        "requires a usable MCP SDK (pip install 'openapi-agent-ready[server]')",
        allow_module_level=True,
    )

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXAMPLES = Path(__file__).parent.parent / "examples"

# Tool names must be safe to use as identifiers in a model's tool-calling
# interface. This pattern is the intersection of what the MCP spec allows and
# what OpenAI and Anthropic both accept.
TOOL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


@pytest.fixture(scope="module")
def generated_server(tmp_path_factory):
    """Generate a scaffold from the well-designed example spec."""
    tmp = tmp_path_factory.mktemp("scaffold")
    spec = load_spec(str(EXAMPLES / "agent_ready_booking_api.yaml"))
    result = run_audit(spec)
    ready = endpoints_ready_for_mcp(result)
    assert ready, "example spec should produce at least one MCP-ready endpoint"

    path = tmp / "server.py"
    generate_mcp_scaffold(spec, ready, str(path))
    return path


def _preflight(server_path):
    """
    Run the generated server directly and capture anything it prints on the way
    down.

    A server that crashes on import or startup surfaces through stdio_client
    only as an opaque TaskGroup exception group, with the real cause buried
    several frames deep. This runs it plainly first so the actual traceback is
    what gets reported.
    """
    proc = subprocess.run(
        [sys.executable, str(server_path)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if "Traceback" in proc.stderr:
        raise AssertionError(
            f"generated server failed to start (exit {proc.returncode}):\n{proc.stderr}"
        )


def _explain(exc: BaseException, depth: int = 0) -> str:
    """
    Flatten an exception group into readable leaf causes.

    anyio surfaces failures as nested TaskGroup exception groups, so the real
    error sits several levels down and default rendering truncates it. This
    walks the tree and reports the leaves, which is the part worth reading.
    """
    pad = "  " * depth
    subs = getattr(exc, "exceptions", None)
    if subs:
        return "\n".join(
            [f"{pad}{type(exc).__name__}: {exc}"]
            + [_explain(sub, depth + 1) for sub in subs]
        )
    return f"{pad}{type(exc).__name__}: {exc}"



def _field(obj, *names):
    """
    Read a result field across SDK generations.

    mcp 2.0 renamed result attributes from camelCase to snake_case
    (serverInfo -> server_info, inputSchema -> input_schema, readOnlyHint ->
    read_only_hint). Tests assert on protocol output, so they must not be
    pinned to one SDK's spelling.
    """
    for name in names:
        if hasattr(obj, name):
            return getattr(obj, name)
    raise AttributeError(
        f"{type(obj).__name__} has none of {names}; "
        f"available: {sorted(n for n in dir(obj) if not n.startswith('_'))[:12]}"
    )


def server_name(init):
    return _field(init, "server_info", "serverInfo").name


def input_schema(tool):
    return _field(tool, "input_schema", "inputSchema")


def read_only(tool):
    return _field(tool.annotations, "read_only_hint", "readOnlyHint")


# One server subprocess per generated file, not one per test.
#
# Each test previously spawned its own server, and pytest-asyncio creates a
# fresh event loop per test. On Python 3.10 that combination races at teardown:
# the subprocess may not be fully reaped before the loop closes, producing
# intermittent TaskGroup failures unrelated to anything under test. Tool
# listings are plain pydantic objects, so caching them is safe across loops.
_TOOLS_CACHE: dict = {}


async def fetch_tools(server_path):
    """
    Launch the generated server over stdio, complete the MCP handshake, and
    return its advertised tool definitions. Cached per server path.
    """
    key = str(server_path)
    if key in _TOOLS_CACHE:
        return _TOOLS_CACHE[key]

    _preflight(server_path)

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(server_path)],
        env=None,
    )
    # errlog is handed to subprocess.Popen, so it needs a real file descriptor.
    # An in-memory buffer has no fileno() and fails before the server even starts.
    with tempfile.TemporaryFile(mode="w+") as errlog:
        try:
            async with (
                stdio_client(params, errlog=errlog) as (read, write),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                listing = await session.list_tools()
                _TOOLS_CACHE[key] = listing.tools
                return listing.tools
        except BaseException as exc:
            errlog.seek(0)
            raise AssertionError(
                f"MCP session failed:\n{_explain(exc)}\n"
                f"--- server stderr ---\n{errlog.read()}"
            ) from exc


# --------------------------------------------------------------------------
# Protocol conformance
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_server_completes_handshake(generated_server):
    """
    The baseline: can a real MCP client connect at all?

    This is what compiling doesn't tell you. A server that imports cleanly can
    still fail initialization and be invisible to every agent.
    """
    _preflight(generated_server)

    params = StdioServerParameters(
        command=sys.executable, args=[str(generated_server)], env=None
    )
    with tempfile.TemporaryFile(mode="w+") as errlog:
        try:
            async with (
                stdio_client(params, errlog=errlog) as (read, write),
                ClientSession(read, write) as session,
            ):
                init = await session.initialize()
                assert server_name(init), "server must report a name"
                assert init.capabilities.tools is not None, (
                    "server must advertise tool capability"
                )
        except BaseException as exc:
            errlog.seek(0)
            raise AssertionError(
                f"handshake failed:\n{_explain(exc)}\n"
                f"--- server stderr ---\n{errlog.read()}"
            ) from exc


@pytest.mark.asyncio
async def test_server_advertises_tools(generated_server):
    tools = await fetch_tools(generated_server)
    assert len(tools) > 0, "scaffold produced a server with no tools"


# --------------------------------------------------------------------------
# Tool definition conformance
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tool_names_are_valid(generated_server):
    tools = await fetch_tools(generated_server)
    """Names must be usable as identifiers by the model providers."""
    for t in tools:
        assert TOOL_NAME_PATTERN.match(t.name), (
            f"tool name {t.name!r} violates the allowed pattern "
            "(alphanumerics, underscore, hyphen, 1-64 chars)"
        )


@pytest.mark.asyncio
async def test_tool_names_are_unique(generated_server):
    tools = await fetch_tools(generated_server)
    """
    73% of real MCP servers were found to have repeated tool names. A generator
    must not contribute to that: duplicate names make selection non-deterministic.
    """
    names = [t.name for t in tools]
    duplicates = {n for n in names if names.count(n) > 1}
    assert not duplicates, f"duplicate tool names: {duplicates}"


@pytest.mark.asyncio
async def test_every_tool_has_a_description(generated_server):
    tools = await fetch_tools(generated_server)
    """
    A tool with no description is unselectable in practice — the model has
    nothing to match against. This is the single most prevalent defect in
    published MCP servers.
    """
    for t in tools:
        assert t.description, f"tool {t.name!r} has no description"
        assert len(t.description.strip()) >= 20, (
            f"tool {t.name!r} description is too short to guide selection: "
            f"{t.description!r}"
        )


@pytest.mark.asyncio
async def test_input_schemas_are_valid_json_schema(generated_server):
    tools = await fetch_tools(generated_server)
    """
    inputSchema is what the model fills in. If it isn't valid JSON Schema, the
    client may reject the tool or the model may generate unusable arguments.
    """
    from jsonschema import Draft7Validator

    for t in tools:
        schema = input_schema(t)
        assert isinstance(schema, dict), f"{t.name}: inputSchema must be an object"
        assert schema.get("type") == "object", (
            f"{t.name}: inputSchema type must be 'object', got {schema.get('type')!r}"
        )
        # Raises if the schema itself is malformed.
        Draft7Validator.check_schema(schema)


@pytest.mark.asyncio
async def test_side_effects_are_disclosed_to_the_model(tmp_path):
    """
    The generator promises to tell the model whether a tool mutates state.

    This deliberately uses a spec whose own descriptions say NOTHING about side
    effects, so the assertion can only pass if the generator added the
    disclosure itself.

    An earlier version of this test ran against the example spec — whose
    descriptions already mention side effects because I wrote them that way — so
    it passed even with the generator's disclosure logic removed entirely.
    Mutation testing caught it. The lesson: assert on the component under test,
    not on fixture text that happens to contain the right words.
    """
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Silent API", "version": "1"},
        "servers": [{"url": "https://api.example.com"}],
        "paths": {
            "/widgets": {
                "get": {
                    "summary": "List widgets",
                    "description": "Returns the widgets available in the catalogue for the account.",
                    "parameters": [],
                    "responses": {"200": {"description": "A list of widgets."},
                                  "default": {"description": "A structured error object."}},
                },
                "post": {
                    "summary": "Create widget",
                    "description": "Adds a widget to the catalogue using the supplied name value.",
                    "parameters": [],
                    "responses": {"201": {"description": "The created widget."},
                                  "default": {"description": "A structured error object."}},
                },
            }
        },
    }

    # Neither description mentions side effects, read-only status, or mutation.
    for op in spec["paths"]["/widgets"].values():
        text = op["description"].lower()
        assert "side effect" not in text and "read-only" not in text, (
            "test fixture must not pre-contain the words being asserted"
        )

    result = run_audit(spec)
    ready = endpoints_ready_for_mcp(result)
    path = tmp_path / "silent_server.py"
    generate_mcp_scaffold(spec, ready, str(path))

    tools = await fetch_tools(path)
    by_name = {t.name: t for t in tools}

    write = by_name.get("post_widgets")
    assert write is not None, f"expected post_widgets, got {sorted(by_name)}"
    assert "side effect" in write.description.lower(), (
        "generator must disclose that a POST mutates state: "
        f"{write.description!r}"
    )

    read = by_name.get("get_widgets")
    assert read is not None
    assert "read-only" in read.description.lower(), (
        f"generator must state that a GET is read-only: {read.description!r}"
    )


@pytest.mark.asyncio
async def test_path_parameters_are_exposed_as_arguments(generated_server):
    tools = await fetch_tools(generated_server)
    """
    Regression guard. Path parameters like {booking_id} must become callable
    arguments — an earlier version dropped them into the URL as literal text,
    producing a tool the model could call but that would always 404.
    """
    cancel = [t for t in tools if "cancel" in t.name]
    assert cancel, "example spec includes a cancel endpoint; it should be exposed"

    props = input_schema(cancel[0]).get("properties", {})
    assert "booking_id" in props, (
        f"path parameter booking_id missing from arguments: {sorted(props)}"
    )


@pytest.mark.asyncio
async def test_header_parameters_become_valid_identifiers(generated_server):
    tools = await fetch_tools(generated_server)
    """
    Regression guard. 'Idempotency-Key' is not a valid Python identifier;
    an earlier version emitted it verbatim and produced a file that
    wouldn't even import.
    """
    cancel = [t for t in tools if "cancel" in t.name]
    props = input_schema(cancel[0]).get("properties", {})
    assert "idempotency_key" in props, (
        f"header parameter not normalised into an identifier: {sorted(props)}"
    )
    assert "Idempotency-Key" not in props, "raw header name leaked into the schema"


@pytest.mark.asyncio
async def test_required_arguments_are_marked_required(generated_server):
    """
    Regression guard. Every argument was previously emitted with a `= None`
    default, so the advertised inputSchema had an empty `required` array. A model
    could call get_bookings_booking_id with no booking_id and produce a broken
    URL — and this is the same defect category 8 flags in other people's specs.
    """
    tools = await fetch_tools(generated_server)
    by_name = {t.name: t for t in tools}

    single = by_name.get("get_bookings_booking_id")
    assert single is not None
    assert "booking_id" in input_schema(single).get("required", []), (
        "path parameters must be required; the URL is malformed without them"
    )


@pytest.mark.asyncio
async def test_request_body_fields_become_arguments(generated_server):
    """
    Regression guard. The generator previously read only `parameters` and
    silently dropped `requestBody` properties — producing a create-booking tool
    with no way to say what to book. Uncallable, but syntactically valid, which
    is exactly the class of bug a compile check cannot catch.
    """
    tools = await fetch_tools(generated_server)
    by_name = {t.name: t for t in tools}

    create = by_name.get("post_bookings")
    assert create is not None
    props = set(input_schema(create).get("properties", {}))
    assert {"item_id", "customer_id"} <= props, (
        f"request body fields missing from tool arguments: {sorted(props)}"
    )


@pytest.mark.asyncio
async def test_tool_annotations_signal_side_effects(generated_server):
    """
    MCP has first-class hints for read-only vs destructive behaviour. Prose in
    the description helps a model reason; annotations let a *client* enforce —
    auto-approving reads while requiring confirmation for writes.

    The generator emitted no annotations at all until mcp 2.0 forced a look at
    the server API, so this locks the behaviour in.
    """
    tools = await fetch_tools(generated_server)
    by_name = {t.name: t for t in tools}

    read = by_name["get_availability"]
    assert read.annotations is not None, "read tool has no annotations"
    assert read_only(read) is True, "GET must be marked read-only"

    write = by_name["post_bookings"]
    assert write.annotations is not None, "write tool has no annotations"
    assert read_only(write) is False, "POST must not be marked read-only"


def test_generated_file_documents_its_own_requirements(tmp_path):
    """
    Someone who receives a generated server out of context should be able to run
    it without having read the agent-ready README. The file states its own
    dependencies, how to run it, and what still needs doing before it ships.
    """
    spec = load_spec(str(EXAMPLES / "agent_ready_booking_api.yaml"))
    path = tmp_path / "documented.py"
    generate_mcp_scaffold(spec, endpoints_ready_for_mcp(run_audit(spec)), str(path))

    header = path.read_text().split('"""')[1]

    assert "pip install mcp httpx" in header, "dependencies not stated"
    assert "documented.py" in header, "run instructions should name the actual file"
    assert "authentication" in header.lower(), "must warn that no credentials are sent"
    assert "Agent-Ready Booking API" in header, "should identify the source API"


@pytest.mark.asyncio
async def test_server_is_named_after_the_api(tmp_path):
    """
    Every generated server used to advertise the same placeholder name. A client
    with two of them connected would show two identically-named servers.
    """
    spec = load_spec(str(EXAMPLES / "agent_ready_booking_api.yaml"))
    path = tmp_path / "named.py"
    generate_mcp_scaffold(spec, endpoints_ready_for_mcp(run_audit(spec)), str(path))

    _preflight(path)
    params = StdioServerParameters(command=sys.executable, args=[str(path)], env=None)
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        init = await session.initialize()
        assert server_name(init) == "agent-ready-booking-api", (
            f"server should be named after the API, got {server_name(init)!r}"
        )
