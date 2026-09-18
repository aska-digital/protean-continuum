"""Audience fixtures (D-US-8) — a self-contained ``hermes_home`` for the audience filter.

Builds three synthetic profile databases (``azaraki``, ``kodekoot``, ``lugia``) that mirror
the exact live rows named in the architecture decisions:

  F1  delegated_brief_file  a session whose first user message equals ``briefs/synthetic_brief.md``
  F2  real_target_session   ``azaraki/20260911_174725_516587`` + the real EvoPet brief text
  F3  real_direct_user      ``azaraki/20260912_221123_360e96``, ``kodekoot/20260913_075841_a0b08c``
  F4  real_delegator        ``lugia/20260911_163629_22fff0`` + an async_delegations row naming it
  F5  envelope_no_file      an envelope with no backing brief file
  F6  automated_sources     cron / subagent-with-parent / kanban rows
  F7  group_relay           ``[Group chat: …]`` sessions with ``chat_type`` NULL
  F8  evopet_no_user_session the 13 real EvoPet members (2 azaraki + 11 kodekoot, all delegated)

The only brief index the tests use is ``./briefs`` — no test reads live state.
"""
from __future__ import annotations

import os
import sqlite3
import time
from typing import List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
BRIEFS_DIR = os.path.join(HERE, "briefs")
SYNTHETIC_BRIEF = os.path.join(BRIEFS_DIR, "synthetic_brief.md")
AZARAKI_BRIEF = os.path.join(BRIEFS_DIR, "azaraki-brief.md")

TARGET_PROFILE = "azaraki"
TARGET_SESSION = "20260911_174725_516587"
TARGET_TITLE = "Write EvoPet levels and evolution contract doc"
ANCHOR_PROFILE = "lugia"
ANCHOR_SESSION = "20260911_163629_22fff0"
ANCHOR_TITLE = "Document evopet handoff package"
DIRECT_AZARAKI = "20260912_221123_360e96"
DIRECT_KODEKOOT = "20260913_075841_a0b08c"

EVOPET_CWD = "/Users/kethuda/evopet-pet"
NANAVEDA_CWD = "/Users/kethuda/work/nanaveda"
GENERIC_CWD = "/Users/kethuda"

DAY = 86400.0

# (profile, session_id, title) — the 13 real EvoPet members copied from the registry.
EVOPET_MEMBERS: List[Tuple[str, str, str]] = [
    ("azaraki", "20260911_174725_516587", "Write EvoPet levels and evolution contract doc"),
    ("kodekoot", "20260911_174648_519ec8", "Migrate 18 EvoPet tests to new curve"),
    ("kodekoot", "20260911_180310_22bb6b", "Wire pet evolution gates end-to-end"),
    ("kodekoot", "20260911_181144_418227", "Fix evopet gates roundtrip test failures"),
    ("azaraki", "20260911_182127_266200", "Update evopet docs after gate wiring"),
    ("kodekoot", "20260911_183001_67a02d", "Remove recovery glyph from floating HUD"),
    ("kodekoot", "20260911_184531_220bc5", "Fix gate-catalogue guard and derived stage load"),
    ("kodekoot", "20260911_185116_95d972", "Gate pet catalog on evolution capability"),
    ("kodekoot", "20260911_185735_5982ce", "Add normalize_ledger tests for Fix B"),
    ("kodekoot", "20260911_191000_67634a", "Fix desktop mirror overwrite of pet packages"),
    ("kodekoot", "20260911_191231_5b98c0", "Add auto upload switch to settings"),
    ("kodekoot", "20260911_192149_74b4e2", "Mirror-ownership rule proved with egg tests"),
    ("kodekoot", "20260911_192841_2c863a", "Build EvoPet project site with library page"),
]

# F5: a dispatch envelope with NO backing brief file (C2 must be the reason).
ENVELOPE_NO_FILE = (
    "---\n"
    "goal: reconcile the F5 fixture envelope\n"
    "team6_agent: kodekoot\n"
    "from: lugia\n"
    "---\n\n"
    "Body of the F5 envelope fixture; no backing file exists for this text.\n"
)

# EvoPet kodekoot members: an envelope-shaped brief with no backing file (C2).
EVOPET_KODEKOOT_ENVELOPE = (
    "---\n"
    "goal: advance the EvoPet pet workstream on the assigned slice\n"
    "team6_agent: kodekoot\n"
    "from: lugia\n"
    "ownership_matrix: /Users/kethuda/.hermes/profiles/lugia/cache/session-project-indexer/OWNERSHIP-MATRIX.md\n"
    "matrix_read_at_dispatch: yes\n"
    "---\n\n"
    "Bounded EvoPet worker brief (fixture).\n"
)

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
CREATE TABLE async_delegations (
  delegation_id TEXT PRIMARY KEY, origin_session TEXT NOT NULL,
  origin_session_id TEXT NOT NULL DEFAULT '', parent_session_id TEXT,
  state TEXT NOT NULL, dispatched_at REAL NOT NULL, updated_at REAL NOT NULL,
  task_json TEXT, event_json TEXT
);
"""


def brief_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _session(conn, sid, *, source="cli", title=None, title_source="derived", cwd=None,
             git=None, started=None, last=None, msgs=4, tools=2, parent=None,
             chat_type=None, display_name=None, hidden=0):
    conn.execute(
        "INSERT INTO sessions (id, source, title, title_source, cwd, git_repo_root, "
        "started_at, last_activity_at, message_count, tool_call_count, parent_session_id, "
        "chat_type, display_name, hidden) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (sid, source, title, title_source, cwd, git, started, last, msgs, tools, parent,
         chat_type, display_name, hidden))


def _messages(conn, sid, rows):
    """rows: [(role, content, timestamp), ...]"""
    for role, content, ts in rows:
        conn.execute(
            "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
            (sid, role, content, ts))


def build_audience_home(root: str, now: Optional[float] = None) -> str:
    """Create ``<root>/profiles/{azaraki,kodekoot,lugia}/state.db`` and return ``root``."""
    now = now if now is not None else time.time()
    os.makedirs(root, exist_ok=True)
    prof_dir = os.path.join(root, "profiles")
    os.makedirs(prof_dir, exist_ok=True)

    target_text = brief_text(AZARAKI_BRIEF)
    synthetic_text = brief_text(SYNTHETIC_BRIEF)

    for name in ("azaraki", "kodekoot", "lugia"):
        db = os.path.join(prof_dir, name, "state.db")
        os.makedirs(os.path.dirname(db), exist_ok=True)
        if os.path.exists(db):
            os.remove(db)
        conn = sqlite3.connect(db)
        conn.executescript(_SESSIONS_DDL)
        conn.execute("INSERT INTO schema_version (version) VALUES (30)")
        if name == "azaraki":
            _build_azaraki(conn, now, target_text, synthetic_text)
        elif name == "kodekoot":
            _build_kodekoot(conn, now)
        else:
            _build_lugia(conn, now)
        conn.commit()
        conn.close()
    return root


def _build_azaraki(conn, now, target_text, synthetic_text):
    # --- F2 + F8: the exact target session, and the EvoPet cluster ----------
    for profile, sid, title in EVOPET_MEMBERS:
        if profile != "azaraki":
            continue
        first = target_text if sid == TARGET_SESSION else EVOPET_KODEKOOT_ENVELOPE
        _session(conn, sid, title=title, cwd=EVOPET_CWD, git=EVOPET_CWD,
                 started=now - 2 * DAY, last=now - 2 * DAY)
        _messages(conn, sid, [
            ("user", first, now - 2 * DAY),
            ("assistant", "Wrote the EvoPet doc slice for this session.", now - 2 * DAY),
        ])

    # --- F3: a direct user session in a worker profile ----------------------
    _session(conn, DIRECT_AZARAKI, title="Compare AI model capabilities", title_source="user",
             cwd=GENERIC_CWD, started=now - DAY, last=now - DAY, msgs=4, tools=0)
    _messages(conn, DIRECT_AZARAKI, [
        ("user", "compare capabilities of the following models: glm 5.3 flash, mimo v2.5, "
                 "deepseek v4.1 flash, mimov2.5 pro", now - DAY),
        ("assistant", "Here is the comparison.", now - DAY),
    ])

    # --- F1: synthetic brief file match ------------------------------------
    _session(conn, "20260912_101010_f1f1f1", title="Synthetic brief fixture",
             cwd="/Users/kethuda/work/f1proj", git="/Users/kethuda/work/f1proj",
             started=now - DAY, last=now - DAY)
    _messages(conn, "20260912_101010_f1f1f1", [
        ("user", synthetic_text, now - DAY),
        ("assistant", "ack", now - DAY),
    ])

    # --- F5: envelope with no backing file ---------------------------------
    _session(conn, "20260912_111111_f5f5f5", title="Envelope fixture",
             cwd="/Users/kethuda/work/f5proj", git="/Users/kethuda/work/f5proj",
             started=now - DAY, last=now - DAY)
    _messages(conn, "20260912_111111_f5f5f5", [
        ("user", ENVELOPE_NO_FILE, now - DAY),
        ("assistant", "ack", now - DAY),
    ])

    # --- F6: automated sources --------------------------------------------
    _session(conn, "20260912_120000_cr0n1", source="cron", title="daily drift monitor",
             cwd=GENERIC_CWD, started=now - DAY, last=now - DAY, msgs=2, tools=0)
    _messages(conn, "20260912_120000_cr0n1", [("user", "run the drift check", now - DAY)])
    _session(conn, "20260912_130000_sub01", source="subagent", title="child worker",
             cwd=SOME_WORK_CWD, git=SOME_WORK_CWD, parent="20260912_120000_cr0n1",
             started=now - DAY, last=now - DAY, msgs=2, tools=1)
    _messages(conn, "20260912_130000_sub01", [("user", "do the subtask", now - DAY)])
    _session(conn, "20260912_140000_kan01", source="kanban", title="board dispatch",
             cwd=SOME_WORK_CWD, git=SOME_WORK_CWD, started=now - DAY, last=now - DAY,
             msgs=2, tools=0)
    _messages(conn, "20260912_140000_kan01", [("user", "advance the card", now - DAY)])

    # --- F7: group relays (chat_type NULL) + a real user session ------------
    for sid, title, text in (
        ("20260912_150000_grp01", "Nanaveda recipe relay",
         '[Group chat: "Nanaveda"] here is the recipe list'),
        ("20260912_150100_grp02", "Nanaveda recipe relay two",
         '[Group chat: "Nanaveda"] second relay'),
        ("20260912_150200_nan01", "Nanaveda recipe book outline",
         "let's build the nanaveda recipe book"),
    ):
        _session(conn, sid, title=title, cwd=NANAVEDA_CWD, git=NANAVEDA_CWD,
                 started=now - DAY, last=now - DAY)
        _messages(conn, sid, [("user", text, now - DAY),
                              ("assistant", "working on it", now - DAY)])


def _build_kodekoot(conn, now):
    # --- F8: the 11 kodekoot EvoPet members (envelope-shaped briefs) --------
    for profile, sid, title in EVOPET_MEMBERS:
        if profile != "kodekoot":
            continue
        _session(conn, sid, title=title, cwd=EVOPET_CWD, git=EVOPET_CWD,
                 started=now - 2 * DAY, last=now - 2 * DAY)
        _messages(conn, sid, [
            ("user", EVOPET_KODEKOOT_ENVELOPE, now - 2 * DAY),
            ("assistant", "advanced the EvoPet slice.", now - 2 * DAY),
        ])

    # --- F3: a direct user session in a worker profile ----------------------
    _session(conn, DIRECT_KODEKOOT, title="How to give me Meta API guy for Spark 1.3",
             title_source="user", cwd=GENERIC_CWD, started=now - DAY, last=now - DAY,
             msgs=5, tools=0)
    _messages(conn, DIRECT_KODEKOOT, [
        ("user", "i want to give you the meta api guy to put you on spark 1.3 - how?",
         now - DAY),
        ("assistant", "Here is how.", now - DAY),
    ])


def _build_lugia(conn, now):
    # --- F4: the user's EvoPet conversation (the anchor) --------------------
    _session(conn, ANCHOR_SESSION, title=ANCHOR_TITLE, title_source="user",
             cwd=GENERIC_CWD, started=now - 3 * DAY, last=now - 2 * DAY, msgs=84, tools=2)
    _messages(conn, ANCHOR_SESSION, [
        ("user", "handoff\nPackage is written, verified and committed.\n\n"
                 "/Users/kethuda/.hermes/eldunari/nexus/state/handoffs/2026-09-11-evopet.md",
         now - 3 * DAY),
        ("assistant", "Handoff package documented for the EvoPet work.", now - 2 * DAY),
    ])
    # async_delegations: the origin session dispatched the EvoPet worker chain.
    conn.execute(
        "INSERT INTO async_delegations (delegation_id, origin_session, origin_session_id, "
        "parent_session_id, state, dispatched_at, updated_at, task_json, event_json) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        ("deleg_fixture_1", ANCHOR_SESSION, ANCHOR_SESSION, ANCHOR_SESSION, "completed",
         now - 2 * DAY, now - 2 * DAY,
         '{"goal": "write the creator-facing EvoPet levels-and-evolution contract"}', None))
    conn.execute(
        "INSERT INTO async_delegations (delegation_id, origin_session, origin_session_id, "
        "parent_session_id, state, dispatched_at, updated_at, task_json, event_json) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        ("deleg_fixture_2", ANCHOR_SESSION, ANCHOR_SESSION, ANCHOR_SESSION, "completed",
         now - 2 * DAY, now - 2 * DAY,
         '{"goal": "finish the bounded local EvoPet runtime recovery"}', None))


# a non-generic work path used by the automated-source fixture rows
SOME_WORK_CWD = "/Users/kethuda/work/subwork"
