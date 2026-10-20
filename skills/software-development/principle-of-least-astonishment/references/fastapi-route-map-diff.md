# Proving a FastAPI routing refactor is behavior-preserving (route-map diff)

When you refactor *where* a router's `/api` prefix is applied (e.g. moving it from
baked-in `APIRouter(prefix="/api/x")` to a single `include_router(x, prefix="/api")`
in `server.py`), the POLA contract is **net URLs must be byte-identical before and
after.** The only honest proof is to dump every registered route on both sides and
diff the sets. This is the §6 verification for any routing-convention fix.

## The trap: naive `app.routes` iteration gives you nothing useful

Modern FastAPI (0.13x+) does NOT flatten included routers into `app.routes` as plain
`APIRoute` objects. Each `include_router(...)` becomes an `_IncludedRouter` wrapper.
So:

- `for r in app.routes: print(r.path)` → prints only the top-level routes (e.g. the
  `/{full_path:path}` catch-all) and `_IncludedRouter` objects that have **no `.path`
  attribute** → `AttributeError: '_IncludedRouter' object has no attribute 'path'`.
- Recursing on `r.routes` fails too — `_IncludedRouter` has no `.routes` either.
- `app.router.routes` is the same story.

The bare paths live on `r.original_router.routes`; the prefix that makes them net URLs
lives on `r.include_context.prefix`. You must walk both.

## Working route walker (run in a FRESH process)

`import server` is cached in `sys.modules` — if you already imported it earlier in the
same REPL/process you'll get stale routes. Always run the walker in a subprocess or a
clean interpreter.

```python
import io, contextlib
buf = io.StringIO()
with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
    import server                      # silence import-time noise (e.g. optional-dep warnings)
    from fastapi.routing import APIRoute

def collect(routes, prefix=''):
    out = []
    for r in routes:
        if isinstance(r, APIRoute):
            methods = ','.join(sorted(r.methods)) if r.methods else '-'
            out.append((prefix + r.path, methods))
        elif type(r).__name__ == '_IncludedRouter':
            p = getattr(r.include_context, 'prefix', '') or ''
            out.extend(collect(r.original_router.routes, prefix + p))
    return out

routes = collect(server.app.routes)
for path, methods in sorted(routes, key=lambda x: x[0]):
    print(methods, path)
```

Capture this output to a file BEFORE the refactor and AFTER; `diff` the two. A clean
diff (or only the intended change, e.g. a removed duplicate) proves behavior preserved.

## Quick dupe / count assertions (cheap post-refactor sanity)

```python
api = [p for p, _ in routes if p.startswith('/api')]
dupes = [p for p in set(api) if api.count(p) > 1]
print('total /api routes:', len(api))
print('dupes:', dupes)        # [] is the goal; a non-empty list = two handlers on one URL
```

A duplicate URL is itself an astonishment: two handlers registered on the same path,
and whichever `include_router` ran first silently wins. The loser is dead code that a
reader will edit expecting it to take effect. Removing the dead duplicate is a valid
behavior-preserving fix (the live handler's behavior is unchanged).

## Pitfalls

- **Run in a fresh process** or `import server` returns cached/stale routes (you'll see
  only the catch-all). A subprocess (`subprocess.run([sys.executable, '-c', ...])`) or
  a brand-new interpreter invocation is the reliable path.
- **Redirect stdout AND stderr around the import.** Optional-dependency guards (e.g. a
  `try/except` around a `python-multipart`-requiring upload router) print to stdout at
  import time *before* the exception is caught, polluting your route list. Suppress both
  streams during `import server`, then print the routes after.
- **A guarded/optional route absent in your venv is not a regression** if it was absent
  before too. Note it explicitly ("`/api/x` disabled: dep not installed, unchanged from
  before") rather than treating it as a diff.
- **`get_openapi` / `/openapi.json` is not a shortcut here** if the app has an auth
  middleware that redirects un-cookied requests — the endpoint 302s to `/` and you get
  HTML, not JSON. The in-process walker above sidesteps auth entirely.
- The `_IncludedRouter` internal shape (`original_router`, `include_context.prefix`) is
  a FastAPI implementation detail, not public API — re-confirm the attribute names if a
  FastAPI major version bump changes the walk.
