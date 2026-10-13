"""Decisions command centre feed: the org-wide `needs decision` GitHub label.

One collector, one local snapshot, one append-only action ledger. GitHub is the
source of truth for what waits; this module only projects it and records what
the maintainer chose. Two action modes, per the owner's ruling:

- ``comment``: the ruling is posted on the issue or PR and the label comes off,
  so the author proceeds from the comment.
- ``dispatch``: a durable dispatch packet (JSON + brief) is written for an
  agent to apply the ruling (merge, salvage with authorship kept, make the
  follow-up PR), the ruling is announced on the item, and the label comes off.

Ion tracker proposals are the one special case: ion decides by label and never
by comment, so a ruling there applies `ion:approved` / `ion:hold` /
`ion:rejected` instead of posting.

Everything GitHub-shaped goes through one runner seam (`gh api`), so tests fake
the network completely. Actions are stale-checked against a fingerprint of the
item as the maintainer saw it, and idempotent on a caller-supplied key.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import urllib.parse
from typing import Any, Callable, Dict, List, Optional

from .config import PLUGIN_DIR

VERSION = 1
EXIT_OK = 0
EXIT_LOCKED = 3
EXIT_SOURCE_MISSING = 4

Runner = Callable[..., str]


class DecisionsError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class SyncLocked(Exception):
    pass


# --------------------------------------------------------------- config paths

def _get(cfg: Any, key: str, default: Any = None) -> Any:
    if cfg is None:
        return default
    if isinstance(cfg, dict):
        return cfg.get(key, default)
    getter = getattr(cfg, "get", None)
    return getter(key, default) if callable(getter) else default


def _resolve(cfg: Any, key: str, default: str) -> str:
    value = _get(cfg, key, default) or default
    path = os.path.expanduser(str(value))
    return path if os.path.isabs(path) else str((PLUGIN_DIR / path).resolve())


def snapshot_path(cfg: Any) -> str:
    return _resolve(cfg, "decisions_snapshot_path", "data/decisions.json")


def events_path(cfg: Any) -> str:
    return _resolve(cfg, "decisions_events_path", "data/decision_events.jsonl")


def dispatch_dir(cfg: Any) -> str:
    return _resolve(cfg, "decisions_dispatch_dir", "data/decision_dispatches")


def _org(cfg: Any) -> str:
    return str(_get(cfg, "decisions_org", "aska-digital") or "aska-digital")


def _label(cfg: Any) -> str:
    return str(_get(cfg, "decisions_label", "needs decision") or "needs decision")


# --------------------------------------------------------------- small helpers

class _Lock:
    def __init__(self, path: str):
        self.path = path
        self.fd = None

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        try:
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError as exc:
            raise SyncLocked(self.path) from exc
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass
        return False


def _lock_for(snapshot_file: str) -> _Lock:
    return _Lock(snapshot_file + ".lock")


def _now(now: Optional[float]) -> float:
    return float(now if now is not None else time.time())


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _age_days(created: str, now: float) -> float:
    try:
        born = time.mktime(time.strptime(created, "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
    except Exception:
        return 0.0
    return round(max(0.0, (now - born) / 86400.0), 1)


def _slug(text: str, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:48]
    return slug or fallback


def _clean(text: str) -> str:
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text or "")
    text = re.sub(r"`([^`]+)`", r"\1", text)
    return re.sub(r"\s+", " ", text).strip(" -*")


def _excerpt(body: str, limit: int = 640) -> str:
    lines = []
    for raw in (body or "").splitlines():
        line = raw.strip()
        if not line or line.startswith((">", "Automated posting", "<!--")):
            continue
        if line.startswith("#"):
            continue
        lines.append(_clean(line))
        if sum(len(x) + 1 for x in lines) > limit:
            break
    out = " ".join(lines)
    return out[:limit].rstrip() + ("..." if len(out) > limit else "")


# --------------------------------------------------------------- github seam

def _default_runner(argv: List[str], stdin: Optional[str] = None) -> str:
    proc = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise DecisionsError("github call failed: " + (proc.stderr or proc.stdout or "")[:240],
                             status_code=502)
    return proc.stdout


class Gh:
    """Thin `gh api` wrapper. Every call is one argv list, never a shell string."""

    def __init__(self, runner: Optional[Runner] = None):
        self.run = runner or _default_runner

    def get(self, path: str) -> Any:
        return json.loads(self.run(["gh", "api", path]) or "{}")

    def post(self, path: str, body: Dict[str, Any]) -> Any:
        out = self.run(["gh", "api", "--method", "POST", path, "--input", "-"],
                       stdin=json.dumps(body))
        return json.loads(out or "{}")

    def delete(self, path: str) -> None:
        self.run(["gh", "api", "--method", "DELETE", path])


def _repo_of(item: Dict[str, Any]) -> str:
    url = item.get("repository_url") or ""
    return url.rstrip("/").rsplit("/", 1)[-1]


def _search(cfg: Any, gh: Gh) -> List[Dict[str, Any]]:
    org, label = _org(cfg), _label(cfg)
    items: List[Dict[str, Any]] = []
    for page in range(1, 11):
        q = urllib.parse.urlencode({
            "q": 'org:{} label:"{}" state:open'.format(org, label),
            "per_page": 100, "page": page, "sort": "created", "order": "asc"})
        data = gh.get("search/issues?" + q)
        batch = data.get("items") or []
        items.extend(batch)
        if len(batch) < 100:
            break
    return items


# --------------------------------------------------------------- option panels

_INTENT_WORDS = (
    ("salvage", ("salvage", "cherry-pick", "cherry pick", "clean pr", "clean branch")),
    ("merge", ("merge", "ship it", "ship the", "land it")),
    ("close", ("close", "drop it", "abandon", "not planned", "reject")),
    ("hold", ("hold", "defer", "wait", "later")),
    ("approve", ("approve", "go ahead", "proceed")),
)


def _intent_of(label: str, kind: str) -> str:
    low = (label or "").lower()
    for intent, words in _INTENT_WORDS:
        if any(w in low for w in words):
            return intent
    return "custom" if kind == "issue" else "custom"


def _mk_option(idx: int, label: str, detail: str, kind: str, source: str,
               intent: Optional[str] = None, cost: str = "",
               recommended: bool = False, worst: bool = False) -> Dict[str, Any]:
    label = _clean(label)[:90] or "Option {}".format(idx + 1)
    return {
        "id": "{}-{}".format(idx + 1, _slug(label, "option")),
        "key": chr(ord("A") + idx) if idx < 26 else str(idx + 1),
        "label": label,
        "detail": _clean(detail)[:320],
        "cost": _clean(cost)[:220],
        "intent": intent or _intent_of(label, kind),
        "source": source,
        "recommended": bool(recommended),
        "worst": bool(worst),
        "modes": ["comment", "dispatch"],
    }


_SECTION_RE = re.compile(r"^#{1,4}\s+.*(decision|options|choices)\b.*$", re.IGNORECASE)
_BULLET_RE = re.compile(r"^(\s*)[-*]\s+(?:\[[ xX]\]\s*)?(.+)$")
_NUMBER_RE = re.compile(r"^(\s*)\d+[.)]\s+(.+)$")


def _explicit_options(text: str, kind: str) -> List[Dict[str, Any]]:
    """Options stated by the author under a decision/options heading."""
    lines = (text or "").splitlines()
    found: List[Dict[str, Any]] = []
    in_section = False
    current: Optional[Dict[str, Any]] = None
    for raw in lines:
        if raw.startswith("#"):
            in_section = bool(_SECTION_RE.match(raw.strip()))
            current = None
            continue
        if not in_section:
            continue
        m = _BULLET_RE.match(raw) or _NUMBER_RE.match(raw)
        if not m:
            if current is not None and raw.strip():
                current["_detail"] = (current.get("_detail", "") + " " + raw.strip()).strip()
            continue
        indent, content = len(m.group(1)), m.group(2).strip()
        if indent >= 2 and current is not None:
            current["_detail"] = (current.get("_detail", "") + " " + content).strip()
            continue
        bold = re.match(r"\*\*([^*]+)\*\*\s*[:.\-]?\s*(.*)$", content)
        if bold:
            label, rest = bold.group(1), bold.group(2)
        else:
            label, _, rest = content.partition(":")
            if not rest:
                label, _, rest = content.partition(". ")
        current = {"label": label, "_detail": rest}
        found.append(current)
    out = []
    for i, entry in enumerate(found[:4]):
        detail = entry.get("_detail", "")
        cost = ""
        cm = re.search(r"cost:\s*(.+)$", detail, re.IGNORECASE)
        if cm:
            cost, detail = cm.group(1), detail[: cm.start()].rstrip(" .")
        out.append(_mk_option(i, entry["label"], detail, kind, "explicit", cost=cost))
    return out


def _either_or_options(text: str, kind: str) -> List[Dict[str, Any]]:
    plain = _clean(re.sub(r"^#.*$", " ", text or "", flags=re.MULTILINE))
    m = re.search(r"either\s+(.{8,120}?),\s*or\s+(.{8,120}?)(?:\.|;|$)", plain, re.IGNORECASE)
    if not m:
        m = re.search(r"(?:compares?|comparing)\s+(?:three|two|\d+)\s+ways[^:]*:\s*(.{10,240}?)(?:\.|$)",
                      plain, re.IGNORECASE)
        if m:
            parts = [p.strip(" ") for p in re.split(r";|,\s+or\s+", m.group(1)) if p.strip()]
            parts = [p[4:] if p.lower().startswith("or ") else p for p in parts]
            if len(parts) >= 2:
                return [_mk_option(i, p[:1].upper() + p[1:], "", kind, "parsed")
                        for i, p in enumerate(parts[:3])]
        return []
    a, b = m.group(1).strip(), m.group(2).strip()
    a = re.sub(r"^(pick|choose|take)\s+", "", a, flags=re.IGNORECASE)
    return [_mk_option(0, a[:1].upper() + a[1:], "", kind, "parsed"),
            _mk_option(1, b[:1].upper() + b[1:], "", kind, "parsed")]


def _mark_recommendation(opts: List[Dict[str, Any]], text: str) -> List[Dict[str, Any]]:
    low = (text or "").lower()
    m = re.search(r"(?:recommend(?:s|ation)?|preferred(?:\s+option)?)\s*[:\-]?\s+([a-z0-9 ,\-]{3,60})", low)
    if not m:
        return opts
    wanted = {w for w in re.findall(r"[a-z]{4,}", m.group(1))}
    best, best_score = None, 0
    for opt in opts:
        words = {w for w in re.findall(r"[a-z]{4,}", opt["label"].lower())}
        score = len(words & wanted)
        if score > best_score:
            best, best_score = opt, score
    if best is not None and best_score:
        best["recommended"] = True
    return opts


def _defaults(kind: str, start: int) -> List[Dict[str, Any]]:
    if kind == "pull_request":
        rows = [
            ("Merge it", "Land the pull request as it stands.", "merge",
             "Whatever is in the diff ships, known warts included."),
            ("Salvage into a clean PR", "Cherry-pick the good work onto a clean branch, authorship kept, and open a replacement PR.", "salvage",
             "An agent does the surgery and the original is closed by reference."),
            ("Send it back to the author", "The author revises against the note you add.", "author_action",
             "Slowest path, but the author keeps full ownership."),
            ("Close without merging", "End the proposal here.", "close",
             "The work does not ship and may need redoing later."),
        ]
    else:
        rows = [
            ("Go ahead as proposed", "Approve the direction in the issue body.", "approve",
             "Work starts on the stated basis, including its stated risks."),
            ("Dispatch an agent to implement it", "Hand the approved direction to an agent with the issue as the brief.", "implement",
             "An agent runs it; you review the resulting PR, not the thinking."),
            ("Send it back for revision", "The author reworks the proposal against the note you add.", "author_action",
             "Another round trip before anything starts."),
            ("Close as not planned", "Decline the proposal.", "close",
             "The underlying problem stays open elsewhere or not at all."),
        ]
    return [_mk_option(start + i, r[0], r[1], kind, "default", intent=r[2], cost=r[3])
            for i, r in enumerate(rows)]


def _ion_options() -> List[Dict[str, Any]]:
    opts = [
        _mk_option(0, "Approve this exact plan", "Records ion:approved for the plan hash on display. Ion executes on its next apply run.",
                   "issue", "ion", intent="approve",
                   cost="The exact commands in the proposal will run, inside ion's caps and guards."),
        _mk_option(1, "Hold the proposal", "Records ion:hold. Ion does nothing until the hold is lifted.",
                   "issue", "ion", intent="hold", cost="The drift or structure it addresses stays as it is."),
        _mk_option(2, "Reject the proposal", "Records ion:rejected and starts the cooldown.",
                   "issue", "ion", intent="reject", cost="Ion stays quiet on this for the cooldown unless the score rises sharply."),
    ]
    for opt in opts:
        opt["modes"] = ["record"]
    return opts


def parse_options(body: str, comments: List[Dict[str, Any]], kind: str = "issue",
                  source: str = "github") -> List[Dict[str, Any]]:
    if source == "ion":
        return _ion_options()
    texts = [body or ""] + [str(c.get("body") or "") for c in (comments or [])]
    for text in texts:
        opts = _explicit_options(text, kind)
        if len(opts) >= 2:
            return _mark_recommendation(opts, "\n".join(texts))[:4]
    for text in texts:
        opts = _either_or_options(text, kind)
        if len(opts) >= 2:
            fill = _defaults(kind, len(opts))
            merged = opts + [f for f in fill if f["intent"] in ("hold", "close")][:1]
            # keep at most 4 panels: parsed choices first, one governance exit
            return _mark_recommendation(merged[:4], "\n".join(texts))
    return _mark_recommendation(_defaults(kind, 0), "\n".join(texts))


# --------------------------------------------------------------- normalisation

def _fingerprint(item: Dict[str, Any], comments: List[Dict[str, Any]], head_sha: str) -> str:
    doc = {
        "title": item.get("title"), "body": item.get("body") or "",
        "updated": item.get("updated_at"), "head": head_sha,
        "comments": [(c.get("id"), c.get("body") or "") for c in comments],
        "labels": sorted(l.get("name") for l in item.get("labels") or []),
    }
    return hashlib.sha256(json.dumps(doc, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _is_ion(cfg: Any, repo: str, item: Dict[str, Any]) -> Optional[str]:
    tracker = str(_get(cfg, "decisions_ion_tracker_repo", "ion-tracker") or "ion-tracker")
    labels = [l.get("name") for l in item.get("labels") or []]
    m = re.match(r"\[(P-\d+)\]", item.get("title") or "")
    if repo == tracker and (m or "ion:proposal" in labels):
        return m.group(1) if m else "ion"
    return None


def _normalize(cfg: Any, raw: Dict[str, Any], comments: List[Dict[str, Any]],
               pr: Optional[Dict[str, Any]], now: float) -> Dict[str, Any]:
    repo = _repo_of(raw)
    number = int(raw.get("number") or 0)
    kind = "pull_request" if raw.get("pull_request") else "issue"
    ion_pid = _is_ion(cfg, repo, raw)
    source = "ion" if ion_pid else "github"
    head_sha = ((pr or {}).get("head") or {}).get("sha", "") if pr else ""
    body = str(raw.get("body") or "")
    return {
        "id": "{}#{}".format(repo, number),
        "repo": repo, "number": number, "kind": kind, "source": source,
        "ion_proposal": ion_pid if source == "ion" else None,
        "title": str(raw.get("title") or ""),
        "url": str(raw.get("html_url") or ""),
        "author": str((raw.get("user") or {}).get("login") or ""),
        "created_at": str(raw.get("created_at") or ""),
        "updated_at": str(raw.get("updated_at") or ""),
        "age_days": _age_days(str(raw.get("created_at") or ""), now),
        "labels": [l.get("name") for l in raw.get("labels") or []],
        "comments": int(raw.get("comments") or len(comments)),
        "context": _excerpt(body),
        "question": ("Approve this exact ion plan?" if source == "ion" else
                     "What should happen to this pull request?" if kind == "pull_request"
                     else "What should happen next?"),
        "options": parse_options(body, comments, kind, source),
        "draft": bool((pr or {}).get("draft")) if pr else False,
        "mergeable": (pr or {}).get("mergeable") if pr else None,
        "head_sha": head_sha,
        "head_ref": ((pr or {}).get("head") or {}).get("ref", "") if pr else "",
        "base_ref": ((pr or {}).get("base") or {}).get("ref", "") if pr else "",
        "changed_files": int((pr or {}).get("changed_files") or 0) if pr else 0,
        "additions": int((pr or {}).get("additions") or 0) if pr else 0,
        "deletions": int((pr or {}).get("deletions") or 0) if pr else 0,
        "fingerprint": _fingerprint(raw, comments, head_sha),
        "local_status": "waiting",
        "last_action": None,
    }


def _fetch_item(cfg: Any, gh: Gh, repo: str, number: int, now: float) -> Dict[str, Any]:
    org = _org(cfg)
    raw = gh.get("repos/{}/{}/issues/{}".format(org, repo, number))
    raw["repository_url"] = "https://api.github.com/repos/{}/{}".format(org, repo)
    comments = gh.get("repos/{}/{}/issues/{}/comments?per_page=50".format(org, repo, number)) or []
    pr = None
    if raw.get("pull_request"):
        pr = gh.get("repos/{}/{}/pulls/{}".format(org, repo, number))
    return _normalize(cfg, raw, list(comments)[-10:], pr, now)


# --------------------------------------------------------------- store + sync

def _read_events(cfg: Any) -> List[Dict[str, Any]]:
    path = events_path(cfg)
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
    except FileNotFoundError:
        pass
    return out


def _append_event(cfg: Any, event: Dict[str, Any]) -> None:
    path = events_path(cfg)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _overlay(cfg: Any, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    last: Dict[str, Dict[str, Any]] = {}
    for ev in _read_events(cfg):
        if ev.get("item_id"):
            last[ev["item_id"]] = ev
    for item in items:
        ev = last.get(item["id"])
        if ev and ev.get("fingerprint") == item["fingerprint"] and ev.get("status") in (
                "done", "dispatched", "queued"):
            item["local_status"] = ev["status"]
            item["last_action"] = {
                "action_id": ev.get("action_id"), "option_label": ev.get("option_label"),
                "mode": ev.get("mode"), "at": ev.get("at"), "status": ev.get("status")}
    return items


def _write_snapshot(cfg: Any, snap: Dict[str, Any]) -> None:
    path = snapshot_path(cfg)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(snap, fh, indent=1, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def sync(cfg: Any, runner: Optional[Runner] = None, now: Optional[float] = None) -> Dict[str, Any]:
    ts = _now(now)
    gh = Gh(runner)
    org = _org(cfg)
    with _lock_for(snapshot_path(cfg)):
        degraded: List[str] = []
        items: List[Dict[str, Any]] = []
        for raw in _search(cfg, gh):
            repo, number = _repo_of(raw), int(raw.get("number") or 0)
            if not repo or not number:
                continue
            comments: List[Dict[str, Any]] = []
            pr = None
            try:
                if int(raw.get("comments") or 0):
                    comments = list(gh.get(
                        "repos/{}/{}/issues/{}/comments?per_page=50".format(org, repo, number)) or [])[-10:]
                if raw.get("pull_request"):
                    pr = gh.get("repos/{}/{}/pulls/{}".format(org, repo, number))
            except Exception as exc:  # one bad item never empties the queue
                degraded.append("{}#{}: {}".format(repo, number, str(exc)[:120]))
            items.append(_normalize(cfg, raw, comments, pr, ts))
        items.sort(key=lambda i: (i["created_at"], i["repo"], i["number"]))
        snap = {
            "version": VERSION, "generated_at": _iso(ts), "org": org, "label": _label(cfg),
            "items": _overlay(cfg, items), "degraded": degraded,
            "counts": {
                "total": len(items),
                "waiting": sum(1 for i in items if i["local_status"] == "waiting"),
                "pull_requests": sum(1 for i in items if i["kind"] == "pull_request"),
                "issues": sum(1 for i in items if i["kind"] == "issue"),
                "ion": sum(1 for i in items if i["source"] == "ion"),
            },
        }
        _write_snapshot(cfg, snap)
        return snap


def show(cfg: Any) -> Dict[str, Any]:
    path = snapshot_path(cfg)
    try:
        with open(path, encoding="utf-8") as fh:
            snap = json.load(fh)
    except FileNotFoundError:
        return {"version": VERSION, "generated_at": None, "org": _org(cfg),
                "label": _label(cfg), "items": [], "degraded": [],
                "counts": {"total": 0, "waiting": 0, "pull_requests": 0, "issues": 0, "ion": 0}}
    snap["items"] = _overlay(cfg, list(snap.get("items") or []))
    snap.setdefault("degraded", [])
    return snap


# --------------------------------------------------------------- acting

def _comment_body(item: Dict[str, Any], option: Dict[str, Any], mode: str,
                  note: str, action_id: str) -> str:
    lines = ["## Decision", "", "**Chosen: {}**".format(option["label"])]
    if option.get("detail"):
        lines += ["", option["detail"]]
    if note.strip():
        lines += ["", note.strip()]
    if mode == "dispatch":
        lines += ["", "An agent has been dispatched to carry this out and will report back here. "
                      "No action is needed from the author unless the agent asks."]
    elif option["intent"] == "hold":
        lines += ["", "Held for now. The `needs decision` label stays on."]
    else:
        lines += ["", "Please proceed on that basis."]
    lines += ["", "Recorded from the Continuum command centre.",
              "<!-- continuum-decision:{} -->".format(action_id), "",
              "> Automated posting by agentic team with human oversight."]
    return "\n".join(lines)


def _dispatch_packet(cfg: Any, item: Dict[str, Any], option: Dict[str, Any],
                     note: str, action_id: str, ts: float) -> Dict[str, Any]:
    d = dispatch_dir(cfg)
    os.makedirs(d, exist_ok=True)
    packet = {
        "action_id": action_id, "created_at": _iso(ts), "status": "queued",
        "item": {k: item.get(k) for k in ("id", "repo", "number", "kind", "title", "url",
                                          "author", "fingerprint", "head_sha", "head_ref",
                                          "base_ref", "context")},
        "chosen": option, "note": note,
        "instructions": [
            "Re-verify the item is still open and unchanged (fingerprint/head) before acting.",
            "Carry out the chosen option. For a salvage, cherry-pick and keep the original author's commits and attribution.",
            "Never force-push or rewrite shared history. Work on a new branch and open a PR unless the option says merge.",
            "Report back with one comment on the item linking the result, then remove the needs decision label if it is still on.",
        ],
    }
    with open(os.path.join(d, action_id + ".json"), "w", encoding="utf-8") as fh:
        json.dump(packet, fh, indent=1, sort_keys=True)
    brief = ["# Dispatch {} — {}".format(action_id, item["title"]), "",
             "Item: {} ({})".format(item["url"], item["id"]),
             "Chosen: {} — {}".format(option["label"], option.get("detail") or ""),
             "Maintainer note: {}".format(note or "(none)"), "",
             "Context: {}".format(item.get("context") or ""), "", "## Instructions", ""]
    brief += ["- " + line for line in packet["instructions"]]
    with open(os.path.join(d, action_id + ".md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(brief) + "\n")
    return packet


def _run_dispatch_command(cfg: Any, packet_path_md: str) -> Optional[str]:
    cmd = _get(cfg, "decisions_dispatch_command", None)
    if not cmd or not isinstance(cmd, (list, tuple)) or not all(isinstance(c, str) for c in cmd):
        return None
    with open(packet_path_md, encoding="utf-8") as fh:
        brief = fh.read()
    proc = subprocess.run(list(cmd), input=brief, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise DecisionsError("dispatch command failed: " + (proc.stderr or "")[:200], status_code=502)
    return (proc.stdout or "dispatched").strip()[:200]


def act(cfg: Any, item_id: str, option_id: str, mode: str, note: str = "",
        expected_fingerprint: str = "", idempotency_key: str = "",
        runner: Optional[Runner] = None, now: Optional[float] = None) -> Dict[str, Any]:
    ts = _now(now)
    if mode not in ("comment", "dispatch", "record"):
        raise DecisionsError("unknown mode: {}".format(mode))
    if not idempotency_key:
        raise DecisionsError("idempotency_key is required")
    action_id = "dec-" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:12]
    with _lock_for(snapshot_path(cfg)):
        for ev in _read_events(cfg):
            if ev.get("action_id") == action_id and ev.get("status") in ("done", "dispatched", "queued"):
                return ev
        snap = show(cfg)
        item = next((i for i in snap["items"] if i["id"] == item_id), None)
        if item is None:
            raise DecisionsError("item not in the current snapshot: {}".format(item_id), status_code=404)
        gh = Gh(runner)
        fresh = _fetch_item(cfg, gh, item["repo"], int(item["number"]), ts)
        label = _label(cfg)
        if label not in (fresh.get("labels") or []):
            raise DecisionsError("item no longer carries the decision label; refresh the queue",
                                 status_code=409)
        if expected_fingerprint and fresh["fingerprint"] != expected_fingerprint:
            raise DecisionsError("item changed since it was shown; refresh the queue",
                                 status_code=409)
        option = next((o for o in fresh["options"] if o["id"] == option_id), None)
        if option is None:
            raise DecisionsError("option not on the fresh item; refresh the queue", status_code=409)
        org = _org(cfg)
        repo, number = item["repo"], int(item["number"])
        base = "repos/{}/{}/issues/{}".format(org, repo, number)
        event = {
            "action_id": action_id, "item_id": item_id, "repo": repo, "number": number,
            "option_id": option_id, "option_label": option["label"], "intent": option["intent"],
            "mode": mode, "note": note or "", "fingerprint": fresh["fingerprint"],
            "at": _iso(ts), "status": "done", "receipt": "",
        }
        keep_label = option["intent"] == "hold" and fresh["source"] != "ion"

        if fresh["source"] == "ion":
            ion_label = {"approve": "ion:approved", "hold": "ion:hold",
                         "reject": "ion:rejected"}.get(option["intent"])
            if not ion_label:
                raise DecisionsError("ion proposals take approve, hold or reject only")
            gh.post(base + "/labels", {"labels": [ion_label]})
            event["mode"] = "record"
            event["receipt"] = ion_label
        else:
            if mode == "dispatch":
                packet = _dispatch_packet(cfg, fresh, option, note or "", action_id, ts)
                md_path = os.path.join(dispatch_dir(cfg), action_id + ".md")
                try:
                    receipt = _run_dispatch_command(cfg, md_path)
                except Exception:
                    receipt = None
                event["status"] = "dispatched" if receipt else "queued"
                event["receipt"] = receipt or "packet queued at {}".format(md_path)
                event["packet"] = packet["action_id"]
            marker = "<!-- continuum-decision:{} -->".format(action_id)
            comments = gh.get("repos/{}/{}/issues/{}/comments?per_page=50".format(org, repo, number)) or []
            if not any(marker in str(c.get("body") or "") for c in comments):
                gh.post(base + "/comments",
                        {"body": _comment_body(fresh, option, mode, note or "", action_id)})
        if not keep_label:
            gh.delete(base + "/labels/" + urllib.parse.quote(label, safe=""))
        _append_event(cfg, event)
        return event




