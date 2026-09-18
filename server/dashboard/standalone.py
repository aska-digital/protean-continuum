"""Continuum — standalone browser dashboard entrypoint (D-SB-1/D-SB-3/D-SB-5).

Launches a FastAPI/uvicorn app that lets the user run Continuum without opening
Hermes Desktop:

    python3 -m build.dashboard.standalone

Behaviour (locked by the architecture decision):

* Default bind is ``127.0.0.1:8765`` — loopback only. ``--host``/``--port`` flags
  override, but no default path ever exposes the network (D-SB-1).
* Serves the static shell (``dashboard/static/index.html`` + ``app.js`` +
  ``styles.css``) and the shared pure interaction module
  (``desktop/kanban-interaction.js``) the browser app imports.
* Mounts the EXISTING ``dashboard.plugin_api.router`` unchanged under
  ``/api/plugins/continuum`` — no route or mutation is duplicated (D-SB-3).
* The service scans whatever profiles exist under ``HERMES_HOME``; there is no
  profile picker (D-SB-5). The registry stays bundle-relative
  (``build/data/registry.db``) — this module never relocates or rewrites it.

Reads are GET-only. The only mutation surface is the audited review route
provided by ``plugin_api``. ``/scan`` is triggered only by an explicit user
action — never on page load. Nothing here writes to a Hermes source database.
"""
from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import functools
import os
import sys
from pathlib import Path
from typing import Any, Callable, List, Optional

# ---------------------------------------------------------------------------
# Make the bundled M1-M7 package + the dashboard adapter importable from the
# ``build/`` root regardless of how the module is launched (mirrors the
# bootstrap in dashboard/plugin_api.py).
# ---------------------------------------------------------------------------
BUILD_ROOT = Path(__file__).resolve().parent.parent
if str(BUILD_ROOT) not in sys.path:
    sys.path.insert(0, str(BUILD_ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402

from dashboard.plugin_api import router as continuum_router  # noqa: E402

API_PREFIX = "/api/plugins/continuum"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_HTML = STATIC_DIR / "index.html"
APP_JS = STATIC_DIR / "app.js"
STYLES_CSS = STATIC_DIR / "styles.css"
REVIEW_HTML = STATIC_DIR / "review.html"
REVIEW_JS = STATIC_DIR / "review.js"
SHARED_INTERACTION_JS = BUILD_ROOT / "desktop" / "kanban-interaction.js"


# ---------------------------------------------------------------------------
# Thread-safe route execution (standalone-only adapter).
#
# FastAPI executes every synchronous ``def`` endpoint on AnyIO's shared worker
# thread pool.  That pool spawns worker threads on demand and lets an idle
# worker exit after a short timeout, so two successive requests can be served
# by two different threads.  The Continuum ``Service`` is a process-wide
# singleton (``dashboard.plugin_api`` caches it in ``_SERVICE``) and it owns a
# single long-lived SQLite connection, which is bound at creation to the thread
# that created it.  When a later request is dispatched to a second worker
# thread, that connection raises the cross-thread SQLite error seen on the live
# board GET (``SQLite objects created in a thread can only be used in that same
# thread``).
#
# The fix changes nothing about the service, its schema, its registry path or
# any route: it changes only *where* the synchronous endpoints execute.  All of
# them — and therefore every access to the cached Service and its connection —
# run on one dedicated, never-exiting worker thread, so the thread that creates
# the connection is always the thread that uses it.  ``ThreadPoolExecutor``
# with ``max_workers=1`` keeps that thread alive for the life of the process
# (its worker blocks on the work queue rather than timing out), unlike AnyIO's
# recyclable pool.  This module is the standalone transport only; the desktop
# surface and the Hermes plugin host are untouched.
# ---------------------------------------------------------------------------
_API_THREAD = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="continuum-api",
)


async def _run_in_threadpool(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run ``func`` on the single dedicated API thread (drop-in for Starlette)."""
    loop = asyncio.get_running_loop()
    call = functools.partial(func, *args, **kwargs)
    return await loop.run_in_executor(_API_THREAD, call)


def _pin_sync_routes_to_one_thread() -> None:
    """Route every synchronous endpoint onto the single dedicated API thread.

    Starlette runs sync endpoints through ``starlette.concurrency.run_in_threadpool``;
    FastAPI binds that name into ``fastapi.routing`` at import time and calls it for
    both endpoints and resolved dependencies.  Installing our single-thread executor
    for both references keeps the cached Service's connection on one thread for the
    whole process, without editing any route, service, or registry code.
    """
    import fastapi.routing as _fastapi_routing
    import starlette.concurrency as _starlette_concurrency

    _fastapi_routing.run_in_threadpool = _run_in_threadpool
    _starlette_concurrency.run_in_threadpool = _run_in_threadpool



def create_app() -> FastAPI:
    """Build the standalone ASGI app. Pure construction — binds nothing."""
    # Pin all synchronous endpoint execution to one dedicated thread BEFORE the
    # router is mounted, so the cached Service's SQLite connection is created
    # and used on the same thread for every request (see the adapter above).
    _pin_sync_routes_to_one_thread()

    app = FastAPI(
        title="Continuum standalone",
        description="Local browser dashboard for Continuum.",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    # Reuse the existing, proven API surface unchanged (D-SB-3).
    app.include_router(continuum_router, prefix=API_PREFIX)

    # ── static shell ────────────────────────────────────────────────────
    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(str(INDEX_HTML), media_type="text/html")

    @app.get("/app.js", include_in_schema=False)
    def app_js() -> FileResponse:
        return FileResponse(str(APP_JS), media_type="text/javascript")

    @app.get("/styles.css", include_in_schema=False)
    def styles_css() -> FileResponse:
        return FileResponse(str(STYLES_CSS), media_type="text/css")

    # The browser app imports the shared pure interaction module browser-relative
    # (``../desktop/kanban-interaction.js``), which resolves to this route.
    @app.get("/desktop/kanban-interaction.js", include_in_schema=False)
    def shared_interaction() -> FileResponse:
        return FileResponse(str(SHARED_INTERACTION_JS), media_type="text/javascript")

    @app.get("/review.html", include_in_schema=False)
    def review_html() -> FileResponse:
        return FileResponse(str(REVIEW_HTML), media_type="text/html")

    @app.get("/review", include_in_schema=False)
    def review_alias() -> FileResponse:
        return FileResponse(str(REVIEW_HTML), media_type="text/html")

    @app.get("/review.js", include_in_schema=False)
    def review_js() -> FileResponse:
        return FileResponse(str(REVIEW_JS), media_type="text/javascript")

    # Fixtures-only review queue (generated at build time, no live API at page load for Pages)
    from fastapi.responses import JSONResponse
    import json
    FIXTURE_QUEUE = Path(__file__).resolve().parent.parent / "fixtures" / "review-queue.json"

    @app.get("/api/plugins/continuum/review-queue", include_in_schema=False)
    def review_queue() -> JSONResponse:
        if FIXTURE_QUEUE.exists():
            try:
                data = json.loads(FIXTURE_QUEUE.read_text(encoding="utf-8"))
                return JSONResponse(data)
            except Exception as exc:
                return JSONResponse({"error": str(exc)}, status_code=500)
        return JSONResponse({"error": "review-queue.json not found; run tools/generate_review_queue.py"}, status_code=404)

    @app.get("/review-queue.json", include_in_schema=False)
    def review_queue_static() -> JSONResponse:
        if FIXTURE_QUEUE.exists():
            try:
                data = json.loads(FIXTURE_QUEUE.read_text(encoding="utf-8"))
                return JSONResponse(data)
            except Exception as exc:
                return JSONResponse({"error": str(exc)}, status_code=500)
        return JSONResponse({"error": "review-queue.json not found"}, status_code=404)

    return app


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse entrypoint flags. Defaults are loopback-only and never exposed."""
    parser = argparse.ArgumentParser(
        prog="python3 -m build.dashboard.standalone",
        description="Run the Continuum standalone browser dashboard (loopback by default).",
    )
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help="bind host (default: %(default)s; loopback only unless overridden)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help="bind port (default: %(default)s)")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)
    import uvicorn

    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")


app = create_app()


if __name__ == "__main__":
    main()
