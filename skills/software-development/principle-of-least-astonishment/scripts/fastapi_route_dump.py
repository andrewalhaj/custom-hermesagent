#!/usr/bin/env python3
"""
FastAPI route-map dumper for POLA route-map-diff verification.

Modern FastAPI (0.13x+) wraps mounted routers in `_IncludedRouter` objects rather
than flattening them into `app.routes` as `APIRoute`s. The `/api` prefix supplied
at `include_router(..., prefix="/api")` lives in `r.include_context.prefix`, NOT
in the wrapped router's own `.routes` paths. A naive `for r in app.routes:
print(r.path)` therefore returns ONLY the catch-all and misses every real route.
This walker resolves the effective net URL by descending into _IncludedRouter and
prepending its prefix.

Usage (from the repo root, with the app importable as `server.app`):
    .venv/bin/python scripts/fastapi_route_dump.py            # dumps to stdout, sorted
    .venv/bin/python scripts/fastapi_route_dump.py > before.txt
    # ...apply routing refactor...
    .venv/bin/python scripts/fastapi_route_dump.py > after.txt
    diff before.txt after.txt    # MUST be empty for a behavior-preserving refactor

Adjust APP_IMPORT below if the app object isn't `server.app`.
Output is wrapped in stdout/stderr suppression so noisy optional-dep warnings
(e.g. python-multipart for an upload route) don't pollute the diff.
"""
import io
import contextlib
import sys

APP_IMPORT = "server"      # module to import
APP_ATTR = "app"           # FastAPI instance attribute on that module


def _load_app():
    sys.path.insert(0, ".")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        mod = __import__(APP_IMPORT)
        app = getattr(mod, APP_ATTR)
    return app, buf.getvalue()


def collect(routes, prefix=""):
    from fastapi.routing import APIRoute
    out = []
    for r in routes:
        if isinstance(r, APIRoute):
            methods = ",".join(sorted(r.methods)) if r.methods else "-"
            out.append((prefix + r.path, methods))
        elif type(r).__name__ == "_IncludedRouter":
            # prefix supplied at include_router() time lives here
            p = getattr(getattr(r, "include_context", None), "prefix", "") or ""
            out.extend(collect(r.original_router.routes, prefix + p))
        elif hasattr(r, "routes"):  # plain Mount
            p = getattr(r, "path", "") or ""
            out.extend(collect(r.routes, prefix + p))
    return out


def main():
    app, noise = _load_app()
    routes = collect(app.routes)
    for path, methods in sorted(routes, key=lambda x: x[0]):
        print(methods, path)
    if "disabled" in noise.lower():
        # surface guarded/optional-dep route disablement without breaking the diff
        sys.stderr.write("[note] one or more guarded routes were disabled at import "
                         "(optional dep missing) — pre-existing, not a refactor change\n")


if __name__ == "__main__":
    main()
