"""MC-S6 — Continuum 2 mission-control measurement harness (MG-1..MG-6).

READ-ONLY harness. Every measurement below exercises the real service/scanner/browser surface
and records a number. It writes ONLY under ``build/review/out/**`` (MC-A27):

    build/review/out/mc-measure.json          the run's measurements
    build/review/out/mc-measure.html          the same numbers as a readable page
    build/review/out/manifest.json            an append-only run manifest
    build/review/out/mc-measure-work/         the harness's OWN temporary registry copy

It never writes to a Hermes source ``state.db`` (sources are opened by the existing read-only
scanner path only), never writes ``build/data/registry.db`` (it is *copied*, not opened), and
makes no network call. MG-1/MG-2a/MG-3/MG-5 measure the LIVE corpus read-only; MG-2b measures one
changed session inside the approved synthetic fixture home (``fixtures.make_fixture``) because
appending to a real source database would be a source write.

No measurement here is a verdict, a readiness claim, or an install/enable claim.

Usage (from build/, with the Hermes venv python):

    python review/mc_measure.py                 # all measurements
    python review/mc_measure.py --self-check    # output-boundary + safety self-check only
    python review/mc_measure.py --no-live-scan  # skip MG-1/2a if the corpus read is slow
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

BUILD_ROOT = Path(__file__).resolve().parent.parent
REVIEW_DIR = BUILD_ROOT / "review"
OUT_DIR = REVIEW_DIR / "out"
WORK_DIR = OUT_DIR / "mc-measure-work"
LIVE_REGISTRY = BUILD_ROOT / "data" / "registry.db"
MEASURE_JSON = OUT_DIR / "mc-measure.json"
MEASURE_HTML = OUT_DIR / "mc-measure.html"
# This harness owns its OWN manifest; `review/out/manifest.json` belongs to the M4 ab_snapshot
# harness and is never touched.
MANIFEST_JSON = OUT_DIR / "mc-measure-manifest.json"
SCHEMA = "mc-measure.v1"
BOARD_PAGE_SIZE_CAP = 200
LATENCY_SAMPLES = 5

if str(BUILD_ROOT) not in sys.path:
    sys.path.insert(0, str(BUILD_ROOT))


# --------------------------------------------------------------------------- safety boundary

# Tokens that would indicate a source-write or outbound path in this harness. The harness must
# contain none of them (MG-1..MG-6 are pure reads over the existing scanner/service paths).
# The tokens are assembled from parts so this declaration itself never contains one of them.
_TOKEN_PARTS = (
    ("mode", "=rw"),
    ("PRAGMA query_only", "=OFF"),
    ("urllib", ".request"),
    ("requests", ".get"),
    ("requests", ".post"),
    ("socket", "."),
    ("open", "ai"),
    ("anthro", "pic"),
)
SOURCE_WRITE_TOKENS = tuple(prefix + suffix for prefix, suffix in _TOKEN_PARTS)


def assert_output_boundary(out_dir: Any) -> Path:
    """Refuse to write anywhere except ``build/review/out/**`` (MC-A27)."""
    resolved = Path(out_dir).resolve()
    allowed = OUT_DIR.resolve()
    if resolved != allowed and allowed not in resolved.parents:
        raise ValueError(
            "harness refuses to write outside {!r}: got {!r}".format(str(allowed), str(resolved)))
    return resolved


def assert_no_source_write_tokens(source_path: Optional[Path] = None) -> None:
    """Static self-check: no source-write/outbound token exists in this harness."""
    text = Path(source_path or __file__).read_text(encoding="utf-8")
    for token in SOURCE_WRITE_TOKENS:
        if token in text:
            raise AssertionError("harness contains a forbidden token: {!r}".format(token))


def _write_json(path: Path, payload: Any) -> str:
    assert_output_boundary(path.parent)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
    return str(path)


# --------------------------------------------------------------------------- service plumbing

def _make_service(home: str, registry_path: Path):
    from continuum.config import load_config
    from continuum.service import Service
    cfg = load_config(hermes_home=home)
    cfg.bundle["registry_path"] = str(registry_path)
    return Service(cfg)


def _work_registry_name(label: str) -> Path:
    assert_output_boundary(WORK_DIR)
    return WORK_DIR / "registry-{}.db".format(label)


def _registry_sha256() -> Optional[str]:
    """sha256 of the committed registry file, or None if it does not exist yet."""
    if not LIVE_REGISTRY.exists():
        return None
    with open(LIVE_REGISTRY, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _fresh_work_registry(label: str) -> Path:
    """A COPY of the committed registry (never the committed file itself)."""
    assert_output_boundary(WORK_DIR)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    dest = _work_registry_name(label)
    for suffix in ("", "-wal", "-shm"):
        stale = Path(str(dest) + suffix)
        if stale.exists():
            stale.unlink()
    if LIVE_REGISTRY.exists():
        shutil.copyfile(str(LIVE_REGISTRY), str(dest))
    return dest


def _live_home() -> str:
    return os.environ.get("HERMES_HOME") if os.path.isdir(
        os.path.join(os.environ.get("HERMES_HOME", ""), "profiles")) else os.path.expanduser(
        "~/.hermes")


def _corpus_bytes(refs) -> Dict[str, Any]:
    per_profile: Dict[str, int] = {}
    total = 0
    for ref in refs:
        try:
            size = os.path.getsize(ref.db_path)
        except OSError:
            continue
        per_profile[ref.profile_name] = size
        total += size
    return {"profiles": per_profile, "total_bytes": total, "profile_count": len(per_profile)}


# --------------------------------------------------------------------------- MG-1 / MG-2

def mg_1_full_scan(home: str, registry: Path) -> Dict[str, Any]:
    """MG-1: full scan (mode=full) wall time on the live corpus + roster and corpus bytes."""
    from continuum import scanner
    svc = _make_service(home, registry)
    refs = scanner.discover_profiles(svc.cfg.hermes_home, svc.cfg)
    corpus = _corpus_bytes(refs)
    started = time.perf_counter()
    svc.scan(full=True)
    elapsed = time.perf_counter() - started
    scan = svc.scan_state_object()
    return {
        "gate": "MG-1",
        "mode": scan.get("mode"),
        "wall_seconds": round(elapsed, 3),
        "elapsed_seconds_reported": scan.get("elapsed_seconds"),
        "roster": sorted(corpus["profiles"].keys()),
        "profile_count": corpus["profile_count"],
        "state_db_bytes": corpus["profiles"],
        "corpus_total_bytes": corpus["total_bytes"],
        "sessions_read": scan.get("sessions_read"),
        "messages_probed": scan.get("messages_probed"),
        "profiles_scanned": scan.get("profiles_scanned"),
        "phase": scan.get("phase"),
        "last_error": scan.get("last_error"),
        "registry_used": str(registry),
        "committed_snapshot": svc.committed_snapshot(),
    }


def mg_2a_no_change_incremental(home: str, registry: Path) -> Dict[str, Any]:
    """MG-2a: an unchanged corpus must be cheaply skipped (read counts recorded)."""
    from continuum import scanner
    svc = _make_service(home, registry)
    refs = scanner.discover_profiles(svc.cfg.hermes_home, svc.cfg)
    started = time.perf_counter()
    svc.scan()
    elapsed = time.perf_counter() - started
    scan = svc.scan_state_object()
    roster = {ref.profile_name for ref in refs}
    scoped = {"skipped": 0, "ok": 0, "other": 0}
    for row in svc.registry.conn.execute(
            "SELECT profile_name, status FROM source_db"):
        if row["profile_name"] not in roster:
            continue
        if row["status"] in ("skipped", "ok"):
            scoped[row["status"]] += 1
        else:
            scoped["other"] += 1
    return {
        "gate": "MG-2a",
        "mode": scan.get("mode"),
        "wall_seconds": round(elapsed, 3),
        "profiles_in_roster": len(refs),
        "roster_source_db_statuses": scoped,
        "sessions_read": scan.get("sessions_read"),
        "messages_probed": scan.get("messages_probed"),
        "profiles_scanned": scan.get("profiles_scanned"),
        "note": ("sessions_read/messages_probed are the scan_run totals; the unchanged "
                 "corpus read nothing in the source databases."),
    }


def mg_2b_one_changed_session() -> Dict[str, Any]:
    """MG-2b: exactly one changed session appearing after one scan.

    Uses the APPROVED synthetic fixture home (a temp tree), because appending a session to a real
    source database would be a source write.
    """
    import sqlite3
    import tempfile
    from fixtures.make_fixture import build_fixture_tree

    with tempfile.TemporaryDirectory(prefix="mc-measure-fixture-") as tmp:
        home = build_fixture_tree(os.path.join(tmp, "hermes_home"))
        registry = Path(tmp) / "registry.db"
        svc = _make_service(home, registry)
        svc.scan(full=True)
        before = svc.scan_state_object()
        sessions_before = svc.registry.conn.execute(
            "SELECT COUNT(*) AS n FROM session_fact WHERE present=1").fetchone()["n"]

        db = os.path.join(home, "profiles", "alpha", "state.db")
        conn = sqlite3.connect(db)
        try:
            conn.execute(
                "INSERT INTO sessions (id, source, title, title_source, cwd, git_repo_root,"
                " started_at, last_activity_at, message_count, tool_call_count)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("20260999_120000_mcmeasure", "cli", "mc measure changed session", "derived",
                 "/Users/kethuda/work/tjgc1", "/Users/kethuda/work/tjgc1",
                 time.time(), time.time(), 3, 1))
            conn.execute(
                "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
                ("20260999_120000_mcmeasure", "user", "continue tjgc1 landing page", time.time()))
            conn.commit()
        finally:
            conn.close()

        started = time.perf_counter()
        svc.scan()
        elapsed = time.perf_counter() - started
        after = svc.scan_state_object()
        sessions_after = svc.registry.conn.execute(
            "SELECT COUNT(*) AS n FROM session_fact WHERE present=1").fetchone()["n"]
        linked = svc.registry.conn.execute(
            "SELECT COUNT(*) AS n FROM project_session WHERE session_id=?",
            ("20260999_120000_mcmeasure",)).fetchone()["n"]
        fake_home = (Path(home) != Path(os.path.expanduser("~/.hermes")))
        return {
            "gate": "MG-2b",
            "fixture_home": str(home),
            "fixture_home_is_synthetic": bool(fake_home),
            "mode": after.get("mode"),
            "wall_seconds": round(elapsed, 3),
            "sessions_before": sessions_before,
            "sessions_after": sessions_after,
            "new_session_linked": bool(linked),
            "first_scan_sessions_read": before.get("sessions_read"),
            "incremental_sessions_read": after.get("sessions_read"),
        }


# --------------------------------------------------------------------------- MG-3

def mg_3_board_latency(home: str, registry: Path) -> Dict[str, Any]:
    """MG-3: response bytes + server-side latency at page_size=200, plus the grid row count."""
    import json as _json
    svc = _make_service(home, registry)
    sizes: List[int] = []
    latencies: List[float] = []
    payload: Dict[str, Any] = {}
    for _ in range(LATENCY_SAMPLES):
        started = time.perf_counter()
        payload = svc.board(view="all", page_size=BOARD_PAGE_SIZE_CAP)
        latencies.append(time.perf_counter() - started)
        sizes.append(len(_json.dumps(payload)))
    cards = payload.get("items") or []
    grid_cell_bytes = None
    if cards:
        sample = cards[0]
        grid_cell_bytes = len(_json.dumps({
            "name": sample.get("name"), "phase": sample.get("derived_lifecycle_name"),
            "owner": sample.get("owner"), "staleness_band": sample.get("staleness_band"),
            "confidence_band": sample.get("confidence_band"),
            "evidence_tier": sample.get("evidence_tier"),
            "next_action_state": sample.get("next_action_state"),
        }))
    return {
        "gate": "MG-3",
        "page_size": BOARD_PAGE_SIZE_CAP,
        "page_items": len(cards),
        "total": payload.get("total"),
        "response_bytes": {"min": min(sizes), "max": max(sizes),
                           "median": int(statistics.median(sizes))},
        "server_latency_seconds": {
            "min": round(min(latencies), 4), "max": round(max(latencies), 4),
            "median": round(statistics.median(latencies), 4), "samples": LATENCY_SAMPLES,
        },
        "dense_grid": {
            "columns": 7,
            "rows_at_cap": len(cards),
            "approx_cell_bytes_example": grid_cell_bytes,
            "render_budget": ("not measured in a real browser: no DOM/browser measurement is "
                              "available to this harness. Bound: the grid renders one row per "
                              "returned item with 7 text cells and no per-row request, measured "
                              "by `rows_at_cap`; the browser render budget for the dense grid at "
                              "the cap remains UNMEASURED."),
        },
        "virtualization_authorized_by_this_measurement": False,
    }


# --------------------------------------------------------------------------- MG-4

def mg_4_snapshot_age(app_js: Optional[Path] = None) -> Dict[str, Any]:
    """MG-4: the snapshot age is rendered from the server markers, never as the current time."""
    path = app_js or (BUILD_ROOT / "dashboard" / "static" / "app.js")
    src = Path(path).read_text(encoding="utf-8")
    return {
        "gate": "MG-4",
        "renderer": str(path),
        "renders_snapshot_age": "Snapshot age:" in src,
        "age_derived_only_from_server_fields": "snapshotAgeText" in src and "data_as_of" in src,
        "renders_time_datetime": "h('time'" in src and "datetime:" in src,
        "stale_banner_after_two_missed_polls": "state.eventFailures >= 2" in src,
        "stale_banner_copy_present": ("Showing the last known board. Refresh could not confirm a "
                                      "newer snapshot.") in src,
        "note": ("Rendered-age correctness in a live browser is NOT measured here (no browser "
                 "available to this harness); the checks above are source-observable only."),
    }


# --------------------------------------------------------------------------- MG-5

def mg_5_standalone_first_run(home: str, registry: Path) -> Dict[str, Any]:
    """MG-5: loopback bind, required routes 200, explicit scan trigger, no scan on load."""
    from fastapi.testclient import TestClient
    from dashboard import plugin_api, standalone

    calls = {"scan": 0}
    holder: Dict[str, Any] = {"svc": None}

    def factory():
        if holder["svc"] is None:
            svc = _make_service(home, registry)
            real_scan = svc.scan

            def counted(*a, **k):
                calls["scan"] += 1
                return real_scan(*a, **k)

            svc.scan = counted
            holder["svc"] = svc
        return holder["svc"]

    original = plugin_api.get_service
    plugin_api.get_service = factory
    try:
        client = TestClient(standalone.create_app())
        routes = {}
        for suffix in ("/", "/app.js", "/styles.css",
                       standalone.API_PREFIX + "/projects",
                       standalone.API_PREFIX + "/attention",
                       standalone.API_PREFIX + "/staleness",
                       standalone.API_PREFIX + "/candidates",
                       standalone.API_PREFIX + "/noise",
                       standalone.API_PREFIX + "/events",
                       standalone.API_PREFIX + "/scan/status"):
            routes[suffix] = client.get(suffix).status_code
        scans_after_load = calls["scan"]
        scan_post = client.post(standalone.API_PREFIX + "/scan").status_code
    finally:
        plugin_api.get_service = original
    return {
        "gate": "MG-5",
        "default_host": standalone.DEFAULT_HOST,
        "default_port": standalone.DEFAULT_PORT,
        "loopback_only_default": standalone.DEFAULT_HOST == "127.0.0.1",
        "route_status": routes,
        "scan_trigger_status": scan_post,
        "scans_during_page_load": scans_after_load,
        "scan_trigger_explicit_only": scans_after_load == 0,
        "installed_plugin_path_claimed": False,
    }


# --------------------------------------------------------------------------- MG-6

def mg_6_resume_safety() -> Dict[str, Any]:
    """MG-6: fixture anchors resolve to a resume command; DELEGATED is never a target."""
    import tempfile
    sys.path.insert(0, str(BUILD_ROOT / "tests" / "fixtures" / "audience"))
    from build_audience_home import build_audience_home  # type: ignore

    with tempfile.TemporaryDirectory(prefix="mc-measure-audience-") as tmp:
        home = build_audience_home(os.path.join(tmp, "audience_home"))
        registry = Path(tmp) / "registry.db"
        svc = _make_service(home, registry)
        svc.scan(full=True)
        row = svc.registry.conn.execute(
            "SELECT project_id, anchor_session, anchor_reason FROM project WHERE name='evopet-pet'"
        ).fetchone()
        anchor_cmd = None
        anchor_ref = None
        delegated_target = None
        if row is not None:
            anchor_ref = row["anchor_session"]
            a_profile, _, a_sid = str(anchor_ref or "").partition("/")
            expected_command = "hermes -p {} --resume {}".format(a_profile, a_sid)
            detail = svc.project_detail(row["project_id"])
            items = svc.board(view="all", page_size=200)["items"]
            card = next((c for c in items if c["project_id"] == row["project_id"]), {})
            # resume_links rows carry `profile_name`; the card's resume_targets carry `profile`.
            # Match on session_id (verbatim) and report the profile-scoped command for the anchor.
            candidates = []
            for link in detail.get("resume_links") or []:
                if link.get("session_id") == a_sid:
                    candidates.append(link.get("copy_command_profile_scoped"))
            for target in card.get("resume_targets") or []:
                if target.get("session_id") == a_sid:
                    candidates.append(target.get("copy_command"))
                if target.get("audience") != "USER_FACING":
                    delegated_target = target
            anchor_cmd = expected_command if expected_command in candidates else (
                candidates[0] if candidates else None)
        inbox_violations = []
        from continuum import audience as audience_mod
        inbox_violations = audience_mod.assert_no_resume_on_delegated(svc.recovery_inbox()["items"])
        return {
            "gate": "MG-6",
            "evopet_anchor": anchor_ref,
            "evopet_anchor_reason": (row["anchor_reason"] if row is not None else None),
            "anchor_copy_command_profile_scoped": anchor_cmd,
            "evopet_command_exact": anchor_cmd == (
                "hermes -p lugia --resume 20260911_163629_22fff0"),
            "non_user_facing_resume_target": delegated_target,
            "audience_violations": len(inbox_violations),
            "resume_commands_have_no_route": all(
                (link.get("route") is None)
                for item in svc.recovery_inbox()["items"]
                for link in item.get("resume_links") or []),
        }


# --------------------------------------------------------------------------- report

def _report_html(measurements: Dict[str, Any], finished_at: float) -> str:
    rows = []
    for key in sorted(measurements):
        payload = measurements[key]
        if not isinstance(payload, dict):
            continue
        cells = "".join(
            "<tr><td>{}</td><td><code>{}</code></td></tr>".format(
                k, json.dumps(v, sort_keys=True))
            for k, v in sorted(payload.items()))
        rows.append("<h2>{}</h2><table>{}</table>".format(key, cells))
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<title>Continuum 2 — MC measurement harness</title>"
            "<style>body{{font:13px/1.45 -apple-system,system-ui,sans-serif;margin:24px;"
            "background:#0b0e14;color:#eef1f7}}h1{{font-size:18px}}h2{{font-size:14px;"
            "margin-top:22px;color:#5ab9d4}}table{{border-collapse:collapse;width:100%;"
            "margin-top:6px}}td{{border-bottom:1px solid #232b3d;padding:4px 8px;"
            "vertical-align:top}}code{{color:#8b95ab;white-space:pre-wrap}}"
            "</style></head><body>"
            "<h1>Continuum 2 — MC-S6 measurement harness (MG-1..MG-6)</h1>"
            "<p>Implementation evidence only. Not a QA verdict, not a readiness, install, "
            "enablement or deployment claim.</p>"
            "<p>finished_at: {} ({})</p>{}</body></html>").format(
        finished_at, time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(finished_at)),
        "\n".join(rows))


def _append_manifest(entry: Dict[str, Any]) -> None:
    existing: List[Any] = []
    if MANIFEST_JSON.exists():
        try:
            with open(MANIFEST_JSON, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            existing = loaded if isinstance(loaded, list) else [loaded]
        except (ValueError, OSError):
            existing = []
    existing.append(entry)
    _write_json(MANIFEST_JSON, existing)


def run_all(no_live_scan: bool = False, out_dir: Path = OUT_DIR) -> Dict[str, Any]:
    assert_output_boundary(out_dir)
    assert_no_source_write_tokens()
    out_dir.mkdir(parents=True, exist_ok=True)
    home = _live_home()
    started_at = time.time()
    measurements: Dict[str, Any] = {}
    measurements["environment"] = {
        "harness": SCHEMA,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "build_root": str(BUILD_ROOT),
        "out_dir": str(out_dir),
        "live_home": home,
        "live_registry_bytes": (LIVE_REGISTRY.stat().st_size if LIVE_REGISTRY.exists() else None),
        "committed_registry_sha256_before": _registry_sha256(),
        "started_at": started_at,
    }
    measurements["MG-4"] = mg_4_snapshot_age()
    measurements["MG-6"] = mg_6_resume_safety()
    measurements["MG-2b"] = mg_2b_one_changed_session()

    live_registry = _fresh_work_registry("live")
    measurements["MG-5"] = mg_5_standalone_first_run(home, live_registry)
    measurements["MG-3"] = mg_3_board_latency(home, live_registry)
    if no_live_scan:
        measurements["MG-1"] = {"gate": "MG-1", "skipped": True,
                                "reason": "--no-live-scan"}
        measurements["MG-2a"] = {"gate": "MG-2a", "skipped": True, "reason": "--no-live-scan"}
    else:
        measurements["MG-1"] = mg_1_full_scan(home, live_registry)
        measurements["MG-2a"] = mg_2a_no_change_incremental(home, live_registry)

    measurements["environment"]["finished_at"] = time.time()
    measurements["environment"]["committed_registry_sha256_after"] = _registry_sha256()
    measurements["non_claims"] = [
        "implementation evidence only",
        "not a QA verdict",
        "no readiness, install, enablement or deployment claim",
        "CP-18 virtualization threshold stays OPEN",
        "browser render budget for the dense grid remains UNMEASURED",
    ]
    json_path = out_dir / MEASURE_JSON.name
    html_path = out_dir / MEASURE_HTML.name
    _write_json(json_path, measurements)
    assert_output_boundary(out_dir)
    html_path.write_text(
        _report_html(measurements, measurements["environment"]["finished_at"]), encoding="utf-8")
    _append_manifest({
        "harness": SCHEMA,
        "started_at": started_at,
        "finished_at": measurements["environment"]["finished_at"],
        "gates": [k for k in measurements if k.startswith("MG-")],
        "outputs": [str(json_path), str(html_path), str(MANIFEST_JSON)],
        "work_dir": str(WORK_DIR),
        "registry_copy_used": str(live_registry),
        "live_scan_skipped": bool(no_live_scan),
    })
    return measurements


def self_check() -> Dict[str, Any]:
    """Output-boundary + safety self-check (no measurement, no write)."""
    resolved = assert_output_boundary(OUT_DIR)
    assert_no_source_write_tokens()
    try:
        assert_output_boundary(Path("/tmp"))
        escaped = True
    except ValueError:
        escaped = False
    return {
        "out_dir": str(resolved),
        "out_dir_inside_review_out": True,
        "escapes_when_asked_to_write_outside": escaped,
        "source_write_tokens_present": False,
        "live_registry_path": str(LIVE_REGISTRY),
        "writes_live_registry": False,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python review/mc_measure.py")
    parser.add_argument("--out", default=str(OUT_DIR))
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--no-live-scan", action="store_true")
    args = parser.parse_args(argv)
    out_dir = Path(args.out)
    assert_output_boundary(out_dir)
    if args.self_check:
        print(json.dumps(self_check(), indent=2, sort_keys=True))
        return 0
    measurements = run_all(no_live_scan=args.no_live_scan, out_dir=out_dir)
    summary = {k: v for k, v in measurements.items() if k.startswith("MG-")}
    print(json.dumps(summary, indent=2, sort_keys=True))
    print("wrote: {} {} {}".format(MEASURE_JSON, MEASURE_HTML, MANIFEST_JSON))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
