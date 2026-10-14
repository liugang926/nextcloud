#!/usr/bin/env python3
"""Check the WeKnora Nextcloud OpenAPI against both patched Go route surfaces.

This is intentionally source-facing: a removed route, changed HTTP method, or
missing key in a success response fails before an image is built. It uses only
Python's standard library so the pinned WeKnora CI jobs can run it directly.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

SPEC = Path(__file__).resolve().parents[2] / "docs/weknora-nextcloud-openapi.json"
VERBS = {"get", "post", "put", "delete", "patch"}
STATUS = {"200": "StatusOK", "201": "StatusCreated", "202": "StatusAccepted", "204": "StatusNoContent"}


class ContractError(Exception):
    pass


@dataclass(frozen=True)
class Route:
    handler_type: str
    handler: str


def span(text: str, start: int, opener: str, closer: str) -> str:
    """Return a balanced Go expression, ignoring strings and comments."""
    if text[start] != opener:
        raise ContractError(f"expected {opener!r} at offset {start}")
    depth = 0
    quote = ""
    escape = False
    line_comment = False
    block_comment = False
    for pos in range(start, len(text)):
        char = text[pos]
        following = text[pos : pos + 2]
        if line_comment:
            if char == "\n":
                line_comment = False
            continue
        if block_comment:
            if following == "*/":
                block_comment = False
            continue
        if quote:
            if escape:
                escape = False
            elif char == "\\" and quote != "`":
                escape = True
            elif char == quote:
                quote = ""
            continue
        if following == "//":
            line_comment = True
            continue
        if following == "/*":
            block_comment = True
            continue
        if char in ('"', "'", "`"):
            quote = char
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start : pos + 1]
    raise ContractError(f"unbalanced {opener}{closer} at offset {start}")


def function_body(text: str, name: str, receiver: str | None = None) -> str:
    if receiver:
        pattern = rf"func\s*\(\s*\w+\s+\*{re.escape(receiver)}\s*\)\s*{re.escape(name)}\s*\("
    else:
        pattern = rf"func\s+{re.escape(name)}\s*\("
    match = re.search(pattern, text)
    if not match:
        raise ContractError(f"Go function {receiver + '.' if receiver else ''}{name} missing")
    opening = text.find("{", match.end())
    if opening < 0:
        raise ContractError(f"Go function {name} has no body")
    return span(text, opening, "{", "}")


def go_route_calls(text: str, prefix: str, handler_type: str | None = None) -> dict[tuple[str, str], Route]:
    routes: dict[tuple[str, str], Route] = {}
    pattern = re.compile(r'\b(r|v1|ds)\.(GET|POST|PUT|DELETE|PATCH)\(\s*"([^"]+)"')
    for match in pattern.finditer(text):
        variable, verb, suffix = match.groups()
        if prefix == "router" and "/integrations/nextcloud/" not in suffix:
            continue
        if prefix != "router" and variable != "ds":
            continue
        call = span(text, text.index("(", match.start()), "(", ")")
        if handler_type is None:
            handlers = re.findall(r"params\.([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)", call)
            if not handlers:
                raise ContractError(f"route {suffix} has no handler")
            actual_type, method = handlers[-1]
        else:
            methods = re.findall(r"\bh\.([A-Za-z_][\w]*)\b", call)
            if not methods:
                raise ContractError(f"route {suffix} has no handler")
            if "g.Admin()" not in call:
                raise ContractError(f"administrator route {verb} {suffix} lost its RBAC guard")
            actual_type, method = handler_type, methods[-1]
        base = "" if variable == "r" else "/api/v1" if variable == "v1" else "/api/v1/datasource"
        path = re.sub(r":([A-Za-z_][\w]*)", r"{\1}", base + suffix)
        key = (verb.lower(), path)
        if key in routes:
            raise ContractError(f"duplicate Go route {verb} {path}")
        routes[key] = Route(actual_type, method)
    return routes


def actual_routes(root: Path) -> dict[tuple[str, str], Route]:
    router = (root / "internal/router/router.go").read_text()
    infra = (root / "internal/router/routes_infra.go").read_text()
    routes = go_route_calls(router, "router")
    for function, handler_type in (
        ("RegisterNextcloudEventConnectionRoutes", "NextcloudEventConnectionHandler"),
        ("RegisterNextcloudSourcePairingRoutes", "NextcloudSourcePairingHandler"),
        ("RegisterNextcloudGCRoutes", "NextcloudGCHandler"),
    ):
        for key, route in go_route_calls(function_body(infra, function), "infra", handler_type).items():
            if key in routes:
                raise ContractError(f"duplicate Go route {key}")
            routes[key] = route
    # Catch a newly added Nextcloud-specific datasource route even if it was
    # registered outside one of the three current route groups.
    for verb, suffix in re.findall(r'\bds\.(GET|POST|PUT|DELETE|PATCH)\(\s*"([^"]*nextcloud[^"]*)"', infra):
        path = re.sub(r":([A-Za-z_][\w]*)", r"{\1}", "/api/v1/datasource" + suffix)
        if (verb.lower(), path) not in routes:
            raise ContractError(f"unexamined Nextcloud datasource route {verb} {path}")
    return routes


def resolve_schema(spec: dict, schema: dict) -> dict:
    seen: set[str] = set()
    while "$ref" in schema:
        ref = schema["$ref"]
        prefix = "#/components/schemas/"
        if not isinstance(ref, str) or not ref.startswith(prefix) or ref in seen:
            raise ContractError(f"invalid or cyclic schema reference: {ref}")
        seen.add(ref)
        schema = spec["components"]["schemas"][ref[len(prefix) :]]
    return schema


def top_level_go_keys(literal: str) -> set[str]:
    keys: set[str] = set()
    depth = 0
    quote = False
    escape = False
    index = 0
    while index < len(literal):
        char = literal[index]
        if quote:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                quote = False
            index += 1
            continue
        if char == '"':
            end = index + 1
            while end < len(literal):
                if literal[end] == "\\":
                    end += 2
                elif literal[end] == '"':
                    break
                else:
                    end += 1
            if end >= len(literal):
                raise ContractError("unterminated Go map key")
            if depth == 1 and re.match(r"\s*:", literal[end + 1 :]):
                keys.add(literal[index + 1 : end])
            index = end + 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        index += 1
    return keys


def gin_response_keys(body: str, status: str, dynamic: bool = False) -> list[set[str]]:
    results: list[set[str]] = []
    pattern = re.compile(r"c\.JSON\(\s*([^,]+),\s*gin\.H\s*\{")
    for match in pattern.finditer(body):
        if match.group(1).strip() != "http." + STATUS[status] and not (dynamic and match.group(1).strip() == "status"):
            continue
        opening = body.find("{", match.start())
        results.append(top_level_go_keys(span(body, opening, "{", "}")))
    return results


def handler_body(root: Path, route: Route, helper: str | None = None) -> tuple[str, str]:
    handler_dir = root / "internal/handler"
    for file in sorted(handler_dir.glob("*.go")):
        if file.name.endswith("_test.go"):
            continue
        text = file.read_text()
        try:
            registered = function_body(text, route.handler, route.handler_type)
        except ContractError:
            continue
        if not helper:
            return registered, str(file.relative_to(root))
        if helper not in registered:
            raise ContractError(f"{route.handler_type}.{route.handler} no longer calls {helper}")
        try:
            return function_body(text, helper, route.handler_type), str(file.relative_to(root))
        except ContractError:
            return function_body(text, helper), str(file.relative_to(root))
    raise ContractError(f"handler {route.handler_type}.{route.handler} missing")


def check_go_structs(spec: dict, root: Path) -> int:
    count = 0
    for name, schema in spec["components"]["schemas"].items():
        binding = schema.get("x-go-struct")
        if not binding:
            continue
        relative, struct_name = binding.split(":", 1)
        source = (root / relative).read_text()
        match = re.search(rf"type\s+{re.escape(struct_name)}\s+struct\s*\{{", source)
        if not match:
            raise ContractError(f"{name}: Go struct {binding} missing")
        body = span(source, source.find("{", match.start()), "{", "}")
        tags = {tag.split(",", 1)[0] for tag in re.findall(r'json:"([^"]+)"', body)} - {"-"}
        required = set(schema.get("required", []))
        properties = set(schema.get("properties", {}))
        if not required or not required <= properties or not properties <= tags:
            raise ContractError(f"{name}: schema fields do not match {binding}; missing {sorted(properties - tags)}")
        count += 1
    return count


def check_contract(spec: dict, root: Path) -> tuple[int, int, int]:
    if not str(spec.get("openapi", "")).startswith("3."):
        raise ContractError("OpenAPI 3.x document required")
    def verify_refs(value: object) -> None:
        if isinstance(value, dict):
            if "$ref" in value:
                resolve_schema(spec, value)
            for child in value.values():
                verify_refs(child)
        elif isinstance(value, list):
            for child in value:
                verify_refs(child)
    verify_refs(spec)
    declared: dict[tuple[str, str], dict] = {}
    for path, path_item in spec["paths"].items():
        if not path.startswith("/api/v1/"):
            raise ContractError(f"unexpected path {path}")
        for method, operation in path_item.items():
            if method in VERBS:
                declared[(method, path)] = operation
    actual = actual_routes(root)
    if declared.keys() != actual.keys():
        missing = sorted(actual.keys() - declared.keys())
        phantom = sorted(declared.keys() - actual.keys())
        raise ContractError(f"OpenAPI/Go route drift: undocumented={missing}; absent_in_go={phantom}")
    checked_responses = 0
    for (method, path), operation in declared.items():
        label = f"{method.upper()} {path}"
        if not operation.get("operationId"):
            raise ContractError(f"{label}: operationId missing")
        machine = path.startswith("/api/v1/integrations/nextcloud/") and not path.endswith("/ask-target")
        expected_security = [{"NextcloudHMAC": []}] if machine else [{"WebSession": []}]
        if operation.get("security") != expected_security:
            raise ContractError(f"{label}: security scheme drift")
        primary = operation.get("x-contract-primary-status")
        if primary not in STATUS or primary not in operation.get("responses", {}):
            raise ContractError(f"{label}: primary success response missing")
        route = actual[(method, path)]
        registered_body, _ = handler_body(root, route)
        for parameter in operation.get("parameters", []):
            name = parameter["name"]
            if parameter.get("in") == "query" and \
                    f'c.Query("{name}")' not in registered_body and \
                    f'query.Get("{name}")' not in registered_body:
                raise ContractError(f"{label}: handler no longer reads query parameter {name}")
            if parameter.get("in") == "header" and f'"{name}"' not in registered_body:
                raise ContractError(f"{label}: handler no longer reads header {name}")
        if primary == "204":
            if "c.Status(http.StatusNoContent)" not in registered_body:
                raise ContractError(f"{label}: no 204 handler response")
            continue
        response = operation["responses"][primary]
        schema = resolve_schema(spec, response["content"]["application/json"]["schema"])
        required = set(schema.get("required", []))
        if not required or not required <= set(schema.get("properties", {})):
            raise ContractError(f"{label}: primary response has no valid required keys")
        if operation.get("x-contract-response-struct"):
            struct_schema = spec["components"]["schemas"][operation["x-contract-response-struct"]]
            if not required <= set(struct_schema.get("properties", {})):
                raise ContractError(f"{label}: response type fields missing")
            body, _ = handler_body(root, route)
            if "c.Data(http.StatusOK" not in body or "SignedFileStatus(" not in body:
                raise ContractError(f"{label}: signed JSON response flow changed")
            for name in response.get("headers", {}):
                if f'c.Header("{name}"' not in body:
                    raise ContractError(f"{label}: signed response header {name} missing")
        else:
            helper = operation.get("x-contract-response-helper")
            body, source = handler_body(root, route, helper)
            if helper and operation.get("x-contract-dynamic-status") and \
                    f"{helper}(c, http.{STATUS[primary]}" not in registered_body:
                raise ContractError(f"{label}: helper is no longer called with HTTP {primary}")
            candidates = gin_response_keys(body, primary, bool(operation.get("x-contract-dynamic-status")))
            if not candidates or any(not required <= keys for keys in candidates):
                raise ContractError(f"{label}: required {sorted(required)} absent from {source} success response {candidates}")
            for field in required:
                child = resolve_schema(spec, schema["properties"][field])
                if child.get("required") and not child.get("x-go-struct") and \
                        f'"{field}": gin.H{{' in body:
                    for child_key in child["required"]:
                        if f'"{child_key}":' not in body:
                            raise ContractError(f"{label}: nested response key {field}.{child_key} missing")
        checked_responses += 1
    structs = check_go_structs(spec, root)
    return len(declared), checked_responses, structs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("weknora_root", type=Path, help="clean WeKnora checkout after applying one pinned patch")
    parser.add_argument("--spec", type=Path, default=SPEC)
    args = parser.parse_args()
    try:
        spec = json.loads(args.spec.read_text())
        routes, responses, structs = check_contract(spec, args.weknora_root)
    except (ContractError, OSError, KeyError, ValueError, TypeError) as exc:
        print(f"WeKnora OpenAPI contract failed: {exc}", file=sys.stderr)
        return 1
    print(f"WeKnora OpenAPI contract OK: {routes} routes, {responses} response shapes, {structs} Go JSON structs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
