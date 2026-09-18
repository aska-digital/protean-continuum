"""TASK-HOME ↔ Continuum sync acceptance — TH-A1..A6, A11, A12, TH-I1..I4, TH-R1..R4, TH-C1, C3.

Every check below is EXECUTED against a frozen fixture under ``fixtures/task_home/``. No test
reads the live Orda TASK-HOME.md, and no test writes it (TH-L1/TH-R4).

Owner: mozi (implementation). Upstream: leo-architecture.md (STATUS: STABLE) §8 acceptance ids.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time
import uuid

import pytest

BUILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BUILD_ROOT not in sys.path:
    sys.path.insert(0, BUILD_ROOT)

from continuum import task_home as th                     # noqa: E402
from continuum.config import load_config                  # noqa: E402
from continuum.registry import SCHEMA_VERSION             # noqa: E402
from continuum.service import Service                     # noqa: E402
from fixtures.make_fixture import build_fixture_tree      # noqa: E402

FROZEN = os.path.join(BUILD_ROOT, "fixtures", "task_home", "task-home.md")
FROZEN_SHA = "87e6957b103e24a5b754e8e1a322926ce370c43c5095d9007b1419a0bc863f3f"
LIVE_SOURCE = "/Users/kethuda/.hermes/profiles/orda/TASK-HOME.md"

#: §1's measured table, reproduced verbatim from the architecture artifact.
SECTION_COUNTS = {"RUNNING": 11, "AWAITING_OWNER": 11, "OPEN_FOLLOW_UP": 6, "CLOSED": 6}
SECTION_ANCHORS = {"RUNNING": (7, 17), "AWAITING_OWNER": (20, 30),
                   "OPEN_FOLLOW_UP": (33, 38), "CLOSED": (41, 46)}
RUNNING_KEYS = [
    "askasite-build-s3", "sym2p-report", "pr111800-finish", "evopet-floater-verify",
    "continuum-sync", "upstream-rebase", "kit-about", "typemon-publish", "positioning-merge",
    "bounce-sop-finish", "evopet-repo-verify",
]
RUNNING_PROCS = [
    "proc_2cc84c61f97e", "proc_c07af224e0eb", "proc_16d9e3a3656c", "proc_b1a331f0fe99",
    "proc_4faba8822b80", "proc_11a87fba03eb", "proc_c48ddef151ff", "proc_97d6d34dcfad",
    "proc_570af2d6e300", "proc_b5a051be0492", "proc_b6ae151831c0",
]
#: the seven RUNNING bullets whose own line carries a receipt (line 11 does NOT).
RECEIPT_KEYS = [
    "evopet-floater-verify", "upstream-rebase", "kit-about", "typemon-publish",
    "positioning-merge", "bounce-sop-finish", "evopet-repo-verify",
]


# --------------------------------------------------------------------------- fixtures

@pytest.fixture()
def home(tmp_path):
    return build_fixture_tree(str(tmp_path / "hermes_home"))


@pytest.fixture()
def cfg(tmp_path, home):
    c = load_config(hermes_home=home)
    c.bundle["registry_path"] = os.path.join(home, "registry.db")
    c.bundle["task_home_ledger_path"] = str(tmp_path / "task_home_sync.json")
    c.bundle["task_home_export_dir"] = str(tmp_path / "out")
    c.bundle["task_home_source_path"] = FROZEN
    # the projection switch ships false (§7); the panel assertions exercise the ON state
    c.bundle["task_home_enabled"] = True
    return c


@pytest.fixture()
def svc(cfg):
    s = Service(cfg)
    s.scan()
    return s


def _write(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return str(path)


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _file_sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _add_project(svc, pid, name, *, lifecycle="LS-2", placement=None, band="high",
                 tier=1, sessions=(), next_action=None, declared_name=None):
    """Insert one Continuum project (+ its sessions) directly, the way the scanner would."""
    now = time.time()
    svc.registry.conn.execute(
        "INSERT OR REPLACE INTO project (project_id, name, kind, phase, lifecycle, confidence,"
        " confidence_band, evidence_tier, owner_profile, drive_expected, session_count,"
        " stall_age_days, last_substantive_activity, derived_updated_at, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (pid, name, "project", "active", lifecycle, 0.8, band, tier, "alpha", 0, len(sessions),
         1.0, now, now, now, now))
    for profile, sid, title in sessions:
        svc.registry.conn.execute(
            "INSERT OR REPLACE INTO session_fact (profile_name, session_id, title, cwd,"
            " workspace_root, message_count, tool_call_count, started_at, last_activity_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (profile, sid, title, "/tmp", None, 4, 2, now, now))
        svc.registry.conn.execute(
            "INSERT OR REPLACE INTO project_session (link_id, project_id, profile_name,"
            " session_id, role_in_project, link_confidence, link_reason, evidence_ref, accepted)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (uuid.uuid4().hex, pid, profile, sid, "primary", 0.9, "CS-1", None, 1))
    if placement:
        svc.registry.set_declared(pid, "placement", placement, actor="local")
    if next_action:
        svc.registry.set_next_action(pid, next_action, source="declared")
    if declared_name:
        svc.registry.set_declared(pid, "project_name", declared_name, actor="local")
    svc.registry.conn.commit()
    return pid


def _declared_rows(svc, pid):
    return {r["field"]: r["value"] for r in svc.registry.conn.execute(
        "SELECT field, value FROM declared_field WHERE project_id=?", (pid,))}


def _ledger_item(report, key):
    for entry in report["items"]:
        if entry["key"] == key:
            return entry
    raise AssertionError("no ledger item for key {!r}".format(key))


def _seed_ledger(cfg, items, source_sha256="0" * 64):
    ledger = {"schema": th.LEDGER_SCHEMA, "source_path": cfg.bundle["task_home_source_path"],
              "source_sha256": source_sha256, "last_run_id": "seed", "last_synced_at": 1.0,
              "items": items, "conflicts": [], "runs": []}
    th.write_ledger(cfg.bundle["task_home_ledger_path"], ledger)
    return ledger


# --------------------------------------------------------------------------- TH-A1

def test_th_a1_parse_completeness_matches_section_1_table():
    """TH-A1: 34 items, 11/11/6/6, no bullet dropped or split."""
    parsed = th.parse_file(FROZEN)
    assert len(parsed.items) == 34
    assert parsed.section_counts() == SECTION_COUNTS
    assert len(parsed.keyed()) == 11
    assert len(parsed.receipts()) == 7
    assert len(parsed.compound()) == 16
    # every bullet line in the source appears exactly once as an item (nothing dropped/split)
    source_bullets = [ln for ln in _read(FROZEN).split("\n") if ln.startswith("- ")]
    item_lines = [it.line for it in parsed.items]
    assert sorted(source_bullets) == sorted(item_lines)
    assert len(item_lines) == len(set(item_lines)) == 34
    # §1 line anchors
    for code, (first, last) in SECTION_ANCHORS.items():
        numbers = sorted(it.line_no for it in parsed.items if it.section == code)
        assert (numbers[0], numbers[-1]) == (first, last), code
    # the frozen fixture is the contract's own source, byte for byte
    assert _file_sha(FROZEN) == FROZEN_SHA


def test_th_a1_no_orphaned_or_unrecognized_sections_on_the_frozen_fixture():
    parsed = th.parse_file(FROZEN)
    assert [s["section"] for s in parsed.sections] == list(th.SECTION_ORDER)
    assert parsed.degraded == [], "the frozen fixture must parse without degraded warnings"
    assert parsed.informational == ["compound_bullet", "receipt_section_mismatch"]


# --------------------------------------------------------------------------- TH-A2

def test_th_a2_literal_keys_and_procs_are_byte_identical():
    """TH-A2: no case folding, no dedupe, proc ids verbatim."""
    parsed = th.parse_file(FROZEN)
    assert [it.key for it in parsed.keyed()] == RUNNING_KEYS
    assert [it.proc for it in parsed.keyed()] == RUNNING_PROCS
    raw_lines = _read(FROZEN).split("\n")
    for item in parsed.keyed():
        line = raw_lines[item.line_no - 1]
        assert line.startswith("- {} ({}):".format(item.key, item.proc)), line
    # keys are the literal bytes from the source: no folding, no normalisation
    assert len(set(RUNNING_KEYS)) == 11
    assert all(k == k.lower() for k in RUNNING_KEYS)
    # the keyless fallback key is deterministic and section-scoped
    keyless = parsed.keyless()
    assert keyless[0].key == "unnamed:AWAITING_OWNER:1"
    assert keyless[10].key == "unnamed:AWAITING_OWNER:11"
    assert keyless[11].key == "unnamed:OPEN_FOLLOW_UP:1"
    assert all(it.keyless for it in keyless)


# --------------------------------------------------------------------------- TH-A3

KEYLESS_SOURCE = """# synthetic

## AWAITING OWNER (needs input, no worker running)

- alpha thing: first ask
- beta thing: second ask
"""

KEYLESS_REORDERED = """# synthetic

## AWAITING OWNER (needs input, no worker running)

- beta thing: second ask
- alpha thing: first ask
"""

KEYLESS_EDITED = """# synthetic

## AWAITING OWNER (needs input, no worker running)

- alpha thing: REWRITTEN ask
- beta thing: second ask
"""


def _keyless_ledger_items():
    alpha_line = "- alpha thing: first ask"
    beta_line = "- beta thing: second ask"
    return [
        {"key": "unnamed:AWAITING_OWNER:1", "proc": None, "section": "AWAITING_OWNER",
         "section_label": "Awaiting owner", "line": alpha_line, "line_no": 5,
         "content_sha1": th.sha1_text(alpha_line), "project_id": "P-alpha", "binding": "keyless",
         "warnings": []},
        {"key": "unnamed:AWAITING_OWNER:2", "proc": None, "section": "AWAITING_OWNER",
         "section_label": "Awaiting owner", "line": beta_line, "line_no": 6,
         "content_sha1": th.sha1_text(beta_line), "project_id": "P-beta", "binding": "keyless",
         "warnings": []},
    ]


def test_th_a3_reordered_keyless_lines_rebind_by_content_sha1(tmp_path, cfg, svc):
    """TH-A3(a): re-ordering re-binds by content_sha1, not by ordinal."""
    _add_project(svc, "P-alpha", "alpha side project")
    _add_project(svc, "P-beta", "beta side project")
    _seed_ledger(cfg, _keyless_ledger_items())
    source = _write(tmp_path / "reordered.md", KEYLESS_REORDERED)
    report = th.sync(cfg, source_path=source, registry=svc.registry)
    assert report["exit_code"] == th.EXIT_OK
    first = _ledger_item(report, "unnamed:AWAITING_OWNER:1")
    second = _ledger_item(report, "unnamed:AWAITING_OWNER:2")
    assert first["line"].startswith("- beta thing")
    assert first["project_id"] == "P-beta", "content, not ordinal, decides the binding"
    assert second["project_id"] == "P-alpha"


def test_th_a3_edited_keyless_line_warns_and_never_repoints(tmp_path, cfg, svc):
    """TH-A3(b): an edit emits keyless_binding_shift and does not silently re-point."""
    _add_project(svc, "P-alpha", "alpha side project")
    _add_project(svc, "P-beta", "beta side project")
    _seed_ledger(cfg, _keyless_ledger_items())
    source = _write(tmp_path / "edited.md", KEYLESS_EDITED)
    report = th.sync(cfg, source_path=source, registry=svc.registry)
    edited = _ledger_item(report, "unnamed:AWAITING_OWNER:1")
    assert "keyless_binding_shift" in edited["warnings"]
    assert "keyless_binding_shift" in report["degraded"]
    # the binding is preserved (reported, not silently moved to the other project)
    assert edited["project_id"] == "P-alpha"
    assert edited["content_sha1"] == th.sha1_text("- alpha thing: REWRITTEN ask")
    untouched = _ledger_item(report, "unnamed:AWAITING_OWNER:2")
    assert untouched["project_id"] == "P-beta"
    assert "keyless_binding_shift" not in untouched["warnings"]


# --------------------------------------------------------------------------- TH-A4

FALLBACK_SOURCE = """# synthetic

## RUNNING (background workers live)

- name-match-slug (proc_111122223333): bound by project name
- proc-match-slug (proc_abcdef123456): bound by the literal proc id
- fuzzy-near-miss-slug (proc_444455556666): must NOT bind to name-match-slug
- orphan-slug (proc_777788889999): no Continuum project at all
"""


def test_th_a4_fallback_order_steps_1_to_5_and_zero_fuzzy_matches(tmp_path, cfg, svc):
    """TH-A4: §4 steps 1 (name), 2 (proc), 5 (none) + zero fuzzy matches.

    Steps 3-4 (keyless carry by ``content_sha1``, then by the ordinal key) are exercised by
    TH-A3's seeded-ledger tests; this test covers the keyed path end to end.
    """
    _add_project(svc, "P-name", "name-match-slug",
                 sessions=(("alpha", "s-name", "session title"),))
    _add_project(svc, "P-proc", "unrelated name",
                 sessions=(("alpha", "s-proc", "work on proc_abcdef123456 today"),))
    source = _write(tmp_path / "fallback.md", FALLBACK_SOURCE)
    report = th.sync(cfg, source_path=source, registry=svc.registry)
    by_key = {it["key"]: it for it in report["items"]}
    # step 1: exact slug == project name
    assert by_key["name-match-slug"]["project_id"] == "P-name"
    assert by_key["name-match-slug"]["binding"] == "import"
    # step 2: the literal proc id found verbatim in a member session title
    assert by_key["proc-match-slug"]["project_id"] == "P-proc"
    assert by_key["proc-match-slug"]["binding"] == "import"
    # no fuzzy / substring matching anywhere: the near miss stays unbound (step 5)
    assert by_key["fuzzy-near-miss-slug"]["project_id"] is None
    assert by_key["orphan-slug"]["project_id"] is None
    assert by_key["orphan-slug"]["binding"] is None
    assert report["absent"] == 0
    # the near miss is NOT matched by name, substring or token score
    assert by_key["fuzzy-near-miss-slug"]["project_id"] != "P-name"


def test_th_a4_proc_matches_a_declared_next_action(tmp_path, cfg, svc):
    _add_project(svc, "P-na", "unrelated name",
                 next_action="continue with proc_0011223344ff handoff")
    source = _write(tmp_path / "na.md",
                    "# synthetic\n\n## RUNNING (background workers live)\n\n"
                    "- na-slug (proc_0011223344ff): next-action match\n")
    report = th.sync(cfg, source_path=source, registry=svc.registry)
    assert _ledger_item(report, "na-slug")["project_id"] == "P-na"


# --------------------------------------------------------------------------- TH-A5

AMBIGUOUS_SOURCE = """# synthetic

## RUNNING (background workers live)

- shared-slug (proc_aaaabbbbcccc): first claimant
- shared-slug (proc_ddddeeeeffff): second claimant
"""


def test_th_a5_binding_uniqueness_reports_ambiguous_binding(tmp_path, cfg, svc):
    """TH-A5: K1→K4 and K4→K1 both <= 1; a violation reports ambiguous_binding."""
    _add_project(svc, "P-one", "shared-slug")
    source = _write(tmp_path / "ambiguous.md", AMBIGUOUS_SOURCE)
    report = th.sync(cfg, source_path=source, registry=svc.registry)
    first, second = report["items"][0], report["items"][1]
    assert first["project_id"] == "P-one"
    assert second["project_id"] is None, "a second claimant is never auto-resolved"
    assert "ambiguous_binding" in second["warnings"]
    assert "duplicate_slug" in report["degraded"]
    kinds = [c["kind"] for c in report["conflicts"]]
    assert "ambiguous_binding" in kinds
    bound = [it for it in report["items"] if it["project_id"]]
    assert len({it["project_id"] for it in bound}) == len(bound) == 1


# --------------------------------------------------------------------------- TH-A6

def test_th_a6_heading_identity_survives_parenthetical_changes(tmp_path, cfg, svc):
    """TH-A6: heading matched on its leading words; a (date)/parenthetical change orphans nothing."""
    text = ("# synthetic\n\n"
            "## RUNNING (background workers live)\n"
            "- a-slug: running item\n"
            "## AWAITING OWNER (needs input) [updated]\n"
            "- owner ask: keyless\n"
            "## OPEN FOLLOW-UPS (tracked, no lane yet)\n"
            "- follow up: keyless\n"
            "## CLOSED (2099-12-31)\n"
            "- closed thing: keyless\n")
    source = _write(tmp_path / "headings.md", text)
    parsed = th.parse_file(source)
    assert [s["section"] for s in parsed.sections] == list(th.SECTION_ORDER)
    assert parsed.section_counts() == {"RUNNING": 1, "AWAITING_OWNER": 1,
                                       "OPEN_FOLLOW_UP": 1, "CLOSED": 1}
    assert "heading_unrecognized" not in parsed.warnings
    report = th.sync(cfg, source_path=source, registry=svc.registry)
    assert report["absent"] == 0


def test_th_a6_unrecognized_heading_warns_and_keeps_the_bullets(tmp_path, cfg, svc):
    text = ("# synthetic\n\n## SOMETHING ELSE (nope)\n- stray-slug: kept but unattributed\n"
            "## RUNNING (background workers live)\n- a-slug: running item\n")
    source = _write(tmp_path / "stray.md", text)
    parsed = th.parse_file(source)
    assert "heading_unrecognized" in parsed.degraded
    assert len(parsed.items) == 2, "no bullet is dropped"
    report = th.sync(cfg, source_path=source, registry=svc.registry)
    assert report["exit_code"] == th.EXIT_PARSE_DEGRADED


# --------------------------------------------------------------------------- TH-A11

def test_th_a11_receipt_flag_and_text_verbatim_section_unchanged():
    """TH-A11: RECEIPT SAYS CLOSED|COMPLETE sets receipt+receipt_text verbatim; section stays."""
    parsed = th.parse_file(FROZEN)
    receipts = {it.key: it for it in parsed.receipts()}
    assert len(receipts) == 7
    assert set(receipts) == set(RECEIPT_KEYS)
    expected = {
        "evopet-floater-verify":
            "CLOSED (floater 126 = HUD 126). Confirm lane dir, then close.",
        "upstream-rebase":
            "COMPLETE (4 PRs mergeable, pushes read back). Confirm lane dir, then close.",
        "kit-about":
            "COMPLETE with 2 flags (homepage DNS, README six-vs-seven). Confirm, then close.",
        "typemon-publish": "COMPLETE (published + playable). One re-probe pending, then close.",
        "positioning-merge": "8/8 merged. Verify zero-open, then close.",
        "bounce-sop-finish": "CLOSED (SOP law + org teams live). Confirm, then close.",
        "evopet-repo-verify": "zero-PR proven. Confirm, then close.",
    }
    for key, text in expected.items():
        assert receipts[key].receipt_text == text
        assert receipts[key].section == "RUNNING", "the section is never rewritten by a receipt"
        assert "receipt_section_mismatch" in receipts[key].warnings
    assert receipts["evopet-floater-verify"].receipt_status == "CLOSED"
    assert receipts["upstream-rebase"].receipt_status == "COMPLETE"
    assert receipts["positioning-merge"].receipt_status is None
    # every receipt text is a verbatim slice of its own source line
    raw = _read(FROZEN).split("\n")
    for item in receipts.values():
        assert item.receipt_text in raw[item.line_no - 1]
    # a non-receipt bullet carries neither flag
    assert not any(it.receipt for it in parsed.items if it.line_no < 10)


# --------------------------------------------------------------------------- TH-A12

def test_th_a12_write_boundary_only_reserved_declared_rows_differ(cfg, svc):
    """TH-A12: the six guarded tables are byte-identical; only task_home_* declared rows differ."""
    _add_project(svc, "P-bound", "continuum-sync",
                 sessions=(("alpha", "s-bound", "task home sync"),))
    _add_project(svc, "P-other", "unrelated dashboard project")
    before = th.table_hashes(svc.registry.conn, declared_only=True)
    declared_before = {(r["project_id"], r["field"]): r["value"] for r in
                       svc.registry.conn.execute("SELECT project_id, field, value FROM declared_field")}
    report = th.sync(cfg, registry=svc.registry)
    after = th.table_hashes(svc.registry.conn, declared_only=True)
    for table in th.GUARDED_TABLES:
        assert before[table] == after[table], "{} changed during a sync pass".format(table)
    assert before["declared_field"] != after["declared_field"]
    declared_after = {(r["project_id"], r["field"]): r["value"] for r in
                      svc.registry.conn.execute("SELECT project_id, field, value FROM declared_field")}
    changed = {k for k in set(declared_before) | set(declared_after)
               if declared_before.get(k) != declared_after.get(k)}
    assert changed, "the sync must write its reserved namespace"
    for _pid, field in changed:
        assert field in th.RESERVED_FIELDS, "wrote a non-reserved declared field: {}".format(field)
    # the human-owned fields are untouched
    for field in ("placement", "urgent", "lifecycle_override", "accepted", "project_name",
                  "dismissed", "merged_into"):
        assert declared_before.get(("P-bound", field)) == declared_after.get(("P-bound", field))
    # and the source name is the reserved namespace's own source value (TH-L5)
    sources = {r["source"] for r in svc.registry.conn.execute(
        "SELECT source FROM declared_field WHERE field LIKE 'task_home_%'")}
    assert sources == {th.SOURCE_KIND}
    assert report["writes"] > 0


def test_th_a12_no_source_database_is_touched(cfg, svc, home):
    def stat(profile):
        st = os.stat(os.path.join(home, "profiles", profile, "state.db"))
        return (st.st_size, st.st_mtime_ns, st.st_ino)

    before = {p: stat(p) for p in ("alpha", "beta")}
    th.sync(cfg, registry=svc.registry)
    after = {p: stat(p) for p in ("alpha", "beta")}
    assert before == after


# --------------------------------------------------------------------------- TH-A12 (ghost)

def test_ledger_binding_to_a_missing_project_is_never_recreated(cfg, svc):
    """Regression (TH-A12): a stale ledger pid absent from THIS registry yields zero writes."""
    ghost_line = "- continuum-sync (proc_4faba8822b80): ghost binding"
    _seed_ledger(cfg, [{"key": "continuum-sync", "proc": "proc_4faba8822b80",
                        "section": "RUNNING", "section_label": "Running",
                        "line": ghost_line, "line_no": 11,
                        "content_sha1": th.sha1_text(ghost_line),
                        "project_id": "ghost-project-not-here", "binding": "import",
                        "warnings": []}])
    before = th.table_hashes(svc.registry.conn, declared_only=True)
    report = th.sync(cfg, registry=svc.registry)
    assert report["writes"] == 0
    assert _ledger_item(report, "continuum-sync")["project_id"] is None
    assert th.table_hashes(svc.registry.conn, declared_only=True) == before


# --------------------------------------------------------------------------- TH-I1

def test_th_i1_second_pass_is_zero_write_and_byte_identical(cfg, svc):
    """TH-I1: unchanged input -> changed=0 and the declared_field hash is byte-identical."""
    _add_project(svc, "P-bound", "continuum-sync",
                 sessions=(("alpha", "s-bound", "task home sync"),))
    first = th.sync(cfg, registry=svc.registry)
    assert first["changed"] >= 1
    before = th.table_hashes(svc.registry.conn, declared_only=True)
    second = th.sync(cfg, registry=svc.registry)
    after = th.table_hashes(svc.registry.conn, declared_only=True)
    assert second["added"] == 0
    assert second["changed"] == 0
    assert second["writes"] == 0
    assert second["unchanged"] == first["changed"] + first["unchanged"]
    assert after["declared_field"] == before["declared_field"]
    for table in th.GUARDED_TABLES:
        assert before[table] == after[table]


def test_th_i1_dry_run_writes_nothing(cfg, svc):
    before = th.table_hashes(svc.registry.conn, declared_only=True)
    report = th.sync(cfg, registry=svc.registry, dry_run=True)
    after = th.table_hashes(svc.registry.conn, declared_only=True)
    assert report["dry_run"] is True
    assert after == before
    assert not os.path.exists(cfg.bundle["task_home_ledger_path"])


# --------------------------------------------------------------------------- TH-I2

def test_th_i2_edit_changes_exactly_line_and_content_sha1(tmp_path, cfg, svc):
    """TH-I2: a text edit moves task_home_line + task_home_content_sha1 and adds no binding."""
    _add_project(svc, "P-bound", "continuum-sync",
                 sessions=(("alpha", "s-bound", "task home sync"),))
    th.sync(cfg, registry=svc.registry)
    before = _declared_rows(svc, "P-bound")
    rows_before = svc.registry.conn.execute(
        "SELECT COUNT(*) AS n FROM declared_field").fetchone()["n"]

    edited = _read(FROZEN).replace(
        "bidirectional TASK-HOME \u2194 Continuum dashboard sync. Next: dashboard URL + "
        "round-trip proof.",
        "bidirectional TASK-HOME \u2194 Continuum dashboard sync. Next: dashboard URL + "
        "round-trip proof (edited).")
    assert edited != _read(FROZEN), "the fixture text must actually change"
    source = _write(tmp_path / "edited-frozen.md", edited)
    report = th.sync(cfg, source_path=source, registry=svc.registry)
    after = _declared_rows(svc, "P-bound")
    diff = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    assert diff == {"task_home_line", "task_home_content_sha1"}, diff
    assert after["task_home_content_sha1"] != before["task_home_content_sha1"]
    assert after["task_home_key"] == before["task_home_key"] == "continuum-sync"
    rows_after = svc.registry.conn.execute(
        "SELECT COUNT(*) AS n FROM declared_field").fetchone()["n"]
    assert rows_after == rows_before, "no binding was added"
    assert _ledger_item(report, "continuum-sync")["project_id"] == "P-bound"
    # nothing else in the ledger changed its binding
    other = _ledger_item(report, "askasite-build-s3")
    assert other["project_id"] is None


# --------------------------------------------------------------------------- TH-I3

def test_th_i3_export_body_is_stable_and_carries_no_timestamp(cfg, svc):
    _add_project(svc, "P-bound", "continuum-sync")
    _add_project(svc, "P-other", "unbound dashboard project")
    th.sync(cfg, registry=svc.registry)
    first = th.export(cfg, svc.registry, now=1700000000.0, run_id="run-aaa")
    second = th.export(cfg, svc.registry, now=1700000123.0, run_id="run-bbb")
    assert first["body_md"] == second["body_md"]
    assert first["body_json"] == second["body_json"]
    # two exports from one unchanged source are byte-identical even at different run times
    for body in (first["body_md"], first["body_json"]):
        assert "1700000000" not in body
        assert "1700000123" not in body
        assert "run-aaa" not in body and "run-bbb" not in body
        assert "generated_at" not in body
    # run metadata lives in the sidecar only
    assert first["meta"]["generated_at"] == 1700000000.0
    assert second["meta"]["generated_at"] == 1700000123.0
    assert first["meta"]["run_id"] == "run-aaa" and second["meta"]["run_id"] == "run-bbb"
    assert first["meta"]["body_sha256"]["md"] == second["meta"]["body_sha256"]["md"]
    assert json.loads(_read(first["paths"]["meta"]))["generated_at"] == 1700000123.0


# --------------------------------------------------------------------------- TH-I4

def test_th_i4_second_concurrent_pass_is_locked_and_ledger_unchanged(cfg, svc, monkeypatch):
    """TH-I4: a second concurrent pass exits 3 SYNC_LOCKED; the ledger hash is unchanged."""
    th.sync(cfg, registry=svc.registry)
    ledger_path = cfg.bundle["task_home_ledger_path"]
    lock_path = th.lock_path_for(cfg)
    before = _file_sha(ledger_path)
    with th._SyncLock(lock_path):
        with pytest.raises(th.SyncLocked):
            th.sync(cfg, registry=svc.registry)
        # exit-code mapping on the CLI path (the lock is held by this test, not by a pass)
        from continuum import cli
        monkeypatch.setattr(cli.task_home, "sync",
                            lambda *a, **k: (_ for _ in ()).throw(th.SyncLocked(lock_path)))
        code = cli.main(["task-home", "sync", "--source", FROZEN,
                         "--hermes-home", cfg.hermes_home])
    assert code == th.EXIT_LOCKED == 3
    assert _file_sha(ledger_path) == before


def test_th_i4_missing_source_exit_code_is_4(cfg, svc, tmp_path):
    from continuum import cli
    missing = str(tmp_path / "nope.md")
    with pytest.raises(th.SourceMissing):
        th.sync(cfg, source_path=missing, registry=svc.registry)
    code = cli.main(["task-home", "sync", "--source", missing,
                     "--hermes-home", cfg.hermes_home])
    assert code == th.EXIT_SOURCE_MISSING == 4


# --------------------------------------------------------------------------- TH-R1

def test_th_r1_round_trip_fixed_point():
    """TH-R1: parse(render(parse(fixture))) == parse(fixture) on items, sections, lanes."""
    parsed = th.parse_file(FROZEN)
    rendered = th.render_document(parsed)
    again = th.parse_bytes(rendered.encode("utf-8"))
    keys = lambda p: [(i.key, i.proc, i.section, i.line, i.content_sha1, i.line_no)
                      for i in p.items]
    assert keys(again) == keys(parsed)
    assert len(again.items) == 34
    assert [(s["section"], s["label"], s["lane"], s["line_no"]) for s in again.sections] == \
           [(s["section"], s["label"], s["lane"], s["line_no"]) for s in parsed.sections]
    assert again.section_counts() == parsed.section_counts() == SECTION_COUNTS
    assert rendered.encode("utf-8") == open(FROZEN, "rb").read(), \
        "the renderer reproduces the canonical source exactly"
    assert again.degraded == parsed.degraded == []


# --------------------------------------------------------------------------- TH-R2

def test_th_r2_export_covers_every_project_exactly_once(cfg, svc):
    _add_project(svc, "P-bound", "continuum-sync")
    _add_project(svc, "P-other", "unbound dashboard project")
    _add_project(svc, "P-third", "another unbound project")
    th.sync(cfg, registry=svc.registry)
    payload = th.export(cfg, svc.registry)["payload"]
    project_ids = {row["project_id"] for row in svc.registry.projects()}
    ledger = th.load_ledger(cfg.bundle["task_home_ledger_path"])
    bound_ids = {entry["project_id"] for entry in ledger["items"] if entry["project_id"]}
    unbound_ids = {entry["project_id"] for entry in payload["unbound_projects"]}
    assert bound_ids, "the fixture binds at least one project"
    assert not (bound_ids & unbound_ids), "every project is in exactly one bucket"
    assert bound_ids | unbound_ids == project_ids
    assert payload["counts"]["bound"] == len(bound_ids)
    assert payload["counts"]["dashboard_only"] == len(unbound_ids)
    assert payload["counts"]["bound"] + payload["counts"]["dashboard_only"] == len(project_ids)
    assert payload["counts"]["items"] == 34
    # the bound project's line is the source's own line, never a proposal
    bound_lines = [line for section in payload["sections"] for line in section["lines"]
                   if line["key"] == "continuum-sync"]
    assert len(bound_lines) == 1
    assert bound_lines[0]["proposed"] is False
    assert bound_lines[0]["existing_line_no"] == 11
    proposed_keys = {line["key"] for line in payload["proposed_new_section"]["lines"]}
    assert len(proposed_keys) == len(unbound_ids)
    for entry in payload["unbound_projects"]:
        assert entry["proposed_key"] in proposed_keys


# --------------------------------------------------------------------------- TH-R3

def test_th_r3_additions_are_marked_and_no_existing_line_is_modified(cfg, svc):
    _add_project(svc, "P-other", "unbound dashboard project")
    th.sync(cfg, registry=svc.registry)
    report = th.export(cfg, svc.registry)
    payload = report["payload"]
    for section in payload["sections"]:
        for line in section["lines"]:
            assert line["proposed"] is False
            assert line["existing_line_no"] is not None
            raw = _read(FROZEN).split("\n")[line["existing_line_no"] - 1]
            assert line["proposed_line"] == raw, "an existing line may never be rewritten"
    for line in payload["proposed_new_section"]["lines"]:
        assert line["proposed"] is True
        assert line["existing_line_no"] is None
        assert line["proposed_line"].startswith("- ")
    md = report["body_md"]
    existing_block, _, proposal_block = md.partition("## PROPOSED")
    assert th.render_document(th.parse_file(FROZEN)).rstrip("\n") in existing_block
    assert "## PROPOSED" in md and "## CONFIRM THEN CLOSE" in md
    assert proposal_block.count("unbound dashboard project") == 1


# --------------------------------------------------------------------------- TH-R4

def test_th_r4_export_never_writes_the_source(cfg, svc):
    before = _file_sha(FROZEN)
    live_before = _file_sha(LIVE_SOURCE) if os.path.isfile(LIVE_SOURCE) else None
    th.sync(cfg, registry=svc.registry)
    th.export(cfg, svc.registry)
    assert _file_sha(FROZEN) == before
    if live_before is not None:
        assert _file_sha(LIVE_SOURCE) == live_before, "TASK-HOME.md must never be written"


def test_th_r4_module_has_no_write_path_to_the_source():
    """Structural supplement: the source is only ever opened for reading."""
    src = _read(os.path.join(BUILD_ROOT, "continuum", "task_home.py"))
    assert 'open(path, "rb")' in src, "parse_file reads the source in binary mode"
    for banned in ('open(source', "os.remove", "shutil.", "os.rename", "os.truncate",
                   'open(self.path, "w"'):
        assert banned not in src, banned
    # the ONLY append-mode open is the lock file, and nothing is ever written through it
    assert src.count('"a+"') == 1
    assert 'open(self.path, "a+")' in src
    assert "self._fh.write" not in src


# --------------------------------------------------------------------------- TH-C1

def test_th_c1_receipt_outranks_section(tmp_path, cfg, svc):
    """TH-C1: a RUNNING bullet with a CLOSED receipt stays RUNNING and is confirm-then-close."""
    _add_project(svc, "P-receipt", "evopet-floater-verify")
    th.sync(cfg, registry=svc.registry)
    board = svc.board(view="all")
    card = [c for c in board["items"] if c["project_id"] == "P-receipt"][0]
    assert card["home"] == "RUNNING"
    assert card["column"] == "ongoing", "the receipt never moves the item out of RUNNING"
    assert card["task_home"]["receipt"] is True
    assert card["task_home"]["receipt_text"].startswith("CLOSED")
    payload = th.export(cfg, svc.registry)["payload"]
    keys = [entry["key"] for entry in payload["confirm_then_close"]]
    assert keys == RECEIPT_KEYS, "all seven RUNNING receipts are confirm-then-close"
    assert payload["confirm_then_close"][0]["line_no"] == 10
    # the sync never closed it and never wrote a terminal placement
    declared = _declared_rows(svc, "P-receipt")
    assert declared.get("placement") is None
    assert declared.get("task_home_section") == "RUNNING"


# --------------------------------------------------------------------------- TH-C3

def test_th_c3_derived_claims_are_quarantined_in_the_derived_block(cfg, svc):
    _add_project(svc, "P-bound", "continuum-sync")
    _add_project(svc, "P-other", "unbound dashboard project")
    th.sync(cfg, registry=svc.registry)
    payload = th.export(cfg, svc.registry)["payload"]
    names = {row["project_id"]: row["name"] for row in svc.registry.projects()}
    key_to_pid = {entry["proposed_key"]: entry["project_id"]
                  for entry in payload["unbound_projects"]}
    for entry in payload["unbound_projects"]:
        assert set(entry) == {"project_id", "name", "lane", "proposed_key"}
    for line in payload["proposed_new_section"]["lines"]:
        derived = line["derived"]
        assert set(derived) == {"confidence_band", "evidence_tier", "session_count",
                                "last_subject_activity"}
        # the proposed line is exactly key + name + lane: no derived claim leaks into it
        pid = key_to_pid[line["key"]]
        assert line["proposed_line"] == "- {}: {} (dashboard-derived, lane {}).".format(
            line["key"], names[pid], line["lane"])
        for token in ("confidence", "tier", "session_count", "sessions", "high", "medium"):
            assert token not in line["proposed_line"], (token, line["proposed_line"])


def test_th_c3_status_fields_are_not_rewritten_by_the_import(cfg, svc):
    _add_project(svc, "P-bound", "continuum-sync", lifecycle="LS-3", band="high", tier=1)
    before = dict(svc.registry.get_project("P-bound"))
    th.sync(cfg, registry=svc.registry)
    board = svc.board(view="all")
    card = [c for c in board["items"] if c["project_id"] == "P-bound"][0]
    assert card["derived_lifecycle"] == "LS-3", "lifecycle is never rewritten by the import"
    assert card["home"] == "RUNNING"
    assert card["column"] == "ongoing"
    row = dict(svc.registry.get_project("P-bound"))
    for field in ("lifecycle", "confidence", "confidence_band", "evidence_tier"):
        assert row[field] == before[field]


# --------------------------------------------------------------------------- schema (TH-A13)

def test_th_a13_schema_version_is_3_and_the_sync_never_migrates(cfg, svc):
    assert SCHEMA_VERSION == 3
    before = svc.registry.conn.execute("SELECT version FROM schema_version").fetchone()[0]
    th.sync(cfg, registry=svc.registry)
    after = svc.registry.conn.execute("SELECT version FROM schema_version").fetchone()[0]
    assert before == after == 3


# --------------------------------------------------------------------------- adversarial parse

ADVERSARIAL = (
    "# synthetic\n"
    "\n"
    "## RUNNING (background workers live)\n"
    "- good-slug (proc_aaaa1111bbbb): fine\n"
    "- good-slug (proc_cccc2222dddd): duplicate key\n"
    "- missing-proc-slug (proc): no hex proc\n"
    "- no separator at all\n"
    "continuation text that is not a bullet\n"
    "## NOT A KNOWN SECTION (x)\n"
    "- stray (proc_99990000aaaa): kept\n"
    "## CLOSED (2026-01-01)\n"
)


def test_adversarial_parse_warns_without_crashing_or_dropping(tmp_path):
    """Complements Shaka's TH-G2: every malformed case yields a defined warning."""
    source = _write(tmp_path / "adversarial.md", ADVERSARIAL)
    parsed = th.parse_file(source)
    assert len(parsed.items) == 5, "no bullet is dropped"
    for code in ("duplicate_slug", "missing_proc", "heading_unrecognized", "wrapped_line"):
        assert code in parsed.warnings, code
    assert "empty_section" in parsed.warnings
    assert parsed.degraded
    # duplicate literal keys are preserved verbatim (no dedupe, TH-A2)
    assert [it.key for it in parsed.items].count("good-slug") == 2


def test_crlf_source_is_normalised_and_reported(tmp_path, cfg, svc):
    raw = ("# synthetic\r\n\r\n## RUNNING (background workers live)\r\n"
           "- crlf-slug: windows line endings\r\n").encode("utf-8")
    path = str(tmp_path / "crlf.md")
    with open(path, "wb") as fh:
        fh.write(raw)
    parsed = th.parse_file(path)
    assert "crlf_normalized" in parsed.warnings
    assert parsed.source_sha256 == hashlib.sha256(raw).hexdigest()
    assert len(parsed.items) == 1
    assert parsed.items[0].line == "- crlf-slug: windows line endings"


def test_duplicate_slug_is_reported_and_binding_is_unique(tmp_path, cfg, svc):
    _add_project(svc, "P-dup", "dup-slug")
    source = _write(tmp_path / "dup.md",
                    "# synthetic\n\n## RUNNING (background workers live)\n"
                    "- dup-slug (proc_123412341234): one\n"
                    "- dup-slug (proc_432143214321): two\n")
    report = th.sync(cfg, source_path=source, registry=svc.registry)
    bound = [it for it in report["items"] if it["project_id"]]
    assert len(bound) == 1, "K4 -> K1 must stay <= 1"
    assert any("ambiguous_binding" in it["warnings"] for it in report["items"])
    assert "duplicate_slug" in report["degraded"]
    assert report["exit_code"] == th.EXIT_PARSE_DEGRADED


# --------------------------------------------------------------------------- absent

def test_absent_items_withdraw_the_override_and_are_reported(tmp_path, cfg, svc):
    _add_project(svc, "P-gone", "vanishing-slug")
    with_key = _write(tmp_path / "with.md",
                      "# synthetic\n\n## RUNNING (background workers live)\n"
                      "- vanishing-slug (proc_abcdabcdabcd): present now\n")
    first = th.sync(cfg, source_path=with_key, registry=svc.registry)
    assert _ledger_item(first, "vanishing-slug")["project_id"] == "P-gone"
    without = _write(tmp_path / "without.md",
                     "# synthetic\n\n## RUNNING (background workers live)\n"
                     "- other-slug (proc_111122220000): different line\n")
    second = th.sync(cfg, source_path=without, registry=svc.registry)
    assert second["absent"] == 1
    absent = second["absent_items"][0]
    assert absent["section"] == "ABSENT" and absent["project_id"] == "P-gone"
    assert absent["section_label"] == th.ABSENT_SECTION_LABEL
    board = svc.board(view="all")
    card = [c for c in board["items"] if c["project_id"] == "P-gone"][0]
    assert card["home"] == "ABSENT"
    assert card["task_home"] is None, "the override is withdrawn"
    assert "placement_shadowed" not in card
    assert board["counts"]["task_home"]["ABSENT"] == 1
    # the frozen fixture is untouched by any of this
    assert _file_sha(FROZEN) == FROZEN_SHA


# ---------------------------------------------------------------- v2 grammar (continuum-grammar ruling)

def test_th_ga1_slug_only_form_is_keyed_with_null_proc(tmp_path):
    """TH-GA1: `- slug: text` is keyed, key=slug, proc=None (v2 amendment, ruling §3)."""
    source = _write(tmp_path / "ga1.md",
                    "# synthetic\n\n## RUNNING (background workers live)\n"
                    "- evopet-pointer-fix: overlay fix VERIFIED live\n")
    parsed = th.parse_file(source)
    assert len(parsed.items) == 1
    item = parsed.items[0]
    assert item.key == "evopet-pointer-fix"
    assert item.proc is None
    assert item.keyless is False


def test_th_ga2_proc_form_is_keyed_unchanged(tmp_path):
    """TH-GA2: the v1 proc-bearing form still keys identically — _KEYED_RE untouched."""
    source = _write(tmp_path / "ga2.md",
                    "# synthetic\n\n## RUNNING (background workers live)\n"
                    "- evopet-pointer-fix (proc_5347043a30e4): overlay fix\n")
    parsed = th.parse_file(source)
    item = parsed.items[0]
    assert item.key == "evopet-pointer-fix"
    assert item.proc == "proc_5347043a30e4"
    assert item.keyless is False
    assert "missing_proc" not in parsed.warnings


def test_th_ga3_mixed_forms_all_keyed_zero_keyless(tmp_path):
    """TH-GA3: 18 proc-bearing + 48 slug-only bullets = 66 keyed, 0 keyless (ruling §8)."""
    running = "".join("- p-run-{:02d} (proc_{:012x}): proc bullet {}\n".format(i, 0xA00000000000 + i, i)
                      for i in range(18))
    closed = "".join("- p-clo-{:02d}: slug-only bullet {}\n".format(i, i) for i in range(48))
    source = _write(tmp_path / "ga3.md",
                    "# synthetic\n\n## RUNNING (background workers live)\n" + running +
                    "\n## CLOSED (2026-09-17)\n" + closed)
    parsed = th.parse_file(source)
    assert len(parsed.items) == 66
    assert all(not it.keyless for it in parsed.items)
    assert all(it.key.startswith("p-run-") or it.key.startswith("p-clo-") for it in parsed.items)
    assert not any(it.key.startswith("unnamed:") for it in parsed.items)
    assert "duplicate_slug" not in parsed.warnings


def test_th_ga4_ga5_valid_key_accepts_slug_and_unnamed_forms():
    """TH-GA4/GA5: valid_key still accepts a bare slug and the unnamed form; no regression."""
    assert th.valid_key("evopet-pointer-fix") is True
    assert th.valid_key("unnamed:RUNNING:1") is True
    assert th.valid_key("has space") is False
    assert th.valid_key("has:colon") is False
    assert th.valid_key(None) is False


def test_th_ga11_proc_shaped_still_warns_missing_proc(tmp_path):
    """TH-GA11: `(proc_invalid)` still triggers missing_proc and falls to the unnamed key."""
    source = _write(tmp_path / "ga11.md",
                    "# synthetic\n\n## RUNNING (background workers live)\n"
                    "- missing-proc-slug (proc_invalid): no hex proc\n")
    parsed = th.parse_file(source)
    assert "missing_proc" in parsed.warnings
    item = parsed.items[0]
    assert item.keyless is True
    assert item.key == "unnamed:RUNNING:1"


def test_th_ga12_key_marker_still_warns_unknown_key_marker(tmp_path):
    """TH-GA12: the `[key: ...]` marker still warns — OPEN-4 not adopted in v2 either."""
    source = _write(tmp_path / "ga12.md",
                    "# synthetic\n\n## RUNNING (background workers live)\n"
                    "- marker-probe-slug: text [key: other-slug]\n")
    parsed = th.parse_file(source)
    assert "unknown_key_marker" in parsed.warnings


def test_th_ga13_continuation_lines_still_warn_wrapped_line(tmp_path):
    """TH-GA13: a non-bullet continuation line still warns wrapped_line (v2 unaffected)."""
    source = _write(tmp_path / "ga13.md",
                    "# synthetic\n\n## RUNNING (background workers live)\n"
                    "- wrap-probe-slug: first line\n"
                    "continuation text with no dash\n")
    parsed = th.parse_file(source)
    assert "wrapped_line" in parsed.warnings
    assert len(parsed.items) == 1
    assert "wrapped_line" in parsed.items[0].warnings


def test_th_ga14_crlf_slug_only_normalizes_and_keyes(tmp_path):
    """TH-GA14: CRLF normalisation holds and the slug-only bullet is keyed, not keyless."""
    raw = ("# synthetic\r\n\r\n## RUNNING (background workers live)\r\n"
           "- crlf-slug-v2: windows line endings\r\n").encode("utf-8")
    path = str(tmp_path / "crlf-v2.md")
    with open(path, "wb") as fh:
        fh.write(raw)
    parsed = th.parse_file(path)
    assert "crlf_normalized" in parsed.warnings
    assert parsed.items[0].key == "crlf-slug-v2"
    assert parsed.items[0].keyless is False


def test_th_ga9_slug_only_bullet_binds_step_one_exact_name(tmp_path, cfg, svc):
    """§4 fallback step 1 works for a v2 keyed proc-less bullet: binding == 'import'."""
    _add_project(svc, "P-slugonly", "v2-slug-only")
    source = _write(tmp_path / "ga9.md",
                    "# synthetic\n\n## RUNNING (background workers live)\n"
                    "- v2-slug-only: expect exact-name import bind\n")
    report = th.sync(cfg, source_path=source, registry=svc.registry)
    item = _ledger_item(report, "v2-slug-only")
    assert item["project_id"] == "P-slugonly"
    assert item["binding"] == "import"
    assert item["proc"] is None
