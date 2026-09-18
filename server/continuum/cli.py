"""Continuum CLI — G0 read-only inventory + candidate listing + TASK-HOME sync.

    python -m continuum.cli inventory [--hermes-home PATH] [--no-probe]
    python -m continuum.cli board|inbox|attention [--hermes-home PATH]
    python -m continuum.cli task-home sync   [--source PATH] [--dry-run] [--json]
    python -m continuum.cli task-home show   [--json]
    python -m continuum.cli task-home export [--out DIR] [--json]
    python -m continuum.cli action-log sync     [--dry-run] [--json]
    python -m continuum.cli action-log show     [--json]
    python -m continuum.cli action-log archive  <action_id> [--actor NAME] [--json]
    python -m continuum.cli action-log unarchive <action_id> [--actor NAME] [--json]
    python -m continuum.cli action-log undo     <audit_id> [--actor NAME] [--json]

Read-only against every source DB. Writes only to the plugin's own registry, and — for the
TASK-HOME pass only — its own ledger/lock under ``data/`` plus the generated export under
``review/out/`` (architecture §5). TASK-HOME.md is never written (TH-L1).

The action-log pass is the same shape for its OWN disjoint store: it reads the evidence sources
(AL-L1) and writes only ``data/action_log.{json,lock}`` and ``data/action_log_events.jsonl``
(§4 write boundary). Exit codes 0/1/3/4/5 (AL-L8).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List

from . import action_log
from . import scanner
from . import task_home
from .config import load_config
from .service import Service


def _cmd_inventory(args) -> int:
    cfg = load_config(args.hermes_home)
    if args.registry:
        cfg.bundle["registry_path"] = args.registry
    svc = Service(cfg)
    before = _stat(ids := [r.db_path for r in scanner.discover_profiles(cfg.hermes_home, cfg)])
    handle = svc.scan(probe=not args.no_probe)
    after = _stat(ids)
    status = svc.scan_status(handle.run_id)

    rows = list(svc.registry.conn.execute(
        "SELECT profile_name, status, session_count, message_count, schema_version "
        "FROM source_db ORDER BY profile_name"))
    print("Continuum G0 inventory  run_id={}".format(handle.run_id))
    print("{:<18}{:<9}{:>10}{:>12}{:>9}".format("profile", "status", "sessions", "messages", "schema"))
    total_s = total_m = 0
    for r in rows:
        print("{:<18}{:<9}{:>10}{:>12}{:>9}".format(
            r["profile_name"], r["status"], r["session_count"], r["message_count"],
            r["schema_version"]))
        total_s += r["session_count"] or 0
        total_m += r["message_count"] or 0
    print("{:<18}{:<9}{:>10}{:>12}".format("TOTAL", "", total_s, total_m))
    print("source DBs mtime/size/sidecar-unchanged: {} (byte identity verified separately)".format(before == after))
    noise = list(svc.registry.conn.execute(
        "SELECT noise_class, COUNT(*) n FROM session_fact WHERE is_noise=1 "
        "GROUP BY noise_class ORDER BY n DESC"))
    print("noise buckets: " + ", ".join("{}={}".format(r["noise_class"], r["n"]) for r in noise))
    inbox = svc.inbox()
    print("recovery inbox candidates: {}".format(inbox["total"]))
    for item in inbox["items"][:10]:
        print("  [{}] {}  sessions={} stall={}d  {}".format(
            item["confidence_band"], item["proposed_name"], len(item["member_refs"]),
            item["stall_age_days"], item["project_id"][:8]))
    print("scan phase={} last_error={}".format(status.get("phase"), status.get("last_error")))
    if args.json:
        print(json.dumps({"board": svc.board(), "inbox": inbox, "attention": svc.attention()},
                         default=str)[:4000])
    return 0


def _stat(paths: List[str]):
    out = {}
    for p in paths:
        try:
            st = os.stat(p)
            out[p] = (st.st_mtime, st.st_size, os.path.exists(p + "-wal"),
                      os.path.exists(p + "-shm"))
        except OSError:
            out[p] = None
    return out


def _cmd_surface(args) -> int:
    cfg = load_config(args.hermes_home)
    if args.registry:
        cfg.bundle["registry_path"] = args.registry
    svc = Service(cfg)
    data = {"board": svc.board, "inbox": svc.inbox, "attention": svc.attention,
            "staleness": svc.staleness, "noise": svc.noise}.get(args.command)
    if data is None:
        print("unknown surface", file=sys.stderr)
        return 2
    print(json.dumps(data(), indent=2, default=str))
    return 0


def _cmd_task_home(args) -> int:
    """The explicit, idempotent TASK-HOME pass (§5). Exit 0 / 3 / 4 / 5."""
    cfg = load_config(args.hermes_home)
    if args.registry:
        cfg.bundle["registry_path"] = args.registry
    sub = args.subcommand or "sync"

    if sub == "show":
        report = task_home.show(cfg, source_path=args.source)
        _print_task_home(report, args)
        return 0

    try:
        if sub == "sync":
            svc = Service(cfg)          # migrates/opens the plugin's own registry
            report = task_home.sync(cfg, source_path=args.source, registry=svc.registry,
                                    dry_run=bool(args.dry_run))
        else:                            # export
            svc = Service(cfg)
            report = task_home.export(cfg, svc.registry, out_dir=args.out,
                                      source_path=args.source)
    except task_home.SyncLocked as exc:
        print(json.dumps({"ok": False, "error": "SYNC_LOCKED", "lock_path": str(exc)})
              if args.json else "SYNC_LOCKED: another pass holds {}".format(exc))
        return task_home.EXIT_LOCKED
    except task_home.SourceMissing as exc:
        print(json.dumps({"ok": False, "error": "SOURCE_MISSING", "source_path": str(exc)})
              if args.json else "SOURCE_MISSING: {}".format(exc))
        return task_home.EXIT_SOURCE_MISSING

    _print_task_home(report, args)
    return int(report.get("exit_code", 0))


def _print_task_home(report: Dict[str, Any], args) -> None:
    if args.json:
        payload = dict(report)
        payload.pop("body_md", None)
        payload.pop("body_json", None)
        print(json.dumps(payload, indent=2, default=str))
        return
    if "items" in report and "exit_code" in report:
        print("TASK-HOME sync  run_id={}  source={}".format(
            report.get("run_id"), report.get("source_path")))
        print("  source_sha256={}".format(report.get("source_sha256")))
        print("  items={} sections={}".format(report.get("item_count"),
                                              report.get("section_counts")))
        print("  added={} changed={} unchanged={} absent={} writes={} dry_run={}".format(
            report.get("added"), report.get("changed"), report.get("unchanged"),
            report.get("absent"), report.get("writes"), report.get("dry_run")))
        print("  ledger={}".format(report.get("ledger_path")))
        print("  degraded={} informational={}".format(
            report.get("degraded"), report.get("informational")))
        for entry in report.get("conflicts") or []:
            print("  conflict [{}] {}: task_home={} vs dashboard={} (winner {})".format(
                entry.get("kind"), entry.get("key"), entry.get("task_home_value"),
                entry.get("dashboard_value"), entry.get("winner")))
        return
    if "paths" in report:
        print("TASK-HOME export  source={}".format(report.get("source_sha256")))
        print("  md={}".format(report["paths"]["md"]))
        print("  json={}".format(report["paths"]["json"]))
        print("  meta={}".format(report["paths"]["meta"]))
        counts = (report.get("payload") or {}).get("counts") or {}
        print("  counts={}".format(json.dumps(counts, sort_keys=True)))
        return
    print("TASK-HOME ledger  present={} source={}".format(
        report.get("ledger_present"), report.get("source_path")))
    print("  ledger={}".format(report.get("ledger_path")))
    print("  source_sha256={} last_run_id={} last_synced_at={}".format(
        report.get("source_sha256"), report.get("last_run_id"), report.get("last_synced_at")))
    for entry in report.get("bindings") or []:
        print("  binding {} -> {} [{}]".format(
            entry.get("key"), entry.get("project_id"), entry.get("section")))
    for entry in report.get("conflicts") or []:
        print("  conflict [{}] {}: task_home={} vs dashboard={} (winner {})".format(
            entry.get("kind"), entry.get("key"), entry.get("task_home_value"),
            entry.get("dashboard_value"), entry.get("winner")))


def _default_hermes_home() -> str:
    """The root that OWNS profiles/. An active profile's HERMES_HOME is nested and must not win."""
    env = os.environ.get("HERMES_HOME")
    if env and os.path.isdir(os.path.join(env, "profiles")):
        return env
    return os.path.expanduser("~/.hermes")


def _cmd_action_log(args) -> int:
    """The explicit, idempotent action-log pass (§4). Exit 0/1/3/4/5 (AL-L8)."""
    cfg = load_config(args.hermes_home)
    if args.registry:
        cfg.bundle["registry_path"] = args.registry
    sub = args.subcommand or "sync"

    try:
        if sub == "sync":
            report = action_log.sync(cfg, dry_run=bool(args.dry_run))
        elif sub == "show":
            report = action_log.show(cfg)
        elif sub in ("archive", "unarchive"):
            report = getattr(action_log, sub)(cfg, args.target_id, actor=args.actor or "local")
        elif sub == "undo":
            report = action_log.undo(cfg, args.target_id, actor=args.actor or "local")
        else:
            print("unknown action-log subcommand", file=sys.stderr)
            return 2
    except action_log.SyncLocked as exc:
        print(json.dumps({"ok": False, "error": "LOCKED", "lock_path": str(exc)})
              if args.json else "LOCKED: another pass holds {}".format(exc))
        return action_log.EXIT_LOCKED
    except action_log.SourceMissing as exc:
        print(json.dumps({"ok": False, "error": "SOURCE_MISSING", "paths": str(exc)})
              if args.json else "SOURCE_MISSING: {}".format(exc))
        return action_log.EXIT_SOURCE_MISSING
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": "ERROR", "detail": str(exc)})
              if args.json else "ERROR: {}".format(exc))
        return 2

    _print_action_log(report, args, sub)
    return int(report.get("exit_code", 0)) if sub in ("sync",) else 0


def _print_action_log(report: Dict[str, Any], args, sub: str) -> None:
    if args.json:
        payload = {k: v for k, v in report.items() if k != "items"}
        print(json.dumps(payload, indent=2, default=str))
        return
    if sub == "sync":
        print("action-log sync  run_id={}".format(report.get("run_id")))
        print("  ledger={}  written={}  dry_run={}".format(
            report.get("ledger_path"), report.get("written"), report.get("dry_run")))
        print("  sources={}  rows={}  added={} changed={} unchanged={} absent={}".format(
            report.get("source_count"), report.get("row_count"), report.get("added"),
            report.get("changed"), report.get("unchanged"), report.get("absent")))
        print("  source_hashes={}".format(json.dumps(report.get("source_hashes"), sort_keys=True)))
        print("  ledger_sha256 before={} after={}".format(
            report.get("ledger_sha256_before"), report.get("ledger_sha256_after")))
        print("  counts={}".format(json.dumps(report.get("counts"), sort_keys=True)))
        print("  exit={}  degraded={}  informational={}".format(
            report.get("exit_code"), report.get("degraded"),
            len(report.get("informational") or [])))
        return
    if sub == "show":
        print("action-log ledger  present={}  rows={}".format(
            report.get("ledger_present"), report.get("row_count")))
        print("  ledger={}  events={}  last_sync_at={}".format(
            report.get("ledger_path"), report.get("events"), report.get("last_sync_at")))
        print("  counts={}".format(json.dumps(report.get("counts"), sort_keys=True)))
        return
    print("action-log {}  ok={}  audit_id={}  noop={}".format(
        sub, report.get("ok"), report.get("audit_id"), report.get("noop")))
    if sub == "undo":
        print("  undoes={}  target_id={}".format(report.get("undoes"), report.get("target_id")))
    else:
        print("  target_id={}".format(report.get("target_id")))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="continuum")
    p.add_argument("command", choices=["inventory", "board", "inbox", "attention",
                                       "staleness", "noise", "task-home", "action-log"])
    p.add_argument("subcommand", nargs="?", default=None,
                   choices=["sync", "show", "export", "archive", "unarchive", "undo"],
                   help="task-home: sync | show | export; action-log: sync | show | archive | "
                        "unarchive | undo")
    p.add_argument("target_id", nargs="?", default=None,
                   help="action-log archive/unarchive: <action_id>; action-log undo: <audit_id>")
    p.add_argument("--hermes-home", default=_default_hermes_home())
    p.add_argument("--registry", default=None, help="override registry.db path")
    p.add_argument("--no-probe", action="store_true", help="skip message probing (fast)")
    p.add_argument("--json", action="store_true")
    # task-home only: the source is READ-ONLY in both directions (TH-L1).
    p.add_argument("--source", default=None, help="task-home: TASK-HOME source path (read-only)")
    p.add_argument("--dry-run", action="store_true",
                   help="task-home/action-log sync: compute, write nothing")
    p.add_argument("--out", default=None, help="task-home export: output directory")
    p.add_argument("--actor", default=None, help="action-log archive/unarchive/undo: audit actor")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "inventory":
        return _cmd_inventory(args)
    if args.command == "task-home":
        return _cmd_task_home(args)
    if args.command == "action-log":
        return _cmd_action_log(args)
    return _cmd_surface(args)


if __name__ == "__main__":
    raise SystemExit(main())
