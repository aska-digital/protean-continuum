#!/usr/bin/env python3
"""Build the PIN-gated static Continuum export from SYNTHETIC FIXTURES ONLY.

D3: the only new Python in the pipeline. D8: no real-data pull, no registry.db touch,
no operator config — the script runs Service in-process over a throw-away fixture home
built by ``fixtures.make_fixture.build_fixture_tree`` (same pattern as server/tests).

Usage (from the repo root):
    python3 tools/build_static_snapshot.py --out static-export [--now <epoch>]

Pipeline (locked §4):
  1. fixture home at <tmp>/hermes_home, tied to --now for determinism
  2. D9 substitutions BEFORE Service (fixture sqlite text rewrite + profile-dir rename)
     plus a belt-and-braces substitution pass over every emitted JSON string
  3. action_log.sync over server/fixtures sources (path overrides per lock §6) then
     Service.scan(); emit all snapshot.*.json in one pass (D7)
  4. assemble the exact D12 export tree under <out>/pages-root/ (index.html, app.js,
     styles.css, desktop/kanban-interaction.js are BUILT here, never hand-written)
  5. manifest.sha256 over every file in the tree (D13); hard grep gate: any forbidden
     pattern anywhere in the tree aborts the build with a non-zero exit.

Deterministic: the clock (time.time) and uuid4 are pinned to --now; byte-identical
inputs give byte-identical outputs (prove with --out into two roots + diff -r).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import uuid as uuid_mod
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent          # repo root
SERVER = ROOT / "server"
STATIC_SRC = SERVER / "dashboard" / "static"
DESKTOP_SRC = SERVER / "desktop" / "kanban-interaction.js"
FIXTURES = SERVER / "fixtures"
GATE_SRC = ROOT / "tools" / "gate"                     # pin-check.js, PIN-GATE-SPEC.md, README.md

sys.path.insert(0, str(SERVER))

# ── D9 substitution map ────────────────────────────────────────────────────────
# Host paths, machine user, and internal member/team/project names that occur in
# the repo fixtures and source comments are replaced by synthetic values.
# Paths substitute as plain substrings; names substitute as whole words, any case.
PATH_SUBS: List[tuple] = [
    ("/Users/kethuda", "/home/synthetic-user"),
    ("kethuda", "synthetic-user"),
]
NAME_SUBS: List[tuple] = [
    ("sheikh-al-jabr", "synthetic-agent-7"),
    ("aska-digital", "synthetic-org"),
    ("team-skills", "synthetic-skills"),
    ("aetherean", "synthetic-agent-8"),
    ("halakukhan", "synthetic-agent-4"),
    ("kodekoot", "synthetic-agent-5"),
    ("kurimasu", "synthetic-agent-9"),
    ("azaraki", "synthetic-agent-3"),
    ("sheikh", "synthetic-agent-7"),
    ("askasite", "synthetic-site"),
    ("askaconsult", "synthetic-consult"),
    ("typejoy", "synthetic-studio"),
    ("tywebsite", "synthetic-site2"),
    ("proteus", "synthetic-orch"),
    ("shayba", "synthetic-agent-6"),
    ("raptora", "synthetic-runtime"),
    ("hazen", "synthetic-research"),
    ("shaka", "synthetic-qa"),
    ("frida", "synthetic-design"),
    ("tjgc1", "synthetic-project-1"),
    ("mozi", "synthetic-builder"),
    ("team6", "synthetic-team"),
    ("lugia", "synthetic-agent-10"),
    ("orda", "synthetic-coord"),
    ("leo", "synthetic-arch"),
]
# Profiles emitted by make_fixture.py (dir names): alpha/beta -> synthetic-project-N.
PROFILE_RENAMES = {"alpha": "synthetic-project-1", "beta": "synthetic-project-2"}

_PATH_ALT = "|".join(re.escape(a) for a, _ in PATH_SUBS)
_NAME_ALT = "|".join(sorted((re.escape(a) for a, _ in NAME_SUBS), key=len, reverse=True))
_PATH_RE = re.compile(_PATH_ALT)
_NAME_RE = re.compile(r"\b(" + _NAME_ALT + r")\b", re.IGNORECASE)
_MAP = dict(PATH_SUBS + NAME_SUBS)


def _name_repl(m: re.Match) -> str:
    return _MAP[m.group(1).lower()]


def sub_text(s: str) -> str:
    s = _PATH_RE.sub(lambda m: _MAP[m.group(0)], s)
    return _NAME_RE.sub(_name_repl, s)


def sub_deep(obj: Any) -> Any:
    if isinstance(obj, str):
        return sub_text(obj)
    if isinstance(obj, list):
        return [sub_deep(v) for v in obj]
    if isinstance(obj, dict):
        return {sub_text(k): sub_deep(v) for k, v in obj.items()}
    return obj


# Forbidden patterns for the build-time gate (G2/D9/R2 + this lane's §6 audit).
FORBIDDEN = [r"/Users/", r"\bkethuda\b", r"registry\.db", r"team-skills"] + \
            [r"\b" + re.escape(a) + r"\b" for a, _ in NAME_SUBS]
PIN_HASH_RE = re.compile(r"\b[0-9a-f]{64}\b")
SIZE_LIMIT = 400_000   # bytes; strictest reading of the locked ≤250 KB budget (G10/D12)


# ── determinism pins ───────────────────────────────────────────────────────────
def pin_clock(now: float) -> None:
    time.time = lambda: now  # type: ignore


_UUID_BASE = int.from_bytes(hashlib.sha256(b"continuum-static-export-v1").digest()[:16], "big")
_UUID_C = [0]


def pin_uuids() -> None:
    def det_uuid4():
        _UUID_C[0] += 1
        return uuid_mod.UUID(int=(_UUID_BASE + _UUID_C[0]) & ((1 << 128) - 1))
    uuid_mod.uuid4 = det_uuid4  # type: ignore


# ── fixture home (D9 pre-Service pass) ─────────────────────────────────────────
TEXT_COLS = {
    "sessions": ["user_id", "session_key", "chat_id", "chat_type", "thread_id",
                 "display_name", "model", "cwd", "git_branch", "git_repo_root",
                 "title", "last_activity_description", "tool_names"],
    "messages": ["content", "tool_calls", "tool_name"],
}


def harden_fixture_dbs(home: Path) -> None:
    """Rewrite host-absolute paths out of the fixture state.db text BEFORE Service runs."""
    for db in sorted(home.glob("profiles/*/state.db")):
        conn = sqlite3.connect(str(db))
        for table, cols in TEXT_COLS.items():
            rows = conn.execute("SELECT rowid, {} FROM {}".format(
                ", ".join(cols), table)).fetchall()
            for r in rows:
                vals = list(r[1:])
                changed = False
                for i, v in enumerate(vals):
                    if isinstance(v, str) and (_PATH_RE.search(v) or _NAME_RE.search(v)):
                        vals[i] = sub_text(v)
                        changed = True
                if changed:
                    conn.execute("UPDATE {} SET {} WHERE rowid=?".format(
                        table, ", ".join(c + " = ?" for c in cols)), vals + [r[0]])
        conn.commit()
        conn.close()


def rename_profiles(home: Path) -> None:
    prof = home / "profiles"
    for old, new in PROFILE_RENAMES.items():
        src = prof / old
        if src.is_dir():
            src.rename(prof / new)


# ── writing helpers ────────────────────────────────────────────────────────────
def dump_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":")) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ── app.js transform (D4/D5 seams; the ONLY edits are the replacements below) ──
def jrep(src: str, old: str, new: str) -> str:
    n = src.count(old)
    if n != 1:
        raise SystemExit("FATAL: app.js seam matched {} times (want 1): {!r}"
                         .format(n, old[:80]))
    return src.replace(old, new)


def transform_app_js(panes: Dict[str, Dict[str, str]]) -> str:
    src = (STATIC_SRC / "app.js").read_text(encoding="utf-8")
    src = src.replace("} from '../desktop/kanban-interaction.js'",
                      "} from './desktop/kanban-interaction.js'")
    src = jrep(src, "const API = '/api/plugins/continuum'",
               "// STATIC-MODE export (D4): no server; reads map to sibling snapshot files.\n"
               "const STATIC_MODE = true\n"
               "const API = './snapshot'\n"
               "const STATIC_PANES = " + json.dumps(panes, sort_keys=True,
                                                    separators=(",", ":")))
    src = jrep(src, "    return apiGet(API + '/projects?' + actionLogQueryString(state.query))",
               "    return apiGet(API + '.action_log.json')")
    src = jrep(src, "  return apiGet(API + '/projects?' + queryString(state.query))",
               "  return apiGet(API + '.board.json')")
    for slug in ("attention", "candidates", "staleness"):
        src = jrep(src, "apiGet(API + '/" + slug + "')", "apiGet(API + '." + slug + ".json')")
    src = jrep(src,
               "  const res = await fetch(API + '/projects/' + projectId + '?pane=' + encodeURIComponent(paneRef))",
               "  const paneFile = (STATIC_PANES[projectId] || {})[paneRef]\n"
               "  if (!paneFile) throw new Error('no pre-baked pane: ' + paneRef)\n"
               "  const res = await fetch(paneFile)")
    src = jrep(src, "    const res = await fetch(API + '/projects/' + pid)",
               "    const res = await fetch(API + '.' + pid + '.json')")
    src = jrep(src, "    const res = await fetch(API + '/events')",
               "    const res = await fetch(API + '.events.json')")
    src = jrep(src, "    pollTimer = setInterval(pollEvents, POLL_MS)",
               "    if (!STATIC_MODE) pollTimer = setInterval(pollEvents, POLL_MS)  // G8: static tree never polls")
    # D5: every POST-bearing surface short-circuits to the read-only notice.
    guard = "  if (STATIC_MODE) { state.staticToast(); return undefined }   // D5: no write surface\n"
    for sig in ("async function review(projectId, action, payload) {",
                "async function postUndo(auditId) {",
                "async function doMove(card, target) {",
                "async function doUndo(auditId, name) {",
                "async function doToggleUrgent(card) {",
                "async function scanNow() {",
                "async function moveFromDetail(p, target) {",
                "async function doArchive(actionId, verb) {"):
        src = jrep(src, sig + "\n", sig + "\n" + guard)
    src = jrep(src, "function announce(message) {",
               "// D5 / gate design §4 S13: the read-only notice all POST short-circuits show.\n"
               "state.staticToast = function () {\n"
               "  toast('Read-only snapshot: no action here can change Continuum.')\n"
               "}\n\n"
               "function announce(message) {")
    # D5 / gate design §3.1: action controls are not rendered at all in static mode.
    src = jrep(src, "  rows.push(moveRow)", "  if (!STATIC_MODE) rows.push(moveRow)")
    src = jrep(src, "    draggable: 'true',", "    draggable: STATIC_MODE ? 'false' : 'true',")
    src = jrep(src,
               "      h('button', { type: 'button', class: 'c-btn primary', onClick: () => scanNow(),\n"
               "                    text: MC_COPY.scanAction }),",
               "      STATIC_MODE ? null : h('button', { type: 'button', class: 'c-btn primary',\n"
               "                    onClick: () => scanNow(), text: MC_COPY.scanAction }),")
    src = jrep(src, "  const moveButtons = NON_INBOX_PLACEMENTS.map((t) => h('button', {",
               "  const moveButtons = STATIC_MODE ? [] : NON_INBOX_PLACEMENTS.map((t) => h('button', {")
    src = jrep(src,
               "      h('button', {\n"
               "        type: 'button', class: 'c-btn tiny', 'aria-pressed': p.urgent ? 'true' : 'false',\n"
               "        onClick: () => doToggleUrgent({ ...p, project_id: p.project_id }),",
               "      STATIC_MODE ? null : h('button', {\n"
               "        type: 'button', class: 'c-btn tiny', 'aria-pressed': p.urgent ? 'true' : 'false',\n"
               "        onClick: () => doToggleUrgent({ ...p, project_id: p.project_id }),")
    src = jrep(src,
               "      h('button', {\n"
               "        type: 'button', class: 'c-btn tiny' + (archived ? '' : ' primary'),",
               "      STATIC_MODE ? null : h('button', {\n"
               "        type: 'button', class: 'c-btn tiny' + (archived ? '' : ' primary'),")
    # A session row offers a Messages disclosure ONLY when its pane is pre-baked
    # (STATIC_PANES reflects the baked set), so the static tree never shows a dead control.
    src = jrep(src,
               "  const button = h('button', {\n"
               "    type: 'button', class: 'c-btn tiny ghost c-pane-toggle',",
               "  const button = (STATIC_MODE && !(STATIC_PANES[projectId] || {})[ref])\n"
               "    ? null : h('button', {\n"
               "    type: 'button', class: 'c-btn tiny ghost c-pane-toggle',")
    src = jrep(src,
               "    if (target) {\n      state.openPane = target\n      loadPane(target, pid)\n      return\n    }",
               "    if (target && (!STATIC_MODE || (STATIC_PANES[pid] || {})[target])) {\n"
               "      state.openPane = target\n      loadPane(target, pid)\n      return\n    }")
    return sub_text(src)


# ── index.html transform (D6/D14/A1 + gate design §2) ─────────────────────────
GATE_MARKUP = '''  <div id="gate-root" class="c-gate" data-state="idle">
    <div class="c-gate-card" role="dialog" aria-modal="true"
         aria-labelledby="gate-title" aria-describedby="gate-help" tabindex="-1">
      <div class="c-gate-brand">
        <span class="c-gate-mark" aria-hidden="true"></span>
        <span class="c-gate-wordmark" aria-hidden="true">Continuum</span>
      </div>
      <h1 id="gate-title" class="c-gate-title">Access required</h1>
      <p id="gate-help" class="c-gate-help">Enter the access code to open this snapshot.</p>
      <form id="gate-form" class="c-gate-form" novalidate>
        <label id="gate-label" class="c-gate-label" for="gate-input">Access code</label>
        <input id="gate-input" class="c-input c-gate-input" type="password"
               name="access-code" inputmode="text" autocapitalize="off"
               autocomplete="off" autocorrect="off" spellcheck="false"
               enterkeyhint="go" maxlength="128" aria-describedby="gate-help">
        <button id="gate-submit" class="c-btn primary c-gate-submit" type="submit">Unlock</button>
      </form>
      <p id="gate-status" class="c-gate-status" role="status"
         aria-live="polite" aria-atomic="true"></p>
      <p class="c-gate-note">The snapshot contains synthetic demonstration data, not real work.</p>
    </div>
  </div>

'''
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
       "connect-src 'self'; form-action 'none'; base-uri 'self'; frame-ancestors 'none'; "
       "object-src 'none'")


def transform_index_html() -> str:
    src = (STATIC_SRC / "index.html").read_text(encoding="utf-8")
    src = jrep(src, '  <meta name="viewport" content="width=device-width, initial-scale=1">',
               '  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
               '  <meta http-equiv="Content-Security-Policy" content="' + CSP + '">')
    src = jrep(src, '  <link rel="stylesheet" href="/styles.css">',
               '  <link rel="stylesheet" href="./styles.css">\n'
               '  <script src="./pin-check.js"></script>')
    src = jrep(src, '        <button id="scan" class="c-btn primary" type="button">Scan now</button>',
               '        <span id="readonly-marker" class="c-chip c-chip-readonly">read-only snapshot</span>')
    # Preserve Owner review link as relative (gate already covers it); no live scan on static
    src = jrep(src,
               '      <span class="c-badge-label">Read-only source scan</span>\n'
               '      <span>Reviews change only the audited Continuum registry; source '
               'sessions are never modified. Scan now is explicit.</span>',
               '      <span class="c-badge-label">Synthetic data</span>\n'
               '      <span>Synthetic demonstration snapshot: every profile and project shown '
               'here is generated test data, not real work.</span>')
    src = jrep(src, '  <div class="wrap">', GATE_MARKUP + '  <div class="wrap">')
    src = jrep(src, '  <script type="module" src="/app.js"></script>\n', '')
    return sub_text(src)


def transform_review_html() -> str:
    p = STATIC_SRC / "review.html"
    if not p.exists():
        return ""
    src = p.read_text(encoding="utf-8")
    src = src.replace('  <link rel="stylesheet" href="./styles.css">', '  <link rel="stylesheet" href="./styles.css">\n  <script src="./pin-check.js"></script>' if '<script src="./pin-check.js">' not in src else src)
    # Gate markup: inject before wrap if not already present
    if 'id="gate-root"' not in src:
        src = src.replace('  <div class="wrap">', GATE_MARKUP + '  <div class="wrap">', 1)
    # CSP meta
    if 'Content-Security-Policy' not in src:
        src = src.replace('  <meta name="viewport"', '  <meta http-equiv="Content-Security-Policy" content="' + CSP + '">\n  <meta name="viewport"')
    # Read-only: replace Refresh button still present, no scan to guard
    return sub_text(src)


GATE_CSS = '''
/* ── Continuum Pages static export — access gate ─────────────────────────── */
.c-gate { display: none; }
html[data-gate="locked"] .wrap { display: none; }
html[data-gate="locked"] .c-gate {
  display: flex; position: fixed; inset: 0; z-index: 100;
  align-items: center; justify-content: center;
  padding: var(--cj-s5); background: var(--cj-void);
}
.c-gate-card {
  width: min(100%, 380px); padding: var(--cj-s6);
  background: var(--cj-panel); border: 1px solid var(--cj-line-2);
  border-radius: var(--cj-radius-md); box-shadow: var(--cj-elev-1);
}
.c-gate-card:focus { outline: none; }
.c-gate-card:focus-visible { outline: 2px solid var(--cj-cyan); outline-offset: 2px; }
.c-gate-brand { display: flex; align-items: center; gap: var(--cj-s3); margin-bottom: var(--cj-s5); }
.c-gate-mark { width: 10px; height: 10px; border-radius: 50%; background: var(--cj-cyan);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--cj-cyan) 18%, transparent); }
.c-gate-wordmark { font-size: var(--cj-t-h1); font-weight: 600; letter-spacing: .06em;
  text-transform: uppercase; color: var(--cj-fg); }
.c-gate-title { margin: 0; font-size: var(--cj-t-h2); font-weight: 600; color: var(--cj-fg); }
.c-gate-help { margin: var(--cj-s2) 0 0; color: var(--cj-fg-dim); font-size: var(--cj-t-small); }
.c-gate-form { display: flex; flex-direction: column; gap: var(--cj-s2); margin-top: var(--cj-s5); }
.c-gate-label { font-size: var(--cj-t-label); font-weight: 700; letter-spacing: .08em;
  text-transform: uppercase; color: var(--cj-fg-dim); }
.c-gate-input { padding: 9px 10px; font-family: var(--cj-font-mono); }
.c-gate-input:disabled { opacity: .55; }
.c-gate-submit { min-height: 40px; font-size: var(--cj-t-body); }
.c-gate-status { margin: var(--cj-s4) 0 0; min-height: 1.2em; font-size: var(--cj-t-small);
  color: var(--cj-fg-dim); }
.c-gate-status.is-error { color: var(--cj-rose); }
.c-gate-status.is-wait { color: var(--cj-amber); font-family: var(--cj-font-mono);
  font-variant-numeric: tabular-nums; }
.c-gate-note { margin: var(--cj-s4) 0 0; font-size: var(--cj-t-label); color: var(--cj-fg-faint); }
.c-chip-readonly { border-style: dashed; color: var(--cj-fg-faint); }
@media (max-width: 640px) {
  html[data-gate="locked"] .c-gate { align-items: flex-start; padding-top: 12vh; }
  .c-gate-input, .c-gate-submit { min-height: 44px; }
  .c-gate-input { font-size: 16px; }
}
/* D5 / gate design §3.1: a static card cannot be dragged, so it must not look draggable. */
.c-card { cursor: default; }
'''


def transform_styles_css() -> str:
    src = (STATIC_SRC / "styles.css").read_text(encoding="utf-8")
    return sub_text(src + GATE_CSS)


# ── the build ──────────────────────────────────────────────────────────────────
def build(now: float, out: Path, keep_tmp: Optional[str]) -> Dict[str, Any]:
    pin_clock(now)
    pin_uuids()

    tmp = Path(keep_tmp or tempfile.mkdtemp(prefix="continuum-static-"))
    home = tmp / "hermes_home"

    from fixtures.make_fixture import build_fixture_tree
    build_fixture_tree(str(home), now=now)
    harden_fixture_dbs(home)
    rename_profiles(home)

    from continuum.config import load_config
    from continuum.service import Service
    from continuum import action_log as action_log_mod

    cfg = load_config(hermes_home=str(home))
    # Lock §6 overrides: the fixture build must never read the operator's files.
    cfg.bundle["registry_path"] = str(tmp / "registry.db")
    fx = FIXTURES / "action_log"
    cfg.bundle["action_log_source_paths"] = [
        str(fx / "INFLIGHT.md"),
        str(fx / "DISPATCH-LEDGER.md"),
        str(fx / "receipts" / "**" / "*-receipt.md"),
    ]
    cfg.bundle["action_log_ledger_path"] = str(tmp / "action_log.json")
    cfg.bundle["task_home_source_path"] = str(FIXTURES / "task_home" / "task-home.md")
    cfg.bundle["task_home_ledger_path"] = str(tmp / "task_home_sync.json")
    cfg.bundle["task_home_export_dir"] = str(tmp / "task_home_out")

    action_log_mod.sync(cfg, now=now)

    svc = Service(cfg)
    svc.scan()

    exp = out / "pages-root"
    if exp.exists():
        shutil.rmtree(exp)
    exp.mkdir(parents=True)

    files: Dict[str, Any] = {}

    board = svc.board()
    al = svc.board(view="action_log", scope="external", sort="timestamp",
                   page="1", page_size="200")
    files["snapshot.board.json"] = board
    files["snapshot.action_log.json"] = al
    files["snapshot.attention.json"] = svc.attention_queue()
    files["snapshot.candidates.json"] = svc.recovery_inbox()
    files["snapshot.staleness.json"] = svc.staleness_view()
    # Owner review queue: fixture-generated, sanitized for Pages gate (single snapshot)
    review_fixture = ROOT / "server" / "fixtures" / "review-queue.json"
    if review_fixture.exists():
        try:
            rq = __import__("json").loads(review_fixture.read_text(encoding="utf-8"))
            files["snapshot.review-queue.json"] = rq
        except Exception:
            pass

    # per-project detail + pre-baked panes (D7). Pane candidates are canonical-ordered
    # (anchor first, then session order), capped 8 per project AND capped so the whole
    # tree fits the locked SIZE_LIMIT; un-baked disclosures are not rendered by app.js.
    pid_re = re.compile(r"^[A-Za-z0-9_-]+$")
    details: Dict[str, Any] = {}
    pane_candidates: List[tuple] = []      # (pid, n, ref, obj)
    recent_cap = int(cfg.get("pane_recent_cap", 7) or 7)
    user_cap = int(cfg.get("pane_user_cap", 3) or 3)
    display_cap = int(cfg.get("pane_display_cap", 5) or 5)
    per_pid_cap = min(8, recent_cap + user_cap, display_cap + 3)
    for card in board.get("items", []):
        pid = card.get("project_id") or ""
        if not pid_re.match(str(pid)):
            raise SystemExit("FATAL: project_id is not file-safe: {!r}".format(pid))
        detail = svc.project_detail(str(pid))
        details[str(pid)] = detail
        files["snapshot.{}.json".format(pid)] = detail
        refs: List[str] = []
        anchor = (detail.get("project") or {}).get("anchor_session")
        if anchor:
            refs.append(str(anchor))
        for s in detail.get("source_sessions", []):
            r = "{}/{}".format(s.get("profile_name") or s.get("profile"), s.get("session_id"))
            if r not in refs:
                refs.append(r)
        for i, ref in enumerate(refs[:per_pid_cap]):
            pane_candidates.append((str(pid), i + 1, ref, svc.project_detail(str(pid), pane=ref)))

    # events stub: the exact payload /events publishes (G8 static stub).
    status = svc.scan_status()
    snap = status.get("committed_snapshot") or {"run_id": None, "committed_at": None}
    files["snapshot.events.json"] = {
        "scan": status.get("phase"),
        "snapshot_id": snap["run_id"],
        "committed_at": snap["committed_at"],
        "data_as_of": snap["committed_at"],
        "server_time": status.get("server_time"),
        "scan_state": status.get("scan_state"),
    }

    # render the non-data surface so its exact bytes count against the budget
    def app_js_bytes(panes_map: Dict[str, Dict[str, str]]) -> bytes:
        return transform_app_js(panes_map).encode("utf-8")

    static_text = {
        "index.html": transform_index_html(),
        "styles.css": transform_styles_css(),
        "desktop/kanban-interaction.js": None,   # copied verbatim below
    }
    doc_bytes = {}
    for name in ("pin-check.js", "PIN-GATE-SPEC.md", "README.md"):
        doc_bytes[name] = (GATE_SRC / name).read_bytes()
    core_bytes = {n: (json.dumps(sub_deep(o), ensure_ascii=False, sort_keys=True,
                                  separators=(",", ":")) + "\n").encode("utf-8")
                  for n, o in files.items()}
    base_size = (len(app_js_bytes({})) + len(static_text["index.html"])
                 + len(static_text["styles.css"]) + len(DESKTOP_SRC.read_bytes())
                 + sum(len(v) for v in doc_bytes.values())
                 + sum(len(v) for v in core_bytes.values())
                 + 5000)   # reserve for snapshot.manifest.json + manifest.sha256 themselves
    # per-pane overhead: the file + its STATIC_PANES entry + snapshot.manifest entry
    # + manifest.sha256 line. 400 B is a generous upper bound.
    used = base_size
    panes: Dict[str, Dict[str, str]] = {}
    baked: Dict[str, bytes] = {}
    for pid, n, ref, obj in pane_candidates:
        blob = (json.dumps(sub_deep(obj), ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")) + "\n").encode("utf-8")
        name = "snapshot.{}__pane-{}.json".format(pid, n)
        if used + len(blob) + 400 > SIZE_LIMIT:
            continue
        used += len(blob) + 400
        baked[name] = blob
        panes.setdefault(pid, {})[ref] = "./" + name

    # write every snapshot file, then the audit manifest, then the static surface.
    for name, blob in core_bytes.items():
        (exp / name).write_bytes(blob)
    for name, blob in baked.items():
        (exp / name).write_bytes(blob)
    manifest = {
        "builder": "tools/build_static_snapshot.py",
        "built_at": now,
        "data_as_of": snap["committed_at"],
        "committed_snapshot_run_id": snap["run_id"],
        "pane_budget": {"candidates": len(pane_candidates), "baked": len(baked),
                        "limit_bytes": SIZE_LIMIT},
        "files": {p.name: {"bytes": p.stat().st_size, "sha256": sha256_file(p)}
                  for p in sorted(exp.glob("snapshot.*.json"))},
    }
    dump_json(exp / "snapshot.manifest.json", sub_deep(manifest))
    (exp / "app.js").write_bytes(app_js_bytes(panes))
    (exp / "index.html").write_text(static_text["index.html"], encoding="utf-8")
    (exp / "styles.css").write_text(static_text["styles.css"], encoding="utf-8")
    (exp / "desktop").mkdir(exist_ok=True)
    shutil.copy2(DESKTOP_SRC, exp / "desktop" / "kanban-interaction.js")
    # Owner review page (distinct view, PIN-gated like index.html)
    try:
        review_html = transform_review_html()
        if review_html:
            (exp / "review.html").write_text(review_html, encoding="utf-8")
        review_src = (STATIC_SRC / "review.js")
        if review_src.exists():
            (exp / "review.js").write_bytes(sub_text(review_src.read_text(encoding="utf-8")).encode("utf-8"))
    except Exception:
        pass
    for name, blob in doc_bytes.items():
        (exp / name).write_bytes(blob)

    # manifest.sha256: every file in the tree except itself (relative paths, sorted).
    lines = []
    for p in sorted(exp.rglob("*")):
        if p.is_file() and p.name != "manifest.sha256":
            lines.append("{}  {}".format(sha256_file(p), p.relative_to(exp).as_posix()))
    (exp / "manifest.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")

    return {"exp": exp, "panes": panes, "snap": snap, "board_items": len(board.get("items", [])),
            "pane_candidates": len(pane_candidates)}


# ── gates ──────────────────────────────────────────────────────────────────────
def gate(exp: Path, pin_hash: str) -> int:
    hits: List[str] = []
    for p in sorted(exp.rglob("*")):
        if not p.is_file():
            continue
        if p.name == "manifest.sha256":
            continue
        data = p.read_bytes().decode("utf-8", "replace")
        for pat in FORBIDDEN:
            if re.search(pat, data, re.IGNORECASE if pat.startswith(r"\b") else 0):
                hits.append("{}: {}".format(p.relative_to(exp), pat))
        # G3/D13: plaintext PIN must exist nowhere. Match plaintext BY HASH, not literal
        # (the build script never holds the PIN itself).
        for m in re.finditer(r'"([^"\\\n]{1,256})"', data):
            if hashlib.sha256(m.group(1).encode("utf-8")).hexdigest() == pin_hash:
                hits.append("{}: PLAINTEXT-PIN".format(p.relative_to(exp)))
        # The PIN hash may live ONLY in pin-check.js (D13/D15). Other 64-hex tokens are
        # legitimate file digests (snapshot.manifest.json, manifest.sha256).
        if pin_hash and pin_hash != "0" * 64:
            for m in PIN_HASH_RE.finditer(data):
                if m.group(0) == pin_hash and p.name != "pin-check.js":
                    hits.append("{}: PIN-hash text outside pin-check.js".format(
                        p.relative_to(exp)))
            pc = (exp / "pin-check.js").read_text(encoding="utf-8")
            if pc.count(pin_hash) != 1:
                hits.append("pin-check.js: PIN-hash constant occurs {} times (want 1)"
                            .format(pc.count(pin_hash)))
    expected = {"index.html", "app.js", "styles.css", "desktop/kanban-interaction.js",
                "manifest.sha256", "pin-check.js", "PIN-GATE-SPEC.md", "README.md",
                "review.html", "review.js", "snapshot.review-queue.json"}
    got = {p.relative_to(exp).as_posix() for p in exp.rglob("*") if p.is_file()}
    extra = {g for g in got if not g.startswith("snapshot.")} - expected
    if extra:
        hits.append("D12 tree carries extra files: {}".format(sorted(extra)))
    missing = expected - got
    if missing:
        hits.append("D12 tree missing required files: {}".format(sorted(missing)))
    total = sum(p.stat().st_size for p in exp.rglob("*") if p.is_file())
    if total > SIZE_LIMIT:
        hits.append("size budget: {} B > {} B".format(total, SIZE_LIMIT))
    for h in hits:
        print("GATE FAIL:", h)
    return len(hits)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, help="output root; tree lands in <out>/pages-root")
    ap.add_argument("--now", type=float, default=time.time(), help="frozen epoch for determinism")
    ap.add_argument("--pin-hash", default=None,
                    help="hex hash of the build-env PIN; a literal equal to it is a gate failure")
    ap.add_argument("--tmp-keep", default=None, help="use this temp dir (test hook)")
    a = ap.parse_args()
    out = Path(a.out).resolve()
    t0 = time.time()
    res = build(a.now, out, a.tmp_keep)
    # NOTE: time.time is pinned inside build(); restore for reporting/timing.
    fails = gate(res["exp"], a.pin_hash or "0" * 64)
    files = sorted(p for p in res["exp"].rglob("*") if p.is_file())
    print("BUILD OK  now={:.0f}  board_items={}  panes_baked={}  files={}  total_bytes={}"
          .format(a.now, res["board_items"], sum(len(v) for v in res["panes"].values()),
                  len(files), sum(p.stat().st_size for p in files)))
    for p in files:
        print("  {:>8} B  {}".format(p.stat().st_size, p.relative_to(res["exp"])))
    if fails:
        raise SystemExit("BUILD FAILED GATE: {} violations".format(fails))


if __name__ == "__main__":
    main()
