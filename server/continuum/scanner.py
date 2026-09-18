"""M1 — continuum.scanner.

Read-only per-profile SQLite adapter.

HARD INVARIANTS (architecture contract §1):
  INV-1  every source DB is opened with SQLite URI ``file:<abs-path>?mode=ro`` and never
         written. If a source is locked the scanner records the miss and moves on — it never
         retries with a writable handle.
  INV-5  ``profile_name`` and ``session_id`` are stored verbatim.

This module MUST NOT classify, cluster, or know about the UI. It emits raw facts only.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote

from .config import Config

# Columns read from `sessions`. Missing columns in older schemas are tolerated.
_SESSION_COLUMNS: Tuple[str, ...] = (
    "id", "source", "title", "title_source", "display_name", "cwd",
    "git_repo_root", "git_branch", "started_at", "last_activity_at",
    "message_count", "tool_call_count", "parent_session_id", "model",
    "tool_names", "archived", "pinned", "hidden", "chat_type", "last_activity_description",
)


@dataclass
class ProfileRef:
    profile_name: str
    db_path: str
    exists: bool = True


@dataclass
class SessionFact:
    """SessionFact shape from architecture contract §3.1 (INV-5 verbatim ids).
    Adds chat_type and last_activity_description as Tier-0 metadata already on the source row.
    """

    profile_name: str
    session_id: str
    source: str
    title: Optional[str]
    title_source: Optional[str]
    display_name: Optional[str]
    cwd: Optional[str]
    workspace_root: Optional[str]
    git_repo_root: Optional[str]
    git_branch: Optional[str]
    started_at: Optional[float]
    last_activity_at: Optional[float]
    message_count: int
    tool_call_count: int
    parent_session_id: Optional[str]
    model: Optional[str]
    archived: int
    pinned: int
    hidden: int
    tool_names: Optional[str]
    content_hash: str
    # extra Tier-0 metadata (not in the §3.1 field list but already on the source row);
    # additive only — nothing above is renamed or removed.
    chat_type: Optional[str] = None
    last_activity_description: Optional[str] = None


@dataclass
class MessageProbe:
    profile_name: str
    session_id: str
    msg_id: int
    role: str
    content: str
    tool_calls: bool
    timestamp: Optional[float]
    position: str  # head|tail
    # Untruncated (bounded by ``audience_probe_chars``) copy of the message content. The
    # excerpt semantics of ``content`` are unchanged; this field exists so audience rule C1
    # can digest-match a full dispatch brief (fact 16: ``excerpt_chars`` clips it to 240).
    content_head: str = ""


@dataclass
class DbStatus:
    profile_name: str
    path: str
    status: str              # ok|locked|missing|error
    session_count: int = 0
    message_count: int = 0
    error: Optional[str] = None
    mtime: Optional[float] = None
    size: Optional[int] = None
    schema_version: Optional[int] = None
    # --- M4 incremental scan data (additive) --------------------------------
    skipped: bool = False            # unchanged profile, cheaply checked only
    changed: bool = True             # profile content is being re-read this run
    reconciliation: str = "full"     # full|skipped|full-ambiguous|full-count-shrink
                                     # |full-sweep|full-first-run|full-schema-change
                                     # |coarse
    partial: bool = False            # coarse-window bounded read: intentionally
                                     # incomplete, so the service must never tombstone
                                     # facts outside the read window


@dataclass
class ScanBatch:
    run_id: str
    started_at: float
    ended_at: float
    mode: str
    facts: List[SessionFact] = field(default_factory=list)
    probes: List[MessageProbe] = field(default_factory=list)
    status: List[DbStatus] = field(default_factory=list)
    counts: Dict[str, int] = field(default_factory=dict)
    # ``ref -> untruncated first non-empty user message`` (bounded by audience_probe_chars).
    # Additive: it exists so the audience pass does not depend on the 240-char excerpt window.
    first_user: Dict[str, str] = field(default_factory=dict)
    # ``profile_name -> [session id, ...]`` named by that profile's read-only
    # ``async_delegations`` linkage columns (positive U-DELEGATOR evidence only).
    delegators: Dict[str, List[str]] = field(default_factory=dict)
    # ``[{origin_session, text}, ...]`` read-only from ``async_delegations`` (anchor evidence;
    # the delegation table never names the delegated child — fact 5).
    delegation_rows: List[Dict[str, str]] = field(default_factory=list)
    # M4: profiles cheaply skipped because their committed snapshot was unchanged.
    skipped_profiles: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------- discovery

def discover_profiles(hermes_home: str, cfg: Optional[Config] = None) -> List[ProfileRef]:
    """Enumerate ``<hermes_home>/profiles/<name>/state.db`` verbatim, sorted by name."""
    cfg = cfg or Config(hermes_home=hermes_home)
    root = Path(cfg.profiles_root if cfg.hermes_home else os.path.join(
        os.path.expanduser(hermes_home), "profiles"))
    if not root.is_absolute():
        root = Path(os.path.expanduser(hermes_home)) / "profiles"
    fname = cfg.get("db_filename", "state.db")
    refs: List[ProfileRef] = []
    if not root.is_dir():
        return refs
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        db = entry / fname
        if entry.is_dir() and db.exists():
            refs.append(ProfileRef(profile_name=entry.name, db_path=str(db), exists=True))
    return refs


# --------------------------------------------------------------------------- read-only open

def build_ro_uri(db_path: str) -> str:
    """SQLite URI forcing read-only mode. ``mode=ro`` is asserted by open_readonly."""
    return "file:{}?mode=ro".format(quote(os.path.abspath(os.path.expanduser(db_path)), safe="/"))


def open_readonly(db_path: str) -> sqlite3.Connection:
    """Open a source DB read-only. Raises sqlite3.OperationalError if it cannot be opened ro."""
    uri = build_ro_uri(db_path)
    if "mode=ro" not in uri:
        raise ValueError("refusing to open a source DB without mode=ro")
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        # Belt-and-braces: even a bug that tries to write will be refused by the engine.
        conn.execute("PRAGMA query_only=ON")
    except sqlite3.Error:
        conn.close()
        raise
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    try:
        return [r[1] for r in conn.execute("PRAGMA table_info({})".format(table))]
    except sqlite3.Error:
        return []


def _schema_version(conn: sqlite3.Connection) -> Optional[int]:
    try:
        row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        return int(row[0]) if row is not None else None
    except sqlite3.Error:
        return None


# --------------------------------------------------------------------------- workspace_root

def derive_workspace_root(cwd: Optional[str], cfg: Config) -> Optional[str]:
    """Deterministic cwd -> workspace_root (OD4/D3). Generic roots never produce a key (AV-4).

    Order: generic-path guard -> config workspace_patterns -> optional bounded .git walk-up.
    """
    if not cwd:
        return None
    norm = os.path.expanduser(cwd).rstrip("/") or "/"
    if cfg.is_generic_path(norm):
        return None

    import re
    for pat in cfg.get("workspace_patterns") or []:
        try:
            m = re.search(pat, norm)
        except re.error:
            continue
        if m:
            captured = (m.groupdict().get("repo") if "repo" in m.groupdict()
                        else (m.group(1) if m.groups() else m.group(0)))
            if captured:
                captured = captured.rstrip("/")
                if captured.startswith("/") and not cfg.is_generic_path(captured):
                    return captured

    if cfg.get("workspace_git_walkup"):
        cur = norm
        depth = int(cfg.get("workspace_walkup_max_depth", 8) or 8)
        for _ in range(depth):
            try:
                if os.path.isdir(os.path.join(cur, ".git")):
                    return cur if not cfg.is_generic_path(cur) else None
            except OSError:
                return None
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
    return None


def _content_hash(parts: Sequence[Any]) -> str:
    h = hashlib.sha256()
    h.update(json.dumps(list(parts), default=str, sort_keys=True).encode("utf-8"))
    return h.hexdigest()


# --------------------------------------------------------------------------- scan

def scan(profiles: Sequence[ProfileRef],
         watermark: Optional[Dict[str, Any]] = None,
         cfg: Optional[Config] = None,
         mode: str = "full",
         probe: bool = True,
         run_id: Optional[str] = None) -> ScanBatch:
    """Read every profile DB read-only and emit a ScanBatch. Never writes a source handle.

    ``watermark`` is the previous committed snapshot per profile
    (``{profile: {mtime, size, session_count, status, last_scan_at}}``, normally read from
    ``registry.source_db``). In ``mode='incremental'`` an unchanged profile is skipped after a
    cheap stat/metadata check (no source query at all). A changed profile, an ambiguous
    change (row count shrank, or a stat that moved backwards), an expired full-sweep interval,
    or a first run selects a full profile reconciliation, so clustering and identity never
    depend on a partial read.
    """
    cfg = cfg or Config()
    watermark = watermark or {}
    run_id = run_id or uuid.uuid4().hex
    started = time.time()
    batch = ScanBatch(run_id=run_id, started_at=started, ended_at=started, mode=mode)
    total_sessions = 0
    total_messages = 0
    total_probed = 0
    sweep_seconds = float(cfg.get("scan_full_sweep_seconds", 0) or 0)
    now_wall = time.time()

    for ref in profiles:
        st = DbStatus(profile_name=ref.profile_name, path=ref.db_path, status="ok")
        if not os.path.exists(ref.db_path):
            st.status = "missing"
            st.changed = False
            st.reconciliation = "missing"
            batch.status.append(st)
            continue
        try:
            st.mtime = os.path.getmtime(ref.db_path)
            st.size = os.path.getsize(ref.db_path)
        except OSError:
            pass

        prior = watermark.get(ref.profile_name) or None
        if mode == "incremental" and prior:
            prior_ok = (prior.get("status") or "ok") == "ok"
            last_scan = prior.get("last_scan_at")
            sweep_due = (sweep_seconds > 0 and last_scan is not None
                         and (now_wall - float(last_scan)) > sweep_seconds)
            same_stat = (prior.get("mtime") == st.mtime and prior.get("size") == st.size
                         and st.mtime is not None)
            # An incomplete watermark (no recorded row count) is AMBIGUOUS: never skip.
            ambiguous = prior.get("session_count") is None
            if prior_ok and same_stat and not sweep_due and not ambiguous:
                st.skipped = True
                st.changed = False
                st.reconciliation = "skipped"
                st.session_count = int(prior.get("session_count") or 0)
                st.message_count = int(prior.get("message_count") or 0)
                st.schema_version = prior.get("schema_version")
                batch.skipped_profiles.append(ref.profile_name)
                batch.status.append(st)
                total_sessions += st.session_count
                total_messages += st.message_count
                continue
            if ambiguous and same_stat and not sweep_due:
                st.reconciliation = "full-ambiguous"
        conn = None
        try:
            conn = open_readonly(ref.db_path)
        except sqlite3.OperationalError as exc:
            st.status = "locked"
            st.error = str(exc)
            batch.status.append(st)
            continue
        except sqlite3.Error as exc:
            st.status = "error"
            st.error = str(exc)
            batch.status.append(st)
            continue
        try:
            st.schema_version = _schema_version(conn)
            cols = _table_columns(conn, "sessions")
            if not cols:
                st.status = "error"
                st.error = "no sessions table"
                batch.status.append(st)
                continue
            selected = [c for c in _SESSION_COLUMNS if c in cols]
            has_messages = bool(_table_columns(conn, "messages"))
            sel = ", ".join('"' + c + '"' for c in selected)

            # Cheap id + activity list to decide coarse-window vs full before any content read.
            id_rows = list(conn.execute(
                "SELECT id, last_activity_at FROM sessions"))
            st.session_count = len(id_rows)
            total_sessions += len(id_rows)

            if prior is None:
                st.reconciliation = "full-first-run"
            elif prior.get("session_count") is not None and len(id_rows) < int(prior["session_count"]):
                st.reconciliation = "full-count-shrink"
            elif st.reconciliation != "full-ambiguous" and (sweep_seconds > 0
                    and prior.get("last_scan_at") is not None
                    and (now_wall - float(prior["last_scan_at"])) > sweep_seconds):
                st.reconciliation = "full-sweep"
            elif st.reconciliation != "full-ambiguous" and prior.get("schema_version") is not None \
                    and st.schema_version is not None \
                    and int(prior["schema_version"]) != int(st.schema_version):
                st.reconciliation = "full-schema-change"
            elif st.reconciliation != "full-ambiguous":
                st.reconciliation = "full"

            # Coarse-window incremental read: consume both committed watermarks. A changed
            # profile reads sessions at/after the coarse watermark (with the configured safety
            # lag) plus newly observed IDs. Fall back to a full reconciliation when a watermark
            # is missing/ambiguous, counts shrank, the schema changed, or the full-sweep
            # interval expired — never read a changed profile partially in those cases.
            coarse_ok = (
                mode == "incremental"
                and prior is not None
                and prior.get("coarse_watermark") is not None
                and prior.get("session_count") is not None        # not ambiguous
                and (prior.get("status") or "ok") == "ok"
                and not (sweep_seconds > 0 and prior.get("last_scan_at") is not None
                         and (now_wall - float(prior["last_scan_at"])) > sweep_seconds)
                and (prior.get("schema_version") is None or st.schema_version is None
                     or int(prior["schema_version"]) == int(st.schema_version))
                and not (len(id_rows) < int(prior["session_count"]))   # no shrink
                and st.reconciliation == "full"                  # nothing already forced full
            )
            if coarse_ok:
                st.reconciliation = "coarse"
                st.partial = True
                safety_lag = float(cfg.get("safety_lag_seconds", 5) or 5)
                coarse_lo = float(prior["coarse_watermark"]) - safety_lag
                known = set(prior.get("known_ids") or [])
                new_ids = {r["id"] for r in id_rows} - known
                window_ids = {r["id"] for r in id_rows
                              if r["last_activity_at"] is not None
                              and float(r["last_activity_at"]) >= coarse_lo}
                target = new_ids | window_ids
                if target:
                    ph = ",".join("?" * len(target))
                    sql = "SELECT {} FROM sessions WHERE id IN ({})".format(sel, ph)
                    rows = list(conn.execute(sql, tuple(sorted(target))))
                else:
                    rows = []
            else:
                sql = "SELECT {} FROM sessions".format(sel)
                rows = list(conn.execute(sql))

            if has_messages:
                try:
                    st.message_count = int(conn.execute(
                        "SELECT COUNT(*) FROM messages").fetchone()[0])
                except sqlite3.Error:
                    st.message_count = 0
            total_messages += st.message_count

            for row in rows:
                rec = dict(zip(selected, row))
                cwd = rec.get("cwd")
                fact = SessionFact(
                    profile_name=ref.profile_name,               # verbatim
                    session_id=str(rec.get("id")),               # verbatim
                    source=rec.get("source") or "unknown",
                    title=rec.get("title"),
                    title_source=rec.get("title_source"),
                    display_name=rec.get("display_name"),
                    cwd=cwd,
                    workspace_root=derive_workspace_root(cwd, cfg),
                    git_repo_root=rec.get("git_repo_root"),
                    git_branch=rec.get("git_branch"),
                    started_at=_f(rec.get("started_at")),
                    last_activity_at=_f(rec.get("last_activity_at")),
                    message_count=int(rec.get("message_count") or 0),
                    tool_call_count=int(rec.get("tool_call_count") or 0),
                    parent_session_id=rec.get("parent_session_id"),
                    model=rec.get("model"),
                    archived=int(rec.get("archived") or 0),
                    pinned=int(rec.get("pinned") or 0),
                    hidden=int(rec.get("hidden") or 0),
                    tool_names=rec.get("tool_names"),
                    content_hash="",
                    chat_type=rec.get("chat_type"),
                    last_activity_description=rec.get("last_activity_description"),
                )
                fact.content_hash = _content_hash([
                    fact.profile_name, fact.session_id, fact.source, fact.title,
                    fact.title_source, fact.cwd, fact.workspace_root, fact.git_repo_root,
                    fact.started_at, fact.last_activity_at, fact.message_count,
                    fact.tool_call_count, fact.parent_session_id, fact.archived,
                    fact.pinned, fact.hidden,
                ])
                batch.facts.append(fact)
                ref_key = "{}/{}".format(ref.profile_name, fact.session_id)
                if has_messages:
                    fu = _first_user_message(conn, fact.session_id,
                                             int(cfg.get("audience_probe_chars", 8192) or 8192))
                    if fu is not None:
                        batch.first_user[ref_key] = fu
                if probe and has_messages:
                    got = _probe_session(conn, ref.profile_name, fact.session_id,
                                         int(cfg.get("probe_head", 1)),
                                         int(cfg.get("probe_tail", 6)),
                                         int(cfg.get("excerpt_chars", 240)),
                                         int(cfg.get("audience_probe_chars", 8192) or 8192))
                    batch.probes.extend(got)
                    total_probed += len(got)
            # async_delegations is read read-only and may be absent on older schemas.
            try:
                batch.delegators[ref.profile_name] = _read_delegators(conn)
                batch.delegation_rows.extend(_read_delegations(conn))
            except sqlite3.Error:
                batch.delegators[ref.profile_name] = []
        except sqlite3.Error as exc:
            st.status = "error"
            st.error = str(exc)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
        batch.status.append(st)

    batch.ended_at = time.time()
    batch.counts = {
        "profiles": len(profiles),
        "profiles_ok": sum(1 for s in batch.status if s.status == "ok"),
        "profiles_skipped": len(batch.skipped_profiles),
        "sessions": total_sessions,
        "messages": total_messages,
        "probes": total_probed,
    }
    return batch


def _f(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _first_user_message(conn: sqlite3.Connection, session_id: str,
                        cap: int) -> Optional[str]:
    """Bounded untruncated first non-empty user message for a session (audience rule C1).

    Read-only; never the whole transcript. Returns ``None`` when the session has no
    non-empty user turn (the caller then classifies on metadata alone).
    """
    cols = _table_columns(conn, "messages")
    if "content" not in cols or "role" not in cols:
        return None
    try:
        row = conn.execute(
            'SELECT content FROM messages WHERE session_id=? AND role=\'user\' '
            "AND content IS NOT NULL AND TRIM(content) != '' ORDER BY id ASC LIMIT 1",
            (session_id,)).fetchone()
    except sqlite3.Error:
        return None
    if row is None or row[0] is None:
        return None
    text = row[0]
    if not isinstance(text, str):
        text = str(text)
    return text[:cap]


def _read_delegators(conn: sqlite3.Connection) -> List[str]:
    """Read the profile's optional ``async_delegations`` table read-only.

    Returns the union of every non-empty value in the linkage columns that exist on this
    schema (``origin_session``, ``origin_session_id``, ``parent_session_id``, ``session_key``).
    A profile without the table yields ``[]`` — an absence, never an error (D-US-4).
    """
    cols = _table_columns(conn, "async_delegations")
    if not cols:
        return []
    link_cols = [c for c in ("origin_session", "origin_session_id",
                             "parent_session_id", "session_key") if c in cols]
    if not link_cols:
        return []
    sql = "SELECT {} FROM async_delegations".format(
        ", ".join('"' + c + '"' for c in link_cols))
    seen = set()
    for row in conn.execute(sql):
        for value in row:
            if value:
                seen.add(str(value))
    return sorted(seen)


def _read_delegations(conn: sqlite3.Connection) -> List[Dict[str, str]]:
    """Read ``(origin_session, text)`` pairs from the profile's optional ``async_delegations``.

    Read-only, bounded per row. Used ONLY as positive anchor evidence (D-US-6 step 6): the
    delegating session dispatched work, so a human was driving it (fact 13).
    """
    cols = _table_columns(conn, "async_delegations")
    if not cols:
        return []
    link = next((c for c in ("origin_session", "origin_session_id") if c in cols), None)
    if link is None:
        return []
    text_cols = [c for c in ("task_json", "event_json", "result_json") if c in cols]
    sel = [link] + text_cols
    sql = "SELECT {} FROM async_delegations".format(
        ", ".join('"' + c + '"' for c in sel))
    out: List[Dict[str, str]] = []
    for row in conn.execute(sql):
        origin = row[0]
        if not origin:
            continue
        text = " ".join(str(v) for v in row[1:] if v)
        out.append({"origin_session": str(origin), "text": text[:2000]})
    return out


def _probe_session(conn: sqlite3.Connection, profile: str, session_id: str,
                   head: int, tail: int, excerpt_chars: int,
                   content_cap: int = 8192) -> List[MessageProbe]:
    """Bounded window: first message + last N messages. Never the whole transcript.

    ``content`` keeps its 240-char excerpt semantics; ``content_head`` carries the same
    message untruncated up to ``content_cap`` so audience rule C1 can digest-match a brief.
    """
    out: List[MessageProbe] = []
    cols = _table_columns(conn, "messages")
    if "content" not in cols or "role" not in cols:
        return out
    has_tool_calls = "tool_calls" in cols
    tc_expr = '"tool_calls" AS tool_calls' if has_tool_calls else "NULL AS tool_calls"
    head_rows: List[sqlite3.Row] = []
    if head > 0:
        head_rows = list(conn.execute(
            'SELECT id, role, content, {}, timestamp FROM messages '
            'WHERE session_id=? ORDER BY id ASC LIMIT ?'.format(tc_expr),
            (session_id, head)))
    tail_rows = list(conn.execute(
        'SELECT id, role, content, {}, timestamp FROM messages '
        'WHERE session_id=? ORDER BY id DESC LIMIT ?'.format(tc_expr),
        (session_id, tail)))
    seen = set()
    for pos, rows in (("head", head_rows), ("tail", list(reversed(tail_rows)))):
        for r in rows:
            mid = int(r["id"])
            if mid in seen:
                continue
            seen.add(mid)
            content = r["content"] or ""
            if not isinstance(content, str):
                content = str(content)
            out.append(MessageProbe(
                profile_name=profile,
                session_id=session_id,
                msg_id=mid,
                role=r["role"] or "",
                content=content[:excerpt_chars],
                tool_calls=bool(r["tool_calls"]),
                timestamp=_f(r["timestamp"]),
                position=pos,
                content_head=content[:content_cap],
            ))
    return out
