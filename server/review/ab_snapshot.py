#!/usr/bin/env python3
"""Offline review harness for the Continuum MVP A/B overview.

D-AB-24..D-AB-28 + D-AB-25(4)-A: one command, no server, no port, no install, no network.
Generates deterministic fixture artifacts plus a point-in-time live sheet
from a byte copy of build/data/registry.db (the live file is never opened
for write). Writes:

  build/review/out/mode-immediate.json
  build/review/out/mode-accepted-only.json
  build/review/out/inbox.json
  build/review/out/live-mode-immediate.json        (if live copy succeeded)
  build/review/out/live-mode-accepted-only.json    (if live copy succeeded)
  build/review/out/live-inbox.json                 (if live copy succeeded)
  build/review/out/manifest.json
  build/review/out/ab-review.html                  (self-contained, inline CSS only)

Run: cd <build> && /Users/kethuda/.hermes/hermes-agent/venv/bin/python review/ab_snapshot.py
Prints the absolute path of ab-review.html and exits 0.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import sys
import tempfile
import time


def _block_egress() -> None:
    """AB-C8 applies to the harness too (D-AB-26): no network, no model call."""
    def _boom(*a, **k):
        raise AssertionError("review harness attempted network egress")
    socket.create_connection = _boom
    socket.socket.connect = _boom

BUILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BUILD_ROOT not in sys.path:
    sys.path.insert(0, BUILD_ROOT)

from continuum.config import load_config  # noqa: E402
from continuum.service import Service  # noqa: E402
from fixtures.make_fixture import build_fixture_tree  # noqa: E402

OUT_DIR = os.path.join(BUILD_ROOT, "review", "out")
LIVE_DB = os.path.join(BUILD_ROOT, "data", "registry.db")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: str, payload: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")


def overview_to_payload(svc: Service, mode: str) -> dict:
    return svc.overview(mode=mode)


def _tier_sentence(tier) -> str:
    if tier == 0:
        return "T0 — deterministic metadata"
    if tier == 1:
        return "T1 — rule-extracted"
    if tier == 2:
        return "T2 — model-inferred"
    return "T{} — unknown".format(tier)


def render_html(fixture_a: dict, fixture_b: dict,
                fixture_inbox: dict | None,
                live_a: dict | None, live_b: dict | None,
                live_inbox: dict | None,
                manifest: dict) -> str:
    def esc(s: str) -> str:
        return (s.replace("&", "&amp;").replace("<", "&lt;")
                 .replace(">", "&gt;").replace('"', "&quot;"))

    def compact_card(it: dict, show_accept: bool = False) -> str:
        name = esc(it.get("name", ""))
        pid = esc(it.get("project_id", ""))
        summary = esc(it.get("summary_text", ""))
        lifecycle_name = esc(it.get("lifecycle_name", "unknown"))
        quiet_days = it.get("quiet_days")
        quiet_since = it.get("quiet_since")
        if quiet_days is None:
            quiet_txt = "quiet unknown"
            quiet_time = '<span>quiet unknown</span>'
        else:
            # round as in service: quiet_days already rounded
            n = int(round(quiet_days)) if isinstance(quiet_days, (int, float)) else quiet_days
            quiet_txt = "{}d quiet".format(n) if isinstance(n, int) else "{} quiet".format(esc(str(quiet_days)))
            if quiet_since:
                try:
                    import datetime
                    dt = datetime.datetime.utcfromtimestamp(quiet_since).isoformat() + "Z"
                    quiet_time = '<time datetime="{}" title="{}">{}</time>'.format(esc(dt), esc(str(quiet_since)), esc(quiet_txt))
                except Exception:
                    quiet_time = esc(quiet_txt)
            else:
                quiet_time = esc(quiet_txt)
        # provenance chip
        tier = it.get("evidence_tier")
        band = it.get("confidence_band") or "unknown"
        conf = it.get("confidence")
        chip_label = "T{} \u00b7 {}".format(tier if tier is not None else "?", esc(str(band)))
        chip_tip = _tier_sentence(tier if tier is not None else "?")
        if conf is not None:
            chip_tip += " \u00b7 confidence {}".format(conf)
        chip_tip += " \u00b7 {}".format(band)
        chip_html = '<span class="badge" title="{}">{}</span>'.format(esc(chip_tip), esc(chip_label))

        # badges
        review_status = it.get("review_status", "candidate")
        review_label = esc(it.get("review_status_label", ""))
        badge_kind = "success" if review_status == "accepted" else "default"
        badge_text = "Accepted" if review_status == "accepted" else "Candidate"
        badge_html = '<span class="badge badge-{}" title="{}">{}</span>'.format(badge_kind, review_label, esc(badge_text))
        needs = it.get("needs_review")
        needs_html = '<span class="badge badge-warning">Needs review</span>' if needs else ""

        # context line duplicate handling: show if non-empty and duplicate? harness shows always if present?
        ctx_line = esc(it.get("context_line", ""))
        ctx_html = '<div class="ctx-line">{}</div>'.format(ctx_line) if ctx_line and ctx_line != ",".join(it.get("profiles", [])) else ""
        # For fixture, context_line is workspace label; show if non-empty
        if it.get("context_line"):
            ctx_html = '<div class="ctx-line">{}</div>'.format(esc(it.get("context_line", "")))

        # primary session
        sessions = it.get("sessions", [])
        if sessions:
            prim = sessions[0]
            title = esc(prim.get("title") or "untitled \u00b7 " + prim.get("session_id", "")[:12])
            pid_short = esc(prim.get("profile", "") + "/" + prim.get("session_id", ""))
            session_html = '<div class="sess-row"><span class="sess-title">{}</span> <code class="mono">{}</code> <button class="copy-btn" disabled>Copy ID</button></div>'.format(title, pid_short)
            if it.get("sessions_omitted"):
                session_html += '<div class="muted">+ {} more sessions \u25b8</div>'.format(it["sessions_omitted"])
        else:
            session_html = '<div class="muted">no linked session</div>'

        accept_html = ""
        if show_accept:
            accept_html = '<button class="accept-btn" disabled aria-busy="false">Accept</button> <span class="muted">demo only \u2014 acceptance happens in the live plugin</span>'

        card_aria = esc("{} \u00b7 {} \u00b7 {} \u00b7 {} \u00b7 {}".format(
            it.get("name", ""), lifecycle_name, quiet_txt, it.get("review_status_label", ""), it.get("inclusion_basis", "")))

        return (
            '<div class="card" role="listitem" aria-label="{}">'
            '<div class="card-row1"><span class="card-name" title="{}">{}</span> {} {} <span class="spacer"></span> {} {}</div>'
            '{}'
            '<div class="card-row2">Waiting on: {} \u00b7 {}</div>'
            '<div class="card-row3" title="{}">{}</div>'
            '{}'
            '</div>'
        ).format(
            card_aria,
            name, name, badge_html, needs_html, chip_html, accept_html,
            ctx_html,
            lifecycle_name, quiet_time,
            summary, esc(summary[:500] if summary else ""),
            session_html,
        )

    def full_details(it: dict) -> str:
        # Full §3 field set behind <details>, collapsed by default
        rows = []
        for k in sorted(it.keys()):
            v = it[k]
            # render sessions specially
            if k == "sessions" and isinstance(v, list):
                sess_str = "<br>".join(esc("{} / {} \u2014 {} \u2014 {}".format(s.get("profile",""), s.get("session_id",""), s.get("title",""), s.get("cli_resume_profile_scoped",""))) for s in v[:5])
                if len(v) > 5:
                    sess_str += "<br><em>+ {} more</em>".format(len(v)-5)
                rows.append("<tr><th>{}</th><td>{}</td></tr>".format(esc(k), sess_str))
            elif isinstance(v, (dict, list)):
                rows.append("<tr><th>{}</th><td><pre>{}</pre></td></tr>".format(esc(k), esc(json.dumps(v, indent=2, sort_keys=True, ensure_ascii=False)[:3000])))
            else:
                rows.append("<tr><th>{}</th><td>{}</td></tr>".format(esc(k), esc(str(v)[:1200])))
        return '<details><summary>Details \u25b8 full fields for {}</summary><table class="details-table"><tbody>{}</tbody></table></details>'.format(esc(it.get("project_id","")[:12]), "".join(rows))

    def overview_block(payload: dict, title: str) -> str:
        items = payload.get("items", [])
        counts = payload.get("counts", {})
        variant = esc(payload.get("variant_label", ""))
        mode = esc(payload.get("mode", ""))
        total = payload.get("total", 0)
        header = '<h3>{} \u2014 mode <code>{}</code> \u2014 {} \u2014 total {} \u2014 candidate {} accepted {} suppressed {}</h3><p class="muted">variant: {}</p>'.format(
            esc(title), mode, esc(variant), total,
            counts.get("candidate","?"), counts.get("accepted","?"), counts.get("suppressed_projects","?"),
            variant)
        if not items:
            return header + '<p class="muted">no items</p>'
        cards = []
        for it in items:
            cards.append(compact_card(it, show_accept=False) + full_details(it))
        return header + '<div role="list">' + "\n".join(cards) + '</div>'

    def inbox_block(inbox_payload: dict | None, title: str) -> str:
        if inbox_payload is None:
            return '<h3>{}</h3><p class="muted">inbox unavailable</p>'.format(esc(title))
        items = inbox_payload.get("items", [])
        total = inbox_payload.get("total", 0)
        header = '<h3>{} \u2014 total {}</h3>'.format(esc(title), total)
        if not items:
            return header + '<p class="muted">All caught up. Every candidate the scanner found has been reviewed.</p>'
        # Map inbox items to compact card shape for rendering
        cards = []
        for c in items:
            # Build pseudo overview item from inbox for compact rendering
            tier = c.get("evidence_tier")
            band = c.get("confidence_band") or "unknown"
            pseudo = {
                "project_id": c.get("project_id", ""),
                "name": c.get("proposed_name") or "(untitled cluster)",
                "context_line": "",
                "summary_text": ", ".join(c.get("signals", [])[:3]) or "candidate",
                "summary_tier": 0,
                "lifecycle": c.get("lifecycle", ""),
                "lifecycle_name": c.get("lifecycle", "unknown"),
                "quiet_days": c.get("stall_age_days"),
                "quiet_since": None,
                "quiet_band": "",
                "confidence": c.get("confidence"),
                "confidence_band": band,
                "evidence_tier": tier,
                "review_status": "candidate",
                "review_status_label": "derived candidate \u2014 not reviewed",
                "inclusion_basis": "derived candidate \u2014 no accepted link",
                "needs_review": True,
                "sessions": [{"profile": l.get("profile",""), "session_id": l.get("session_id",""), "title": l.get("title","")} for l in c.get("resume_links", [])[:20]],
                "sessions_omitted": max(0, len(c.get("member_refs", [])) - len(c.get("resume_links", []))),
                "profiles": [],
            }
            # full inbox fields behind details
            details_rows = []
            for k in sorted(c.keys()):
                v = c[k]
                if isinstance(v, (dict, list)):
                    details_rows.append("<tr><th>{}</th><td><pre>{}</pre></td></tr>".format(esc(k), esc(json.dumps(v, indent=2, sort_keys=True, ensure_ascii=False)[:3000])))
                else:
                    details_rows.append("<tr><th>{}</th><td>{}</td></tr>".format(esc(k), esc(str(v)[:1200])))
            details = '<details><summary>Details \u25b8 full fields for {}</summary><table class="details-table"><tbody>{}</tbody></table></details>'.format(esc(c.get("project_id","")[:12]), "".join(details_rows))
            cards.append(compact_card(pseudo, show_accept=True) + details)
        return header + '<div role="list">' + "\n".join(cards) + '</div>'

    fixture_ids_a = {i["project_id"] for i in fixture_a.get("items", [])}
    fixture_ids_b = {i["project_id"] for i in fixture_b.get("items", [])}
    only_a = len(fixture_ids_a - fixture_ids_b)
    only_b = len(fixture_ids_b - fixture_ids_a)

    live_section = ""
    if live_a is not None and live_b is not None:
        live_ids_a = {i["project_id"] for i in live_a.get("items", [])}
        live_ids_b = {i["project_id"] for i in live_b.get("items", [])}
        live_delta = "only in A: {} \u00b7 only in B: {} \u00b7 shared: {}".format(
            len(live_ids_a - live_ids_b), len(live_ids_b - live_ids_a), len(live_ids_a & live_ids_b))
        inbox_live = ""
        if live_inbox is not None:
            inbox_live = inbox_block(live_inbox, "Live \u2014 Review candidates (inbox)")
        live_section = (
            "<h2>Live corpus (point-in-time copy of build/data/registry.db)</h2>"
            "<p class=muted>This is a read-only byte copy taken at harness start; the live file was never opened for write.</p>"
            "<p><strong>Delta (live):</strong> {}</p>"
            "{}"
            "{}"
            "{}"
        ).format(esc(live_delta), overview_block(live_a, "Live \u2014 Variant A (immediate)"), overview_block(live_b, "Live \u2014 Variant B (accepted_only)"), inbox_live)
    else:
        live_section = "<h2>Live corpus</h2><p class=muted>live sheet unavailable \u2014 copy of build/data/registry.db failed or file absent</p>"

    fixture_inbox_html = inbox_block(fixture_inbox, "Fixture \u2014 Review candidates (inbox)")

    return """<!doctype html>
<html lang=en>
<meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>Continuum \u2014 A/B Review (offline, same-data, same-instant)</title>
<style>
  * {{ box-sizing: border-box }}
  body {{ font: 13px/1.45 ui-sans-system, system-ui, -apple-system, sans-serif; margin: 0; padding: 20px; color: #1a1a1a; background: #fafaf9 }}
  h1 {{ font-size: 20px; margin: 0 0 4px }}
  h2 {{ font-size: 16px; margin: 28px 0 8px; border-top: 1px solid #ddd; padding-top: 16px }}
  h3 {{ font-size: 13px; margin: 16px 0 6px }}
  p {{ margin: 6px 0 }}
  .muted {{ color: #6b6b6b; font-size: 12px }}
  .badge {{ display: inline-block; padding: 1px 6px; border-radius: 999px; background: #eee; border: 1px solid #ddd; font-size: 11px }}
  .badge-success {{ background: #dcfce7; border-color: #86efac }}
  .badge-warning {{ background: #fef3c7; border-color: #fcd34d }}
  .card {{ background: white; border: 1px solid #ddd; border-radius: 6px; padding: 10px; margin: 8px 0 }}
  .card-row1 {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap }}
  .card-name {{ font-size: 15px; font-weight: 600 }}
  .card-row2 {{ color: #6b6b6b; font-size: 12px; padding-top: 4px }}
  .card-row3 {{ padding-top: 4px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; font-size: 13px }}
  .sess-row {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; padding-top: 6px }}
  .sess-title {{ color: #2563eb }}
  .mono {{ font-family: ui-monospace, monospace; background: #f5f5f3; padding: 1px 4px; border-radius: 3px; font-size: 11px }}
  .copy-btn {{ padding: 2px 8px; border: 1px solid #ddd; border-radius: 4px; background: white; font-size: 11px }}
  .accept-btn {{ padding: 4px 10px; border: 1px solid #ddd; border-radius: 4px; background: #f0f0f0; font-size: 12px }}
  .spacer {{ flex: 1 }}
  details {{ margin: 4px 0 12px; background: #fafafa; border: 1px solid #eee; border-radius: 4px; padding: 6px 8px }}
  details summary {{ cursor: pointer; font-size: 12px; color: #6b6b6b }}
  .details-table {{ width: 100%; border-collapse: collapse; margin: 6px 0 }}
  .details-table th, .details-table td {{ text-align: left; padding: 4px 6px; border-bottom: 1px solid #eee; vertical-align: top; font-size: 11px }}
  .details-table th {{ background: #f5f5f3; width: 160px }}
  code {{ background: #eee; padding: 1px 4px; border-radius: 3px; font-size: 12px }}
  .banner {{ background: #fffbeb; border: 1px solid #fcd34d; padding: 10px 12px; border-radius: 6px; margin: 12px 0 }}
  .delta {{ background: white; border: 1px solid #ddd; padding: 10px 12px; border-radius: 6px; margin: 8px 0 }}
  pre {{ white-space: pre-wrap; word-break: break-word; font-size: 11px }}
</style>
<h1>Continuum \u2014 A/B Review Sheet</h1>
<p class=muted>Generated {ts} \u00b7 interpreter {interp} \u00b7 build root <code>{build_root}</code></p>
<div class=banner>
  <strong>Which variant is which:</strong> Variant A = <code>mode=immediate</code> \u2014 every non-suppressed materialized project, candidates shown as derived/unreviewed. Variant B = <code>mode=accepted_only</code> \u2014 only accepted projects (original board behavior). Both renderings are derived from <strong>one registry state, one instant, one process</strong>; the mode changes which items are present, never which fields an item carries. Candidates are <strong>unreviewed</strong> \u2014 displaying is never accepting. This sheet is a review artifact and not a claim that either variant is ready.
</div>
<div class=delta>
  <strong>Fixture corpus delta (deterministic, same scan):</strong> only in A: {only_a} \u00b7 only in B: {only_b} \u00b7 shared: {shared} \u00b7 A total {a_total} \u00b7 B total {b_total}<br>
  <span class=muted>Fixture: synthetic, 2 projects (tjgc1, tywebsite), 0 accepted. A shows both, B shows none. This is the gate fixture; the numbers are pinned by AB-F4.</span>
</div>
<h2>Fixture corpus (deterministic, throw-away home + registry under TMPDIR)</h2>
{fa}
{fb}
{fi}
{live_section}
<h2>Manifest</h2>
<pre style="background:white; border:1px solid #ddd; padding:10px; overflow:auto; font-size:11px">{manifest_json}</pre>
<h2>Decision</h2>
<p>After comparing the two renderings above:</p>
<ul>
  <li>Does seeing probable candidates immediately (A) help or add noise?</li>
  <li>Is the derived/unreviewed marking sufficient to prevent a candidate being mistaken for an accepted project?</li>
  <li>In variant A, is the Recovery Inbox still necessary as a separate surface?</li>
</ul>
<p><strong>Default mode:</strong> <code>immediate</code> | <code>accepted_only</code> \u2014 circle one. Third answer: <em>neither yet \u2014 needs changes</em>. Recorded by the user, not by an agent.</p>
<p class=muted>Manifest sha256: fixture-A {sha_a} \u00b7 fixture-B {sha_b}</p>
""".format(
        ts=esc(time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime())),
        interp=esc(sys.version.split()[0]),
        build_root=esc(BUILD_ROOT),
        only_a=only_a, only_b=only_b,
        shared=len(fixture_ids_a & fixture_ids_b),
        a_total=fixture_a.get("total", 0), b_total=fixture_b.get("total", 0),
        fa=overview_block(fixture_a, "Fixture \u2014 Variant A (immediate)"),
        fb=overview_block(fixture_b, "Fixture \u2014 Variant B (accepted_only)"),
        fi=fixture_inbox_html,
        live_section=live_section,
        manifest_json=esc(json.dumps(manifest, indent=2, sort_keys=True)),
        sha_a=manifest.get("artifacts", {}).get("mode-immediate.json", {}).get("sha256", "")[:12],
        sha_b=manifest.get("artifacts", {}).get("mode-accepted-only.json", {}).get("sha256", "")[:12],
    )


def main() -> int:
    _block_egress()
    os.makedirs(OUT_DIR, exist_ok=True)

    # 1. Deterministic fixture corpus in TMPDIR
    tmpdir = tempfile.mkdtemp(prefix="continuum-ab-")
    try:
        home = build_fixture_tree(os.path.join(tmpdir, "hermes_home"))
        cfg = load_config(hermes_home=home)
        cfg.bundle["registry_path"] = os.path.join(tmpdir, "registry.db")
        svc = Service(cfg)
        svc.scan()
        # Same-data, same-instant: two overviews + inbox from one registry state, one process
        fixture_a = overview_to_payload(svc, "immediate")
        fixture_b = overview_to_payload(svc, "accepted_only")
        fixture_inbox = svc.inbox()
    finally:
        pass  # keep tmpdir for manifest registry copy hash; cleaned below

    # 2. Live corpus via byte copy (never the live file for write)
    live_a = None
    live_b = None
    live_inbox = None
    live_copy = None
    live_copy_hash = None
    if os.path.exists(LIVE_DB):
        live_tmp = tempfile.mkdtemp(prefix="continuum-ab-live-")
        live_copy = os.path.join(live_tmp, "registry-copy.db")
        try:
            shutil.copyfile(LIVE_DB, live_copy)
            for ext in ("-wal", "-shm", "-journal"):
                src = LIVE_DB + ext
                if os.path.exists(src):
                    shutil.copyfile(src, live_copy + ext)
            live_copy_hash = sha256_file(live_copy)
            cfg2 = load_config(hermes_home=os.path.join(tmpdir, "nope"))
            cfg2.bundle["registry_path"] = live_copy
            svc2 = Service(cfg2)
            live_a = overview_to_payload(svc2, "immediate")
            live_b = overview_to_payload(svc2, "accepted_only")
            live_inbox = svc2.inbox()
        except Exception as exc:
            print("live sheet unavailable: {}".format(exc), file=sys.stderr)
            live_a = None
            live_b = None
            live_inbox = None

    # 3. Write payloads
    fa_path = os.path.join(OUT_DIR, "mode-immediate.json")
    fb_path = os.path.join(OUT_DIR, "mode-accepted-only.json")
    write_json(fa_path, fixture_a)
    write_json(fb_path, fixture_b)
    fi_path = os.path.join(OUT_DIR, "inbox.json")
    write_json(fi_path, fixture_inbox)
    la_path = os.path.join(OUT_DIR, "live-mode-immediate.json")
    lb_path = os.path.join(OUT_DIR, "live-mode-accepted-only.json")
    li_path = os.path.join(OUT_DIR, "live-inbox.json")
    if live_a is not None:
        write_json(la_path, live_a)
        write_json(lb_path, live_b)
        write_json(li_path, live_inbox)

    # 4. Manifest
    manifest = {
        "generated_at": time.time(),
        "generated_at_human": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "interpreter": sys.version,
        "build_root": BUILD_ROOT,
        "fixture_tmpdir": tmpdir,
        "live_copy_sha256": live_copy_hash,
        "artifacts": {
            "mode-immediate.json": {"path": fa_path, "sha256": sha256_file(fa_path)},
            "mode-accepted-only.json": {"path": fb_path, "sha256": sha256_file(fb_path)},
            "inbox.json": {"path": fi_path, "sha256": sha256_file(fi_path)},
        },
    }
    if live_a is not None:
        manifest["artifacts"]["live-mode-immediate.json"] = {"path": la_path, "sha256": sha256_file(la_path)}
        manifest["artifacts"]["live-mode-accepted-only.json"] = {"path": lb_path, "sha256": sha256_file(lb_path)}
        manifest["artifacts"]["live-inbox.json"] = {"path": li_path, "sha256": sha256_file(li_path)}
    if live_copy is not None and os.path.exists(live_copy):
        manifest["artifacts"]["registry-copy.db"] = {"path": live_copy, "sha256": live_copy_hash}

    manifest_path = os.path.join(OUT_DIR, "manifest.json")
    write_json(manifest_path, manifest)

    # 5. HTML
    html = render_html(fixture_a, fixture_b, fixture_inbox, live_a, live_b, live_inbox, manifest)
    html_path = os.path.join(OUT_DIR, "ab-review.html")
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    manifest["artifacts"]["ab-review.html"] = {"path": html_path, "sha256": sha256_file(html_path)}
    # rewrite manifest with html hash
    write_json(manifest_path, manifest)

    print(html_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
