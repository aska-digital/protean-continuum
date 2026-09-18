"""M2 — continuum.registry.

Owns the local registry SQLite DB (schema per architecture contract §5). This is the ONLY
module that persists, and the ONLY writable database in the whole system. It never reads a
source DB and never calls a model.

Reversibility (INV-3): every human decision is an append-only ``review_event`` with a
before/after image; ``undo`` replays the inverse. Declared fields carry verified_at/expires_at
and are re-applied on every rescan, so a rebuild never loses a declared decision.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .scanner import SessionFact

SCHEMA_VERSION = 3

# Additive v2 -> v3 columns (D-US-1/D-US-7). Declared here once; applied both in the base DDL
# (fresh DBs) and as idempotent ALTERs guarded by PRAGMA table_info (existing DBs).
_ADDITIVE_COLUMNS = (
    ("session_fact", "audience", "TEXT NOT NULL DEFAULT 'UNKNOWN'"),
    ("session_fact", "audience_reason", "TEXT"),
    ("session_fact", "audience_evidence_ref", "TEXT"),
    ("project", "anchor_session", "TEXT"),
    ("project", "anchor_reason", "TEXT"),
    ("project", "anchor_updated_at", "REAL"),
)

_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS source_db (
  profile_name TEXT NOT NULL,
  path TEXT NOT NULL,
  exists_now INTEGER NOT NULL DEFAULT 1,
  db_mtime REAL, db_size INTEGER,
  schema_version INTEGER,
  fine_watermark REAL, coarse_watermark REAL,
  last_scan_at REAL,
  session_count INTEGER, message_count INTEGER,
  status TEXT,
  PRIMARY KEY (profile_name)
);
CREATE TABLE IF NOT EXISTS scan_run (
  run_id TEXT PRIMARY KEY, started_at REAL, ended_at REAL,
  mode TEXT, profiles_scanned INTEGER, sessions_read INTEGER,
  messages_probed INTEGER, status TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS session_fact (
  profile_name TEXT NOT NULL, session_id TEXT NOT NULL,
  source TEXT, title TEXT, title_source TEXT, display_name TEXT,
  cwd TEXT, workspace_root TEXT, git_repo_root TEXT, git_branch TEXT,
  started_at REAL, last_activity_at REAL, message_count INTEGER, tool_call_count INTEGER,
  parent_session_id TEXT, model TEXT, tool_names TEXT,
  archived INTEGER, pinned INTEGER, hidden INTEGER,
  content_hash TEXT, first_seen_at REAL, last_seen_at REAL,
  present INTEGER NOT NULL DEFAULT 1,
  is_noise INTEGER NOT NULL DEFAULT 0, noise_class TEXT,
  audience TEXT NOT NULL DEFAULT 'UNKNOWN', audience_reason TEXT, audience_evidence_ref TEXT,
  PRIMARY KEY (profile_name, session_id)
);
CREATE TABLE IF NOT EXISTS project (
  project_id TEXT PRIMARY KEY,
  name TEXT, kind TEXT, phase TEXT,
  lifecycle TEXT, confidence REAL, confidence_band TEXT, evidence_tier INTEGER,
  owner_profile TEXT,
  drive_expected INTEGER NOT NULL DEFAULT 0,
  stall_age_days REAL, last_substantive_activity REAL, session_count INTEGER,
  derived_updated_at REAL, created_at REAL, updated_at REAL,
  declared_rev INTEGER NOT NULL DEFAULT 0,
  anchor_session TEXT, anchor_reason TEXT, anchor_updated_at REAL
);
CREATE TABLE IF NOT EXISTS project_session (
  link_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL, profile_name TEXT NOT NULL, session_id TEXT NOT NULL,
  role_in_project TEXT, link_confidence REAL, link_reason TEXT, evidence_ref TEXT,
  accepted INTEGER NOT NULL DEFAULT 0,
  first_linked_at REAL, declared_rev INTEGER NOT NULL DEFAULT 0,
  UNIQUE(project_id, profile_name, session_id)
);
CREATE TABLE IF NOT EXISTS evidence (
  evidence_id TEXT PRIMARY KEY,
  project_id TEXT, cluster_id TEXT,
  profile_name TEXT, session_id TEXT,
  tier INTEGER NOT NULL, kind TEXT NOT NULL,
  excerpt TEXT, locator TEXT,
  extracted_at REAL, source_hash TEXT
);
CREATE TABLE IF NOT EXISTS next_action (
  action_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
  text TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'open',
  source TEXT NOT NULL,
  verified_at REAL, expires_at REAL, declared_rev INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS declared_field (
  project_id TEXT NOT NULL, field TEXT NOT NULL,
  value TEXT, source TEXT, verified_at REAL, expires_at REAL, actor TEXT,
  declared_rev INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (project_id, field)
);
CREATE TABLE IF NOT EXISTS review_event (
  event_id TEXT PRIMARY KEY, ts REAL NOT NULL, actor TEXT,
  action TEXT NOT NULL, target_type TEXT, target_id TEXT,
  before_json TEXT, after_json TEXT, rev INTEGER
);
CREATE TABLE IF NOT EXISTS project_signature (
  signature TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  updated_at REAL
);
CREATE INDEX IF NOT EXISTS idx_ps_project ON project_session(project_id);
CREATE INDEX IF NOT EXISTS idx_ev_project ON evidence(project_id);
CREATE INDEX IF NOT EXISTS idx_re_tid ON review_event(target_id, ts);
CREATE INDEX IF NOT EXISTS idx_sig_pid ON project_signature(project_id);
"""


@dataclass
class ProjectRow:
    project_id: str
    name: Optional[str]
    kind: str = "project"
    phase: Optional[str] = None
    lifecycle: str = "LS-9"
    confidence: float = 0.0
    confidence_band: str = "unknown"
    evidence_tier: int = 0
    owner_profile: Optional[str] = None
    drive_expected: int = 0
    stall_age_days: Optional[float] = None
    last_substantive_activity: Optional[float] = None
    session_count: int = 0


@dataclass
class LinkRow:
    link_id: str
    project_id: str
    profile_name: str
    session_id: str
    role_in_project: str = "supporting"
    link_confidence: float = 0.0
    link_reason: str = ""
    evidence_ref: Optional[str] = None
    accepted: int = 0


@dataclass
class ReviewEvent:
    event_id: str
    ts: float
    actor: str
    action: str
    target_type: str
    target_id: str
    before_json: str
    after_json: str
    rev: int


class Registry:
    def __init__(self, path: str):
        self.path = path
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=10.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")   # registry only; never a source DB
        self._tx_depth = 0

    def transaction(self):
        """Context manager for atomic review mutations (INV-KB-1).

        All Registry writes inside the block plus the review_event
        commit in one SQLite transaction; on exception the whole
        mutation rolls back. Public method signatures are unchanged;
        methods simply skip their internal commit while inside a
        transaction.
        """
        from contextlib import contextmanager as _cm

        @ _cm
        def _tx():
            self._tx_depth += 1
            begun = False
            if self._tx_depth == 1:
                try:
                    self.conn.execute("BEGIN IMMEDIATE")
                    begun = True
                except sqlite3.OperationalError:
                    begun = False
            try:
                yield self
            except Exception:
                if begun:
                    try:
                        self.conn.rollback()
                    except Exception:
                        pass
                raise
            else:
                if begun:
                    self.conn.commit()
            finally:
                self._tx_depth -= 1
                if self._tx_depth < 0:
                    self._tx_depth = 0
        return _tx()

    def _should_commit(self) -> bool:
        return self._tx_depth == 0

    def commit(self) -> None:
        """Commit only when NOT inside an explicit ``transaction()`` block.

        This is the M4 scan atomicity boundary (§8.2): the whole derived ingest runs inside
        one ``transaction()`` so a failed scan rolls its derived writes back instead of
        leaving a partial snapshot on the board.
        """
        if self._should_commit():
            self.conn.commit()

    def declared_rows_with_timestamps(self, project_ids: Optional[Sequence[str]] = None) -> Dict[str, Dict[str, Tuple[str, float]]]:
        """Batched read of placement/urgent rows with verified_at.

        Returns {project_id: {field: (value, verified_at)}} for the
        requested projects (or all). Uses a single query; no signature
        change to existing accessors. TTL filtering is left to the
        caller via effective_declared; this returns raw rows.
        """
        if project_ids is not None and len(project_ids) == 0:
            return {}
        if project_ids is None:
            rows = self.conn.execute(
                "SELECT project_id, field, value, verified_at FROM declared_field "
                "WHERE field IN ('placement','urgent')"
            ).fetchall()
        else:
            placeholders = ",".join("?" * len(project_ids))
            rows = self.conn.execute(
                "SELECT project_id, field, value, verified_at FROM declared_field "
                "WHERE field IN ('placement','urgent') AND project_id IN ({})".format(placeholders),
                tuple(project_ids)
            ).fetchall()
        out: Dict[str, Dict[str, Tuple[str, float]]] = {}
        for r in rows:
            out.setdefault(r["project_id"], {})[r["field"]] = (r["value"], r["verified_at"])
        return out

    # ------------------------------------------------------------------ schema
    def migrate(self) -> None:
        self.conn.executescript(_DDL)
        self._ensure_additive_columns()
        row = self.conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        if row is None:
            self.conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        elif int(row[0]) < SCHEMA_VERSION:
            # Additive only: the version is a marker, never a destructive migration (D-US-7).
            self.conn.execute("UPDATE schema_version SET version=?", (SCHEMA_VERSION,))
        self.conn.commit()

    def _ensure_additive_columns(self) -> None:
        """Idempotent ``ALTER TABLE ... ADD COLUMN`` for the v3 audience/anchor columns."""
        for table, column, decl in _ADDITIVE_COLUMNS:
            try:
                cols = {r[1] for r in self.conn.execute(
                    "PRAGMA table_info({})".format(table))}
            except sqlite3.Error:
                continue
            if not cols or column in cols:
                continue
            try:
                self.conn.execute(
                    "ALTER TABLE {} ADD COLUMN {} {}".format(table, column, decl))
            except sqlite3.Error:
                pass

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass

    # ------------------------------------------------------------------ scan bookkeeping
    def begin_scan_run(self, run_id: str, mode: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO scan_run (run_id, started_at, mode, status) VALUES (?,?,?,?)",
            (run_id, time.time(), mode, "running"))
        self.conn.commit()

    def end_scan_run(self, run_id: str, *, sessions_read: int, messages_probed: int,
                     profiles_scanned: int, status: str = "ok", error: Optional[str] = None) -> None:
        self.conn.execute(
            "UPDATE scan_run SET ended_at=?, sessions_read=?, messages_probed=?, "
            "profiles_scanned=?, status=?, error=? WHERE run_id=?",
            (time.time(), sessions_read, messages_probed, profiles_scanned, status, error, run_id))
        self.conn.commit()

    def upsert_source_db(self, profile_name: str, path: str, *, exists_now: int,
                         db_mtime: Optional[float], db_size: Optional[int],
                         schema_version: Optional[int], session_count: int,
                         message_count: int, status: str,
                         fine_watermark: Optional[float] = None,
                         coarse_watermark: Optional[float] = None) -> None:
        self.conn.execute(
            """INSERT INTO source_db (profile_name, path, exists_now, db_mtime, db_size,
                  schema_version, fine_watermark, coarse_watermark, last_scan_at,
                  session_count, message_count, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(profile_name) DO UPDATE SET
                path=excluded.path, exists_now=excluded.exists_now, db_mtime=excluded.db_mtime,
                db_size=excluded.db_size, schema_version=excluded.schema_version,
                fine_watermark=COALESCE(excluded.fine_watermark, source_db.fine_watermark),
                coarse_watermark=COALESCE(excluded.coarse_watermark, source_db.coarse_watermark),
                last_scan_at=excluded.last_scan_at, session_count=excluded.session_count,
                message_count=excluded.message_count, status=excluded.status""",
            (profile_name, path, exists_now, db_mtime, db_size, schema_version, fine_watermark,
             coarse_watermark, time.time(), session_count, message_count, status))
        self.commit()

    def source_db_watermarks(self) -> Dict[str, Dict[str, Any]]:
        """Previous committed per-profile snapshot, for the incremental scan decision.

        Includes the stored ``fine_watermark`` / ``coarse_watermark`` so a caller sees exactly
        what the registry holds (CP-9); the scanner's skip decision does not yet consume them.
        """
        out: Dict[str, Dict[str, Any]] = {}
        for r in self.conn.execute(
                "SELECT profile_name, db_mtime, db_size, session_count, message_count, "
                "schema_version, status, last_scan_at, fine_watermark, coarse_watermark "
                "FROM source_db"):
            out[r["profile_name"]] = {
                "mtime": r["db_mtime"], "size": r["db_size"],
                "session_count": r["session_count"], "message_count": r["message_count"],
                "schema_version": r["schema_version"], "status": r["status"],
                "last_scan_at": r["last_scan_at"],
                "fine_watermark": r["fine_watermark"],
                "coarse_watermark": r["coarse_watermark"],
            }
        return out

    def get_watermark(self, profile_name: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT fine_watermark, coarse_watermark FROM source_db WHERE profile_name=?",
            (profile_name,)).fetchone()
        if row is None:
            return None
        return {"fine_watermark": row["fine_watermark"], "coarse_watermark": row["coarse_watermark"]}

    def put_watermark(self, profile_name: str, fine: Optional[float],
                      coarse: Optional[float] = None) -> None:
        self.conn.execute(
            "UPDATE source_db SET fine_watermark=?, coarse_watermark=? WHERE profile_name=?",
            (fine, coarse, profile_name))
        self.commit()

    # ------------------------------------------------------------------ raw facts
    def upsert_session_facts(self, facts: Sequence[SessionFact]) -> int:
        now = time.time()
        rows = []
        for f in facts:
            rows.append((
                f.profile_name, f.session_id, f.source, f.title, f.title_source, f.display_name,
                f.cwd, f.workspace_root, f.git_repo_root, f.git_branch, f.started_at,
                f.last_activity_at, f.message_count, f.tool_call_count, f.parent_session_id,
                f.model, f.tool_names, f.archived, f.pinned, f.hidden, f.content_hash, now, now,
            ))
        self.conn.executemany(
            """INSERT INTO session_fact (profile_name, session_id, source, title, title_source,
                   display_name, cwd, workspace_root, git_repo_root, git_branch, started_at,
                   last_activity_at, message_count, tool_call_count, parent_session_id, model,
                   tool_names, archived, pinned, hidden, content_hash, first_seen_at, last_seen_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(profile_name, session_id) DO UPDATE SET
                 source=excluded.source, title=excluded.title, title_source=excluded.title_source,
                 display_name=excluded.display_name, cwd=excluded.cwd,
                 workspace_root=excluded.workspace_root, git_repo_root=excluded.git_repo_root,
                 git_branch=excluded.git_branch, started_at=excluded.started_at,
                 last_activity_at=excluded.last_activity_at, message_count=excluded.message_count,
                 tool_call_count=excluded.tool_call_count,
                 parent_session_id=excluded.parent_session_id, model=excluded.model,
                 tool_names=excluded.tool_names, archived=excluded.archived,
                 pinned=excluded.pinned, hidden=excluded.hidden,
                 content_hash=excluded.content_hash, last_seen_at=excluded.last_seen_at,
                 present=1""",
            rows)
        self.commit()
        return len(rows)

    def apply_noise(self, noise: Dict[str, Tuple[int, str]]) -> None:
        for ref, (is_noise, cls) in noise.items():
            profile, _, sid = ref.partition("/")
            self.conn.execute(
                "UPDATE session_fact SET is_noise=?, noise_class=? WHERE profile_name=? AND session_id=?",
                (int(is_noise), cls or None, profile, sid))
        self.commit()

    # ------------------------------------------------------------------ audience (D-US)
    def apply_audience(self, audience: Dict[str, Tuple[str, Optional[str], Optional[str]]]) -> None:
        """Persist the derived audience for each ``profile/session_id`` ref.

        ``audience`` maps a ref to ``(audience, reason, evidence_ref)``. Derived on every
        scan, exactly like the other derived fields; never a declared field.
        """
        for ref, (value, reason, evidence_ref) in audience.items():
            profile, _, sid = ref.partition("/")
            self.conn.execute(
                "UPDATE session_fact SET audience=?, audience_reason=?, audience_evidence_ref=? "
                "WHERE profile_name=? AND session_id=?",
                (value, reason, evidence_ref, profile, sid))
        if self._should_commit():
            self.conn.commit()

    def session_audience(self, profile_name: str, session_id: str
                         ) -> Optional[Tuple[str, Optional[str], Optional[str]]]:
        row = self.conn.execute(
            "SELECT audience, audience_reason, audience_evidence_ref FROM session_fact "
            "WHERE profile_name=? AND session_id=?", (profile_name, session_id)).fetchone()
        if row is None:
            return None
        return (row["audience"] or "UNKNOWN", row["audience_reason"], row["audience_evidence_ref"])

    def set_anchor(self, project_id: str, anchor_session: Optional[str],
                   reason: str) -> None:
        """Persist a project's resolved user-facing conversation (D-US-6)."""
        self.conn.execute(
            "UPDATE project SET anchor_session=?, anchor_reason=?, anchor_updated_at=? "
            "WHERE project_id=?",
            (anchor_session, reason, time.time(), project_id))
        if self._should_commit():
            self.conn.commit()

    def set_primary(self, project_id: str, primary_ref: Optional[str]) -> None:
        """Rewrite ``role_in_project`` for one project from the audience ranking (D-US-5/D-US-6).

        ``accepted`` and ``declared_rev`` are never touched (D-US-7). A ``DELEGATED`` or
        ``AUTOMATED`` member can never be made primary: ``primary_ref`` must already be a
        USER_FACING/UNKNOWN member chosen by ``audience.select_primary``.
        """
        self.conn.execute(
            "UPDATE project_session SET role_in_project='supporting' WHERE project_id=?",
            (project_id,))
        if primary_ref:
            profile, _, sid = primary_ref.partition("/")
            self.conn.execute(
                "UPDATE project_session SET role_in_project='primary' "
                "WHERE project_id=? AND profile_name=? AND session_id=?",
                (project_id, profile, sid))
        if self._should_commit():
            self.conn.commit()

    def session_facts(self, profile_name: Optional[str] = None) -> List[sqlite3.Row]:
        if profile_name:
            return list(self.conn.execute(
                "SELECT * FROM session_fact WHERE profile_name=? AND present=1", (profile_name,)))
        return list(self.conn.execute("SELECT * FROM session_fact WHERE present=1"))

    def durable_session_facts(self, profile_name: Optional[str] = None) -> List[sqlite3.Row]:
        """Every session_fact row for durable links, INCLUDING tombstoned (present=0) rows.

        A tombstoned fact keeps its preserved ``message_count``, so the aggregate can treat the
        durable link's count as known instead of missing (architecture §6.2 durable-link rule).
        SELECT-only; never writes.
        """
        if profile_name:
            return list(self.conn.execute(
                "SELECT * FROM session_fact WHERE profile_name=?", (profile_name,)))
        return list(self.conn.execute("SELECT * FROM session_fact"))

    def source_db_statuses(self) -> Dict[str, str]:
        """Per-profile recorded source-db status (``ok`` when readable). SELECT-only."""
        return {r["profile_name"]: r["status"]
                for r in self.conn.execute("SELECT profile_name, status FROM source_db")}

    def tombstone_missing(self, profile_name: str, seen_ids: Iterable[str]) -> int:
        seen = set(seen_ids)
        rows = list(self.conn.execute(
            "SELECT session_id FROM session_fact WHERE profile_name=? AND present=1",
            (profile_name,)))
        missing = [r["session_id"] for r in rows if r["session_id"] not in seen]
        for sid in missing:
            self.conn.execute(
                "UPDATE session_fact SET present=0 WHERE profile_name=? AND session_id=?",
                (profile_name, sid))
        self.commit()
        return len(missing)

    # ------------------------------------------------------------------ projects / links
    def upsert_projects(self, projects: Sequence[ProjectRow]) -> None:
        now = time.time()
        for p in projects:
            self.conn.execute(
                """INSERT INTO project (project_id, name, kind, phase, lifecycle, confidence,
                       confidence_band, evidence_tier, owner_profile, drive_expected,
                       stall_age_days, last_substantive_activity, session_count,
                       derived_updated_at, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(project_id) DO UPDATE SET
                     name=excluded.name, kind=excluded.kind, phase=excluded.phase,
                     lifecycle=excluded.lifecycle, confidence=excluded.confidence,
                     confidence_band=excluded.confidence_band, evidence_tier=excluded.evidence_tier,
                     stall_age_days=excluded.stall_age_days,
                     last_substantive_activity=excluded.last_substantive_activity,
                     session_count=excluded.session_count,
                     derived_updated_at=excluded.derived_updated_at, updated_at=excluded.updated_at""",
                (p.project_id, p.name, p.kind, p.phase, p.lifecycle, p.confidence,
                 p.confidence_band, p.evidence_tier, p.owner_profile, p.drive_expected,
                 p.stall_age_days, p.last_substantive_activity, p.session_count,
                 now, now, now))
        self.commit()

    def upsert_links(self, links: Sequence[LinkRow]) -> None:
        now = time.time()
        for l in links:
            self.conn.execute(
                """INSERT INTO project_session (link_id, project_id, profile_name, session_id,
                       role_in_project, link_confidence, link_reason, evidence_ref, accepted,
                       first_linked_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(project_id, profile_name, session_id) DO UPDATE SET
                     role_in_project=excluded.role_in_project,
                     link_confidence=excluded.link_confidence, link_reason=excluded.link_reason,
                     evidence_ref=excluded.evidence_ref""",
                (l.link_id, l.project_id, l.profile_name, l.session_id, l.role_in_project,
                 l.link_confidence, l.link_reason, l.evidence_ref, l.accepted, now))
        self.commit()

    def clear_project_sessions(self, project_ids: Sequence[str]) -> None:
        for pid in project_ids:
            self.conn.execute(
                "DELETE FROM project_session WHERE project_id=? AND declared_rev<=0", (pid,))
        self.commit()

    def links_for(self, project_id: str) -> List[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM project_session WHERE project_id=? ORDER BY session_id", (project_id,)))

    def links_for_many(self, project_ids: Sequence[str]) -> Dict[str, List[sqlite3.Row]]:
        """Batched SELECT-only link accessor (M4 §8.3: no per-card registry round trip)."""
        out: Dict[str, List[sqlite3.Row]] = {}
        if not project_ids:
            return out
        placeholders = ",".join("?" * len(project_ids))
        rows = self.conn.execute(
            "SELECT * FROM project_session WHERE project_id IN ({}) "
            "ORDER BY project_id, session_id".format(placeholders),
            tuple(project_ids))
        for row in rows:
            out.setdefault(row["project_id"], []).append(row)
        return out

    def projects(self) -> List[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM project ORDER BY project_id"))

    def get_project(self, project_id: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM project WHERE project_id=?", (project_id,)).fetchone()

    # ------------------------------------------------------------------ evidence
    def upsert_evidence(self, cluster_id: str, project_id: Optional[str],
                        items: Sequence[Any]) -> None:
        now = time.time()
        for e in items:
            loc = getattr(e, "locator", {}) or {}
            eid = str(uuid.uuid5(uuid.NAMESPACE_URL, "continuum:ev:{}:{}:{}:{}".format(
                cluster_id, loc.get("profile"), loc.get("session_id"), loc.get("msg_id"))))
            self.conn.execute(
                """INSERT OR REPLACE INTO evidence (evidence_id, project_id, cluster_id,
                       profile_name, session_id, tier, kind, excerpt, locator, extracted_at,
                       source_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (eid, project_id, cluster_id, loc.get("profile"), loc.get("session_id"),
                 int(getattr(e, "tier", 1)), getattr(e, "kind", "note"),
                 getattr(e, "excerpt", ""), json.dumps(loc), now,
                 getattr(e, "source_hash", "")))
        self.commit()

    def evidence_for(self, project_id: str) -> List[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM evidence WHERE project_id=? ORDER BY tier, kind", (project_id,)))

    def evidence_for_many(self, project_ids: Sequence[str]) -> Dict[str, List[sqlite3.Row]]:
        """Batched SELECT-only evidence accessor (M4 §8.3: no per-card evidence fetch)."""
        out: Dict[str, List[sqlite3.Row]] = {}
        if not project_ids:
            return out
        placeholders = ",".join("?" * len(project_ids))
        rows = self.conn.execute(
            "SELECT * FROM evidence WHERE project_id IN ({}) "
            "ORDER BY project_id, tier, kind".format(placeholders),
            tuple(project_ids))
        for row in rows:
            out.setdefault(row["project_id"], []).append(row)
        return out

    # ------------------------------------------------------------------ declared fields
    def set_declared(self, project_id: str, field_name: str, value: Optional[str],
                     actor: str = "local", ttl_days: Optional[float] = None) -> None:
        now = time.time()
        expires = None if ttl_days is None else now + ttl_days * 86400.0
        self.conn.execute(
            """INSERT INTO declared_field (project_id, field, value, source, verified_at,
                   expires_at, actor, declared_rev)
               VALUES (?,?,?,?,?,?,?, COALESCE((SELECT declared_rev FROM declared_field
                   WHERE project_id=? AND field=?), 0) + 1)
               ON CONFLICT(project_id, field) DO UPDATE SET
                 value=excluded.value, source='declared', verified_at=excluded.verified_at,
                 expires_at=excluded.expires_at, actor=excluded.actor,
                 declared_rev=declared_field.declared_rev + 1""",
            (project_id, field_name, value, "declared", now, expires, actor,
             project_id, field_name))
        self.conn.execute(
            "UPDATE project SET declared_rev = declared_rev + 1 WHERE project_id=?", (project_id,))
        if self._should_commit():
            self.conn.commit()

    def clear_declared(self, project_id: str, field_name: str) -> None:
        self.conn.execute(
            "DELETE FROM declared_field WHERE project_id=? AND field=?", (project_id, field_name))
        if self._should_commit():
            self.conn.commit()

    def declared_fields(self, project_id: str, now: Optional[float] = None) -> List[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM declared_field WHERE project_id=?", (project_id,)))

    def update_project_declared(self, project_id: str, *, lifecycle: Optional[str] = None,
                                owner_profile: Optional[str] = None,
                                drive_expected: Optional[int] = None) -> None:
        """Re-apply declared overlays onto a materialized project row (declared wins)."""
        sets, vals = [], []
        if lifecycle is not None:
            sets.append("lifecycle=?"); vals.append(lifecycle)
        if owner_profile is not None:
            sets.append("owner_profile=?"); vals.append(owner_profile)
        if drive_expected is not None:
            sets.append("drive_expected=?"); vals.append(int(drive_expected))
        if not sets:
            return
        vals.append(project_id)
        self.conn.execute("UPDATE project SET {} WHERE project_id=?".format(", ".join(sets)), vals)
        if self._should_commit():
            self.conn.commit()

    def effective_declared(self, project_id: str, now: Optional[float] = None) -> Dict[str, str]:
        """Declared fields that have NOT expired, keyed by field name."""
        now = now if now is not None else time.time()
        out: Dict[str, str] = {}
        for r in self.conn.execute(
                "SELECT field, value, expires_at FROM declared_field WHERE project_id=?",
                (project_id,)):
            if r["expires_at"] is not None and r["expires_at"] < now:
                continue
            out[r["field"]] = r["value"]
        return out

    def is_declared_stale(self, project_id: str, now: Optional[float] = None) -> bool:
        now = now if now is not None else time.time()
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM declared_field WHERE project_id=? AND expires_at IS NOT NULL "
            "AND expires_at < ?", (project_id, now)).fetchone()
        return bool(row and row["n"])

    # ------------------------------------------------------------------ next actions
    def set_next_action(self, project_id: str, text: str, source: str = "declared",
                        ttl_days: Optional[float] = 14.0, actor: str = "local") -> str:
        now = time.time()
        aid = str(uuid.uuid5(uuid.NAMESPACE_URL, "continuum:na:{}:{}".format(project_id, text)))
        expires = None if ttl_days is None else now + ttl_days * 86400.0
        self.conn.execute(
            """INSERT OR REPLACE INTO next_action (action_id, project_id, text, state, source,
                   verified_at, expires_at, declared_rev) VALUES (?,?,?,?,?,?,?,?)""",
            (aid, project_id, text, "open", source, now, expires, 1))
        if self._should_commit():
            self.conn.commit()
        return aid

    def next_actions_for(self, project_id: str, now: Optional[float] = None) -> List[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM next_action WHERE project_id=? AND state='open' ORDER BY verified_at DESC",
            (project_id,)))

    # ------------------------------------------------------------------ audit
    def append_review_event(self, ev: ReviewEvent) -> None:
        self.conn.execute(
            """INSERT INTO review_event (event_id, ts, actor, action, target_type, target_id,
                   before_json, after_json, rev) VALUES (?,?,?,?,?,?,?,?,?)""",
            (ev.event_id, ev.ts, ev.actor, ev.action, ev.target_type, ev.target_id,
             ev.before_json, ev.after_json, ev.rev))
        if self._should_commit():
            self.conn.commit()

    def review_events(self, target_id: Optional[str] = None, limit: int = 200) -> List[sqlite3.Row]:
        if target_id:
            return list(self.conn.execute(
                "SELECT * FROM review_event WHERE target_id=? ORDER BY ts DESC LIMIT ?",
                (target_id, limit)))
        return list(self.conn.execute(
            "SELECT * FROM review_event ORDER BY ts DESC LIMIT ?", (limit,)))

    def get_review_event(self, event_id: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM review_event WHERE event_id=?", (event_id,)).fetchone()

    # ------------------------------------------------------------------ snapshots (undo)
    def snapshot(self, project_ids: Sequence[str]) -> Dict[str, Any]:
        snap: Dict[str, Any] = {"projects": {}, "links": [], "declared": [], "next_actions": []}
        for pid in project_ids:
            snap["projects"][pid] = dict(self.get_project(pid)) if self.get_project(pid) else None
            for l in self.links_for(pid):
                snap["links"].append(dict(l))
            for d in self.conn.execute("SELECT * FROM declared_field WHERE project_id=?", (pid,)):
                snap["declared"].append(dict(d))
            for n in self.conn.execute("SELECT * FROM next_action WHERE project_id=?", (pid,)):
                snap["next_actions"].append(dict(n))
        return snap

    def restore(self, snap: Dict[str, Any]) -> None:
        """Hard-restore a snapshot: links, declared fields, next actions for the snapshotted ids."""
        pids = list((snap.get("projects") or {}).keys())
        for pid in pids:
            self.conn.execute("DELETE FROM project_session WHERE project_id=?", (pid,))
            self.conn.execute("DELETE FROM declared_field WHERE project_id=?", (pid,))
            self.conn.execute("DELETE FROM next_action WHERE project_id=?", (pid,))
        for l in snap.get("links", []):
            self.conn.execute(
                """INSERT OR REPLACE INTO project_session (link_id, project_id, profile_name,
                       session_id, role_in_project, link_confidence, link_reason, evidence_ref,
                       accepted, first_linked_at, declared_rev)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (l.get("link_id"), l.get("project_id"), l.get("profile_name"), l.get("session_id"),
                 l.get("role_in_project"), l.get("link_confidence"), l.get("link_reason"),
                 l.get("evidence_ref"), l.get("accepted", 0), l.get("first_linked_at"),
                 l.get("declared_rev", 0)))
        for d in snap.get("declared", []):
            self.conn.execute(
                """INSERT OR REPLACE INTO declared_field (project_id, field, value, source,
                       verified_at, expires_at, actor, declared_rev)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (d.get("project_id"), d.get("field"), d.get("value"), d.get("source"),
                 d.get("verified_at"), d.get("expires_at"), d.get("actor"),
                 d.get("declared_rev", 0)))
        for n in snap.get("next_actions", []):
            self.conn.execute(
                """INSERT OR REPLACE INTO next_action (action_id, project_id, text, state, source,
                       verified_at, expires_at, declared_rev) VALUES (?,?,?,?,?,?,?,?)""",
                (n.get("action_id"), n.get("project_id"), n.get("text"), n.get("state", "open"),
                 n.get("source", "declared"), n.get("verified_at"), n.get("expires_at"),
                 n.get("declared_rev", 0)))
        if self._should_commit():
            self.conn.commit()

    # ------------------------------------------------------------------ stable identity (F1)
    def get_project_signature(self, signature: str) -> Optional[str]:
        """Return the project_id for a cluster signature, or None."""
        row = self.conn.execute(
            "SELECT project_id FROM project_signature WHERE signature=?",
            (signature,)).fetchone()
        return row["project_id"] if row else None

    def set_project_signature(self, signature: str, project_id: str) -> None:
        self.conn.execute(
            """INSERT INTO project_signature (signature, project_id, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(signature) DO UPDATE SET
                 project_id=excluded.project_id, updated_at=excluded.updated_at""",
            (signature, project_id, time.time()))
        if self._should_commit():
            self.conn.commit()

    def current_project_for(self, member_refs: List[str]) -> Optional[str]:
        """Find an existing project_id whose project_session contains any of the given members."""
        for ref in member_refs:
            profile, _, sid = ref.partition("/")
            row = self.conn.execute(
                "SELECT project_id FROM project_session WHERE profile_name=? AND session_id=? "
                "ORDER BY first_linked_at DESC LIMIT 1",
                (profile, sid)).fetchone()
            if row:
                return row["project_id"]
        return None
