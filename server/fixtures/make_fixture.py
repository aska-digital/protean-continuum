"""Isolated fixture builder for Continuum tests.

Creates a throw-away ``hermes_home`` with two synthetic profile ``state.db`` files that mirror
the columns the scanner reads. Every fixture is deterministic and self-contained — no test ever
depends on the live profile databases.

Encodes the acceptance vectors as data:
  AV-2 cron sessions exist and must be bucketed as noise, never board entries
  AV-3 three sessions share git_repo_root .../typejoy/wrk/tjgc1 -> one HIGH cluster
  AV-4 sessions share only the generic cwd /synthetic/home (and '.') -> NEVER merged
  AV-5 one cluster is stale-only -> must emit zero alerts
  AV-6 title_source user / derived / blank -> DECLARED / DERIVED / UNTITLED
  AV-7 a child session folds into its parent, never standalone
  AV-8 profile names and session ids are emitted byte-identical
"""
from __future__ import annotations

import os
import sqlite3
import time
from typing import List, Tuple

DAY = 86400.0

_SESSIONS_DDL = """
CREATE TABLE sessions (
  id TEXT PRIMARY KEY, source TEXT NOT NULL, user_id TEXT, session_key TEXT, chat_id TEXT,
  chat_type TEXT, thread_id TEXT, display_name TEXT, model TEXT, parent_session_id TEXT,
  started_at REAL NOT NULL, ended_at REAL, message_count INTEGER DEFAULT 0,
  tool_call_count INTEGER DEFAULT 0, cwd TEXT, git_branch TEXT, git_repo_root TEXT,
  title TEXT, title_source TEXT, last_activity_at REAL, last_activity_description TEXT,
  profile_name TEXT, archived INTEGER NOT NULL DEFAULT 0, pinned INTEGER NOT NULL DEFAULT 0,
  hidden INTEGER NOT NULL DEFAULT 0, tool_names TEXT
);
CREATE TABLE messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, role TEXT NOT NULL,
  content TEXT, tool_call_id TEXT, tool_calls TEXT, tool_name TEXT, timestamp REAL NOT NULL
);
CREATE TABLE schema_version (version INTEGER NOT NULL);
"""

TJGC1 = "/synthetic/work/tjgc1"
WS = "/synthetic/work/tywebsite"
GENERIC = "/synthetic/home"


def _session(conn, sid, *, source="cli", title=None, title_source=None, cwd=None,
             git=None, started=None, last=None, msgs=4, tools=2, parent=None,
             chat_type=None, display_name=None, archived=0, pinned=0, hidden=0):
    conn.execute(
        "INSERT INTO sessions (id, source, title, title_source, cwd, git_repo_root, started_at, "
        "last_activity_at, message_count, tool_call_count, parent_session_id, chat_type, "
        "display_name, archived, pinned, hidden) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (sid, source, title, title_source, cwd, git, started, last, msgs, tools, parent,
         chat_type, display_name, archived, pinned, hidden))


def _messages(conn, sid, rows: List[Tuple[str, str, float]]):
    """rows: (role, content, timestamp)."""
    for role, content, ts in rows:
        conn.execute("INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
                     (sid, role, content, ts))


def build_fixture_tree(root: str, now: float = None) -> str:
    """Create <root>/profiles/{alpha,beta}/state.db and return the hermes_home (== root)."""
    now = now if now is not None else time.time()
    os.makedirs(root, exist_ok=True)
    prof_dir = os.path.join(root, "profiles")
    os.makedirs(prof_dir, exist_ok=True)

    for name in ("alpha", "beta"):
        db = os.path.join(prof_dir, name, "state.db")
        os.makedirs(os.path.dirname(db), exist_ok=True)
        if os.path.exists(db):
            os.remove(db)
        conn = sqlite3.connect(db)
        conn.executescript(_SESSIONS_DDL)
        conn.execute("INSERT INTO schema_version (version) VALUES (30)")
        if name == "alpha":
            _build_alpha(conn, now)
        else:
            _build_beta(conn, now)
        conn.commit()
        conn.close()
    return root


def _build_alpha(conn, now: float):
    # --- AV-3: three sessions sharing the same git repo -> HIGH cluster -----
    for i, sid in enumerate(("20260101_000001_aaa111", "20260102_000002_aaa222",
                             "20260103_000003_aaa333")):
        _session(conn, sid, title="typejoy landing page pass {}".format(i + 1),
                 title_source="derived", cwd=TJGC1, git=TJGC1,
                 started=now - (3 - i) * DAY, last=now - 2 * DAY)
        _messages(conn, sid, [
            ("user", "work on the trojan-go client landing page", now - (3 - i) * DAY),
            ("assistant", "Updated the typejoy wrk/tjgc1 landing page. Next step: run the build.",
             now - 2 * DAY),
        ])

    # --- AV-5: stale-only cluster, no drive_expected, no next action --------
    for sid in ("20260104_000004_bbb111", "20260105_000005_bbb222"):
        _session(conn, sid, title="tywebsite hero section", title_source="derived",
                 cwd=WS, started=now - 60 * DAY, last=now - 40 * DAY)
        _messages(conn, sid, [
            ("user", "tweak the hero", now - 60 * DAY),
            ("assistant", "Hero updated and deployed.", now - 40 * DAY),
        ])

    # --- AV-4: generic cwd only -> must never merge ------------------------
    for i, sid in enumerate(("20260106_000006_ccc111", "20260107_000007_ccc222",
                             "20260108_000008_ccc333", "20260109_000009_ccc444")):
        _session(conn, sid, title="random build note {}".format(i + 1),
                 title_source="derived", cwd=GENERIC, started=now - 5 * DAY, last=now - 4 * DAY)
        _messages(conn, sid, [("user", "build site {}".format(i), now - 4 * DAY)])

    # --- AV-4b: cwd '.' -> context, not a key ------------------------------
    for i, sid in enumerate(("20260110_000010_ddd111", "20260111_000011_ddd222")):
        _session(conn, sid, title="audit note {}".format(i + 1), title_source="derived",
                 cwd=".", started=now - 5 * DAY, last=now - 4 * DAY)
        _messages(conn, sid, [("user", "audit the repo {}".format(i), now - 4 * DAY)])

    # --- AV-2: cron noise (categorically bucketed, never a project seed) ---
    for i, sid in enumerate(("20260112_000012_eee111", "20260113_000013_eee222")):
        _session(conn, sid, source="cron", title="daily drift monitor · Sep 1{} 09:38".format(i),
                 cwd=GENERIC, started=now - DAY, last=now - DAY, msgs=2, tools=0)
        _messages(conn, sid, [("user", "run the drift check", now - DAY)])

    # --- AV-7: subagent child folds into its parent ------------------------
    _session(conn, "20260114_000014_fff111", source="subagent", title="child worker",
             cwd=TJGC1, git=TJGC1, started=now - 2 * DAY, last=now - 2 * DAY,
             parent="20260101_000001_aaa111", msgs=2, tools=1)
    _messages(conn, "20260114_000014_fff111",
              [("user", "do the subtask", now - 2 * DAY)])

    # --- AV-6b: untitled fragment -> NS-6 ----------------------------------
    _session(conn, "20260115_000015_ggg111", title=None, title_source=None,
             cwd=GENERIC, started=now - DAY, last=now - DAY, msgs=1, tools=0)
    _messages(conn, "20260115_000015_ggg111", [("user", "hi", now - DAY)])

    # --- AV-6a: DECLARED user title must survive verbatim ------------------
    _session(conn, "20260116_000016_hhh111", title="My Own Project Name",
             title_source="user", cwd="/synthetic/work/onlyone",
             started=now - 10 * DAY, last=now - 9 * DAY)
    _messages(conn, "20260116_000016_hhh111", [("user", "note", now - 9 * DAY)])

    # --- AV-6c: llm title -> DERIVED ---------------------------------------
    _session(conn, "20260117_000017_iii111", title="llm generated title",
             title_source="llm", cwd="/synthetic/work/onlytwo",
             started=now - 10 * DAY, last=now - 9 * DAY)
    _messages(conn, "20260117_000017_iii111", [("user", "note", now - 9 * DAY)])

    # --- attention: awaiting-user on the git cluster (LS-4) -----------------
    _messages(conn, "20260103_000003_aaa333", [
        ("assistant", "All work is done. Please confirm the release plan so I can proceed.",
         now - 2 * DAY),
    ])

    # --- NS-4 test/handshake ------------------------------------------------
    _session(conn, "20260118_000018_jjj111", source="subagent", title="ping handshake",
             cwd=GENERIC, started=now - DAY, last=now - DAY, msgs=1, tools=0)
    _messages(conn, "20260118_000018_jjj111", [("user", "confirm receipt", now - DAY)])


def _build_beta(conn, now: float):
    # cross-profile member of the AV-3 git cluster
    _session(conn, "20260201_000001_bbb901", title="typejoy wrk/tjgc1 review",
             title_source="user", cwd=TJGC1, git=TJGC1,
             started=now - 2 * DAY, last=now - 1 * DAY)
    _messages(conn, "20260201_000001_bbb901",
              [("user", "review /synthetic/work/tjgc1", now - 1 * DAY)])
