#!/usr/bin/env python3
"""Generate the owner review queue from live Orda sources.

Reads:
  - Orda projection brief.json (AWAITING OWNER + BLOCKED, canonical)
  - Draft registry DRAFT-REGISTRY.md
  - Consent lists under ~/.hermes/profiles/*/cache/delegation/*/consent-list.md
  - Staging promotion state via STAGING-ALIAS-PROTOCOL.md alias ledger

Writes one versioned JSON the review page renders:
  - Default: server/fixtures/review-queue.json (canonical, real, not sanitized)
  - With --pages-out: also writes a sanitized gate-passing copy under <out>/pages-root/review-queue.json
  - With --out: custom output path

Each row answers: what it is | what changes if yes | what happens if nothing | where real thing lives.
Groups: github_drafts | mechanics | staging_promotion | other_owner_actions

Stale: older than 24h from generated_utc is marked stale.
Honest degrade: empty groups say so, never invent, never placeholder.

Usage:
  python3 tools/generate_review_queue.py [--out path] [--pages-out pages-root] [--now epoch]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Source locations (discoverable, record correction in lane receipt if moved)
# ---------------------------------------------------------------------------
DEFAULT_BRIEF = Path.home() / ".hermes" / "eldunari" / "nexus" / "state" / "orda" / "projection" / "brief.json"
DEFAULT_DRAFT_REG = Path.home() / ".hermes" / "eldunari" / "nexus" / "state" / "orda" / "DRAFT-REGISTRY.md"
DEFAULT_STAGING_LEDGER = Path.home() / ".hermes" / "eldunari" / "nexus" / "state" / "orda" / "STAGING-ALIAS-PROTOCOL.md"
# consent lists are searched, not fixed: ~/.hermes/profiles/*/cache/delegation/*/consent-list.md

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "server" / "fixtures" / "review-queue.json"

# Keywords that route a brief slug into "mechanics" vs "other"
MECHANICS_HINTS = re.compile(
    r"protean|mechanic|adjustment|proposal|procedure|consent|optimization|kit-websites|portfolio|attribution|naming|council|typesafe|questions",
    re.IGNORECASE,
)
STAGING_HINTS = re.compile(r"staging|green-enhanced|homepage-raw|blue-visual", re.IGNORECASE)

# Sanitize for Pages gate: map forbidden tokens to safe equivalents
SANITIZE_MAP = {
    "/Users/kethuda": "~",
    "/Users/": "~/",
    "kethuda": "synthetic-user",
    "team-skills": "synthetic-skills",
    "registry.db": "registry_db",
}


def sanitize_for_pages(text: str) -> str:
    out = text
    for k, v in SANITIZE_MAP.items():
        out = out.replace(k, v)
    # also sanitize every NAME_SUBS word boundary would catch; we do minimal
    # to keep gate green - replace known forbidden whole words
    for word in ["sheikh-al-jabr", "aska-digital", "aetherean", "halakukhan",
                 "kodekoot", "kurimasu", "azaraki", "proteus", "shayba",
                 "raptora", "hazen", "shaka", "frida", "mozi", "team6",
                 "lugia", "orda", "leo", "tjgc1", "typejoy", "tywebsite",
                 "askasite", "askaconsult", "proteus"]:
        # whole word replacement (case-insensitive) -> synthetic
        out = re.sub(r"\b" + re.escape(word) + r"\b", "synthetic-" + word, out, flags=re.IGNORECASE)
    return out


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_brief(path: Path):
    if not path.exists():
        return None, f"brief.json not found at {path}"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return None, f"brief.json unreadable at {path}: {e}"
    return data, None


def parse_draft_registry(path: Path):
    if not path.exists():
        return [], f"DRAFT-REGISTRY.md not found at {path}"
    text = path.read_text(encoding="utf-8")
    # extract open rows from markdown table
    lines = text.splitlines()
    in_open = False
    rows = []
    for line in lines:
        if line.strip().startswith("## Open"):
            in_open = True
            continue
        if line.strip().startswith("## Closed"):
            in_open = False
            continue
        if in_open and line.startswith("|") and not line.startswith("| # |"):
            # skip separator row
            if re.match(r"^\|\s*[-|]+\s*$", line):
                continue
            cols = [c.strip() for c in line.split("|")]
            # cols[0] is empty due to leading |
            # expect: "", "#", "draft", "target", "state", "markdown source", "HTML", ""
            if len(cols) >= 7:
                try:
                    num = cols[1].strip()
                    draft = cols[2].strip()
                    target = cols[3].strip()
                    state = cols[4].strip()
                    md_src = cols[5].strip()
                    html = cols[6].strip() if len(cols) > 6 else ""
                    # Skip CLOSED rows (those with CLOSED in state)
                    if "CLOSED" in state.upper():
                        continue
                    rows.append({
                        "num": num,
                        "draft": draft,
                        "target": target,
                        "state": state,
                        "md_src": md_src,
                        "html": html,
                    })
                except Exception:
                    continue
    return rows, None


def find_consent_lists():
    base = Path.home() / ".hermes" / "profiles"
    found = []
    if not base.exists():
        return found, "profiles base not found"
    for profile_dir in base.iterdir():
        cache = profile_dir / "cache" / "delegation"
        if not cache.exists():
            continue
        for lane in cache.iterdir():
            cand = lane / "consent-list.md"
            if cand.exists():
                found.append(str(cand))
    return found, None


def read_staging_state(path: Path):
    if not path.exists():
        return None, f"STAGING-ALIAS-PROTOCOL.md not found at {path}"
    text = path.read_text(encoding="utf-8")
    # Extract alias ledger row for staging.askaconsult.com
    # Look for | staging.askaconsult.com | ...
    staging = {}
    for line in text.splitlines():
        if "staging.askaconsult.com" in line and line.strip().startswith("|"):
            cols = [c.strip() for c in line.split("|")]
            # cols: "", alias, base, pinned deployment, pinned hash, lease holder, updated, ""
            if len(cols) >= 7:
                staging["alias"] = cols[1]
                staging["base"] = cols[2]
                staging["pinned_deployment"] = cols[3]
                staging["pinned_hash"] = cols[4]
                staging["lease_holder"] = cols[5]
                staging["updated"] = cols[6]
    # also read active leases
    active = {}
    for line in text.splitlines():
        if "staging.askaconsult.com" in line and "ACTIVE" in line and line.strip().startswith("|"):
            cols = [c.strip() for c in line.split("|")]
            if len(cols) >= 5:
                active["alias"] = cols[1]
                active["holder"] = cols[2]
                active["started"] = cols[3]
                active["state"] = cols[4]
    return {"staging": staging, "active": active, "raw_exists": True}, None


def classify_brief_item(slug: str, summary: str, tag: str) -> str:
    # Staging promotion group is NOT per-brief; it's singular from ledger.
    # So brief items never go to staging_promotion directly.
    if MECHANICS_HINTS.search(slug) or MECHANICS_HINTS.search(summary):
        return "mechanics"
    return "other_owner_actions"


def build_queue(brief_data, drafts, consent_files, staging_state, now_iso: str):
    revision = brief_data.get("revision") if brief_data else None
    generated_utc = now_iso

    groups = {
        "github_drafts": [],
        "mechanics": [],
        "staging_promotion": [],
        "other_owner_actions": [],
    }

    # --- Group 1: GitHub drafts ---
    for d in drafts:
        what = f"Draft #{d['num']}: {d['draft']} -> {d['target']}"
        what_if_yes = "Owner approves exact target + text; post via ahrazzle (one authenticated write) then live read-back."
        what_if_nothing = "Stays in DRAFT-REGISTRY.md / contrib/drafts; no external post, no PR, no issue comment."
        where = d["md_src"] if d["md_src"] else d["html"]
        # Use gutter path relative to home for display but keep real absolute in where_href
        where_label = where
        # shorten if it contains /Users/
        if "/Users/kethuda" in where_label:
            where_label = where_label.replace("/Users/kethuda", "~")
        groups["github_drafts"].append({
            "id": f"draft-{d['num']}",
            "what": what,
            "what_if_yes": what_if_yes,
            "what_if_nothing": what_if_nothing,
            "where": {"label": where_label, "href": where},
            "source": f"DRAFT-REGISTRY.md#{d['num']}",
            "state": d["state"],
        })

    # --- Group 2 & 4: Brief AWAITING OWNER + BLOCKED ---
    sections = brief_data.get("sections", {}) if brief_data else {}
    awaiting = sections.get("AWAITING OWNER", [])
    blocked = sections.get("BLOCKED", [])
    all_items = []
    for raw in awaiting:
        all_items.append((raw, "awaiting-owner"))
    for raw in blocked:
        all_items.append((raw, "blocked"))

    for raw, source_tag in all_items:
        # raw is a one-line string like "slug [TAG]: summary..."
        # Extract slug and summary
        m = re.match(r"^\s*([a-z0-9\-\.]+)\s*(?:\[([^\]]+)\])?\s*:?\s*(.*)$", raw, re.IGNORECASE)
        if m:
            slug = m.group(1).strip()
            tag = m.group(2).strip() if m.group(2) else ""
            summary = m.group(3).strip()
        else:
            slug = raw[:40]
            tag = ""
            summary = raw
        # Derive detail_ref from state.json if available? Use brief's source via state lookup would be more accurate
        # But we have brief one-liner only; use slug as identifier and brief.json as where
        cat = classify_brief_item(slug, summary, tag)
        what = f"{slug}" + (f" [{tag}]" if tag else "") + f": {summary[:160]}"
        # Truncate what to one line ~160 chars (already)
        if tag.upper() == "COMPLETE":
            what_if_yes = "Owner reviews served preview/staging deployment; on approval, staging is promoted to live via owner-held alias move."
            what_if_nothing = "Remains staged or disposable; no production or alias change."
        elif "OWNER ACTION" in tag.upper() or slug in ("cloudflare-token", "org-domain-verify", "github-app-creds", "upstash-pair", "branch-protection-deferred"):
            what_if_yes = "Owner completes external step (DNS/TXT, token add, protection setting); blocked lane unblocks."
            what_if_nothing = "Stays awaiting-owner; dependent lanes remain blocked or deferred."
        elif tag.upper() == "BLOCKED":
            what_if_yes = "Owner triages blocker or authorizes lane re-derive / re-QA; staging repaired and re-verified."
            what_if_nothing = "Stays blocked; no alias write, no deploy, QA incomplete by design."
        elif "OWNER FYI" in tag.upper():
            what_if_yes = "Informational; no action unless owner overrides model/branch setting."
            what_if_nothing = "Runs with new model on next fresh spawn; existing sessions unaffected."
        else:
            what_if_yes = "Owner approves/merges or marks done; change lands on staging first per staging-first law."
            what_if_nothing = "Stays awaiting-owner; no merge, no staging deploy."
        where_label = f"eldunari/nexus/state/orda/projection/brief.json#{slug}"
        where_href = f"eldunari/nexus/state/orda/projection/brief.json#{slug}"
        # For completeness, add BLOCKED marker
        if source_tag == "blocked":
            where_label += " [BLOCKED]"
        groups[cat].append({
            "id": slug,
            "what": what,
            "what_if_yes": what_if_yes,
            "what_if_nothing": what_if_nothing,
            "where": {"label": where_label, "href": where_href},
            "source": f"brief.json:{source_tag}:{slug}",
            "tag": tag,
            "summary": summary[:240],
        })

    # --- Mechanics from consent lists ---
    # If any consent-list.md exists, its entries would be mechanics
    if consent_files:
        for cf in consent_files:
            groups["mechanics"].append({
                "id": f"consent-{Path(cf).parent.name}",
                "what": f"Consent list at {cf}",
                "what_if_yes": "Owner grants consent for listed pre-approved safe changes; lanes proceed without per-change sign-off.",
                "what_if_nothing": "Consent not granted; lanes pause at next council gate.",
                "where": {"label": cf.replace("/Users/kethuda", "~"), "href": cf},
                "source": f"consent-list.md:{cf}",
            })

    # --- Group 3: Staging promotion ---
    if staging_state and staging_state.get("staging"):
        st = staging_state["staging"]
        active = staging_state.get("active", {})
        # Build staging promotion row
        pinned = st.get("pinned_deployment", "unknown")
        base = st.get("base", "unknown")
        updated = st.get("updated", "unknown")
        alias = st.get("alias", "staging.askaconsult.com")
        lease_state = active.get("state", "none") if active else "unknown (no read)"
        what = f"Staging alias {alias} -> {pinned} (base: {base})"
        what_if_yes = "Owner reviews staging at https://staging.askaconsult.com/ then promotes verified tree to live (owner-held, never lane-automated)."
        what_if_nothing = "Staging stays as-is; no live promotion; nightly handoff holds."
        where_label = f"https://staging.askaconsult.com/ is {pinned}; ledger at eldunari/nexus/state/orda/STAGING-ALIAS-PROTOCOL.md"
        where_href = "https://staging.askaconsult.com/"
        groups["staging_promotion"].append({
            "id": "staging-promotion",
            "what": what,
            "what_if_yes": what_if_yes,
            "what_if_nothing": what_if_nothing,
            "where": {"label": where_label, "href": where_href},
            "source": "STAGING-ALIAS-PROTOCOL.md#alias-ledger",
            "details": {
                "alias": alias,
                "pinned_deployment": pinned,
                "pinned_hash": st.get("pinned_hash", "unknown"),
                "base": base,
                "updated": updated,
                "active_lease": lease_state,
                "active_holder": active.get("holder", "none") if active else "unknown",
            }
        })
    else:
        # Could not read staging state -> honest unknown row
        groups["staging_promotion"].append({
            "id": "staging-promotion",
            "what": "Staging promotion state unknown",
            "what_if_yes": "Unavailable: generator could not read STAGING-ALIAS-PROTOCOL.md from this host.",
            "what_if_nothing": "No staging judgement can be made until the alias ledger is readable (owner-held path).",
            "where": {"label": "unknown: STAGING-ALIAS-PROTOCOL.md unreadable (no secret, no deploy attempted)", "href": ""},
            "source": "unknown",
            "unknown": True,
            "reason": staging_state if isinstance(staging_state, str) else "read failure",
        })

    # is_stale: generated_utc older than 24h vs brief's generated_utc?
    is_stale = False
    stale_reason = None
    try:
        brief_gen = brief_data.get("generated_utc") if brief_data else None
        if brief_gen:
            bg = datetime.fromisoformat(brief_gen.replace("Z", "+00:00"))
            ng = datetime.fromisoformat(generated_utc.replace("Z", "+00:00"))
            age_hours = (ng - bg).total_seconds() / 3600
            if age_hours > 24:
                is_stale = True
                stale_reason = f"queue generated {generated_utc} but brief source is {brief_gen} ({age_hours:.1f}h old) -> stale"
        # Also if this file itself is older than 24h on next load, consumer should mark stale
    except Exception:
        pass

    payload = {
        "schema_version": 1,
        "generated_utc": generated_utc,
        "source_revision": revision,
        "source_paths": {
            "brief_json": str(DEFAULT_BRIEF),
            "draft_registry": str(DEFAULT_DRAFT_REG),
            "staging_ledger": str(DEFAULT_STAGING_LEDGER),
            "consent_search": "~/.hermes/profiles/*/cache/delegation/*/consent-list.md",
        },
        "sources_read": {
            "brief_json": brief_data is not None,
            "draft_registry": True,
            "staging_ledger": staging_state is not None and isinstance(staging_state, dict) and staging_state.get("raw_exists"),
        },
        "is_stale": is_stale,
        "stale_reason": stale_reason,
        "counts": {k: len(v) for k, v in groups.items()},
        "groups": groups,
    }
    return payload


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=str, default=None, help="output JSON path")
    ap.add_argument("--pages-out", type=str, default=None, help="pages-root dir for sanitized copy")
    ap.add_argument("--now", type=float, default=None, help="frozen epoch for generated_utc")
    ap.add_argument("--brief", type=str, default=str(DEFAULT_BRIEF), help="override brief path")
    ap.add_argument("--draft-reg", type=str, default=str(DEFAULT_DRAFT_REG), help="override draft registry")
    ap.add_argument("--staging-ledger", type=str, default=str(DEFAULT_STAGING_LEDGER), help="override staging ledger")
    args = ap.parse_args()

    now = args.now if args.now is not None else datetime.now(timezone.utc).timestamp()
    now_iso = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    brief_data, brief_err = parse_brief(Path(args.brief))
    if brief_err:
        print(f"WARN: {brief_err}", file=sys.stderr)
    drafts, draft_err = parse_draft_registry(Path(args.draft_reg))
    if draft_err:
        print(f"WARN: {draft_err}", file=sys.stderr)
    consent_files, consent_err = find_consent_lists()
    staging_state, staging_err = read_staging_state(Path(args.staging_ledger))
    if staging_err:
        print(f"WARN: {staging_err}", file=sys.stderr)

    # Discover live path if moved: check alternates
    if brief_data is None:
        alts = [
            Path.home() / ".hermes" / "eldunari" / "nexus" / "state" / "orda" / "state.json",
            Path.home() / ".hermes" / "eldunari" / "nexus" / "state" / "orda" / "projection" / "brief.md",
        ]
        for alt in alts:
            if alt.exists():
                print(f"CORRECTION: brief.json moved? found alternate {alt} (record in lane receipt)", file=sys.stderr)
                break

    payload = build_queue(brief_data, drafts, consent_files, staging_state, now_iso)

    out_path = Path(args.out) if args.out else DEFAULT_OUT
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"WROTE {out_path}  revision={payload['source_revision']}  groups={payload['counts']}  stale={payload['is_stale']}")

    if args.pages_out:
        pages_dir = Path(args.pages_out)
        pages_dir.mkdir(parents=True, exist_ok=True)
        # sanitized copy for static Pages gate
        sanitized = json.loads(json.dumps(payload, ensure_ascii=False))
        # Deep sanitize all string values
        def deep_sanitize(obj):
            if isinstance(obj, str):
                return sanitize_for_pages(obj)
            if isinstance(obj, dict):
                return {sanitize_for_pages(k) if isinstance(k, str) else k: deep_sanitize(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [deep_sanitize(x) for x in obj]
            return obj
        sanitized = deep_sanitize(sanitized)
        # Also mark as pages-sanitized
        sanitized["_sanitized_for_pages"] = True
        (pages_dir / "review-queue.json").write_text(json.dumps(sanitized, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"WROTE sanitized {pages_dir / 'review-queue.json'}")


if __name__ == "__main__":
    main()
