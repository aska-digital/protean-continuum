"""M7 — continuum.service.

The façade over M1–M6 and the ONLY module the HTTP layer talks to. Contains no transport and no
JS. Every payload carries ``data_as_of``, ``confidence``, ``confidence_band``, ``evidence_tier``
and ``source_sessions[]`` with verbatim ids (INV-5, INV-6).

Review mutations (§9) are registry-only, append-only audited, and reversible. Nothing here ever
writes to a Hermes source database (INV-2).
"""
from __future__ import annotations

import functools
import json
import math
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import classify as classify_mod
from . import cluster as cluster_mod
from . import evidence as evidence_mod
from . import model as model_mod
from . import scanner
from . import audience as audience_mod
from . import task_home as task_home_mod
from . import action_log as action_log_mod
from .config import Config, load_config
from .registry import Registry, ReviewEvent

DAY = 86400.0

LIFECYCLE_NAMES = classify_mod.LIFECYCLE_NAMES

# ---------------------------------------------------------------------------
# Hybrid Kanban — human-only placement overlay (D-KB-1 .. D-KB-5)
PLACEMENTS = ("ongoing", "blocked", "waiting_on_you", "paused", "done", "shipped", "scrapped")
PLACEMENT_SET = set(PLACEMENTS)
# derived -> placement for accepted cards with no placement row.
# CP-3 (M4): LS-7 and LS-8 are deliberately ABSENT. A derived lifecycle can never create a
# terminal placement; LS-1/LS-5/LS-7/LS-8/LS-9 all fall through to
# ongoing + placement_source='unplaced_unmapped'. Only an audited human placement is terminal.
_DERIVED_TO_PLACEMENT = {
    "LS-2": "ongoing",
    "LS-3": "blocked",
    "LS-4": "waiting_on_you",
    "LS-6": "paused",
}
# unmapped derived lifecycles resolve to ongoing with placement_source unplaced_unmapped
_UNMAPPED_PLACEMENT_SOURCE = "unplaced_unmapped"
# Lane order for the board (Inbox + 7 placements)
BOARD_COLUMNS = ("inbox", "ongoing", "blocked", "waiting_on_you", "paused", "done", "shipped", "scrapped")
# placement values that are terminal (visible, not suppressed)
_TERMINAL_PLACEMENTS = {"done", "shipped", "scrapped"}
# MC-S4: parked/hiatus/terminal cards are QUIET. They stay on the board and are never removed.
_QUIET_COLUMNS = {"paused"} | set(_TERMINAL_PLACEMENTS)

# ---------------------------------------------------------------------------
# MC-L3 / MC-S4 — staleness bands, anchored on USER-FACING activity.
#
# `stall_age_days` is a different question ("is it stuck?") and is NEVER the band anchor.
# Edges come from config (`staleness_band_edges`, locked defaults [3, 7, 30]); a NULL anchor
# gets its own explicit band and is never presented as the oldest band.
STALENESS_BANDS = ("0-3d", "3-7d", "7-30d", "30d+")
STALENESS_BAND_UNKNOWN = "unknown_anchor"
STALENESS_BAND_QUIET = "quiet"
# The vocabulary a `band=` filter accepts (MC-L3 §1.2). `quiet` is a group, not a filter value.
STALENESS_BAND_FILTERS = STALENESS_BANDS + (STALENESS_BAND_UNKNOWN,)
STALENESS_BAND_ANCHOR = "last_user_worked_on"
_DEFAULT_BAND_EDGES = (3.0, 7.0, 30.0)

# ---------------------------------------------------------------------------
# M4 — Continuity Playground read model (CP-1 .. CP-18)
#
# All of this is a READ-TIME projection over the registry. It adds no table, column,
# lifecycle value, lane, or mutation, and it never writes a source database.

VIEWS = ("today", "all")

# AL-L6/AL-L7: the action log is an ADDITIVE third view on the SAME playground path, and it is a
# DISTINCT projection — flat action rows in its own §5 envelope, never a filtered board variant.
ACTION_LOG_VIEW = action_log_mod.VIEW
#: AL-L13 filters: valid only with view=action_log (they are not board filters).
#: `scope` is the additive Orda-directive-2026-09-17 filter (external|all; default external).
ACTION_LOG_ONLY_KEYS = ("kind", "operator", "status", "date_from", "date_to", "scope")

# §5.2 canonical sort fields. `message_count_total` / `session_count_total` are canonical
# because the materialized project.session_count can be stale (projection addendum).
SORT_FIELDS = (
    "last_worked_on", "last_user_worked_on", "message_count_total", "session_count_total",
    "user_facing_session_count", "delegated_session_count", "name", "placement",
    "derived_lifecycle", "attention_state", "recency_state", "confidence",
    "confidence_band", "primary_profile", "next_action_state", "last_stopping_point_on",
)
# §5.1 compatibility aliases: sort=quiet == last_worked_on desc; the Today default
# `attention` is the attention_state field (ascending = least attention, desc = most).
SORT_ALIASES = {"quiet": ("last_worked_on", "desc"), "attention": ("attention_state", None)}
_SORT_DEFAULT_DIRECTION = {
    "last_worked_on": "desc", "last_user_worked_on": "desc", "message_count_total": "desc",
    "session_count_total": "desc", "user_facing_session_count": "desc",
    "delegated_session_count": "desc", "name": "asc", "placement": "asc",
    "derived_lifecycle": "asc", "attention_state": "desc", "recency_state": "asc",
    "confidence": "desc", "confidence_band": "desc", "primary_profile": "asc",
    "next_action_state": "desc", "last_stopping_point_on": "desc",
}
# §7.2 attention rank (0 = most attention). Ascending sort = least attention first.
ATTENTION_RANK = {
    "blocked": 0, "waiting_on_user": 1, "stale_with_commitment": 2, "needs_review": 3,
    "no_user_work": 4, "recent": 5, "quiet": 6, "unknown": 7,
}
# MC-L2: the rail's three server groups, in locked order. The attention vocabulary is CLOSED:
# every card's `attention_state` is one of the eight ATTENTION_RANK keys and nothing else — the
# old ninth value is normalized away (see `_attention_state`).
ATTENTION_GROUPS = ("awaiting_user", "blocked", "stale_active")
ATTENTION_ORDERED_CAP = 20
RECENCY_RANK = {"recent": 0, "stale": 1, "no_user_work": 2, "quiet": 3, "unknown": 4}
CONFIDENCE_BAND_RANK = {"unknown": 0, "low": 1, "medium": 2, "high": 3}
NEXT_ACTION_STATE_RANK = {"none": 0, "done": 1, "unknown": 2, "open": 3}
LIFECYCLE_ORDER = {("LS-{}".format(i)): i for i in range(1, 10)}

_LANE_COUNT_KEYS = tuple(BOARD_COLUMNS) + ("total", "continuity_total", "suppressed_total",
                                           "unplaced_accepted")

# ---------------------------------------------------------------------------
# MVP review overview (architecture decisions continuum-architecture-mvp-overview-001
# + continuum-architecture-mvp-ab-001). One codebase, one endpoint, one response
# shape; variant selection is a read-time filter over project_session.accepted.

DEFAULT_OVERVIEW_MODE = "immediate"

_OVERVIEW_MODES = ("immediate", "accepted_only")

_OVERVIEW_VARIANT_LABELS = {
    "immediate": "Overview \u2014 candidates shown as derived",
    "accepted_only": "Overview \u2014 accepted projects only",
}

SESSIONS_INLINE_LIMIT = 20

SUMMARY_RULE_ID = "mvp.summary.v1"
SUMMARY_MAX_CHARS = 320
SUMMARY_LINE1_MAX_CHARS = 160
SUMMARY_MIN_EXCERPT_CHARS = 24
SUMMARY_KIND_PRIORITY = {"goal": 0, "next_action": 1, "decision": 2,
                         "blocker": 3, "link": 4}

_MVP_INTERRUPTED_RE = re.compile(r"^\s*Operation interrupted")
_MVP_JSON_BODY_RE = re.compile(r"^\s*\{")
_MVP_BARE_URL_RE = re.compile(r"^https?://\S+$")
_MVP_SIGNAL_RE = re.compile(r"CS-\d+")
_MVP_LEADING_DASHES_RE = re.compile(r"^\s*-{2,}\s*")
_MVP_LEADING_LABEL_RE = re.compile(r"^(?:goal|decision)\s*:\s*", re.IGNORECASE)
_MVP_META_TAIL_RE = re.compile(r"\s+(?:team6_agent|from)\s*:.*$", re.DOTALL)
_MVP_WS_RE = re.compile(r"\s+")


class OverviewModeError(ValueError):
    """Bad mode input -> HTTP 400 (unknown mode or contradictory mode+flag)."""


class OverviewConfigError(RuntimeError):
    """Invalid configured overview_mode -> HTTP 500 (never silent default)."""


def _safe_compile(pattern: str) -> Optional[Any]:
    try:
        return re.compile(pattern)
    except re.error:
        return None


def _mvp_collapse(text: str) -> str:
    return _MVP_WS_RE.sub(" ", (text or "")).strip()


def _mvp_clean_excerpt(text: str) -> str:
    """Drop leading --- markers/labels and metadata tails, collapse whitespace."""
    out = _MVP_LEADING_DASHES_RE.sub("", text or "")
    out = _MVP_LEADING_LABEL_RE.sub("", out)
    out = _MVP_META_TAIL_RE.sub("", out)
    return _mvp_collapse(out)


def _mvp_cap_words(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].strip()
    return cut or text[:limit].strip()


def _mvp_rejected(excerpt: str, health_res: Sequence[Any]) -> bool:
    """Pinned reject filter for mvp.summary.v1 (deterministic, module-level)."""
    if not excerpt:
        return True
    if _MVP_INTERRUPTED_RE.search(excerpt):
        return True
    if _MVP_JSON_BODY_RE.search(excerpt):
        return True
    if _MVP_BARE_URL_RE.search(_mvp_collapse(excerpt)):
        return True
    for rx in health_res:
        try:
            if rx.search(excerpt):
                return True
        except Exception:
            continue
    return False


def _mvp_summarize(name: str, lifecycle_name: str, quiet_days: Optional[float],
                   linked_count: int, n_profiles: int, workspace_label: Optional[str],
                   evidence_rows: Sequence[Any],
                   health_res: Sequence[Any]
                   ) -> Tuple[str, int, List[Dict[str, Any]], List[Any]]:
    """Deterministic rule mvp.summary.v1. Model-free; never sees the mode."""
    scored: List[Tuple[Tuple[int, str, str, str], Any]] = []
    for e in evidence_rows:
        excerpt = e["excerpt"] if isinstance(e, dict) else e["excerpt"]
        if excerpt is None or _mvp_collapse(str(excerpt)) == "":
            continue
        if len(_mvp_collapse(str(excerpt))) < SUMMARY_MIN_EXCERPT_CHARS:
            continue
        kind = e["kind"] if isinstance(e, dict) else e["kind"]
        if kind not in SUMMARY_KIND_PRIORITY:
            continue
        if _mvp_rejected(str(excerpt), health_res):
            continue
        profile = e["profile_name"] if isinstance(e, dict) else e["profile_name"]
        sid = e["session_id"] if isinstance(e, dict) else e["session_id"]
        eid = e["evidence_id"] if isinstance(e, dict) else e["evidence_id"]
        scored.append(((SUMMARY_KIND_PRIORITY[kind], str(profile or ""),
                        str(sid or ""), str(eid or "")), e))
    if scored:
        scored.sort(key=lambda t: t[0])
        best = scored[0][1]
        line1 = _mvp_cap_words(_mvp_clean_excerpt(str(
            best["excerpt"] if isinstance(best, dict) else best["excerpt"])),
            SUMMARY_LINE1_MAX_CHARS)
        if not line1:
            pass
        else:
            tier = int(best["tier"] if isinstance(best, dict) else best["tier"])
            basis = [{"evidence_id": str(best["evidence_id"] if isinstance(best, dict)
                                          else best["evidence_id"]),
                      "kind": str(best["kind"] if isinstance(best, dict) else best["kind"]),
                      "tier": tier,
                      "profile": str(best["profile_name"] if isinstance(best, dict)
                                     else best["profile_name"]),
                      "session_id": str(best["session_id"] if isinstance(best, dict)
                                        else best["session_id"])}]
            quiet_txt = ("%.1f" % quiet_days) if quiet_days is not None else "unknown"
            text = ("{} \u00b7 {} \u00b7 quiet {}d \u00b7 {} sessions across {} profiles"
                    " \u00b7 T{}".format(line1, lifecycle_name, quiet_txt,
                                         linked_count, n_profiles, tier))
            # LS-3 blocked-on tail: cleanest surviving blocker row, 80 chars.
            blockers = [t for t in scored
                        if (t[1]["kind"] if isinstance(t[1], dict) else t[1]["kind"])
                        == "blocker"]
            return text, tier, basis, blockers
    # Tier-0 fallback: stored metadata only, no evidence basis.
    quiet_txt = ("%.1f" % quiet_days) if quiet_days is not None else "unknown"
    ws = workspace_label or "no shared workspace"
    text = ("{} \u00b7 {} sessions across {} profiles \u00b7 {} \u00b7 {}"
            " \u00b7 quiet {}d".format(name, linked_count, n_profiles, ws,
                                       lifecycle_name, quiet_txt))
    return _mvp_cap_words(text, SUMMARY_MAX_CHARS), 0, [], []


# --------------------------------------------------------------------------- helpers

def _resume_links(links: Sequence[Any], aud: Optional[Dict[Any, Any]] = None
                  ) -> List[Dict[str, Any]]:
    """OD3 is DRAFT: no invented route. The verified-shape copy fallback ships (ux-contract §2.4).

    CP-5 / D-US-5: ONLY a verified ``USER_FACING`` session is a resume target. ``DELEGATED``,
    ``AUTOMATED`` and ``UNKNOWN`` sessions never produce a resume link or any copy command;
    UNKNOWN may still appear as supporting evidence elsewhere but carries no affordance.
    The command strings are unchanged and byte-exact.
    """
    out = []
    aud = aud or {}
    for l in links:
        profile = l["profile_name"] if not isinstance(l, dict) else l["profile_name"]
        sid = l["session_id"] if not isinstance(l, dict) else l["session_id"]
        a = aud.get((profile, sid), ("UNKNOWN", None))
        value = a[0] if isinstance(a, (tuple, list)) else "UNKNOWN"
        if value != "USER_FACING":
            continue
        out.append({
            "profile": profile,
            "session_id": sid,
            "kind": "desktop",
            "route": None,  # DRAFT (OD3) — never invented
            "os_url": None,
            "audience": value,
            "copy_command": "hermes --resume {}".format(sid),
            "copy_command_profile_scoped": "hermes -p {} --resume {}".format(profile, sid),
        })
    return out


def _row_to_dict(row: Any) -> Dict[str, Any]:
    return dict(row) if row is not None else {}


# --------------------------------------------------------------------------- M4 helpers

def _norm_multi(value: Any) -> List[str]:
    """Normalise a repeatable query parameter into a list of non-empty strings."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value if v is not None and str(v) != ""]
    text = str(value)
    return [text] if text != "" else []


def _json_locator(value: Any) -> Dict[str, Any]:
    """Evidence locator column -> dict. Malformed/absent locators never raise."""
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


# §7.2/§7.1 plain-language reason codes for the Today reference groups. No score, no
# countdown, no guilt language (§7.3).
_ATTENTION_REASONS = {
    "blocked": "derived blocked state with evidence",
    "waiting_on_user": "waiting on you",
    "stale_with_commitment": "quiet with an open commitment",
    "needs_review": "candidate not yet reviewed",
    "no_user_work": "no user-facing conversation",
    "recent": "recent user work",
    "quiet": "paused or placed by you",
    "unknown": "no reliable activity evidence",
}
_START_REASON = {
    "blocked": "blocked",
    "waiting_on_user": "waiting_on_user",
    "stale_with_commitment": "open_next_action",
    "needs_review": "needs_review",
    "recent": "recent_user_work",
    "no_user_work": "no_recent_user_work",
}

# Session-pane presentation strings — VERBATIM from Shayba's ux-continuum-session-panes.md §3.
# The service never composes its own copy; the browser renders exactly these strings.
_PANE_COPY = {
    "block_recent": "RECENT MESSAGES",
    "block_user": "YOUR RECENT MESSAGES",
    "role_user": "YOU",
    "role_agent": "AGENT",
    "window": "Last {N} captured \u00b7 {M} messages in this session",
    "window_no_count": "Recent captured messages",
    "excerpt_chip": "excerpt",
    "excerpt_note": "Captured excerpt \u2014 the full message is not available here.",
    "empty_session": "No messages captured for this session.",
    "empty_user_window": "No messages of yours in the captured window.",
    "empty_not_user_facing": ("No messages of yours in this session. "
                              "It was started by an agent, not by you."),
    "empty_unknown_audience": "No messages of yours in this session.",
    "loading": "Loading messages\u2026",
    "load_failed": "Couldn't load messages.",
    "unavailable": "Message capture isn't available yet.",
    "disclosure": "Messages",
}

_ZERO_WIDTH_RE = re.compile("[\u200b-\u200f\u2028\u2029\ufeff]")
_PANE_WS_RE = re.compile(r"\s+")


def _pane_text(value: Any) -> str:
    """Display-safe whitespace whitelist only (Shayba D-SP-11): nothing else is altered."""
    if value is None:
        return ""
    text = _ZERO_WIDTH_RE.sub("", str(value))
    return _PANE_WS_RE.sub(" ", text).strip()


def _pane_row(probe: Any, excerpt_chars: Optional[int] = None) -> Dict[str, Any]:
    """One plain-text pane row. Text is byte-exact except the D-SP-11 whitespace whitelist.

    D-SP-8: ``excerpt`` is TRUE only for a row the capture actually clipped. ``content`` is the
    ``excerpt_chars``-clipped copy of ``content_head``, so a short message that survived capture
    whole is not an excerpt and must not carry the chip or suppress a reveal affordance.
    """
    text = probe.content or ""
    head = getattr(probe, "content_head", "") or ""
    if head:
        clipped = len(head) > len(text)
    else:
        clipped = excerpt_chars is not None and len(text) >= int(excerpt_chars)
    return {
        "role": "YOU" if probe.role == "user" else "AGENT",
        "role_raw": probe.role,
        "timestamp": probe.timestamp,
        "text": _pane_text(probe.content),
        "msg_id": probe.msg_id,
        # Only capture-clipped rows are honest excerpts; the renderer keeps no show-more for
        # those (D-SP-8(b)) and treats an unclipped row as the complete captured text.
        "excerpt": bool(clipped),
    }


# --------------------------------------------------------------------------- service

@dataclass
class ScanHandle:
    run_id: str
    started_at: float
    mode: str
    status: str = "running"


class Service:
    def __init__(self, cfg: Optional[Config] = None, registry: Optional[Registry] = None):
        self.cfg = cfg or load_config()
        self.registry = registry or Registry(self.cfg.registry_path_resolved())
        self.registry.migrate()
        self._scan_lock = threading.Lock()
        self._now: float = time.time()
        self._scan_state: Dict[str, Any] = {
            "phase": "idle", "progress": 0.0, "last_scan_at": None,
            "last_error": None, "run_id": None,
        }

    # ------------------------------------------------------------------ scan (M1-M6)

    def scan(self, profiles: Optional[Sequence[str]] = None, full: bool = False,
             probe: bool = True) -> ScanHandle:
        """Kick M1->M6. Synchronous (the desktop plugin polls /scan/status).

        CP-9: a second concurrent caller receives the ACTIVE run handle and never queues a
        competing scan that would re-read the corpus after the first one commits.
        """
        refs = scanner.discover_profiles(self.cfg.hermes_home, self.cfg)
        if profiles:
            wanted = set(profiles)
            refs = [r for r in refs if r.profile_name in wanted]
        mode = "full" if full else "incremental"
        watermark = self.registry.source_db_watermarks()
        # Enrich each profile's committed snapshot with the known present session ids, so the
        # scanner's coarse-window read can detect newly-observed IDs and read them even when
        # their activity timestamp is outside the coarse window.
        for _ref in refs:
            prof = _ref.profile_name
            if prof in watermark:
                known = [r["session_id"] for r in self.registry.session_facts(prof)]
                watermark[prof]["known_ids"] = known
        run_id = uuid.uuid4().hex
        if not self._scan_lock.acquire(blocking=False):
            return ScanHandle(run_id=str(self._scan_state.get("run_id") or ""),
                              started_at=float(self._scan_state.get("active_started_at") or 0.0),
                              mode=str(self._scan_state.get("active_mode") or mode),
                              status="running")
        try:
            self._scan_state.update({"phase": "scanning", "progress": 0.0,
                                     "last_error": None, "active_mode": mode,
                                     "active_started_at": time.time(),
                                     "run_id": run_id})
            batch = scanner.scan(refs, watermark=watermark, cfg=self.cfg, mode=mode,
                                 probe=probe, run_id=run_id)
            handle = ScanHandle(run_id=batch.run_id, started_at=batch.started_at, mode=batch.mode)
            self.registry.begin_scan_run(batch.run_id, batch.mode)
            try:
                # CP-9 / §8.2: ONE commit boundary per scan. A failure inside the ingest
                # rolls the derived writes back and leaves the last committed snapshot in
                # place; declared overlays and review history are never touched here.
                with self.registry.transaction():
                    self._ingest(batch)
                self.registry.end_scan_run(
                    batch.run_id, sessions_read=len(batch.facts),
                    messages_probed=len(batch.probes),
                    profiles_scanned=len(batch.status), status="ok")
                self._scan_state.update({"phase": "idle", "progress": 1.0,
                                         "last_scan_at": time.time(), "run_id": batch.run_id})
            except Exception as exc:  # a failed scan must never delete registry rows
                self.registry.end_scan_run(
                    batch.run_id, sessions_read=len(batch.facts),
                    messages_probed=len(batch.probes),
                    profiles_scanned=len(batch.status), status="error", error=str(exc))
                self._scan_state.update({"phase": "error", "last_error": str(exc)})
                raise
            return handle
        finally:
            self._scan_lock.release()

    def committed_snapshot(self) -> Dict[str, Any]:
        """One marker for the last COMMITTED scan, shared by /projects, /scan/status, /events.

        CP-9: it moves only when a scan transaction commits, so an event poll can never refresh
        the board for a merely started or errored run.
        """
        row = self.registry.conn.execute(
            "SELECT run_id, ended_at FROM scan_run WHERE status='ok'"
            " ORDER BY COALESCE(ended_at, started_at) DESC LIMIT 1").fetchone()
        if row is None:
            return {"run_id": None, "committed_at": None}
        return {"run_id": str(row["run_id"]),
                "committed_at": (float(row["ended_at"]) if row["ended_at"] is not None else None)}

    def scan_state_object(self) -> Dict[str, Any]:
        """MC-L5: the ONE structured scan-observability object every read payload carries.

        Duration, mode and counts come from the existing ``scan_run`` row (no DDL, MC-L13) plus
        the in-flight phase from the live scan handle. Honest ``null``s are used when no run
        exists, so a caller can never mistake "unknown" for "zero". ``elapsed_seconds`` is
        exactly ``ended_at - started_at`` for the last completed run (MC-A4).
        """
        row = self.registry.conn.execute(
            "SELECT run_id, started_at, ended_at, mode, profiles_scanned, sessions_read,"
            " messages_probed, status, error FROM scan_run"
            " ORDER BY COALESCE(ended_at, started_at) DESC LIMIT 1").fetchone()
        live = self._scan_state
        phase = live.get("phase") or "idle"
        started_at: Optional[float] = None
        ended_at: Optional[float] = None
        mode: Optional[str] = None
        sessions_read: Optional[int] = None
        messages_probed: Optional[int] = None
        profiles_scanned: Optional[int] = None
        last_error = live.get("last_error")
        run_id = live.get("run_id")
        if row is not None:
            started_at = row["started_at"]
            ended_at = row["ended_at"]
            mode = row["mode"]
            sessions_read = row["sessions_read"]
            messages_probed = row["messages_probed"]
            profiles_scanned = row["profiles_scanned"]
            run_id = run_id or row["run_id"]
            if row["error"]:
                last_error = row["error"]
            # A run that finished between two reads is still observable: only the LIVE handle
            # may claim "scanning", and a stored error is reported while no newer run exists.
            if phase == "idle" and row["status"] == "error":
                phase = "error"
        if phase == "scanning":
            started_at = live.get("active_started_at") or started_at
            ended_at = None
            mode = live.get("active_mode") or mode
        elapsed: Optional[float] = None
        if started_at is not None and ended_at is not None:
            elapsed = float(ended_at) - float(started_at)
        return {
            "phase": phase,
            "mode": mode,
            "run_id": run_id,
            "started_at": started_at,
            "ended_at": ended_at,
            "elapsed_seconds": elapsed,
            "sessions_read": sessions_read,
            "messages_probed": messages_probed,
            "profiles_scanned": profiles_scanned,
            "last_error": last_error,
            "last_scan_at": (ended_at if ended_at is not None else live.get("last_scan_at")),
        }

    def _ingest(self, batch: scanner.ScanBatch) -> None:
        self.registry.upsert_session_facts(batch.facts)
        for st in batch.status:
            if st.skipped:
                # An unchanged profile was cheaply skipped: neither its facts nor its
                # tombstones are re-derived from an empty batch (§8.1 step 2/5).
                continue
            seen = [f.session_id for f in batch.facts if f.profile_name == st.profile_name]
            # A coarse-window read is intentionally partial: never tombstone sessions that
            # were outside the read window (§8.1 "never tombstone because partial").
            is_full = st.status == "ok" and not st.partial
            if is_full:
                self.registry.tombstone_missing(st.profile_name, seen)
                fine = max((f.last_activity_at or 0) for f in batch.facts
                           if f.profile_name == st.profile_name) if seen else None
            else:
                fine = None
            self.registry.upsert_source_db(
                st.profile_name, st.path, exists_now=1 if st.status != "missing" else 0,
                db_mtime=st.mtime, db_size=st.size, schema_version=st.schema_version,
                session_count=st.session_count, message_count=st.message_count,
                status=st.status, fine_watermark=fine or None, coarse_watermark=fine or None)

        # --- identity closure (M4 §8.1): clustering must see the whole connected project
        # component, including retained facts/links from skipped and partially-read profiles.
        # Build the clustering universe = freshly read facts + retained present facts for every
        # profile that was cheaply skipped or read through a bounded coarse window. Rerunning
        # the existing clustering over this closed component means a changed session can
        # reconcile into a project whose other members live in a profile we did not re-read.
        universe = list(batch.facts)
        have = {"{}/{}".format(f.profile_name, f.session_id) for f in batch.facts}
        for st in batch.status:
            if not (st.skipped or st.partial) or not batch.facts:
                # A fully-skipped no-op scan (no changed session) keeps the prior empty
                # universe: nothing needs to reconcile. Closure is only needed when a
                # changed session could share its project component with a profile we
                # did not re-read.
                continue
            for row in self.registry.session_facts(st.profile_name):
                ref = "{}/{}".format(row["profile_name"], row["session_id"])
                if ref in have:
                    continue
                universe.append(self._fact_from_row(row))
                have.add(ref)
        if len(universe) > len(batch.facts):
            # a retained fact was folded in only because a changed session could share its
            # component; that is the closure. If no retained fact could close a skipped or
            # partial profile that was expected to have facts, the scan already fell back to a
            # full reconciliation (ambiguous/shrink/schema/sweep) before we got here.
            pass

        noise = cluster_mod.classify_noise(universe, batch.probes, self.cfg)
        self.registry.apply_noise(noise)

        # --- audience (D-US-1/D-US-2/D-US-3): derived, total, deterministic -------------
        brief_index = audience_mod.build_brief_index(self.cfg)
        delegators = audience_mod.build_delegator_index(batch.delegators)
        head_by_ref: Dict[str, str] = {}
        for p in batch.probes:
            key = "{}/{}".format(p.profile_name, p.session_id)
            if p.position == "head" or key not in head_by_ref:
                head_by_ref.setdefault(key, p.content_head or p.content)
        first_turn_by_ref = dict(head_by_ref)
        first_turn_by_ref.update(batch.first_user)
        aud_map: Dict[str, Any] = {}
        for f in batch.facts:
            ref = "{}/{}".format(f.profile_name, f.session_id)
            value, reason, evidence_ref = audience_mod.classify_audience(
                f, first_turn_by_ref.get(ref), brief_index, delegators, self.cfg)
            aud_map[ref] = (value, reason, evidence_ref)
        self.registry.apply_audience(aud_map)

        keys = evidence_mod.canonical_keys(universe, [], batch.probes, self.cfg)
        clusters = cluster_mod.build_clusters(universe, keys, self.cfg, noise)
        clusters_by_id = {c.cluster_id: c for c in clusters}
        ev_items = evidence_mod.extract(batch.probes, self.cfg)
        ev_by_ref: Dict[str, List[evidence_mod.EvidenceItem]] = {}
        for e in ev_items:
            ev_by_ref.setdefault("{}/{}".format(e.locator.get("profile"),
                                                e.locator.get("session_id")), []).append(e)
        facts_by_ref = {"{}/{}".format(f.profile_name, f.session_id): f for f in universe}
        probes_by_ref: Dict[str, List[scanner.MessageProbe]] = {}
        for p in batch.probes:
            probes_by_ref.setdefault("{}/{}".format(p.profile_name, p.session_id), []).append(p)
        ev_by_cluster: Dict[str, List[evidence_mod.EvidenceItem]] = {}
        for c in clusters:
            items: List[evidence_mod.EvidenceItem] = []
            for m in c.member_refs:
                items.extend(ev_by_ref.get(m, []))
            ev_by_cluster[c.cluster_id] = items
        classifications = classify_mod.classify(
            clusters, facts_by_ref, probes_by_ref, ev_by_cluster, self.cfg,
            model=None)
        model_mod.reconcile(classifications, clusters_by_id, self.registry, self.cfg,
                            evidence_by_cluster=ev_by_cluster,
                            audience_ctx={
                                "first_turn_by_ref": first_turn_by_ref,
                                "delegations": list(batch.delegation_rows),
                            })

    @staticmethod
    def _fact_from_row(row: Any) -> scanner.SessionFact:
        """Rebuild a SessionFact from a retained ``session_fact`` registry row.

        The registry row carries every clustering-relevant field the scanner emits, so a
        skipped profile's committed facts can close a changed session's project component
        without re-reading the source DB. ``chat_type``/``last_activity_description`` are not
        persisted (Tier-0 source extras); their defaults are used.
        """
        return scanner.SessionFact(
            profile_name=row["profile_name"],
            session_id=row["session_id"],
            source=row["source"] or "unknown",
            title=row["title"],
            title_source=row["title_source"],
            display_name=row["display_name"],
            cwd=row["cwd"],
            workspace_root=row["workspace_root"],
            git_repo_root=row["git_repo_root"],
            git_branch=row["git_branch"],
            started_at=row["started_at"],
            last_activity_at=row["last_activity_at"],
            message_count=int(row["message_count"] or 0),
            tool_call_count=int(row["tool_call_count"] or 0),
            parent_session_id=row["parent_session_id"],
            model=row["model"],
            archived=int(row["archived"] or 0),
            pinned=int(row["pinned"] or 0),
            hidden=int(row["hidden"] or 0),
            tool_names=row["tool_names"],
            content_hash=row["content_hash"] or "",
        )

    def scan_status(self, run_id: Optional[str] = None) -> Dict[str, Any]:
        row = self.registry.conn.execute(
            "SELECT * FROM scan_run ORDER BY started_at DESC LIMIT 1").fetchone()
        out = dict(self._scan_state)
        if row:
            out["run_id"] = out.get("run_id") or row["run_id"]
            out["last_scan_at"] = row["ended_at"] or row["started_at"]
            out["last_error"] = row["error"]
        # CP-9: `data_as_of` is the last COMMITTED snapshot, never the wall clock of this call,
        # and the same marker is what /events and /projects publish.
        snapshot = self.committed_snapshot()
        out["committed_snapshot"] = snapshot
        out["data_as_of"] = snapshot["committed_at"]
        # MC-L4: "now" is its own field; it is never published as `data_as_of`.
        out["server_time"] = time.time()
        out["scan_state"] = self.scan_state_object()
        return out

    # ------------------------------------------------------------------ read surfaces

    def _projects(self) -> List[Dict[str, Any]]:
        rows = [dict(r) for r in self.registry.projects()]
        now = time.time()
        for p in rows:
            p["_derived_lifecycle"] = p["lifecycle"]
            p["_derived_lifecycle_name"] = LIFECYCLE_NAMES.get(p["lifecycle"], "unknown")
            declared = self.registry.effective_declared(p["project_id"], now=now)
            p["_declared"] = declared
            p["declared_stale"] = self.registry.is_declared_stale(p["project_id"], now=now)
            p["_accepted"] = self._is_accepted(p["project_id"])
            p["_dismissed"] = declared.get("dismissed") == "1"
            p["_merged_into"] = declared.get("merged_into")
            if declared.get("parked") == "1":
                p["lifecycle"] = "LS-6"
            elif declared.get("lifecycle_override"):
                p["lifecycle"] = declared["lifecycle_override"]
            if declared.get("drive_expected") == "1":
                p["drive_expected"] = 1
            na = self.registry.next_actions_for(p["project_id"], now=now)
            p["_next_action"] = None
            for n in na:
                expired = n["expires_at"] is not None and n["expires_at"] < now
                if n["source"] == "declared" and not expired:
                    p["_next_action"] = {"text": n["text"], "state": n["state"],
                                         "source": n["source"], "verified_at": n["verified_at"],
                                         "expires_at": n["expires_at"], "expired": False}
                    break
        return rows

    def _is_accepted(self, project_id: str) -> bool:
        row = self.registry.conn.execute(
            "SELECT COUNT(*) AS n FROM project_session WHERE project_id=? AND accepted=1",
            (project_id,)).fetchone()
        return bool(row and row["n"])

    # ------------------------------------------------------------------ audience helpers

    def _audience_snapshot(self) -> Tuple[Dict[Any, Any], Dict[str, Dict[str, Any]]]:
        """Audience of every session plus per-project aggregates, in a single read.

        Returns ``({(profile, session_id): (audience, reason)}, {project_id: {...}})``.
        D-US-5: the aggregates keep delegated/automated sessions in the counts; they are
        excluded from primary selection and from resume affordances only.
        """
        aud: Dict[Any, Any] = {}
        for f in self.registry.session_facts():
            aud[(f["profile_name"], f["session_id"])] = (
                f["audience"] or "UNKNOWN", f["audience_reason"])
        proj: Dict[str, Dict[str, Any]] = {}
        for r in self.registry.conn.execute(
                "SELECT ps.project_id, ps.profile_name, ps.session_id, ps.role_in_project, "
                "sf.audience, sf.audience_reason "
                "FROM project_session ps LEFT JOIN session_fact sf "
                "ON sf.profile_name=ps.profile_name AND sf.session_id=ps.session_id"):
            a = r["audience"] or "UNKNOWN"
            d = proj.setdefault(r["project_id"], {
                "counts": {"USER_FACING": 0, "DELEGATED": 0, "AUTOMATED": 0, "UNKNOWN": 0},
                "primary": None, "primary_reason": None})
            d["counts"][a] = d["counts"].get(a, 0) + 1
            if r["role_in_project"] == "primary":
                d["primary"] = "{}/{}".format(r["profile_name"], r["session_id"])
                d["primary_reason"] = r["audience_reason"]
        return aud, proj

    @staticmethod
    def _audience_of(aud: Dict[Any, Any], profile: str, session_id: str) -> str:
        a = aud.get((profile, session_id))
        return a[0] if isinstance(a, (tuple, list)) else "UNKNOWN"

    @staticmethod
    def _audience_reason(aud: Dict[Any, Any], profile: str, session_id: str) -> Optional[str]:
        """Stored v3 audience reason for a session, or None when no fact is present.

        Never fabricated: an absent fact yields an explicit ``None`` rather than a invented
        code, so a supporting ``source_sessions`` row is honest about what is known (CP-5).
        """
        a = aud.get((profile, session_id))
        return a[1] if isinstance(a, (tuple, list)) else None

    def _audience_fields(self, p: Dict[str, Any], proj_info: Optional[Dict[str, Any]],
                         aud: Dict[Any, Any], fact_by_ref: Optional[Dict[str, Any]] = None
                         ) -> Dict[str, Any]:
        """The card-level audience block: anchor, NO_USER_SESSION and unverified state (D-US-6)."""
        info = proj_info or {"counts": {"USER_FACING": 0, "DELEGATED": 0,
                                        "AUTOMATED": 0, "UNKNOWN": 0},
                             "primary": None, "primary_reason": None}
        counts = dict(info["counts"])
        primary = info.get("primary")
        primary_aud = "UNKNOWN"
        if primary:
            prof, _, sid = primary.partition("/")
            primary_aud = self._audience_of(aud, prof, sid)
        anchor_ref = p.get("anchor_session")
        anchor_reason = p.get("anchor_reason") or audience_mod.NONE_REASON
        anchor: Optional[Dict[str, Any]] = None
        line = audience_mod.no_user_session_line()
        if anchor_ref:
            prof, _, sid = anchor_ref.partition("/")
            f = (fact_by_ref or {}).get(anchor_ref)
            title = (f["title"] if f is not None and f["title"] else "") or ""
            anchor_aud = self._audience_of(aud, prof, sid)
            anchor = {
                "profile": prof, "session_id": sid, "title": title,
                "audience": anchor_aud,
                # CP-5: only a verified USER_FACING anchor exposes a resume command.
                "copy_command": ("hermes --resume {}".format(sid)
                                 if anchor_aud == "USER_FACING" else None),
                "copy_command_profile_scoped":
                    ("hermes -p {} --resume {}".format(prof, sid)
                     if anchor_aud == "USER_FACING" else None),
            }
            line = "Conversation: {} ({})".format(title, prof)
        elif primary:
            prof, _, sid = primary.partition("/")
            f = (fact_by_ref or {}).get(primary)
            title = (f["title"] if f is not None and f["title"] else "") or ""
            line = "Conversation: {} ({})".format(title, prof)
        primary_block = None
        if primary and primary_aud == "USER_FACING":
            # CP-5: a stale v3 role_in_project='primary' on a non-USER_FACING session is not
            # trusted — no primary projection is exposed for it (audience_unverified still
            # records the unverified state).
            p_prof, _, p_sid = primary.partition("/")
            pf = (fact_by_ref or {}).get(primary)
            primary_block = {
                "profile": p_prof, "session_id": p_sid,
                "title": ((pf["title"] if pf is not None and pf["title"] else "") or ""),
                "audience": primary_aud, "selection": "linked_primary",
            }
        return {
            "primary_session": primary_block,
            "primary_audience": primary_aud,
            "primary_audience_reason": info.get("primary_reason"),
            "audience_counts": counts,
            "audience_unverified": bool(primary and primary_aud == "UNKNOWN"),
            "no_user_session": bool(anchor is None and primary is None),
            "anchor_session": anchor_ref,
            "anchor_reason": anchor_reason,
            "anchor": anchor,
            "conversation_line": line,
        }

    def _suppressed(self, p: Dict[str, Any]) -> bool:
        # CP-1 (M4): continuity_visible iff not explicitly dismissed and not explicitly merged.
        # A derived LS-7 complete state is NOT a suppression: the project stays visible.
        return bool(p["_dismissed"] or p["_merged_into"])

    def _board_suppressed(self, p: Dict[str, Any]) -> bool:
        # Board excludes only dismissed/merged; terminal placement is NOT an archive
        return bool(p["_dismissed"] or p["_merged_into"])

    def _validate_placement(self, value: Optional[str]) -> None:
        if value is None:
            return
        if value == "inbox" or value not in PLACEMENT_SET:
            raise ValueError("invalid placement: {!r}; expected one of: {}".format(value, ", ".join(sorted(PLACEMENT_SET))))

    def _resolve_lane(self, p: Dict[str, Any], declared_ts: Dict[str, Dict[str, Tuple[str, float]]]) -> Tuple[str, str]:
        # Returns (column, placement_source)
        ts_map = declared_ts.get(p["project_id"], {})
        placement_entry = ts_map.get("placement")
        # Effective placement value must be active (TTL null -> effective_declared keeps it)
        placement_val = None
        if placement_entry is not None:
            # verify it is still effective (not expired) — placement is durable so check effective
            eff = p["_declared"].get("placement")
            if eff is not None and eff == placement_entry[0]:
                placement_val = eff
        if placement_val is not None and placement_val in PLACEMENT_SET:
            return placement_val, "human"
        if not p["_accepted"]:
            return "inbox", "candidate"
        derived = p.get("_derived_lifecycle") or p["lifecycle"]
        mapped = _DERIVED_TO_PLACEMENT.get(derived)
        if mapped is not None:
            return mapped, "derived_unplaced"
        return "ongoing", "unplaced_unmapped"

    def _card(self, p: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "project_id": p["project_id"],
            "name": p["name"] or "(untitled cluster)",
            "lifecycle": p["lifecycle"],
            "lifecycle_name": LIFECYCLE_NAMES.get(p["lifecycle"], "unknown"),
            "phase": p["phase"],
            "owner_profile": p["owner_profile"],
            "last_substantive_activity": p["last_substantive_activity"],
            "stall_age_days": p["stall_age_days"],
            "session_count": p["session_count"],
            "confidence": p["confidence"],
            "confidence_band": p["confidence_band"],
            "evidence_tier": p["evidence_tier"],
            "evidence_tiers": [p["evidence_tier"]],
            "next_action": p["_next_action"],
            "drive_expected": bool(p["drive_expected"]),
            "alert_state": self._alert_state(p),
            "derived_updated_at": p["derived_updated_at"],
            "declared_stale": p["declared_stale"],
            # MC-L4: one meaning on every surface — the committed registry snapshot timestamp.
            "data_as_of": self._snapshot_ts(),
        }

    def _alert_state(self, p: Dict[str, Any]) -> str:
        if p["lifecycle"] in ("LS-6", "LS-7") or p["_dismissed"] or p["_merged_into"]:
            return "quiet"
        if p["lifecycle"] in ("LS-4", "LS-3"):
            return "attention"
        if p["lifecycle"] == "LS-5" and (p["drive_expected"] or p["_next_action"]):
            return "attention"
        return "none"

    # ------------------------------------------------------------------ attention (§8)

    def _attention_groups(self) -> Tuple[Dict[str, List[Dict[str, Any]]], int]:
        """MC-L2: attention is derived from grounded conditions only — never from age alone."""
        groups: Dict[str, List[Dict[str, Any]]] = {g: [] for g in ATTENTION_GROUPS}
        declared_ts = self.registry.declared_rows_with_timestamps()
        for p in self._projects():
            if self._suppressed(p):
                continue
            # MC-A8: a paused / terminal / complete placement is quiet by construction, so it is
            # never an attention item — while staying fully visible on the board.
            column, _source = self._resolve_lane(p, declared_ts)
            if self._band_quiet(column, p.get("_derived_lifecycle") or p["lifecycle"]):
                continue
            if p["confidence_band"] in ("low", "unknown"):
                continue  # CR-3: low/unknown confidence is a candidate, not an attention item
            card = self._card(p)
            if p["lifecycle"] == "LS-4":
                groups["awaiting_user"].append({
                    "kind": "awaiting_user", "project_id": p["project_id"],
                    "reason": "awaiting you", "since": p["last_substantive_activity"],
                    "confidence_band": p["confidence_band"], "card": card})
            elif p["lifecycle"] == "LS-3":
                groups["blocked"].append({
                    "kind": "blocked", "project_id": p["project_id"],
                    "reason": "blocked", "since": p["last_substantive_activity"],
                    "confidence_band": p["confidence_band"], "card": card})
            elif p["lifecycle"] == "LS-5" and (p["drive_expected"] or p["_next_action"]):
                who = "drive-expected" if p["drive_expected"] else "next action open"
                groups["stale_active"].append({
                    "kind": "stale_active", "project_id": p["project_id"],
                    "reason": "{} & quiet {}d".format(who, int(p["stall_age_days"] or 0)),
                    "since": p["last_substantive_activity"],
                    "confidence_band": p["confidence_band"], "card": card})
        total = sum(len(v) for v in groups.values())
        return groups, total

    def attention_queue(self) -> Dict[str, Any]:
        """MC-S2: the server's GLOBAL, unpaged attention answer (the rail's authority).

        Counts are per group over the COMPLETE continuity set, `ordered` is a bounded id list in
        locked group order with an honest `ordered_omitted`, and `data_as_of` is the committed
        snapshot. The browser renders these values and derives none of them (INV-MC-10).
        """
        groups, total = self._attention_groups()
        counts: Dict[str, Any] = {g: len(groups[g]) for g in ATTENTION_GROUPS}
        counts["total"] = total
        ordered: List[Dict[str, Any]] = []
        for group in ATTENTION_GROUPS:
            for item in groups[group]:
                if len(ordered) >= ATTENTION_ORDERED_CAP:
                    break
                ordered.append({"rank": len(ordered) + 1, "group": group,
                                "kind": item["kind"], "project_id": item["project_id"]})
        snapshot = self.committed_snapshot()
        return {"groups": groups, "counts": counts, "attention_count": total,
                "ordered": ordered, "ordered_omitted": max(0, total - len(ordered)),
                "data_as_of": snapshot["committed_at"], "server_time": time.time(),
                "committed_snapshot": snapshot, "scan_state": self.scan_state_object()}

    def attention(self, **_) -> Dict[str, Any]:
        """M4 attention envelope (top-level keys frozen by the KB-N1 unchanged-surface guard).

        The mission-control additions (per-group `counts`, `ordered`, `ordered_omitted`,
        `scan_state`) are published on the existing ``/attention`` route; this method keeps the
        M4 shape byte-compatible while `data_as_of` moves to the committed snapshot (MC-L4).
        """
        groups, total = self._attention_groups()
        return {"groups": groups, "attention_count": total,
                "data_as_of": self._snapshot_ts()}

    # ------------------------------------------------------------------ board

    def board(self, **kwargs: Any) -> Dict[str, Any]:
        """Read surface for ``GET /projects``.

        With NO recognised query argument the legacy envelope is returned unchanged, so
        every existing client (and the frozen bounded-kanban key-set guard) stays
        byte-compatible. With any recognised M4 query argument the Continuity Playground
        envelope is returned: canonical query echo, deterministic server-side sorting and
        filtering, complete lane counts, and the Today reference groups.
        """
        query = self._parse_board_query(kwargs)
        if query is None:
            return self._board_legacy()
        if query.get("view") == ACTION_LOG_VIEW:
            return self._action_log_view(query)
        return self._board_playground(query)

    def _board_legacy(self) -> Dict[str, Any]:
        raw = self._projects()
        visible = [p for p in raw if not self._board_suppressed(p)]
        pids = [p["project_id"] for p in visible]
        declared_ts = self.registry.declared_rows_with_timestamps(pids)
        accepted_map: Dict[str, float] = {}
        if pids:
            placeholders = ",".join("?" * len(pids))
            for row in self.registry.conn.execute(
                "SELECT target_id, MAX(ts) AS m FROM review_event WHERE action='accept' AND target_id IN ({}) GROUP BY target_id".format(placeholders),
                tuple(pids),
            ):
                if row["m"] is not None:
                    accepted_map[row["target_id"]] = float(row["m"])
        # D-KB-15: one session_facts read per request for title lookup
        fact_by_ref: Dict[str, Any] = {}
        for f in self.registry.session_facts():
            fact_by_ref["{}/{}".format(f["profile_name"], f["session_id"])] = f
        aud, proj_aud = self._audience_snapshot()
        counts: Dict[str, int] = {c: 0 for c in BOARD_COLUMNS}
        counts["total"] = 0
        counts["unplaced_accepted"] = 0
        enriched = []
        for p in visible:
            column, source = self._resolve_lane(p, declared_ts)
            counts[column] = counts.get(column, 0) + 1
            counts["total"] += 1
            if p["_accepted"] and source in ("derived_unplaced", "unplaced_unmapped"):
                counts["unplaced_accepted"] += 1
            card = self._card(p)
            urgent = p["_declared"].get("urgent") == "1"
            urgent_since = None
            ts_entry = declared_ts.get(p["project_id"], {}).get("urgent")
            if urgent and ts_entry is not None:
                urgent_since = ts_entry[1]
            card["column"] = column
            card["placement_source"] = source
            card["derived_lifecycle"] = p.get("_derived_lifecycle") or p["lifecycle"]
            card["derived_lifecycle_name"] = LIFECYCLE_NAMES.get(card["derived_lifecycle"], "unknown")
            card["urgent"] = urgent
            card["urgent_since"] = urgent_since
            # D-KB-15 projection: review status (overview semantics, exact label strings)
            status = "accepted" if p["_accepted"] else "candidate"
            card["review_status"] = status
            card["review_status_label"] = ("accepted by you" if status == "accepted" else "derived candidate \u2014 not reviewed")
            card["needs_review"] = (status == "candidate" and (p["confidence_band"] in ("low", "unknown") or p["evidence_tier"] == 0))
            # D-US-5/D-US-6: card-level audience block, anchor, NO_USER_SESSION state.
            card.update(self._audience_fields(p, proj_aud.get(p["project_id"]), aud, fact_by_ref))
            # D-KB-15 sessions: ordered primary first then session_id, cap 20, always list, omitted non-negative.
            # D-US-5: DELEGATED/AUTOMATED sessions stay in the counts and the detail view but are
            # never a resume affordance, so they are not in the inline session list.
            links = self.registry.links_for(p["project_id"])
            ordered = [l for l in sorted(
                links, key=lambda l: (0 if l["role_in_project"] == "primary" else 1,
                                      l["session_id"]))
                if self._audience_of(aud, l["profile_name"], l["session_id"])
                not in ("DELEGATED", "AUTOMATED")]
            sessions = []
            for l in ordered[:SESSIONS_INLINE_LIMIT]:
                f = fact_by_ref.get("{}/{}".format(l["profile_name"], l["session_id"]))
                title = (f["title"] if f is not None and f["title"] else "") or ""
                # CP-5 / D-US-5: only a USER_FACING session carries a resume/copy command.
                # UNKNOWN stays as supporting evidence with no affordance.
                resumable = (self._audience_of(
                    aud, l["profile_name"], l["session_id"]) == "USER_FACING")
                cli = "hermes --resume {}".format(l["session_id"]) if resumable else None
                cli_scoped = ("hermes -p {} --resume {}".format(
                    l["profile_name"], l["session_id"]) if resumable else None)
                sessions.append({
                    "profile": l["profile_name"],
                    "session_id": l["session_id"],
                    "title": title,
                    "role": l["role_in_project"],
                    "accepted": int(l["accepted"] or 0),
                    "cli_resume": cli,
                    "cli_resume_profile_scoped": cli_scoped,
                    "copy_command": cli,
                    "copy_command_profile_scoped": cli_scoped,
                    "route": None,
                    "link_reason": l["link_reason"] or "",
                })
            card["sessions"] = sessions
            card["sessions_omitted"] = len(links) - len(sessions)
            placement_ts = None
            pe = declared_ts.get(p["project_id"], {}).get("placement")
            if source == "human" and pe is not None:
                placement_ts = pe[1]
            accepted_at = accepted_map.get(p["project_id"])
            enriched.append((card, urgent, urgent_since, placement_ts, accepted_at, p))
        def _sort_key(t):
            card, urgent, urgent_since, placement_ts, accepted_at, p = t
            urgent_rank = 0 if urgent else 1
            us = urgent_since if (urgent and urgent_since is not None) else float("inf")
            pt = -placement_ts if (placement_ts is not None) else float("inf")
            if accepted_at is not None:
                fourth = -accepted_at
            else:
                q = p.get("stall_age_days")
                fourth = float(q) if q is not None else float("inf")
            return (urgent_rank, us, pt, fourth, card["project_id"])
        enriched.sort(key=_sort_key)
        items = [t[0] for t in enriched]
        violations = audience_mod.assert_no_resume_on_delegated(items)
        if violations:
            raise AssertionError(
                "audience invariant D-US-6 step 4 violated: {}".format(violations))
        return {"items": items, "page": 1, "total": len(items), "counts": counts,
                "data_as_of": self._snapshot_ts(),
                "scan_state": self.scan_state_object()}

    # ------------------------------------------------------------------ Continuity Playground (M4)

    def _parse_board_query(self, kwargs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Validate/normalise the additive ``/projects`` query (§5.1). ValueError -> HTTP 400.

        Returns ``None`` when the caller supplied no recognised query argument at all, which
        is the compatibility path (legacy envelope).
        """
        provided = {k: v for k, v in (kwargs or {}).items()
                    if v is not None and v != "" and v != [] and v != ()}
        if not provided:
            return None
        cfg = self.cfg
        view = provided.get("view")
        if view is None:
            view = str(cfg.get("board_default_view", "all") or "all")
        view = str(view)
        if view == ACTION_LOG_VIEW:
            # AL-L13: the action-log view has its own vocabulary. A board filter is never
            # silently ignored — the same ValueError path returns HTTP 400. `sort=` stays
            # valid, but only against the action-log sort vocabulary (timestamp|kind|target|
            # operator_flag); `direction` is not part of it.
            for key in ("lane", "lifecycle", "attention", "band", "profile", "home", "q",
                        "direction"):
                if provided.get(key) not in (None, ""):
                    raise ValueError(
                        "{} is not a view={} filter".format(key, ACTION_LOG_VIEW))
            return self._parse_action_log_query(provided)
        if view not in VIEWS:
            raise ValueError("invalid view: {!r}; expected one of: {}".format(
                view, ", ".join(VIEWS)))
        for key in ACTION_LOG_ONLY_KEYS:
            if provided.get(key) not in (None, ""):
                raise ValueError("{} is only valid with view={}".format(key, ACTION_LOG_VIEW))

        sort_raw = provided.get("sort")
        if sort_raw is None:
            if view == "today":
                sort_raw = str(cfg.get("board_default_sort_today", "attention") or "attention")
            else:
                sort_raw = str(cfg.get("board_default_sort_all", "quiet") or "quiet")
        sort_raw = str(sort_raw)
        sort_field, alias_direction = SORT_ALIASES.get(sort_raw, (sort_raw, None))
        if sort_field not in SORT_FIELDS:
            raise ValueError("invalid sort: {!r}; expected one of: {}".format(
                sort_raw, ", ".join(SORT_FIELDS)))

        direction = provided.get("direction")
        if direction is None:
            direction = alias_direction or _SORT_DEFAULT_DIRECTION[sort_field]
        direction = str(direction).lower()
        if direction not in ("asc", "desc"):
            raise ValueError("invalid direction: {!r}; expected 'asc' or 'desc'".format(
                direction))

        lanes = _norm_multi(provided.get("lane"))
        for value in lanes:
            if value not in BOARD_COLUMNS:
                raise ValueError("invalid lane: {!r}".format(value))
        lifecycles = _norm_multi(provided.get("lifecycle"))
        for value in lifecycles:
            if value not in LIFECYCLE_ORDER:
                raise ValueError("invalid lifecycle: {!r}".format(value))
        attentions = _norm_multi(provided.get("attention"))
        for value in attentions:
            if value not in ATTENTION_RANK:
                raise ValueError("invalid attention: {!r}".format(value))
        bands = _norm_multi(provided.get("band"))
        for value in bands:
            if value not in STALENESS_BAND_FILTERS:
                raise ValueError("invalid band: {!r}; expected one of: {}".format(
                    value, ", ".join(STALENESS_BAND_FILTERS)))
        profiles = _norm_multi(provided.get("profile"))
        # TH-L11/§6: the TASK-HOME panel filter is an additive PLAYGROUND-path query. An
        # invalid value raises ValueError and therefore returns HTTP 400 on the existing path.
        homes = _norm_multi(provided.get("home"))
        for value in homes:
            if value not in task_home_mod.PANEL_GROUPS:
                raise ValueError("invalid home: {!r}; expected one of: {}".format(
                    value, ", ".join(task_home_mod.PANEL_GROUPS)))

        q = provided.get("q")
        q = "" if q is None else str(q)

        if "page" in provided:
            try:
                page = int(provided["page"])
            except (TypeError, ValueError):
                raise ValueError("invalid page: {!r}".format(provided.get("page")))
        else:
            page = 1
        if page < 1:
            raise ValueError("invalid page: {!r}; must be >= 1".format(page))

        page_size_max = int(cfg.get("board_page_size_max", 200) or 200)
        if "page_size" in provided:
            try:
                page_size = int(provided["page_size"])
            except (TypeError, ValueError):
                raise ValueError("invalid page_size: {!r}".format(provided.get("page_size")))
        else:
            page_size = int(cfg.get("board_page_size_default", 100) or 100)
        if page_size < 1 or page_size > page_size_max:
            raise ValueError("invalid page_size: {!r}; must be 1..{}".format(
                page_size, page_size_max))

        return {
            "view": view, "sort": sort_field, "direction": direction, "sort_raw": sort_raw,
            "lanes": lanes, "lifecycles": lifecycles, "attention": attentions,
            "bands": bands, "profiles": profiles, "homes": homes, "q": q, "page": page,
            "page_size": page_size,
        }

    # ------------------------------------------------------------------ Orda action log (AL-*)

    def _parse_action_log_query(self, provided: Dict[str, Any]) -> Dict[str, Any]:
        """AL-L13 vocabulary for ``view=action_log`` (ValueError -> HTTP 400).

        The view is the READ projection of the action-log ledger and is switched by
        ``action_log_enabled``; a disabled projection fails loudly (never a silent empty board).
        """
        if not action_log_mod.enabled_for(self.cfg):
            raise ValueError(
                "view={} is disabled (config action_log_enabled is false)".format(ACTION_LOG_VIEW))
        page_size_max = int(self.cfg.get("board_page_size_max", 200) or 200)
        return action_log_mod.parse_query(provided, page_size_default=action_log_mod.PAGE_SIZE_DEFAULT,
                                          page_size_max=page_size_max)

    def _action_log_view(self, q: Dict[str, Any]) -> Dict[str, Any]:
        """§5 envelope for the action-log projection. READ-ONLY: never writes, never locks (AL-L8)."""
        return action_log_mod.view(self.cfg, q)

    def _snapshot_ts(self) -> float:
        """``data_as_of`` — the committed registry snapshot time, never the wall clock (§8.2)."""
        row = self.registry.conn.execute(
            "SELECT MAX(COALESCE(ended_at, started_at)) AS m FROM scan_run WHERE status='ok'"
        ).fetchone()
        if row is not None and row["m"] is not None:
            return float(row["m"])
        row = self.registry.conn.execute(
            "SELECT MAX(derived_updated_at) AS m FROM project").fetchone()
        if row is not None and row["m"] is not None:
            return float(row["m"])
        return 0.0

    # ------------------------------------------------------------------ staleness bands (MC-S4)

    def band_edges(self) -> List[float]:
        """The configured staleness-band edges, validated at config load (MC-A17)."""
        raw = self.cfg.get("staleness_band_edges") or list(_DEFAULT_BAND_EDGES)
        return [float(v) for v in raw]

    def _band_quiet(self, column: str, derived_lifecycle: Optional[str]) -> bool:
        """Parked / hiatus / terminal placements are quiet, never an alert (MC-A19)."""
        return column in _QUIET_COLUMNS or derived_lifecycle == "LS-7"

    def _staleness_band(self, anchor: Optional[float], quiet: bool, now: float) -> str:
        """MC-L3: band a project from its USER-FACING activity anchor and config edges."""
        if quiet:
            return STALENESS_BAND_QUIET
        if anchor is None:
            return STALENESS_BAND_UNKNOWN
        days = (float(now) - float(anchor)) / DAY
        edges = self.band_edges()
        if days <= edges[0]:
            return STALENESS_BANDS[0]
        if days <= edges[1]:
            return STALENESS_BANDS[1]
        if days <= edges[2]:
            return STALENESS_BANDS[2]
        return STALENESS_BANDS[3]

    def _band_map(self, values: Sequence[Dict[str, Any]]) -> Dict[str, int]:
        counts = {b: 0 for b in STALENESS_BAND_FILTERS}
        counts[STALENESS_BAND_QUIET] = 0
        for card in values:
            band = card.get("staleness_band")
            if band is None:
                continue
            counts[band] = counts.get(band, 0) + 1
        return counts

    def _link_aggregate(self, p: Dict[str, Any], links: Sequence[Any],
                        fact_by_ref: Dict[str, Any], aud: Dict[Any, Any],
                        preserved_by_ref: Optional[Dict[str, Any]] = None,
                        source_status: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Batched per-project aggregates (§6.2). Registry reads only; no source DB access.

        CP-7: message-count completeness is decided per durable link against the CURRENT fact or,
        for a tombstoned link, its PRESERVED count; source readability is carried separately so a
        card can never be called evidence-complete on message-count grounds alone.
        """
        preserved_by_ref = preserved_by_ref or {}
        source_status = source_status or {}
        counts = {"USER_FACING": 0, "DELEGATED": 0, "AUTOMATED": 0, "UNKNOWN": 0}
        msg_total = 0
        complete = True
        source_ok = True
        last_worked = None
        last_user = None
        last_user_ref = None
        last_support = None
        primary_ref = None
        linked_profiles: List[str] = []
        for l in links:
            profile = l["profile_name"]
            sid = l["session_id"]
            if profile not in linked_profiles:
                linked_profiles.append(profile)
            audience = self._audience_of(aud, profile, sid)
            counts[audience] = counts.get(audience, 0) + 1
            ref = "{}/{}".format(profile, sid)
            f = fact_by_ref.get(ref)
            count = None
            if f is not None:
                count = f["message_count"]
                act = f["last_activity_at"]
                if act is not None:
                    if last_worked is None or act > last_worked:
                        last_worked = act
                    if audience == "USER_FACING":
                        if last_user is None or act > last_user:
                            last_user = act
                            last_user_ref = ref
                    elif last_support is None or act > last_support:
                        last_support = act
            else:
                # a tombstoned / non-current fact keeps its preserved count (durable-link rule)
                pv = preserved_by_ref.get(ref)
                if pv is not None:
                    count = pv["message_count"]
            if count is not None:
                msg_total += int(count)
            else:
                complete = False
            st = source_status.get(profile)
            if st is not None and st != "ok":
                source_ok = False
            if audience == "USER_FACING" and l["role_in_project"] == "primary":
                primary_ref = ref
        if last_worked is None:
            basis = "unknown"
        elif last_support is None:
            basis = "user_facing"
        elif last_user is None:
            basis = "supporting"
        else:
            basis = "mixed"
        return {
            "session_count_total": len(links),
            "message_count_total": msg_total if links else None,
            "message_count_known": msg_total,
            "message_count_complete": bool(complete and links),
            "source_ok": bool(source_ok and links),
            "source_status": {prof: source_status.get(prof) for prof in sorted(linked_profiles)},
            "audience_counts": counts,
            "last_worked_on": last_worked,
            "last_worked_basis": basis,
            "last_user_worked_on": last_user,
            "last_user_ref": last_user_ref,
            "last_support_worked_on": last_support,
            "primary_ref": primary_ref,
            "linked_profiles": sorted(linked_profiles),
        }

    def _stopping_point(self, p: Dict[str, Any], evidence: Sequence[Any],
                        primary_ref: Optional[str], fact_by_ref: Dict[str, Any]
                        ) -> Dict[str, Any]:
        """Deterministic bounded stopping point (§6.3). Never a generated summary."""
        band = p.get("confidence_band") or "unknown"
        declared = p.get("_next_action")
        if declared and declared.get("text"):
            return {
                "kind": "declared", "text": str(declared["text"]), "source": "declared",
                "profile": None, "session_id": None, "evidence_id": declared.get("evidence_id"),
                "observed_at": declared.get("verified_at"), "evidence_tier": 0,
                "confidence_band": band,
            }

        def _latest(rows: Sequence[Any]) -> Optional[Any]:
            if not rows:
                return None
            return sorted(rows, key=lambda r: (float(r["extracted_at"] or 0),
                                               str(r["evidence_id"])))[-1]

        tier1 = [e for e in evidence if int(e["tier"] or 0) == 1]
        for kind in ("next_action", "blocker"):
            hit = _latest([e for e in tier1 if e["kind"] == kind])
            if hit is not None:
                loc = _json_locator(hit["locator"])
                return {
                    "kind": "derived_next_action" if kind == "next_action" else "derived_blocker",
                    "text": str(hit["excerpt"] or ""), "source": "tier1",
                    "profile": loc.get("profile"), "session_id": loc.get("session_id"),
                    "evidence_id": str(hit["evidence_id"]),
                    "observed_at": hit["extracted_at"], "evidence_tier": 1,
                    "confidence_band": band,
                }
        if primary_ref:
            profile, _, sid = primary_ref.partition("/")
            f = fact_by_ref.get(primary_ref)
            title = (f["title"] if f is not None and f["title"] else "") or ""
            if title:
                return {
                    "kind": "session_title", "text": title, "source": "tier0",
                    "profile": profile, "session_id": sid, "evidence_id": None,
                    "observed_at": (f["last_activity_at"] if f is not None else None),
                    "evidence_tier": 0, "confidence_band": band,
                }
        return {"kind": "none", "text": None, "source": None, "profile": None,
                "session_id": None, "evidence_id": None, "observed_at": None,
                "evidence_tier": 0, "confidence_band": band}

    def _next_action_block(self, p: Dict[str, Any], evidence: Sequence[Any]) -> Dict[str, Any]:
        declared = p.get("_next_action")
        if declared and declared.get("text"):
            return {
                "text": str(declared["text"]), "state": declared.get("state") or "open",
                "source": "declared", "verified_at": declared.get("verified_at"),
                "expires_at": declared.get("expires_at"), "expired": bool(declared.get("expired")),
                "evidence_id": declared.get("evidence_id"),
            }
        hits = [e for e in evidence
                if int(e["tier"] or 0) == 1 and e["kind"] == "next_action"]
        if hits:
            hit = sorted(hits, key=lambda r: (float(r["extracted_at"] or 0),
                                              str(r["evidence_id"])))[-1]
            return {
                "text": str(hit["excerpt"] or ""), "state": "open", "source": "derived",
                "verified_at": hit["extracted_at"], "expires_at": None, "expired": False,
                "evidence_id": str(hit["evidence_id"]),
            }
        return {"text": None, "state": "none", "source": None, "verified_at": None,
                "expires_at": None, "expired": False, "evidence_id": None}

    def _attention_state(self, p: Dict[str, Any], column: str, ag: Dict[str, Any],
                         evidence: Sequence[Any], next_action: Dict[str, Any]) -> str:
        """§7.2 attention rank. Grounded conditions only — never age alone."""
        if column in _TERMINAL_PLACEMENTS or column == "paused":
            return "quiet"
        derived = p.get("_derived_lifecycle") or p["lifecycle"]
        if p["_dismissed"] or p["_merged_into"]:
            return "quiet"
        if derived == "LS-3" and evidence:
            return "blocked"
        if derived == "LS-4":
            return "waiting_on_user"
        if (next_action.get("state") == "open" and next_action.get("source") == "declared"
                and not next_action.get("expired")):
            return "waiting_on_user"
        if derived == "LS-5" and (p["drive_expected"] or next_action.get("state") == "open"):
            return "stale_with_commitment"
        if (not p["_accepted"] and (p["confidence_band"] in ("low", "unknown")
                                    or p["evidence_tier"] == 0)):
            return "needs_review"
        if ag["audience_counts"]["USER_FACING"] == 0:
            return "no_user_work"
        if ag["last_user_worked_on"] is None:
            return "unknown"
        recent_days = float(self.cfg.get("recent_days", 3) or 3)
        # MC-A7: the vocabulary is CLOSED at the eight §7.2 states. A card with user work that is
        # merely not recent is `quiet` — never an age-only alert (INV-MC-6). The retired ninth
        # value no longer exists anywhere in this module or in any payload.
        return "recent" if ag["last_user_worked_on"] >= self._now - recent_days * DAY else "quiet"

    def _recency_state(self, column: str, ag: Dict[str, Any]) -> str:
        if column in _TERMINAL_PLACEMENTS or column == "paused":
            return "quiet"
        if ag["audience_counts"]["USER_FACING"] == 0:
            return "no_user_work"
        if ag["last_user_worked_on"] is None:
            return "unknown"
        recent_days = float(self.cfg.get("recent_days", 3) or 3)
        if (self._now - ag["last_user_worked_on"]) <= recent_days * DAY:
            return "recent"
        return "stale"

    def _playground_card(self, p: Dict[str, Any], links: Sequence[Any],
                         evidence: Sequence[Any], fact_by_ref: Dict[str, Any],
                         aud: Dict[Any, Any], proj_aud: Dict[str, Any],
                         declared_ts: Dict[str, Dict[str, Tuple[str, float]]],
                         preserved_by_ref: Optional[Dict[str, Any]] = None,
                         source_status: Optional[Dict[str, str]] = None,
                         task_home_ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        pid = p["project_id"]
        column, source = self._resolve_lane(p, declared_ts)
        ag = self._link_aggregate(p, links, fact_by_ref, aud, preserved_by_ref, source_status)
        primary_ref = ag["primary_ref"]
        anchor_ref = p.get("anchor_session")
        selected_primary = primary_ref
        selection = "linked_primary" if primary_ref else None
        if selected_primary is None and anchor_ref:
            aprofile, _, asid = str(anchor_ref).partition("/")
            if self._audience_of(aud, aprofile, asid) == "USER_FACING":
                selected_primary = str(anchor_ref)
                selection = "anchor"
        card = self._card(p)
        card["session_count_total"] = ag["session_count_total"]
        card["message_count_total"] = ag["message_count_total"]
        card["message_count_known"] = ag["message_count_known"]
        card["message_count_complete"] = ag["message_count_complete"]
        # compatibility aliases (§5.2): the canonical totals, not the possibly stale columns
        card["session_count"] = ag["session_count_total"]
        card["message_count"] = ag["message_count_total"]
        card["user_facing_session_count"] = ag["audience_counts"]["USER_FACING"]
        card["delegated_session_count"] = ag["audience_counts"]["DELEGATED"]
        card["automated_session_count"] = ag["audience_counts"]["AUTOMATED"]
        card["unknown_session_count"] = ag["audience_counts"]["UNKNOWN"]
        card["audience_counts"] = dict(ag["audience_counts"])
        card["last_worked_on"] = ag["last_worked_on"]
        card["last_worked_basis"] = ag["last_worked_basis"]
        card["last_user_worked_on"] = ag["last_user_worked_on"]
        card["last_support_worked_on"] = ag["last_support_worked_on"]
        card["linked_profiles"] = list(ag["linked_profiles"])
        card["column"] = column
        card["placement"] = p["_declared"].get("placement")
        card["placement_source"] = source
        card["derived_lifecycle"] = p.get("_derived_lifecycle") or p["lifecycle"]
        card["derived_lifecycle_name"] = LIFECYCLE_NAMES.get(card["derived_lifecycle"], "unknown")
        urgent = p["_declared"].get("urgent") == "1"
        ts_entry = declared_ts.get(pid, {}).get("urgent")
        card["urgent"] = urgent
        card["urgent_since"] = ts_entry[1] if (urgent and ts_entry is not None) else None
        status = "accepted" if p["_accepted"] else "candidate"
        card["review_status"] = status
        card["review_status_label"] = ("accepted by you" if status == "accepted"
                                       else "derived candidate \u2014 not reviewed")
        card["needs_review"] = (status == "candidate"
                                and (p["confidence_band"] in ("low", "unknown")
                                     or p["evidence_tier"] == 0))
        # MC-L6: a bounded, self-sufficient evidence bundle. `excerpt` is the capture-bounded
        # stored extraction (never a message body); locator keeps the verbatim profile/session id.
        card["evidence_refs"] = [
            {"evidence_id": str(e["evidence_id"]), "tier": int(e["tier"] or 0),
             "kind": e["kind"], "excerpt": e["excerpt"] or "",
             "locator": _json_locator(e["locator"]),
             "source_hash": e["source_hash"], "extracted_at": e["extracted_at"]}
            for e in evidence]
        card["evidence_tiers"] = sorted({int(e["tier"] or 0) for e in evidence}) or [0]
        # CP-7: evidence completeness is independent of message-count completeness. It is false
        # when a linked source is unavailable, or when a tiered card has no supporting evidence.
        card["source_status"] = dict(ag["source_status"])
        card["evidence_complete"] = bool(
            links and ag["source_ok"]
            and (bool(evidence) or int(p["evidence_tier"] or 0) == 0))
        card["next_action"] = self._next_action_block(p, evidence)
        card["next_action_state"] = card["next_action"]["state"]
        card["last_stopping_point"] = self._stopping_point(
            p, evidence, selected_primary, fact_by_ref)
        card["attention_state"] = self._attention_state(
            p, column, ag, evidence, card["next_action"])
        card["attention_reason"] = _ATTENTION_REASONS.get(card["attention_state"])
        card["recency_state"] = self._recency_state(column, ag)
        # audience fields (anchor / NO_USER_SESSION / unverified) — existing projection
        card.update(self._audience_fields(p, proj_aud.get(pid), aud, fact_by_ref))
        # bounded inline session projection (unchanged shape; audience-filtered affordances)
        ordered = [l for l in sorted(
            links, key=lambda l: (0 if l["role_in_project"] == "primary" else 1,
                                  l["session_id"]))
            if self._audience_of(aud, l["profile_name"], l["session_id"])
            not in ("DELEGATED", "AUTOMATED")]
        sessions = []
        resume_targets = []
        for l in ordered:
            profile = l["profile_name"]
            sid = l["session_id"]
            audience = self._audience_of(aud, profile, sid)
            f = fact_by_ref.get("{}/{}".format(profile, sid))
            title = (f["title"] if f is not None and f["title"] else "") or ""
            resumable = (audience == "USER_FACING")
            cli = "hermes --resume {}".format(sid) if resumable else None
            cli_scoped = ("hermes -p {} --resume {}".format(profile, sid)
                          if resumable else None)
            if len(sessions) < SESSIONS_INLINE_LIMIT:
                sessions.append({
                    "profile": profile, "session_id": sid, "title": title,
                    "role": l["role_in_project"], "accepted": int(l["accepted"] or 0),
                    "cli_resume": cli, "cli_resume_profile_scoped": cli_scoped,
                    "copy_command": cli, "copy_command_profile_scoped": cli_scoped,
                    "route": None, "link_reason": l["link_reason"] or "",
                })
            if resumable:
                resume_targets.append({
                    "profile": profile, "session_id": sid, "title": title,
                    "audience": audience,
                    "copy_command": "hermes -p {} --resume {}".format(profile, sid),
                    "route": None,
                })
        # §4.2: when the verified user-facing anchor resolves OUTSIDE the cluster it is still
        # the project's resume target, and it is listed first.
        if selection == "anchor" and selected_primary and not any(
                t["profile"] == selected_primary.partition("/")[0]
                and t["session_id"] == selected_primary.partition("/")[2]
                for t in resume_targets):
            aprofile, _, asid = selected_primary.partition("/")
            af = fact_by_ref.get(selected_primary)
            resume_targets.insert(0, {
                "profile": aprofile, "session_id": asid,
                "title": ((af["title"] if af is not None and af["title"] else "") or ""),
                "audience": "USER_FACING",
                "copy_command": "hermes -p {} --resume {}".format(aprofile, asid),
                "route": None,
            })
        card["sessions"] = sessions
        card["sessions_omitted"] = len(links) - len(sessions)
        card["resume_targets"] = resume_targets
        primary_block = None
        if selected_primary:
            aprofile, _, asid = selected_primary.partition("/")
            f = fact_by_ref.get(selected_primary)
            primary_block = {
                "profile": aprofile, "session_id": asid,
                "title": ((f["title"] if f is not None and f["title"] else "") or ""),
                "audience": self._audience_of(aud, aprofile, asid),
                "selection": selection,
            }
        card["primary_session"] = primary_block
        card["primary_profile"] = selected_primary.partition("/")[0] if selected_primary else None
        card["source_sessions"] = [
            {"profile": l["profile_name"], "session_id": l["session_id"],
             "audience": self._audience_of(aud, l["profile_name"], l["session_id"]),
             "reason": self._audience_reason(aud, l["profile_name"], l["session_id"])}
            for l in links]
        # ---- MC-S4: staleness band — user-facing anchor, config edges, explicit NULL band ----
        card["staleness_band"] = self._staleness_band(
            ag["last_user_worked_on"],
            self._band_quiet(column, card["derived_lifecycle"]), self._now)
        card["staleness_band_anchor"] = STALENESS_BAND_ANCHOR
        # `stall_age_days` stays a SEPARATE signal and is never rewritten to agree (MC-A18).
        # ---- MC-L7: owner is the declared field verbatim, else the verified primary, else none
        declared_owner = p["_declared"].get("owner")
        if declared_owner:
            card["owner"] = declared_owner
            card["owner_source"] = "declared"
        elif card["primary_profile"]:
            card["owner"] = card["primary_profile"]
            card["owner_source"] = "primary_profile"
        else:
            card["owner"] = None
            card["owner_source"] = "no_owner"
        # ---- MC-L6 / INV-MC-11: every derived label publishes a resolvable claim_source ----
        card["claim_source"] = self._claim_sources(p, card, evidence, ag, fact_by_ref)
        card["data_as_of"] = self._snapshot_ts()
        card["_q_blob"] = self._search_blob(card, evidence)
        # ---- §3 rule 1 / §6: TASK-HOME projection. Applied LAST, after every existing
        # projection has been computed from the dashboard's own lane, so the override can never
        # rewrite lifecycle, attention, recency or the staleness band (TH-L2 / §3 rule 2).
        self._attach_task_home(p, card, task_home_ctx, column, source)
        return card

    def _attach_task_home(self, p: Dict[str, Any], card: Dict[str, Any],
                          ctx: Optional[Dict[str, Any]], pre_column: str,
                          pre_source: str) -> None:
        """Attach the §6 card block and apply the §3 read-time lane override.

        TH-L10: TASK-HOME-only keys live in the panel only — they never enter ``items[]`` and
        never change ``counts.total``. TH-L5: nothing here is persisted; the override exists
        only in this response. TH-C2: the shadowed local value is published alongside
        TASK-HOME's, which wins.
        """
        pid = p["project_id"]
        item = (ctx or {}).get("by_pid", {}).get(pid) if ctx else None
        section = item.get("section") if item else None
        lane = task_home_mod.SECTION_LANES.get(section or "") if section else None
        if item is None or lane is None:
            # §3: DASHBOARD_ONLY (no TASK-HOME line) or ABSENT (line gone; override withdrawn)
            card["home"] = section or "DASHBOARD_ONLY"
            card["task_home"] = None
            return
        conflicts = self._task_home_conflicts(item, pre_column, pre_source)
        if pre_column != lane:
            card["placement_shadowed"] = pre_column
            card["column"] = lane
            card["placement_source"] = "task_home"
        card["state_source"] = "task_home"
        card["home"] = section
        card["task_home"] = {
            "key": item.get("key"),
            "proc": item.get("proc"),
            "section": section,
            "section_label": item.get("section_label"),
            "lane": lane,
            "line": item.get("line"),
            "line_no": item.get("line_no"),
            "receipt": bool(item.get("receipt")),
            "receipt_text": item.get("receipt_text"),
            "content_sha1": item.get("content_sha1"),
            "binding": item.get("binding"),
            "synced_at": (ctx or {}).get("synced_at"),
            "age_seconds": (ctx or {}).get("age_seconds"),
            "stale": (ctx or {}).get("stale"),
            "conflicts": conflicts,
        }

    @staticmethod
    def _task_home_conflicts(item: Dict[str, Any], pre_column: str,
                             pre_source: str) -> List[Dict[str, Any]]:
        """TH-L6/TH-C2: TASK-HOME wins on status; the local value is preserved and named.

        Every entry carries both values and the winner (§5 conflict shape, TH-A10).
        """
        lane = item.get("lane")
        if not lane or lane == pre_column:
            return []
        return [{
            "key": item.get("key"),
            "kind": ("placement_conflict" if pre_source == "human" else "lane_conflict"),
            "task_home_value": lane,
            "dashboard_value": pre_column,
            "winner": "task_home",
            "line_no": item.get("line_no"),
        }]

    def _claim_sources(self, p: Dict[str, Any], card: Dict[str, Any],
                       evidence: Sequence[Any], ag: Dict[str, Any],
                       fact_by_ref: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """MC-L6 / INV-MC-11: the resolvable stored source of every user-visible derived label.

        Every entry is NON-null and names a concrete stored row it resolves to — an evidence
        row, a declared-field row, a session_fact row, or the project row itself — so a client
        opens the bundle instead of trusting the label (Frida UX-11). A label whose source
        cannot be resolved is never rendered as an unexplained derived claim.
        """
        pid = p["project_id"]
        by_id = {str(e["evidence_id"]): e for e in evidence}
        out: Dict[str, Dict[str, Any]] = {}

        def project_row(field: str, label: str, basis: str) -> Dict[str, Any]:
            return {"label": label, "kind": "project_row", "project_id": pid,
                    "field": field, "basis": basis}

        def declared_row(field: str, label: str, basis: str) -> Dict[str, Any]:
            return {"label": label, "kind": "declared_field", "project_id": pid,
                    "field": field, "basis": basis}

        def evidence_row(eid: Any, label: str, basis: str) -> Optional[Dict[str, Any]]:
            row = by_id.get(str(eid))
            if row is None:
                return None
            loc = _json_locator(row["locator"])
            return {"label": label, "kind": "evidence",
                    "evidence_id": str(row["evidence_id"]), "tier": int(row["tier"] or 0),
                    "evidence_kind": row["kind"], "profile": loc.get("profile"),
                    "session_id": loc.get("session_id"), "msg_id": loc.get("msg_id"),
                    "basis": basis}

        def fact_row(ref: Optional[str], label: str, basis: str,
                     fallback: str) -> Dict[str, Any]:
            if ref and str(ref) in fact_by_ref:
                profile, _, sid = str(ref).partition("/")
                return {"label": label, "kind": "session_fact", "profile": profile,
                        "session_id": sid, "basis": basis}
            return project_row("lifecycle", label, fallback)

        # confidence / evidence grade — always a stored column on the project row
        out["confidence"] = project_row(
            "confidence_band", "confidence", "project confidence band (registry row)")
        # attention state and derived lifecycle — deterministic projections of the project row
        out["attention_state"] = project_row(
            "lifecycle", "attention_state",
            "derived from placement, lifecycle, evidence and the declared overlay")
        out["derived_lifecycle"] = project_row(
            "lifecycle", "derived_lifecycle", "derived lifecycle (registry row)")
        # next action: the declared overlay if present, else the tier-1 evidence row
        na = card.get("next_action") or {}
        entry = None
        if na.get("source") == "declared" and na.get("text"):
            entry = declared_row("next_action_verified", "next_action",
                                 "declared next action (audited overlay)")
        elif na.get("evidence_id"):
            entry = evidence_row(na["evidence_id"], "next_action",
                                 "rule-extracted next-action evidence")
        out["next_action"] = entry or project_row(
            "lifecycle", "next_action", "no declared or derived next action")
        # stopping point: the declared overlay, the tier-1 evidence row, or the primary title
        stop = card.get("last_stopping_point") or {}
        if stop.get("evidence_id"):
            entry = evidence_row(stop["evidence_id"], "stopping_point",
                                 "grounded stopping-point evidence")
            out["stopping_point"] = entry or project_row(
                "lifecycle", "stopping_point", "stopping-point evidence is no longer stored")
        elif stop.get("source") == "tier0" and stop.get("profile"):
            out["stopping_point"] = fact_row(
                "{}/{}".format(stop["profile"], stop.get("session_id") or ""),
                "stopping_point", "primary session title (stored session fact)",
                "no grounded stopping point")
        else:
            out["stopping_point"] = project_row(
                "name", "stopping_point", "no grounded stopping point")
        # staleness band: the user-facing activity anchor, or the honest quiet/unknown reason
        band = card.get("staleness_band")
        if band == STALENESS_BAND_UNKNOWN:
            out["staleness_band"] = project_row(
                "lifecycle", "staleness_band", "no user-facing activity anchor")
        elif band == STALENESS_BAND_QUIET:
            out["staleness_band"] = project_row(
                "lifecycle", "staleness_band", "parked, hiatus or terminal placement")
        else:
            out["staleness_band"] = fact_row(
                ag.get("last_user_ref"), "staleness_band",
                "most recent user-facing session activity", "no user-facing activity anchor")
        # owner: the declared owner, else the verified user-facing primary session
        primary = card.get("primary_session") or {}
        p_ref = ("{}/{}".format(primary.get("profile"), primary.get("session_id"))
                 if primary.get("profile") and primary.get("session_id") else None)
        if card.get("owner_source") == "declared":
            out["owner"] = declared_row("owner", "owner", "declared owner field (audited overlay)")
        elif p_ref and (p_ref in fact_by_ref
                        or str(p.get("anchor_session") or "") == p_ref):
            out["owner"] = fact_row(p_ref, "owner",
                                    "verified user-facing primary session",
                                    "no user-facing primary session")
            if out["owner"]["kind"] != "session_fact":
                out["owner"] = project_row(
                    "owner_profile", "owner",
                    "no declared owner and no resolvable primary session")
        else:
            out["owner"] = project_row(
                "owner_profile", "owner",
                "no declared owner and no resolvable primary session")
        return out

    @staticmethod
    def _search_blob(card: Dict[str, Any], evidence: Sequence[Any]) -> str:
        """Bounded metadata/evidence search text (§5.1 ``q``). Never a transcript."""
        parts: List[str] = [str(card.get("name") or ""), str(card.get("project_id") or "")]
        for s in card.get("sessions") or []:
            parts.append(str(s.get("title") or ""))
            parts.append("{}/{}".format(s.get("profile") or "", s.get("session_id") or ""))
        for ref in card.get("source_sessions") or []:
            parts.append("{}/{}".format(ref.get("profile") or "", ref.get("session_id") or ""))
        stop = card.get("last_stopping_point") or {}
        if stop.get("text"):
            parts.append(str(stop["text"]))
        na = card.get("next_action") or {}
        if na.get("text"):
            parts.append(str(na["text"]))
        # bounded evidence excerpts already stored in the registry (never a transcript)
        for e in evidence or []:
            if e["excerpt"]:
                parts.append(str(e["excerpt"]))
        return " \u00b7 ".join(p for p in parts if p).casefold()

    @staticmethod
    def _sort_value(card: Dict[str, Any], field: str) -> Tuple[str, Any, str]:
        value: Any = None
        if field == "last_worked_on":
            value = card.get("last_worked_on")
        elif field == "last_user_worked_on":
            value = card.get("last_user_worked_on")
        elif field == "message_count_total":
            value = card.get("message_count_total")
        elif field == "session_count_total":
            value = card.get("session_count_total")
        elif field == "user_facing_session_count":
            value = card.get("user_facing_session_count")
        elif field == "delegated_session_count":
            value = card.get("delegated_session_count")
        elif field == "name":
            value = card.get("name")
        elif field == "placement":
            column = card.get("column")
            value = BOARD_COLUMNS.index(column) if column in BOARD_COLUMNS else None
        elif field == "derived_lifecycle":
            value = LIFECYCLE_ORDER.get(card.get("derived_lifecycle"))
        elif field == "attention_state":
            rank = ATTENTION_RANK.get(card.get("attention_state"))
            value = -rank if rank is not None else None
        elif field == "recency_state":
            value = RECENCY_RANK.get(card.get("recency_state"))
        elif field == "confidence":
            value = card.get("confidence")
        elif field == "confidence_band":
            value = CONFIDENCE_BAND_RANK.get(card.get("confidence_band"))
        elif field == "primary_profile":
            value = card.get("primary_profile")
        elif field == "next_action_state":
            value = NEXT_ACTION_STATE_RANK.get(card.get("next_action_state"), 3)
        elif field == "last_stopping_point_on":
            value = (card.get("last_stopping_point") or {}).get("observed_at")
        if isinstance(value, str):
            return ("s", value.casefold(), value)
        if isinstance(value, float) and not math.isfinite(value):
            # §5.2: NULL, unavailable and non-finite (NaN/±inf) values sort last for both
            # directions. NaN otherwise compares as an artificial tie (both < and > false).
            return ("n", None, "")
        return ("n", value, "")

    def _sort_comparator(self, field: str, direction: str):
        def compare(a: Dict[str, Any], b: Dict[str, Any]) -> int:
            va = self._sort_value(a, field)
            vb = self._sort_value(b, field)
            if va[1] is None and vb[1] is None:
                result = 0
            elif va[1] is None:
                result = 1          # NULL sorts last for every field and direction
            elif vb[1] is None:
                result = -1
            else:
                result = -1 if va < vb else (1 if va > vb else 0)
                if result and direction == "desc":
                    result = -result
            if result:
                return result
            pa = str(a.get("project_id") or "")
            pb = str(b.get("project_id") or "")
            return -1 if pa < pb else (1 if pa > pb else 0)
        return compare

    def _lane_counts(self, cards: Sequence[Dict[str, Any]]) -> Dict[str, int]:
        counts = {c: 0 for c in BOARD_COLUMNS}
        counts["total"] = 0
        counts["unplaced_accepted"] = 0
        for card in cards:
            column = card.get("column") or "inbox"
            counts[column] = counts.get(column, 0) + 1
            counts["total"] += 1
            if card.get("placement_source") in ("derived_unplaced", "unplaced_unmapped") \
                    and card.get("review_status") == "accepted":
                counts["unplaced_accepted"] += 1
        return counts

    def _board_playground(self, q: Dict[str, Any]) -> Dict[str, Any]:
        self._now = time.time()   # request-scoped 'now', used by attention/recency bands
        now = self._now
        raw = self._projects()
        visible = [p for p in raw if not self._board_suppressed(p)]
        suppressed_total = len(raw) - len(visible)
        pids = [p["project_id"] for p in visible]
        declared_ts = self.registry.declared_rows_with_timestamps(pids)
        fact_by_ref = {"{}/{}".format(f["profile_name"], f["session_id"]): f
                       for f in self.registry.session_facts()}
        # CP-7: durable (incl. tombstoned) facts for preserved counts, plus source readability.
        preserved_by_ref = {"{}/{}".format(f["profile_name"], f["session_id"]): f
                            for f in self.registry.durable_session_facts()}
        source_status = self.registry.source_db_statuses()
        aud, proj_aud = self._audience_snapshot()
        links_by_pid = self.registry.links_for_many(pids)
        evidence_by_pid = self.registry.evidence_for_many(pids)

        cards = []
        # TH-L7/TH-L13: the TASK-HOME read model is computed from the ledger file only. This is
        # a GET path: it writes nothing and starts no pass.
        th_ctx = task_home_mod.projection_context(self.cfg, now=now)
        for p in visible:
            pid = p["project_id"]
            cards.append(self._playground_card(
                p, links_by_pid.get(pid, []), evidence_by_pid.get(pid, []),
                fact_by_ref, aud, proj_aud, declared_ts, preserved_by_ref, source_status,
                th_ctx))

        continuity_total = len(cards)
        # MC-A20: band totals describe the set filtered by everything EXCEPT `band`, so a
        # `band=` filter always reproduces its own per-band total exactly (OR-within,
        # AND-across like every other filter).
        q_without_band = dict(q, bands=[])
        banded = [c for c in cards if self._matches_query(c, q_without_band)]
        filtered = [c for c in cards if self._matches_query(c, q)]
        filtered_counts = self._lane_counts(filtered)
        filtered_counts["continuity_total"] = continuity_total
        filtered_counts["suppressed_total"] = suppressed_total
        filtered_counts["staleness_bands"] = self._band_map(banded)
        filtered_counts["task_home"] = self._task_home_counts(cards, th_ctx)
        total = len(filtered)

        filtered.sort(key=functools.cmp_to_key(
            self._sort_comparator(q["sort"], q["direction"])))
        start = (q["page"] - 1) * q["page_size"]
        page_items = filtered[start:start + q["page_size"]]
        for card in page_items:
            card.pop("_q_blob", None)

        playground = self._playground_references(page_items, q)
        data_as_of = self._snapshot_ts()
        snapshot = self.committed_snapshot()
        return {
            "items": page_items,
            "page": q["page"],
            "page_size": q["page_size"],
            "total": total,
            "counts": filtered_counts,
            "query": self._query_echo(q),
            "playground": playground,
            "data_as_of": data_as_of,
            "server_time": now,
            "snapshot_id": snapshot["run_id"],
            "committed_snapshot": snapshot,
            "scan_state": self.scan_state_object(),
            "task_home": self._task_home_envelope(th_ctx, cards),
        }

    @staticmethod
    def _task_home_counts(cards: Sequence[Dict[str, Any]],
                          ctx: Dict[str, Any]) -> Dict[str, Any]:
        """§6 ``counts.task_home`` — one entry per panel group, plus source_only and conflicts.

        Counted over the COMPLETE continuity set (like ``continuity_total``), never the returned
        page, so the panel and the server totals cannot disagree (INV-MC-10). TH-L10: a
        TASK-HOME-only key is counted in ``source_only`` and never in ``total``.
        """
        counts: Dict[str, Any] = {code: 0 for code in task_home_mod.PANEL_GROUPS}
        for card in cards:
            home = card.get("home") or "DASHBOARD_ONLY"
            counts[home] = counts.get(home, 0) + 1
        counts["source_only"] = int((ctx.get("counts") or {}).get("source_only") or 0)
        counts["conflicts"] = sum(
            len((card.get("task_home") or {}).get("conflicts") or []) for card in cards)
        return counts

    @staticmethod
    def _task_home_envelope(ctx: Dict[str, Any],
                            cards: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        """§6 ``task_home`` object. Every value is server-computed (TH-L13); the browser derives
        none of them. ``conflicts`` is the CURRENT read-time set (the ledger's own sync-time
        rows stay in the ledger and are published by ``task-home show``)."""
        conflicts: List[Dict[str, Any]] = []
        for card in cards:
            conflicts.extend((card.get("task_home") or {}).get("conflicts") or [])
        conflicts.sort(key=lambda c: (str(c.get("key") or ""), c.get("line_no") or 0))
        return {
            "enabled": bool(ctx.get("enabled")),
            "source_path": ctx.get("source_path"),
            "source_sha256": ctx.get("source_sha256"),
            "source_mtime": ctx.get("source_mtime"),
            "synced_at": ctx.get("synced_at"),
            "age_seconds": ctx.get("age_seconds"),
            "stale": bool(ctx.get("stale")),
            "stale_after_seconds": ctx.get("stale_after_seconds"),
            "run_id": ctx.get("run_id"),
            "warnings": list(ctx.get("warnings") or []),
            "conflicts": conflicts,
            "items": list(ctx.get("items") or []),
        }

    @staticmethod
    def _query_echo(q: Dict[str, Any]) -> Dict[str, Any]:
        """Canonical query echo (§5.1) — one shape for both the lanes and the dense grid.

        Every M4 filter is echoed even when empty, so the frozen M4 echo contract is preserved
        exactly. `bands` is the milestone's additive filter: it is echoed canonically WHEN IN
        USE, and an unused filter adds no key to the M4 echo shape (MC-L3 §1.2).
        """
        echo: Dict[str, Any] = {
            "view": q["view"], "sort": q["sort"], "direction": q["direction"],
            "lanes": list(q["lanes"]), "lifecycles": list(q["lifecycles"]),
            "attention": list(q["attention"]), "profiles": list(q["profiles"]),
            "q": q["q"], "page": q["page"], "page_size": q["page_size"],
        }
        if q["bands"]:
            echo["bands"] = list(q["bands"])
        if q["homes"]:
            echo["home"] = list(q["homes"])
        return echo

    @staticmethod
    def _matches_query(card: Dict[str, Any], q: Dict[str, Any]) -> bool:
        """Filters are ANDed across types and ORed inside one repeated parameter (§5.1)."""
        if q["lanes"] and card.get("column") not in q["lanes"]:
            return False
        if q["lifecycles"] and card.get("derived_lifecycle") not in q["lifecycles"]:
            return False
        if q["attention"] and card.get("attention_state") not in q["attention"]:
            return False
        if q["bands"] and card.get("staleness_band") not in q["bands"]:
            return False
        # §3 rule 1 / TH-A7: the panel group and the overridden `column` cannot disagree —
        # `lane=` already honours the override, and `home=` reads the same server value.
        if q["homes"] and card.get("home") not in q["homes"]:
            return False
        if q["profiles"]:
            linked = set(card.get("linked_profiles") or [])
            if not (linked & set(q["profiles"])):
                return False
        if q["q"]:
            if q["q"].casefold() not in (card.get("_q_blob") or ""):
                return False
        return True

    def _playground_references(self, page_items: Sequence[Dict[str, Any]],
                               q: Dict[str, Any]) -> Dict[str, Any]:
        """Today's deterministic pointers into the SAME returned lane cards (§7.1). Read-only.

        CP-8: every reference is derived from the RETURNED page set, so a reference can never
        point at a project outside ``items`` on page > 1 or a small page size.
        """
        cap = int(self.cfg.get("playground_group_cap", 5) or 5)
        ranked = sorted(page_items, key=lambda c: (
            ATTENTION_RANK.get(c.get("attention_state"), 7),
            RECENCY_RANK.get(c.get("recency_state"), 4),
            str(c.get("project_id") or "")))
        start_here = []
        for card in ranked:
            reason = _START_REASON.get(card.get("attention_state"))
            if reason is None:
                continue
            start_here.append({"project_id": card.get("project_id"), "reason": reason,
                               "rank": len(start_here) + 1})
            if len(start_here) >= cap:
                break
        eligible = [c for c in page_items
                    if c.get("last_user_worked_on") is None
                    or c.get("recency_state") in ("stale", "no_user_work", "unknown")]
        eligible.sort(key=lambda c: (
            0 if c.get("last_user_worked_on") is None else 1,
            c.get("last_user_worked_on") if c.get("last_user_worked_on") is not None else 0.0,
            str(c.get("project_id") or "")))
        rediscover = [{"project_id": c.get("project_id"),
                       "reason": ("no_recent_user_work"
                                  if c.get("last_user_worked_on") is None
                                  else "old_user_work"),
                       "last_user_worked_on": c.get("last_user_worked_on")}
                      for c in eligible[:cap]]
        return {"mode": q["view"], "start_here": start_here, "rediscover": rediscover,
                "rediscover_omitted": max(0, len(eligible) - len(rediscover))}

    # ------------------------------------------------------------------ inbox

    def inbox(self, **_) -> Dict[str, Any]:
        items = []
        aud, _proj_aud = self._audience_snapshot()
        # Build a lookup of session_fact titles for member_refs
        fact_titles: Dict[str, str] = {}
        for f in self.registry.session_facts():
            fact_titles["{}/{}".format(f["profile_name"], f["session_id"])] = f["title"] or ""
        for p in self._projects():
            if self._suppressed(p) or p["_accepted"]:
                continue
            if p["session_count"] < 2:
                continue
            links = self.registry.links_for(p["project_id"])
            evidence = self.registry.evidence_for(p["project_id"])
            evidence_tiers = sorted({int(e["tier"]) for e in evidence}) if evidence else [0]
            # Build signals from build_reason and strong_keys stored in link_reason
            signals = set()
            for l in links:
                reason = l["link_reason"] or ""
                for part in reason.split("; "):
                    part = part.strip()
                    if part:
                        signals.add(part)
            items.append({
                "cluster_id": p["project_id"],
                "project_id": p["project_id"],
                "proposed_name": p["name"],
                "member_refs": [{"profile": l["profile_name"], "session_id": l["session_id"],
                                 "title": fact_titles.get(
                                     "{}/{}".format(l["profile_name"], l["session_id"]))}
                                for l in links],
                "resume_links": _resume_links(links, aud),
                "signals": sorted(signals),
                "confidence": p["confidence"],
                "confidence_band": p["confidence_band"],
                "evidence_tier": p["evidence_tier"],
                "evidence_tiers": evidence_tiers,
                "evidence_refs": [{"tier": int(e["tier"]), "kind": e["kind"],
                                   "excerpt": e["excerpt"][:120] if e["excerpt"] else ""}
                                  for e in evidence[:8]],
                "lifecycle": p["lifecycle"],
                "stall_age_days": p["stall_age_days"],
                "data_as_of": self._snapshot_ts(),
            })
        items.sort(key=lambda c: -(c["confidence"] or 0))
        violations = audience_mod.assert_no_resume_on_delegated(items)
        if violations:
            raise AssertionError(
                "audience invariant D-US-6 step 4 violated: {}".format(violations))
        return {"items": items, "total": len(items), "data_as_of": self._snapshot_ts()}

    def recovery_inbox(self) -> Dict[str, Any]:
        """MC-S5: Recovery Inbox projection — candidate/accepted/suppressed counts + order.

        Candidates are the derived, not-yet-accepted projects (the continuity entry lane).
        Accepted projects are counted and never re-offered. Suppression is the audited
        dismiss/merge surface: visible, counted and reversible — nothing is deleted (INV-MC-2,
        MC-L11). The triage order is the SERVER's, deterministic and complete; the browser
        renders it and computes no part of it (INV-MC-10).
        """
        base = self.inbox()
        items = list(base["items"])
        # Deterministic server triage order: confidence desc, then project_id ASC. No score, no
        # countdown, no guilt copy (INV-MC-9) — an ordering aid only.
        items.sort(key=lambda c: (-(c["confidence"] or 0), str(c["project_id"] or "")))
        accepted_total = 0
        suppressed_total = 0
        suppressed_sessions = 0
        for p in self._projects():
            if self._suppressed(p):
                suppressed_total += 1
                suppressed_sessions += int(self.registry.conn.execute(
                    "SELECT COUNT(*) AS n FROM project_session WHERE project_id=?",
                    (p["project_id"],)).fetchone()["n"])
            elif p["_accepted"]:
                accepted_total += 1
        triage_order = [{"rank": idx + 1, "project_id": c["project_id"],
                         "confidence_band": c["confidence_band"]}
                        for idx, c in enumerate(items)]
        snapshot = self.committed_snapshot()
        return {
            "items": items,
            "total": len(items),
            "counts": {"candidates": len(items), "accepted": accepted_total,
                       "suppressed_total": suppressed_total,
                       "suppressed_sessions": suppressed_sessions},
            "triage_order": triage_order,
            "triage_order_total": len(triage_order),
            "data_as_of": snapshot["committed_at"],
            "server_time": time.time(),
            "committed_snapshot": snapshot,
            "scan_state": self.scan_state_object(),
        }

    # ------------------------------------------------------------------ overview (MVP A/B)

    def _resolve_overview_mode(self, mode: Optional[str],
                               include_candidates: Optional[bool]
                               ) -> Tuple[str, bool, str]:
        """Resolve the single semantic knob. Raises OverviewModeError (400) or
        OverviewConfigError (500). Never invents a third behavior."""
        if mode is not None:
            if mode not in _OVERVIEW_MODES:
                raise OverviewModeError(
                    "unknown mode: {}; expected one of: {}".format(
                        mode, ", ".join(_OVERVIEW_MODES)))
            want = (mode == "immediate")
            if include_candidates is not None and bool(include_candidates) != want:
                raise OverviewModeError(
                    "contradictory mode and include_candidates: "
                    "mode={!r} vs include_candidates={!r}".format(mode,
                                                                  include_candidates))
            return mode, want, "request"
        if include_candidates is not None:
            want = bool(include_candidates)
            return ("immediate" if want else "accepted_only"), want, "request"
        if "overview_mode" in (self.cfg.bundle or {}):
            configured = self.cfg.bundle.get("overview_mode")
            if configured not in _OVERVIEW_MODES:
                raise OverviewConfigError(
                    "invalid configured overview_mode: {!r}; expected one of: "
                    "{}".format(configured, ", ".join(_OVERVIEW_MODES)))
            return configured, (configured == "immediate"), "config"
        return DEFAULT_OVERVIEW_MODE, (DEFAULT_OVERVIEW_MODE == "immediate"), "default"

    def _quiet_band(self, quiet_days: Optional[float]) -> str:
        if quiet_days is None:
            return "unknown"
        if quiet_days <= 3:
            return "0-3d"
        if quiet_days <= 7:
            return "3-7d"
        if quiet_days <= 30:
            return "7-30d"
        return "30d+"

    def _overview_item(self, p: Dict[str, Any], now: float,
                       health_res: Sequence[Any],
                       fact_by_ref: Dict[str, Any],
                       aud: Optional[Dict[Any, Any]] = None) -> Dict[str, Any]:
        pid = p["project_id"]
        links = self.registry.links_for(pid)
        evidence = self.registry.evidence_for(pid)
        linked_count = len(links)
        accepted_count = sum(1 for l in links if l["accepted"])
        review_status = "accepted" if accepted_count > 0 else "candidate"
        if review_status == "accepted":
            row = self.registry.conn.execute(
                "SELECT MAX(ts) AS m FROM review_event "
                "WHERE target_id=? AND action='accept'", (pid,)).fetchone()
            accepted_at = row["m"] if row and row["m"] is not None else None
            if accepted_at is None:
                accepted_at = now
        else:
            accepted_at = None

        profiles = sorted({l["profile_name"] for l in links})
        primary_profile = None
        for l in links:
            if l["role_in_project"] == "primary":
                primary_profile = l["profile_name"]
                break

        # Context line: most common workspace_root, last-3-segments label.
        ws_roots = []
        for l in links:
            f = fact_by_ref.get("{}/{}".format(l["profile_name"], l["session_id"]))
            if f is not None and f["workspace_root"]:
                ws_roots.append(f["workspace_root"])
        if ws_roots:
            top = max(set(ws_roots), key=lambda w: (ws_roots.count(w), w))
            segs = [s for s in str(top).split("/") if s]
            label = "/".join(segs[-3:]) if len(segs) >= 3 else (segs[-1] if segs else top)
            context_line = "{} \u2014 {}".format(label, top)
            ws_label = segs[-1] if segs else None
        else:
            context_line = ", ".join(profiles) if profiles else "(no sessions)"
            ws_label = None

        # Session entries: primary first, then session_id ASC; bounded.
        # D-US-5: DELEGATED/AUTOMATED members keep their counts but are not resume affordances.
        aud = aud or {}
        ordered = [l for l in sorted(
            links, key=lambda l: (0 if l["role_in_project"] == "primary" else 1,
                                  l["session_id"]))
            if self._audience_of(aud, l["profile_name"], l["session_id"])
            not in ("DELEGATED", "AUTOMATED")]
        sessions = []
        for l in ordered[:SESSIONS_INLINE_LIMIT]:
            f = fact_by_ref.get("{}/{}".format(l["profile_name"], l["session_id"]))
            title = (f["title"] if f is not None and f["title"] else "") or ""
            cli = "hermes --resume {}".format(l["session_id"])
            cli_scoped = "hermes -p {} --resume {}".format(l["profile_name"],
                                                           l["session_id"])
            sessions.append({
                "profile": l["profile_name"],
                "session_id": l["session_id"],
                "title": title,
                "role": l["role_in_project"],
                "accepted": int(l["accepted"] or 0),
                "cli_resume": cli,
                "cli_resume_profile_scoped": cli_scoped,
                # Aliases so the shipped ResumeLink component renders unchanged.
                "copy_command": cli,
                "copy_command_profile_scoped": cli_scoped,
                "route": None,
                "link_reason": l["link_reason"] or "",
            })

        last_act = p["last_substantive_activity"]
        quiet_days = round((now - last_act) / DAY, 2) if last_act else None
        quiet_since = float(last_act) if last_act else None
        lifecycle_name = LIFECYCLE_NAMES.get(p["lifecycle"], "unknown")

        declared = p.get("_declared") or {}
        name_source = "declared" if declared.get("project_name") else "derived"
        state_source = ("declared" if (declared.get("parked") == "1"
                                       or declared.get("lifecycle_override"))
                        else "derived")

        signals = sorted({_MVP_SIGNAL_RE.search(part).group(0)
                          for l in links for part in (l["link_reason"] or "").split(";")
                          if _MVP_SIGNAL_RE.search(part)})

        tiers = sorted({int(e["tier"]) for e in evidence}) if evidence else [0]
        name = p["name"] or "(untitled cluster)"
        summary_text, summary_tier, summary_basis, blockers = _mvp_summarize(
            name, lifecycle_name, quiet_days, linked_count, len(profiles),
            ws_label, evidence, health_res)
        if p["lifecycle"] == "LS-3" and blockers:
            blocker_clean = _mvp_clean_excerpt(str(blockers[0][1]["excerpt"]))
            blocker_clean = _mvp_cap_words(blocker_clean, 80)
            if blocker_clean:
                summary_text = "{} \u00b7 blocked on: {}".format(summary_text,
                                                                 blocker_clean)
        summary_text = _mvp_cap_words(_mvp_collapse(summary_text).replace("\n", " "),
                                      SUMMARY_MAX_CHARS)
        if "\n" in summary_text:
            summary_text = " ".join(summary_text.split())

        needs_review = (review_status == "candidate"
                        and (p["confidence_band"] in ("low", "unknown")
                             or p["evidence_tier"] == 0))

        return {
            "project_id": pid,
            "name": name,
            "name_source": name_source,
            "context_line": context_line,
            "summary_text": summary_text,
            "summary_tier": summary_tier,
            "summary_basis": summary_basis,
            "summary_rule": SUMMARY_RULE_ID,
            "lifecycle": p["lifecycle"],
            "lifecycle_name": lifecycle_name,
            "state_source": state_source,
            "phase": p["phase"],
            "quiet_days": quiet_days,
            "quiet_since": quiet_since,
            "quiet_band": self._quiet_band(quiet_days),
            "reported_stall_age_days": p["stall_age_days"],
            "confidence": p["confidence"],
            "confidence_band": p["confidence_band"],
            "evidence_tier": p["evidence_tier"],
            "evidence_tiers": tiers,
            "signals": signals,
            "profiles": profiles,
            "primary_profile": primary_profile,
            "linked_session_count": linked_count,
            "accepted_session_count": accepted_count,
            "reported_session_count": p["session_count"],
            "review_status": review_status,
            "review_status_label": ("accepted by you" if review_status == "accepted"
                                    else "derived candidate \u2014 not reviewed"),
            "accepted_at": accepted_at,
            "needs_review": needs_review,
            "inclusion_basis": ("accepted link" if review_status == "accepted"
                                else "derived candidate \u2014 no accepted link"),
            "sessions": sessions,
            "sessions_omitted": linked_count - len(sessions),
            "drive_expected": bool(p["drive_expected"]),
            "next_action": p["_next_action"],
            "alert_state": self._alert_state(p),
            "data_as_of": self._snapshot_ts(),
        }

    def overview(self, mode: Optional[str] = None,
                 include_candidates: Optional[bool] = None,
                 include_suppressed: bool = False,
                 sort: str = "quiet", page: int = 1,
                 page_size: int = 50) -> Dict[str, Any]:
        """Additive read surface. Read-only: never writes registry rows and never
        appends a review_event. Acceptance stays in inbox/detail mutations."""
        if page is None or page < 1:
            raise OverviewModeError("invalid page: {!r}".format(page))
        if page_size is None or page_size < 1 or page_size > 200:
            raise OverviewModeError("invalid page_size: {!r}".format(page_size))
        eff_mode, want_candidates, mode_source = self._resolve_overview_mode(
            mode, include_candidates)
        now = time.time()

        scan_row = self.registry.conn.execute(
            "SELECT status, error, ended_at FROM scan_run "
            "ORDER BY started_at DESC LIMIT 1").fetchone()
        scan_phase = self._scan_state.get("phase", "idle")
        scan_error = self._scan_state.get("last_error")
        if scan_row is not None and scan_row["status"] == "error":
            scan_phase = "error"
            scan_error = scan_row["error"]
        # MC-L5: the structured observability object, with overview's own phase/error override.
        scan_state = self.scan_state_object()
        scan_state["phase"] = scan_phase
        scan_state["last_error"] = scan_error
        if scan_row is not None:
            scan_state["last_scan_at"] = scan_row["ended_at"] or scan_row["started_at"]
        if scan_phase == "error":
            return {"items": [], "page": page, "page_size": page_size, "total": 0,
                    "counts": {"total": 0, "candidate": 0, "accepted": 0,
                               "suppressed_projects": 0, "suppressed_sessions": 0},
                    "sort": sort or "quiet",
                    "mode": eff_mode, "mode_source": mode_source,
                    "include_candidates": want_candidates,
                    "include_suppressed": bool(include_suppressed),
                    "variant_label": _OVERVIEW_VARIANT_LABELS[eff_mode],
                    "scan_state": scan_state, "data_as_of": self._snapshot_ts(),
                    "limits": {"sessions_inline": SESSIONS_INLINE_LIMIT}}

        health_res = [rx for rx in
                      (_safe_compile(p) for p in
                       (self.cfg.get("health_ack_content_patterns") or []))
                      if rx is not None]
        fact_by_ref = {"{}/{}".format(f["profile_name"], f["session_id"]): f
                       for f in self.registry.session_facts()}

        projects = self._projects()
        suppressed = [p for p in projects if self._suppressed(p)]
        visible_all = [p for p in projects if not self._suppressed(p)]
        n_candidate = sum(1 for p in visible_all if not p["_accepted"])
        n_accepted = sum(1 for p in visible_all if p["_accepted"])
        suppressed_sessions = 0
        for p in suppressed:
            suppressed_sessions += self.registry.conn.execute(
                "SELECT COUNT(*) AS n FROM project_session WHERE project_id=?",
                (p["project_id"],)).fetchone()["n"]

        if want_candidates:
            shown = list(visible_all)
        else:
            shown = [p for p in visible_all if p["_accepted"]]
        if include_suppressed:
            shown = shown + suppressed

        if (sort or "quiet") == "name":
            shown.sort(key=lambda p: ((p["name"] or ""), p["project_id"]))
        else:
            def _qkey(p: Dict[str, Any]) -> Tuple[float, str]:
                la = p["last_substantive_activity"]
                q = (now - la) / DAY if la else None
                # Most-quiet first; unknown last; project_id tie-break (total order).
                return (-1.0 if q is None else -q, p["project_id"])
            shown.sort(key=_qkey)

        total = len(shown)
        start = (page - 1) * page_size
        aud, _proj_aud = self._audience_snapshot()
        items = [self._overview_item(p, now, health_res, fact_by_ref, aud)
                 for p in shown[start:start + page_size]]
        return {"items": items, "page": page, "page_size": page_size, "total": total,
                "counts": {"total": len(visible_all), "candidate": n_candidate,
                           "accepted": n_accepted,
                           "suppressed_projects": len(suppressed),
                           "suppressed_sessions": suppressed_sessions},
                "sort": sort or "quiet",
                "mode": eff_mode, "mode_source": mode_source,
                "include_candidates": want_candidates,
                "include_suppressed": bool(include_suppressed),
                "variant_label": _OVERVIEW_VARIANT_LABELS[eff_mode],
                "scan_state": scan_state, "data_as_of": self._snapshot_ts(),
                "limits": {"sessions_inline": SESSIONS_INLINE_LIMIT}}

    # ------------------------------------------------------------------ staleness (§8 bands)

    def staleness_view(self) -> Dict[str, Any]:
        """MC-S4: the banded staleness projection, anchored on USER-FACING activity.

        One lens over the SAME continuity set — no new lifecycle, no new lane, no mutation
        (MC-L1/MC-L3). `stall_age_days` is a separate "is it stuck?" signal and is never the
        anchor; parked/hiatus/terminal cards are an explicit QUIET group.
        """
        now = time.time()
        self._now = now
        declared_ts = self.registry.declared_rows_with_timestamps()
        fact_by_ref = {"{}/{}".format(f["profile_name"], f["session_id"]): f
                       for f in self.registry.session_facts()}
        aud, _proj_aud = self._audience_snapshot()
        buckets: List[Dict[str, Any]] = [
            {"band": band, "count": 0, "items": []} for band in STALENESS_BAND_FILTERS]
        index = {b["band"]: b for b in buckets}
        quiet: Dict[str, Any] = {"band": STALENESS_BAND_QUIET, "count": 0, "items": []}
        parked: List[Dict[str, Any]] = []
        for p in self._projects():
            if self._dismissed_or_merged(p):
                continue
            pid = p["project_id"]
            links = self.registry.links_for(pid)
            ag = self._link_aggregate(p, links, fact_by_ref, aud)
            column, _source = self._resolve_lane(p, declared_ts)
            derived = p.get("_derived_lifecycle") or p["lifecycle"]
            card = self._card(p)
            if derived in ("LS-6", "LS-7"):
                parked.append(card)
            band = self._staleness_band(ag["last_user_worked_on"],
                                       self._band_quiet(column, derived), now)
            target = quiet if band == STALENESS_BAND_QUIET else index[band]
            target["items"].append(card)
            target["count"] += 1
        snapshot = self.committed_snapshot()
        counts = {b["band"]: b["count"] for b in buckets}
        counts[STALENESS_BAND_QUIET] = quiet["count"]
        return {
            "buckets": buckets,
            "quiet": quiet,
            "parked": parked,
            "band_anchor": STALENESS_BAND_ANCHOR,
            "band_edges": self.band_edges(),
            "counts": counts,
            "data_as_of": snapshot["committed_at"],
            "server_time": now,
            "committed_snapshot": snapshot,
            "scan_state": self.scan_state_object(),
        }

    def staleness(self, **_) -> Dict[str, Any]:
        """M4 staleness envelope (top-level keys frozen by the KB-N1 unchanged-surface guard).

        Re-anchored on ``last_user_worked_on`` with config-driven edges (MC-S4); the added
        per-band counts, the ``quiet`` group and ``band_anchor`` are published on the existing
        ``/staleness`` route. ``parked`` keeps its exact M4 meaning (LS-6/LS-7 cards).
        """
        view = self.staleness_view()
        return {"buckets": view["buckets"], "parked": view["parked"],
                "data_as_of": view["data_as_of"]}

    def _dismissed_or_merged(self, p: Dict[str, Any]) -> bool:
        return bool(p["_dismissed"] or p["_merged_into"])

    # ------------------------------------------------------------------ noise controls

    def noise(self, **_) -> Dict[str, Any]:
        rows = list(self.registry.conn.execute(
            "SELECT noise_class, COUNT(*) AS n FROM session_fact "
            "WHERE is_noise=1 GROUP BY noise_class ORDER BY n DESC"))
        suppressions = []
        for r in rows:
            suppressions.append({"source": "rule", "noise_class": r["noise_class"],
                                 "count": r["n"], "reversible": True})
        for p in self._projects():
            if p["_dismissed"]:
                suppressions.append({"source": "human", "noise_class": "user-dismissed",
                                     "project_id": p["project_id"], "count": 1,
                                     "reversible": True})
        shown = sum(1 for p in self._projects() if not self._suppressed(p))
        hidden = sum(1 for p in self._projects() if self._suppressed(p))
        return {"suppressions": suppressions,
                "rules": sorted({r["noise_class"] for r in rows if r["noise_class"]}),
                "counts": {"suppressed_sessions": sum(r["n"] for r in rows),
                           "visible_projects": shown, "suppressed_projects": hidden},
                "data_as_of": self._snapshot_ts()}

    # ------------------------------------------------------------------ detail

    def project_detail(self, project_id: str, pane: Optional[str] = None) -> Dict[str, Any]:
        row = self.registry.get_project(project_id)
        if row is None:
            return {}
        p = dict(row)
        p["_derived_lifecycle"] = p["lifecycle"]
        p["_derived_lifecycle_name"] = LIFECYCLE_NAMES.get(p["lifecycle"], "unknown")
        links = self.registry.links_for(project_id)
        evidence = [dict(e) for e in self.registry.evidence_for(project_id)]
        p["_declared"] = self.registry.effective_declared(project_id)
        p["_next_action"] = None
        na = self.registry.next_actions_for(project_id)
        if na:
            n = na[0]
            p["_next_action"] = {"text": n["text"], "state": n["state"], "source": n["source"],
                                 "verified_at": n["verified_at"], "expires_at": n["expires_at"]}
        p["_accepted"] = self._is_accepted(project_id)
        p["_merged_into"] = p["_declared"].get("merged_into")
        p["_dismissed"] = p["_declared"].get("dismissed") == "1"
        p["declared_stale"] = self.registry.is_declared_stale(project_id)
        if p["_declared"].get("parked") == "1":
            p["lifecycle"] = "LS-6"
        elif p["_declared"].get("lifecycle_override"):
            p["lifecycle"] = p["_declared"]["lifecycle_override"]
        if p["_declared"].get("drive_expected") == "1":
            p["drive_expected"] = 1
        card = self._card(p)
        # board projection fields for detail
        ts_map = self.registry.declared_rows_with_timestamps([project_id]).get(project_id, {})
        placement_val = p["_declared"].get("placement")
        # reuse lane resolver logic: compute column/source
        # Build minimal declared_ts dict for resolver
        declared_ts = {project_id: ts_map}
        # Need _projects-style lane resolution requires full p with _derived
        # Temporarily craft resolver input already satisfied
        # Direct inline resolve to avoid extra fetch
        if placement_val is not None and placement_val in PLACEMENT_SET:
            col, src = placement_val, "human"
        elif not p["_accepted"]:
            col, src = "inbox", "candidate"
        else:
            m = _DERIVED_TO_PLACEMENT.get(p["_derived_lifecycle"])
            if m is not None:
                col, src = m, "derived_unplaced"
            else:
                col, src = "ongoing", "unplaced_unmapped"
        urgent = p["_declared"].get("urgent") == "1"
        urgent_since = ts_map.get("urgent", (None, None))[1] if urgent and "urgent" in ts_map else None
        card["column"] = col
        card["placement_source"] = src
        card["derived_lifecycle"] = p["_derived_lifecycle"]
        card["derived_lifecycle_name"] = p["_derived_lifecycle_name"]
        card["urgent"] = urgent
        card["urgent_since"] = urgent_since
        # D-KB-15 detail projection: review status only (no sessions on detail card)
        d_status = "accepted" if p["_accepted"] else "candidate"
        card["review_status"] = d_status
        card["review_status_label"] = ("accepted by you" if d_status == "accepted" else "derived candidate \u2014 not reviewed")
        card["needs_review"] = (d_status == "candidate" and (p["confidence_band"] in ("low", "unknown") or p["evidence_tier"] == 0))
        aud, proj_aud = self._audience_snapshot()
        fact_by_ref = {"{}/{}".format(f["profile_name"], f["session_id"]): f
                       for f in self.registry.session_facts()}
        card.update(self._audience_fields(p, proj_aud.get(project_id), aud, fact_by_ref))
        detail_sessions = []
        for l in sorted(links, key=lambda l: (0 if l["role_in_project"] == "primary" else 1,
                                              l["session_id"])):
            row = dict(l)
            a = aud.get((l["profile_name"], l["session_id"]), ("UNKNOWN", None))
            row["audience"] = a[0] if isinstance(a, (tuple, list)) else "UNKNOWN"
            row["audience_reason"] = a[1] if isinstance(a, (tuple, list)) else None
            detail_sessions.append(row)
        # D-SP-2 / §4.2: a verified anchor that lives OUTSIDE the cluster is still the project's
        # anchor, so the detail projection must be able to render and open its pane.
        anchor_ref = p.get("anchor_session")
        def _ref_of(row):
            return "{}/{}".format(row.get("profile_name") or row.get("profile"),
                                  row.get("session_id"))
        if anchor_ref and not any(_ref_of(s) == anchor_ref for s in detail_sessions):
            a_prof, _, a_sid = str(anchor_ref).partition("/")
            a = aud.get((a_prof, a_sid), ("UNKNOWN", None))
            detail_sessions.insert(0, {
                "profile_name": a_prof, "session_id": a_sid, "role_in_project": "anchor",
                "accepted": 0, "link_reason": "anchor",
                "audience": a[0] if isinstance(a, (tuple, list)) else "UNKNOWN",
                "audience_reason": a[1] if isinstance(a, (tuple, list)) else None,
            })
        resume_links = _resume_links(links, aud)
        violations = audience_mod.assert_no_resume_on_delegated(
            [{"project_id": project_id, "resume_links": resume_links,
              "sessions": detail_sessions}])
        if violations:
            raise AssertionError(
                "audience invariant D-US-6 step 4 violated: {}".format(violations))
        # §6.2: detail metadata is bounded with an honest omitted count; the lazy pane endpoint
        # stays a single-session read, never a bulk session or message transport.
        shown_sessions = detail_sessions[:SESSIONS_INLINE_LIMIT]
        shown_resume = resume_links[:SESSIONS_INLINE_LIMIT]
        shown_sources = [{"profile": s["profile_name"], "session_id": s["session_id"],
                          "audience": s["audience"], "reason": s["audience_reason"]}
                         for s in detail_sessions[:SESSIONS_INLINE_LIMIT]]
        result: Dict[str, Any] = {
            "project": card,
            "sessions": shown_sessions,
            "sessions_total": len(detail_sessions),
            "sessions_omitted": max(0, len(detail_sessions) - len(shown_sessions)),
            "source_sessions": shown_sources,
            "resume_links": shown_resume,
            "resume_links_total": len(resume_links),
            "resume_links_omitted": max(0, len(resume_links) - len(shown_resume)),
            "evidence": evidence,
            "decisions": [dict(e) for e in self.registry.review_events(project_id, limit=100)],
            "audit": [dict(e) for e in self.registry.review_events(project_id, limit=100)],
            "declared_fields": self.registry.declared_fields(project_id),
            "data_as_of": self._snapshot_ts(),
            "server_time": time.time(),
            "committed_snapshot": self.committed_snapshot(),
            "scan_state": self.scan_state_object(),
        }
        if pane:
            # Additive detail-only payload (no new route): requested lazily per session pane.
            result["session_pane"] = self._session_pane(project_id, pane)
        return result

    def _session_pane(self, project_id: str, pane_ref: str) -> Dict[str, Any]:
        """Lazy, bounded, plain-text message pane for ONE linked session (Shayba D-SP-*).

        Nothing is persisted: the pane re-reads the same bounded window the scanner uses
        (``probe_head`` + ``probe_tail`` messages, ``excerpt_chars`` per row) from the
        profile's source database with ``mode=ro`` + ``PRAGMA query_only=ON``. The capture is
        clipped at capture time, so every row is reported as an honest excerpt and no
        show-more affordance is offered (Shayba D-SP-8(b) / Q-3 fallback).

        Tool-only and system turns are excluded; the user-only block is emitted only for a
        ``USER_FACING`` session, so a delegated dispatch brief is never rendered as the
        user's own message (D-SP-13, Q-2).
        """
        profile, sep, sid = str(pane_ref or "").partition("/")
        if not sep or not profile or not sid:
            raise ValueError("invalid pane reference: {!r}; expected '<profile>/<session_id>'"
                             .format(pane_ref))
        linked = self.registry.conn.execute(
            "SELECT 1 FROM project_session WHERE project_id=? AND profile_name=? AND session_id=?",
            (project_id, profile, sid)).fetchone()
        if linked is None:
            # §4.2 (D-SP-2): the project's own verified anchor is pane-eligible even when it sits
            # OUTSIDE the cluster. That is one bounded session read, never an arbitrary fetch.
            anchor_row = self.registry.conn.execute(
                "SELECT anchor_session FROM project WHERE project_id=?", (project_id,)).fetchone()
            if anchor_row is None or anchor_row["anchor_session"] != pane_ref:
                raise ValueError("session is not linked to this project: {}".format(pane_ref))
        aud = self.registry.session_audience(profile, sid) or ("UNKNOWN", None, None)
        audience = aud[0] or "UNKNOWN"
        fact = self.registry.conn.execute(
            "SELECT title, message_count FROM session_fact WHERE profile_name=? AND session_id=?",
            (profile, sid)).fetchone()
        message_count = (fact["message_count"] if fact is not None else None)
        row = self.registry.conn.execute(
            "SELECT path, status, exists_now FROM source_db WHERE profile_name=?",
            (profile,)).fetchone()
        base: Dict[str, Any] = {
            "profile": profile, "session_id": sid,
            "title": ((fact["title"] if fact is not None and fact["title"] else "") or ""),
            "audience": audience, "audience_reason": aud[1],
            "message_count": message_count,
            "full_text_available": False,
            "copy": dict(_PANE_COPY),
            "capture": {"probe_head": int(self.cfg.get("probe_head", 1) or 1),
                        "probe_tail": int(self.cfg.get("probe_tail", 6) or 6),
                        "excerpt_chars": int(self.cfg.get("excerpt_chars", 240) or 240)},
        }
        if row is None or not row["path"]:
            base.update({"available": False, "reason": "no_source", "recent_messages": [],
                         "user_messages": []})
            base["copy"]["window"] = _PANE_COPY["unavailable"]
            return base
        try:
            conn = scanner.open_readonly(row["path"])
        except Exception as exc:  # locked/missing source is a status, never a write retry
            base.update({"available": False, "reason": "source_unreadable",
                         "detail": str(exc), "recent_messages": [], "user_messages": []})
            return base
        try:
            probes = scanner._probe_session(
                conn, profile, sid,
                int(self.cfg.get("probe_head", 1) or 1),
                int(self.cfg.get("probe_tail", 6) or 6),
                int(self.cfg.get("excerpt_chars", 240) or 240),
                int(self.cfg.get("audience_probe_chars", 8192) or 8192))
        finally:
            try:
                conn.close()
            except Exception:
                pass
        rows = [p for p in sorted(probes, key=lambda p: p.msg_id)
                if (p.role in ("user", "assistant")) and _pane_text(p.content)]
        recent_cap = int(self.cfg.get("pane_recent_cap", 7) or 7)
        user_cap = int(self.cfg.get("pane_user_cap", 3) or 3)
        # D-SP-9: the architectural RESPONSE cap stays separate from the UX DISPLAY cap.
        display_cap = int(self.cfg.get("pane_display_cap", 5) or 5)
        excerpt_chars = int(self.cfg.get("excerpt_chars", 240) or 240)
        recent = [_pane_row(p, excerpt_chars) for p in rows][-recent_cap:] if recent_cap else []
        if audience == "USER_FACING":
            user_rows = [p for p in rows if p.role == "user"]
            user_messages = ([_pane_row(p, excerpt_chars) for p in user_rows][-user_cap:]
                             if user_cap else [])
            user_state = "ok" if user_messages else "empty_window"
        elif audience in ("DELEGATED", "AUTOMATED"):
            user_messages, user_state = [], "not_user_facing"
        else:
            user_messages, user_state = [], "unknown_audience"
        base.update({
            "available": True,
            "recent_messages": recent,
            "user_messages": user_messages,
            "user_messages_state": user_state,
            # D-SP-10: the capture window is every row the probe returned, not what is shown.
            "captured_window": len(rows),
            "display_cap": display_cap,
            "rows_shown": min(len(recent), display_cap),
            "rows_omitted_by_display": max(0, len(rows) - display_cap),
        })
        line = _PANE_COPY["window"]
        if message_count is not None:
            # {N} is the captured window; the display cap may show fewer (D-SP-10).
            line = line.format(N=len(rows), M=message_count)
        else:
            line = _PANE_COPY["window_no_count"]
        base["copy"]["window"] = line
        return base

    def evidence_for(self, project_id: str) -> List[Dict[str, Any]]:
        return [dict(e) for e in self.registry.evidence_for(project_id)]

    # ------------------------------------------------------------------ review (§9)

    def review(self, action: str, target_id: str, payload: Optional[Dict[str, Any]] = None,
               actor: str = "local") -> Dict[str, Any]:
        payload = payload or {}
        registry = self.registry
        ttl_lifecycle = float(self.cfg.get("ttl_lifecycle_days", 30) or 30)
        ttl_drive = float(self.cfg.get("ttl_drive_expected_days", 90) or 90)
        ttl_next = float(self.cfg.get("ttl_next_action_days", 14) or 14)

        if action == "undo":
            return self._undo(payload.get("audit_id"), actor)

        # ---- placement validation (before any write) ----
        if action == "accept" and payload.get("placement") is not None:
            self._validate_placement(payload.get("placement"))
        if action == "set_placement":
            placement = payload.get("placement")
            if placement is None:
                raise ValueError("set_placement requires placement")
            self._validate_placement(placement)
        if action == "set_urgent":
            # payload value bool; absence means false? tolerate both
            pass
        if action == "bind_task_home":
            # TH-L9: a human binding rides the EXISTING audited review route (no new route), so
            # it inherits the append-only audit and `undo` with no new persistence.
            if not task_home_mod.valid_key(payload.get("key")):
                raise ValueError(
                    "bind_task_home requires key: a non-empty slug without whitespace, "
                    "'(' , ')' or ':'")
            if not task_home_mod.valid_proc(payload.get("proc")):
                raise ValueError(
                    "bind_task_home proc must be a literal proc_<hex> token or null: "
                    "{!r}".format(payload.get("proc")))
        if action in ("bind_task_home", "unbind_task_home"):
            if registry.get_project(target_id) is None:
                raise ValueError("unknown project_id: {}".format(target_id))

        involved = [target_id]
        if action == "merge":
            involved = [target_id, payload.get("target_project_id", "")]
        # snapshot before (outside transaction, read-only)
        before = registry.snapshot([x for x in involved if x])

        # Atomic mutation: all writes plus audit in one transaction
        with registry.transaction():
            if action == "accept":
                registry.conn.execute(
                    "UPDATE project_session SET accepted=1, declared_rev=declared_rev+1 "
                    "WHERE project_id=?", (target_id,))
                registry.set_declared(target_id, "accepted", "1", actor=actor)
                if payload.get("name"):
                    registry.conn.execute("UPDATE project SET name=? WHERE project_id=?",
                                          (payload["name"], target_id))
                    registry.set_declared(target_id, "project_name", payload["name"], actor=actor)
                if payload.get("lifecycle"):
                    registry.set_declared(target_id, "lifecycle_override", payload["lifecycle"],
                                          actor=actor, ttl_days=ttl_lifecycle)
                if payload.get("owner"):
                    registry.set_declared(target_id, "owner", payload["owner"], actor=actor)
                if payload.get("placement") is not None:
                    registry.set_declared(target_id, "placement", payload["placement"], actor=actor)
            elif action == "set_placement":
                registry.set_declared(target_id, "placement", payload["placement"], actor=actor)
            elif action == "set_urgent":
                val = payload.get("value")
                if val:
                    registry.set_declared(target_id, "urgent", "1", actor=actor)
                else:
                    registry.clear_declared(target_id, "urgent")
            elif action in ("reject", "dismiss"):
                registry.set_declared(target_id, "dismissed", "1", actor=actor)
                registry.set_declared(target_id, "dismissed_class",
                                      payload.get("noise_class", "user-dismissed"), actor=actor)
            elif action == "set_lifecycle":
                registry.set_declared(target_id, "lifecycle_override", payload.get("lifecycle"),
                                      actor=actor, ttl_days=ttl_lifecycle)
            elif action == "set_next_action":
                if payload.get("text"):
                    registry.set_next_action(target_id, payload["text"], source="declared",
                                             ttl_days=ttl_next, actor=actor)
                    registry.set_declared(target_id, "next_action_verified", payload["text"],
                                          actor=actor, ttl_days=ttl_next)
                else:
                    registry.conn.execute("DELETE FROM next_action WHERE project_id=?", (target_id,))
                    registry.clear_declared(target_id, "next_action_verified")
            elif action == "set_owner":
                registry.set_declared(target_id, "owner", payload.get("owner"), actor=actor)
                registry.update_project_declared(target_id, owner_profile=payload.get("owner"))
            elif action == "park":
                registry.set_declared(target_id, "parked", "1", actor=actor)
            elif action == "unpark":
                registry.clear_declared(target_id, "parked")
            elif action == "drive_expected":
                val = "1" if payload.get("value", True) else "0"
                registry.set_declared(target_id, "drive_expected", val, actor=actor,
                                      ttl_days=ttl_drive)
                registry.update_project_declared(target_id, drive_expected=int(val))
            elif action == "merge":
                src, dst = target_id, payload.get("target_project_id")
                if not dst:
                    raise ValueError("merge requires target_project_id")
                registry.conn.execute(
                    "UPDATE project_session SET project_id=?, declared_rev=declared_rev+1 "
                    "WHERE project_id=?", (dst, src))
                registry.set_declared(src, "merged_into", dst, actor=actor)
                involved = [src, dst]
            elif action == "split":
                rows = payload.get("session_refs") or []
                new_pid = str(uuid.uuid5(uuid.NAMESPACE_URL,
                                         "continuum:split:{}:{}".format(target_id, time.time())))
                registry.conn.execute(
                    """INSERT INTO project (project_id, name, kind, phase, lifecycle, confidence,
                           confidence_band, evidence_tier, drive_expected, session_count,
                           derived_updated_at, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,0,0,?,?,?)""",
                    (new_pid, payload.get("name") or "split project", "project", "unknown",
                     "LS-2", 0.5, "medium", 1, time.time(), time.time(), time.time()))
                for ref in rows:
                    profile, _, sid = str(ref).partition("/")
                    registry.conn.execute(
                        "UPDATE project_session SET project_id=?, declared_rev=declared_rev+1 "
                        "WHERE project_id=? AND profile_name=? AND session_id=?",
                        (new_pid, target_id, profile, sid))
                involved = [target_id, new_pid]
            elif action == "set_audience":
                ref = payload.get("session_ref")
                value = payload.get("audience")
                if not ref or value not in audience_mod.AUDIENCES:
                    raise ValueError(
                        "set_audience requires session_ref and one of {}".format(
                            ", ".join(audience_mod.AUDIENCES)))
                profile, _, sid = str(ref).partition("/")
                linked = registry.conn.execute(
                    "SELECT 1 FROM project_session WHERE project_id=? AND profile_name=? "
                    "AND session_id=?", (target_id, profile, sid)).fetchone()
                if linked is None:
                    raise ValueError(
                        "session_ref is not linked to project: {}".format(ref))
                registry.set_declared(target_id,
                                      "audience_override:{}".format(ref),
                                      value, actor=actor)
            elif action == "bind_task_home":
                registry.set_declared(target_id, "task_home_key", payload["key"], actor=actor)
                registry.set_declared(target_id, "task_home_proc", payload.get("proc") or "",
                                      actor=actor)
                registry.set_declared(target_id, "task_home_binding", "human", actor=actor)
            elif action == "unbind_task_home":
                # The audited overlay is REMOVED, not rewritten: the next pass decides again.
                for field_name in task_home_mod.ITEM_FIELDS:
                    registry.clear_declared(target_id, field_name)
                registry.set_declared(target_id, "task_home_binding", "unbound", actor=actor)
            else:
                raise ValueError("unknown review action: {}".format(action))

            after = registry.snapshot([x for x in involved if x])
            rev = self._next_rev()
            ev = ReviewEvent(event_id=uuid.uuid4().hex, ts=time.time(), actor=actor, action=action,
                             target_type="project", target_id=target_id,
                             before_json=json.dumps(before), after_json=json.dumps(after), rev=rev)
            registry.append_review_event(ev)
        return {"ok": True, "audit_id": ev.event_id, "action": action, "target_id": target_id,
                "rev": rev}

    def _next_rev(self) -> int:
        row = self.registry.conn.execute("SELECT COALESCE(MAX(rev),0) AS r FROM review_event").fetchone()
        return int(row["r"]) + 1

    # ------------------------------------------------------------------ action-log archive (AL-L5)

    def action_log_review(self, action: str, action_id: str,
                          actor: str = "local") -> Dict[str, Any]:
        """Archive/unarchive/undo for an action-log row — the review() pattern (AL-L5).

        Snapshot -> mutate -> audit event -> atomic ledger write, under the ledger lock. The
        action-log store is its own JSON ledger, so this deliberately does NOT ride
        ``POST /projects/{id}/review`` (which requires a Continuum project_id, service.py:2846).
        """
        if action in ("archive", "archive_action_log"):
            return action_log_mod.archive(self.cfg, action_id, actor)
        if action in ("unarchive", "unarchive_action_log"):
            return action_log_mod.unarchive(self.cfg, action_id, actor)
        if action == "undo":
            return self._action_log_undo(action_id, actor)
        raise ValueError("unknown action-log action: {}".format(action))

    def _action_log_undo(self, audit_id: Optional[str], actor: str = "local") -> Dict[str, Any]:
        """Undo an action-log archive/unarchive by restoring its ``before_json`` (AL-A7)."""
        return action_log_mod.undo(self.cfg, audit_id or "", actor)

    def _undo(self, audit_id: Optional[str], actor: str) -> Dict[str, Any]:
        if not audit_id:
            raise ValueError("undo requires audit_id")
        ev = self.registry.get_review_event(audit_id)
        if ev is None:
            raise ValueError("unknown audit_id: {}".format(audit_id))
        before = json.loads(ev["before_json"] or "{}")
        current = self.registry.snapshot(list((before.get("projects") or {}).keys()))
        with self.registry.transaction():
            self.registry.restore(before)
            rev = self._next_rev()
            new_ev = ReviewEvent(event_id=uuid.uuid4().hex, ts=time.time(), actor=actor, action="undo",
                                 target_type="project", target_id=ev["target_id"],
                                 before_json=json.dumps(current), after_json=json.dumps(before),
                                 rev=rev)
            self.registry.append_review_event(new_ev)
        return {"ok": True, "audit_id": new_ev.event_id, "undoes": audit_id,
                "target_id": ev["target_id"], "rev": rev}
