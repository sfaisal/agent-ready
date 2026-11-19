"""
Translate a Swagger 2.0 spec into the OpenAPI 3 layout, so the rubric only
ever sees one format. Covers what the rubric and MCP generator read; mirrors
swagger2openapi's output for those parts.
"""

import copy

_REF_PREFIXES = {
    "#/definitions/": "#/components/schemas/",
    "#/parameters/": "#/components/parameters/",
    "#/responses/": "#/components/responses/",
}
_SCHEMA_KEYS = (
    "type", "format", "items", "enum", "default", "pattern", "minimum", "maximum",
    "exclusiveMinimum", "exclusiveMaximum", "minLength", "maxLength",
    "minItems", "maxItems", "uniqueItems", "multipleOf",
)
_FLOWS = {
    "implicit": "implicit",
    "password": "password",
    "application": "clientCredentials",
    "accessCode": "authorizationCode",
}
_METHODS = {"get", "put", "post", "delete", "options", "head", "patch"}


def is_swagger2(spec) -> bool:
    return isinstance(spec, dict) and str(spec.get("swagger", "")).startswith("2")


def _rewrite_refs(node):
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k == "$ref" and isinstance(v, str):
                for old, new in _REF_PREFIXES.items():
                    if v.startswith(old):
                        v = new + v[len(old):]
                        break
            out[k] = _rewrite_refs(v)
        return out
    if isinstance(node, list):
        return [_rewrite_refs(v) for v in node]
    return node


def _schema_of(param: dict) -> dict:
    """Swagger keeps type/format/enum on the parameter itself; v3 nests them."""
    schema = {k: param[k] for k in _SCHEMA_KEYS if k in param}
    if schema.get("type") == "file":
        schema = {"type": "string", "format": "binary"}
    return schema


def _convert_param(param: dict) -> dict:
    out = {k: v for k, v in param.items() if k not in _SCHEMA_KEYS
           and k not in ("collectionFormat", "allowEmptyValue")}
    out["schema"] = _schema_of(param)
    return out


def _convert_security(schemes: dict) -> dict:
    out = {}
    for name, s in (schemes or {}).items():
        if not isinstance(s, dict):
            continue
        s = dict(s)
        kind = s.get("type")
        if kind == "basic":
            s = {"type": "http", "scheme": "basic", **{k: v for k, v in s.items() if k == "description"}}
        elif kind == "oauth2":
            flow = {"scopes": s.pop("scopes", {}) or {}}
            for key in ("authorizationUrl", "tokenUrl"):
                if key in s:
                    flow[key] = s.pop(key)
            s["flows"] = {_FLOWS.get(s.pop("flow", ""), "implicit"): flow}
        out[name] = s
    return out


def _convert_operation(op: dict, path_params: list, spec: dict) -> dict:
    op = dict(op)
    consumes = op.pop("consumes", None) or spec.get("consumes") or ["application/json"]
    produces = op.pop("produces", None) or spec.get("produces") or ["application/json"]
    shared_params = spec.get("parameters") or {}

    params, body, form = [], None, []
    for p in path_params + list(op.get("parameters") or []):
        if not isinstance(p, dict):
            continue
        target = p
        ref = p.get("$ref", "")
        if ref.startswith("#/parameters/"):
            target = shared_params.get(ref[len("#/parameters/"):], {})
        where = target.get("in") if isinstance(target, dict) else None
        if where == "body":
            body = target
        elif where == "formData":
            form.append(target)
        elif "$ref" in p:
            params.append(p)  # still points at a v3 component parameter
        else:
            params.append(_convert_param(p))
    if params:
        op["parameters"] = params
    else:
        op.pop("parameters", None)

    if body is not None:
        rb = {"content": {ct: {"schema": body.get("schema") or {}} for ct in consumes}}
        for key in ("description", "required"):
            if key in body:
                rb[key] = body[key]
        op["requestBody"] = rb
    elif form:
        ct = "multipart/form-data" if "multipart/form-data" in consumes else (
            "application/x-www-form-urlencoded")
        schema = {"type": "object", "properties": {}}
        for f in form:
            prop = _schema_of(f)
            if f.get("description"):
                prop["description"] = f["description"]
            schema["properties"][f.get("name")] = prop
        required = [f.get("name") for f in form if f.get("required")]
        if required:
            schema["required"] = required
        op["requestBody"] = {"content": {ct: {"schema": schema}}}

    op["responses"] = {
        code: _convert_response(r, produces)
        for code, r in (op.get("responses") or {}).items()
    }
    return op


def _convert_response(resp, produces) -> dict:
    if not isinstance(resp, dict) or "$ref" in resp:
        return resp
    resp = dict(resp)
    schema = resp.pop("schema", None)
    resp.pop("examples", None)
    if schema is not None:
        resp["content"] = {ct: {"schema": schema} for ct in produces}
    return resp


def to_openapi3(spec: dict) -> dict:
    """Return an OpenAPI 3.0 dict equivalent to the given Swagger 2.0 dict."""
    src = copy.deepcopy(spec)
    out = {"openapi": "3.0.0"}
    for key in ("info", "tags", "externalDocs", "security"):
        if key in src:
            out[key] = src[key]
    out.update({k: v for k, v in src.items() if k.startswith("x-")})

    if src.get("host"):
        scheme = (src.get("schemes") or ["https"])[0]
        out["servers"] = [{"url": f"{scheme}://{src['host']}{src.get('basePath', '')}"}]
    elif src.get("basePath"):
        out["servers"] = [{"url": src["basePath"]}]

    paths = {}
    for path, item in (src.get("paths") or {}).items():
        if not isinstance(item, dict):
            paths[path] = item
            continue
        path_params = list(item.get("parameters") or [])
        new_item = {}
        for k, v in item.items():
            if k.lower() in _METHODS and isinstance(v, dict):
                new_item[k] = _convert_operation(v, path_params, src)
            elif k != "parameters":
                new_item[k] = v
        paths[path] = new_item
    out["paths"] = paths

    components = {}
    if src.get("definitions"):
        components["schemas"] = src["definitions"]
    if src.get("securityDefinitions"):
        components["securitySchemes"] = _convert_security(src["securityDefinitions"])
    shared = {k: _convert_param(p) for k, p in (src.get("parameters") or {}).items()
              if isinstance(p, dict) and p.get("in") not in ("body", "formData")}
    if shared:
        components["parameters"] = shared
    produces = src.get("produces") or ["application/json"]
    if src.get("responses"):
        components["responses"] = {
            k: _convert_response(r, produces) for k, r in src["responses"].items()
        }
    if components:
        out["components"] = components

    return _rewrite_refs(out)
