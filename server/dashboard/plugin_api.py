"""Continuum dashboard plugin backend — mounted at ``/api/plugins/continuum/``.

Imports M7 (``continuum.service``) and exposes the STABLE endpoint surface from architecture
contract §3.2 / ux-contract §11. The renderer (M8) talks only to this.

The scanner runs here, server-side: 140k messages never enter the renderer. Nothing in this
module writes to a Hermes source DB — the registry is the only writable store (INV-2).
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

# --- make the bundled M1-M7 package importable from either layout -------------
_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

from continuum.config import load_config              # noqa: E402
from continuum.service import Service                 # noqa: E402
from continuum.service import OverviewConfigError, OverviewModeError  # noqa: E402

router = APIRouter()

_SERVICE: Optional[Service] = None


def get_service() -> Service:
    global _SERVICE
    if _SERVICE is None:
        env_home = os.environ.get("HERMES_HOME")
        # an active profile's HERMES_HOME is nested under the home that owns profiles/
        home = env_home if env_home and os.path.isdir(os.path.join(env_home, "profiles")) \
            else os.path.expanduser("~/.hermes")
        _SERVICE = Service(load_config(home))
    return _SERVICE


class ReviewBody(BaseModel):
    action: str
    payload: Optional[Dict[str, Any]] = None


class UndoBody(BaseModel):
    audit_id: str


class ActionLogBody(BaseModel):
    """AL-L5 archive/unarchive request body (the actor is optional, defaulted server-side)."""

    actor: Optional[str] = None


@router.get("/projects")
def list_projects(view: Optional[str] = None,
                  sort: Optional[str] = None,
                  direction: Optional[str] = None,
                  lane: Optional[List[str]] = Query(None),
                  lifecycle: Optional[List[str]] = Query(None),
                  attention: Optional[List[str]] = Query(None),
                  band: Optional[List[str]] = Query(None),
                  profile: Optional[List[str]] = Query(None),
                  home: Optional[List[str]] = Query(None),
                  q: Optional[str] = None,
                  page: Optional[int] = None,
                  page_size: Optional[int] = None,
                  kind: Optional[List[str]] = Query(None),
                  operator: Optional[List[str]] = Query(None),
                  status: Optional[List[str]] = Query(None),
                  date_from: Optional[str] = None,
                  date_to: Optional[str] = None,
                  scope: Optional[str] = None) -> Dict[str, Any]:
    """The one board route. Omitted parameters keep the legacy envelope (compat).

    AL-L7/AL-L13: ``view=action_log`` rides this SAME path with its own additive vocabulary
    (kind/operator/status/date_from/date_to, plus the Orda 2026-09-17 ``scope`` pass-through:
    external|all, default external); the board envelope is byte-unchanged when they are
    absent, and the action-log envelope is a different shape entirely (``actions[]``).
    """
    provided: Dict[str, Any] = {}
    for key, value in (("view", view), ("sort", sort), ("direction", direction),
                       ("lane", lane), ("lifecycle", lifecycle), ("attention", attention),
                       ("band", band), ("profile", profile), ("home", home), ("q", q),
                       ("page", page), ("page_size", page_size), ("kind", kind),
                       ("operator", operator), ("status", status), ("date_from", date_from),
                       ("date_to", date_to), ("scope", scope)):
        if value is not None:
            provided[key] = value
    try:
        return get_service().board(**provided)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/projects/{project_id}")
def project_detail(project_id: str, pane: Optional[str] = None) -> Dict[str, Any]:
    try:
        detail = get_service().project_detail(project_id, pane=pane)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not detail:
        raise HTTPException(status_code=404, detail="unknown project_id")
    return detail


@router.get("/candidates")
def candidates() -> Dict[str, Any]:
    """Recovery Inbox (MC-S5): candidates + accepted/suppressed counts + server triage order."""
    return get_service().recovery_inbox()


@router.get("/attention")
def attention() -> Dict[str, Any]:
    """Global attention queue (MC-S2): per-group counts, bounded `ordered`, committed marker."""
    return get_service().attention_queue()


@router.get("/staleness")
def staleness() -> Dict[str, Any]:
    """Banded staleness view (MC-S4): user-anchored bands, config edges, explicit quiet group."""
    return get_service().staleness_view()


@router.get("/noise")
def noise() -> Dict[str, Any]:
    return get_service().noise()


@router.get("/scan/status")
def scan_status() -> Dict[str, Any]:
    return get_service().scan_status()


@router.post("/scan")
def trigger_scan() -> Dict[str, Any]:
    handle = get_service().scan()
    return {"started_at": handle.started_at, "run_id": handle.run_id, "mode": handle.mode}


@router.post("/projects/{project_id}/review")
def review(project_id: str, body: ReviewBody) -> Dict[str, Any]:
    try:
        return get_service().review(body.action, project_id, body.payload or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/review/undo")
def undo(body: UndoBody) -> Dict[str, Any]:
    try:
        return get_service().review("undo", "", {"audit_id": body.audit_id})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ── AL-L5: action-log archive routes (new routes, EXISTING review pattern) ────────────────────
# Action-log rows are not Continuum projects and carry no project_id, so they cannot ride
# POST /projects/{id}/review (service.py:2846). The pattern (snapshot + atomic write + audit
# event + undo) is identical; only the store and the path differ.
@router.post("/action-log/{action_id}/archive")
def action_log_archive(action_id: str, body: Optional[ActionLogBody] = None) -> Dict[str, Any]:
    try:
        return get_service().action_log_review("archive", action_id,
                                              (body.actor if body else None) or "local")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/action-log/{action_id}/unarchive")
def action_log_unarchive(action_id: str, body: Optional[ActionLogBody] = None) -> Dict[str, Any]:
    try:
        return get_service().action_log_review("unarchive", action_id,
                                              (body.actor if body else None) or "local")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/overview")
def overview(mode: Optional[str] = None,
             include_candidates: Optional[bool] = None,
             include_suppressed: bool = False,
             sort: str = "quiet",
             page: int = 1,
             page_size: int = 50) -> Dict[str, Any]:
    """MVP review overview (one route, two modes). Read-only."""
    try:
        return get_service().overview(
            mode=mode, include_candidates=include_candidates,
            include_suppressed=include_suppressed, sort=sort,
            page=page, page_size=page_size)
    except OverviewModeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except OverviewConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# -- Owner review queue (fixtures-only, generated at build time) ---------------
import json as _json
from pathlib import Path as _Path
from fastapi.responses import JSONResponse as _JSONResponse

_FIXTURE_QUEUE = _Path(__file__).resolve().parent.parent / "fixtures" / "review-queue.json"

@router.get("/review-queue")
def review_queue():
    if _FIXTURE_QUEUE.exists():
        try:
            data = _json.loads(_FIXTURE_QUEUE.read_text(encoding="utf-8"))
            return _JSONResponse(data)
        except Exception as exc:
            return _JSONResponse({"error": str(exc)}, status_code=500)
    return _JSONResponse({"error": "review-queue.json not found; run tools/generate_review_queue.py"}, status_code=404)


@router.get("/events")
def events() -> Dict[str, Any]:
    """Polling fallback for the socket (socket is a no-op on OAuth remotes).

    CP-9: the only refresh signal is the last COMMITTED snapshot marker — identical to the one
    /scan/status and /projects publish — so a started or errored run never triggers a reload.
    MC-L5: the structured `scan_state` rides along so the browser never holds the board on a
    scan and always renders a server-published phase.
    """
    svc = get_service()
    status = svc.scan_status()
    snapshot = status.get("committed_snapshot") or {"run_id": None, "committed_at": None}
    return {"scan": status.get("phase"), "snapshot_id": snapshot["run_id"],
            "committed_at": snapshot["committed_at"], "data_as_of": snapshot["committed_at"],
            "server_time": status.get("server_time"),
            "scan_state": status.get("scan_state")}
