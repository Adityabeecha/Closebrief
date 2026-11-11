"""Frozen API contracts captured before the A11 router-only move."""

import json
from pathlib import Path

from starlette.routing import Match

from app import api
from app.main import app

FIXTURES = Path(__file__).parent / "fixtures"
_DEPENDENCY_NAMES = {
    id(getattr(api, name)): name
    for name in ("require_read", "require_write", "require_member", "require_admin", "get_current_user")
}


def effective_routes():
    # New FastAPI releases retain included routers as lazy branches; flatten
    # their effective contexts so prefixes and include-level options are audited.
    for route in app.routes:
        if hasattr(route, "effective_route_contexts"):
            yield from route.effective_route_contexts()
        else:
            yield route


def methods(route):
    original = getattr(route, "original_route", route)
    return sorted(getattr(route, "methods", getattr(original, "methods", [])) or [])


def route_table():
    def dependencies(dependant):
        names = []
        for dependency in getattr(dependant, "dependencies", []):
            call = dependency.call
            names.append(_DEPENDENCY_NAMES.get(id(call), getattr(call, "__name__", type(call).__name__)))
            names.extend(dependencies(dependency))
        return names

    return sorted([
        [route.path, methods(route), getattr(route, "include_in_schema", False),
         sorted(dependencies(getattr(route, "dependant", None)))]
        for route in effective_routes()
    ])


def test_route_table_matches_before_router_move():
    assert route_table() == json.loads((FIXTURES / "route_table.json").read_text(encoding="utf-8"))


def test_openapi_paths_match_before_router_move():
    assert app.openapi()["paths"] == json.loads((FIXTURES / "openapi_paths.json").read_text(encoding="utf-8"))


def test_fixed_routes_are_not_shadowed_by_parameterised_siblings():
    # A sorted snapshot alone cannot detect registration-order regressions.
    routes = list(effective_routes())
    for index, route in enumerate(routes):
        if "{" in route.path:
            continue
        for method in methods(route):
            scope = {"type": "http", "path": route.path, "root_path": "", "method": method}
            for earlier in routes[:index]:
                assert earlier.matches(scope)[0] != Match.FULL, (route.path, method, earlier.path)
