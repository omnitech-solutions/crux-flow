"""In-child framework introspection (ADR-0075 decisions 1 + 3, the Unit-3 grammar).

Runs INSIDE the confined child, AFTER `child.py` has entered the network cut,
set the resource rlimits, and installed the audit deny-list. Imports the human-
named target (so the target's import-time code runs, fully confined) and reads
exactly one framework registry.

Two mutually exclusive grammars the harness never auto-detects:

  * ``--app module:attr`` — import ``module``, resolve ``attr``. A FastAPI or
    Flask *instance* is introspected directly (FastAPI ``app.routes``; Flask
    ``app.url_map.iter_rules()``). Any other *callable* is treated as a zero-
    argument application factory (``create_app``), called, then introspected. A
    factory that requires arguments raises on the zero-argument call and yields
    an honest no-capture, never a guess. When ``attr`` is neither an app nor a
    zero-arg factory, the result is an honest no-capture.

  * ``--settings module.path`` — set ``DJANGO_SETTINGS_MODULE``, inject the
    dummy DB backend, ``django.setup()``, then recursively walk
    ``settings.ROOT_URLCONF`` resolving every ``URLResolver``/``include`` node
    (recursion depth cap 50 + visited-set cycle guard) and read
    ``apps.get_models()`` ``_meta``. Django URL patterns expose no HTTP method,
    so Django routes carry ``method: null``.

STDLIB + TARGET FRAMEWORKS ONLY. This module imports nothing under ``crux`` and
never `derive`/`capture` — the confined child stays lean, and the trusted parent
is the sole trust boundary. Returns plain ``{"routes": [...], "models": [...]}``
dicts; `child.py` writes them to the capture descriptor.

A `NoCapture` is raised for every honest "cannot recover" outcome; `child.py`
turns it into a no-capture exit with the reason on stderr.
"""

from __future__ import annotations

import importlib

DJANGO_MAX_DEPTH = 50


class NoCapture(Exception):
    """An honest no-capture: routes/models are not recoverable at module scope
    (unnamed factory, factory needing args, unknown app type, lazy bootstrap)."""


# ── FastAPI / Flask via --app module:attr ───────────────────────────────────

def _is_flask(app) -> bool:
    return hasattr(app, "url_map") and hasattr(app, "add_url_rule")


def _is_fastapi(app) -> bool:
    # FastAPI/Starlette expose a `.routes` list; check after Flask so the two
    # never collide (Flask has no `.routes`).
    return hasattr(app, "routes")


def _fastapi_routes(app) -> list[dict]:
    routes: list[dict] = []
    for r in getattr(app, "routes", []):
        path = getattr(r, "path", None)
        if path is None:
            continue
        name = getattr(r, "name", "") or ""
        methods = getattr(r, "methods", None)
        if methods:
            for m in methods:
                routes.append({"method": str(m), "path": str(path), "name": str(name)})
        else:  # Mount / WebSocket route — no HTTP method
            routes.append({"method": None, "path": str(path), "name": str(name)})
    return routes


def _flask_routes(app) -> list[dict]:
    routes: list[dict] = []
    for rule in app.url_map.iter_rules():
        methods = set(rule.methods or ()) - {"HEAD", "OPTIONS"}
        endpoint = str(rule.endpoint)
        if methods:
            for m in sorted(methods):
                routes.append({"method": str(m), "path": str(rule.rule), "name": endpoint})
        else:
            routes.append({"method": None, "path": str(rule.rule), "name": endpoint})
    return routes


def _introspect_app_instance(app) -> dict:
    if _is_flask(app):
        return {"routes": _flask_routes(app), "models": []}
    if _is_fastapi(app):
        return {"routes": _fastapi_routes(app), "models": []}
    raise NoCapture(
        f"resolved object is not a FastAPI or Flask app "
        f"(type {type(app).__module__}.{type(app).__name__})"
    )


def introspect_app(spec: str) -> dict:
    """`spec` is ``module:attr``. Import the module (import-time code runs here,
    confined), resolve ``attr``, and dispatch: app instance → direct; other
    callable → zero-arg factory; else → no-capture."""
    if ":" not in spec:
        raise NoCapture(f"--app value {spec!r} is not in module:attr form")
    module_name, _, attr = spec.partition(":")
    module = importlib.import_module(module_name)
    if not attr:
        raise NoCapture("--app value has an empty attr after ':'")
    try:
        obj = getattr(module, attr)
    except AttributeError as e:
        raise NoCapture(f"{module_name!r} has no attribute {attr!r}") from e

    # App instance first (a FastAPI/Flask instance is itself callable, so the
    # instance check must precede the factory branch).
    if _is_flask(obj) or _is_fastapi(obj):
        return _introspect_app_instance(obj)

    if callable(obj):
        try:
            app = obj()  # zero-argument application factory
        except TypeError as e:
            raise NoCapture(
                f"factory {attr!r} is not zero-argument (needs args): {e}"
            ) from e
        return _introspect_app_instance(app)

    raise NoCapture(
        f"{attr!r} is neither an app instance nor a callable factory"
    )


# ── Django via --settings module.path ───────────────────────────────────────

def _django_walk(patterns, prefix: str, depth: int, visited: set, out: list[dict]) -> None:
    if depth > DJANGO_MAX_DEPTH:
        return
    for p in patterns:
        pid = id(p)
        if pid in visited:
            continue
        visited.add(pid)
        sub = getattr(p, "url_patterns", None)
        if sub is not None:  # URLResolver (an include node)
            _django_walk(sub, prefix + str(p.pattern), depth + 1, visited, out)
        else:  # URLPattern (a leaf)
            name = p.name
            if not name:
                cb = getattr(p, "callback", None)
                name = getattr(cb, "__name__", "") if cb else ""
            out.append({"method": None, "path": prefix + str(p.pattern), "name": str(name or "")})


def _django_models() -> list[dict]:
    from django.apps import apps

    models: list[dict] = []
    for model in apps.get_models():
        meta = model._meta
        fields: list[dict] = []
        for f in meta.fields:  # concrete local + inherited; excludes reverse/m2m
            get_type = getattr(f, "get_internal_type", None)
            ftype = get_type() if callable(get_type) else type(f).__name__
            fields.append({
                "name": str(f.name),
                "type": str(ftype),
                "nullable": bool(getattr(f, "null", False)),
            })
        models.append({
            "name": str(model.__name__),
            "table": str(meta.db_table),
            "fields": fields,
        })
    return models


def introspect_settings(settings_module: str) -> dict:
    """`settings_module` is a dotted path. Set `DJANGO_SETTINGS_MODULE`, force
    the dummy DB backend (so any connection attempt fails fast, never hangs),
    `django.setup()`, then read routes + models."""
    import os

    os.environ["DJANGO_SETTINGS_MODULE"] = settings_module
    import django
    from django.conf import settings

    # Inject the dummy backend defensively before setup (AppConfig.ready or an
    # import side effect that tries a real connection fails fast under it).
    try:
        dbs = settings.DATABASES  # triggers settings load from the module
    except Exception:
        dbs = None
    if isinstance(dbs, dict):
        for alias in dbs:
            dbs[alias]["ENGINE"] = "django.db.backends.dummy"

    django.setup()

    from django.urls import get_resolver

    resolver = get_resolver()
    routes: list[dict] = []
    _django_walk(resolver.url_patterns, "", 0, set(), routes)
    models = _django_models()
    return {"routes": routes, "models": models}
