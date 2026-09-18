"""M3 — continuum.evidence.

Deterministic Tier-1 extraction from bounded message probes, plus canonical token generation
for clustering (CS-3). No model calls. Never clusters. Nothing here touches a source DB.
"""
from __future__ import annotations

import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .config import Config
from .scanner import MessageProbe, SessionFact

_URL_RE = re.compile(r"https?://[^\s<>'\"\)\]]+")
# A distinctive issue reference needs real digits: a bare "#10" or "BC-5" is noise, not a key.
_PR_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,9}-\d{3,6})\b")
_HASH_ISSUE_RE = re.compile(r"(?<![\w/])#(\d{4,6})\b")
_PATH_RE = re.compile(r"(?:/[\w .@-]+){2,}")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{3,}")
_REPO_SEGMENT_RE = re.compile(r"/(?:wrk|working|work)/([\w.-]+)")


@dataclass
class EvidenceItem:
    tier: int
    kind: str                 # goal|decision|next_action|blocker|phase|link
    excerpt: str
    locator: Dict[str, object]  # {profile, session_id, msg_id}
    confidence: float
    extracted_at: float = 0.0
    source_hash: str = ""

    def to_row(self) -> Dict[str, object]:
        return asdict(self)


def _compile(pats: Iterable[str]) -> List[re.Pattern]:
    out = []
    for p in pats or []:
        try:
            out.append(re.compile(p))
        except re.error:
            continue
    return out


def _clip(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text[:n]


def extract(probes: Sequence[MessageProbe], cfg: Config) -> List[EvidenceItem]:
    """Rule-extract goals / decisions / next actions / blockers / links from probe text."""
    excerpt_chars = int(cfg.get("excerpt_chars", 240))
    goal_re = _compile(cfg.get("goal_patterns"))
    decision_re = _compile(cfg.get("decision_patterns"))
    next_re = _compile(cfg.get("next_action_patterns"))
    blocker_re = _compile(cfg.get("blocker_patterns"))
    health_re = _compile(cfg.get("health_ack_content_patterns"))

    out: List[EvidenceItem] = []
    for probe in probes:
        text = probe.content or ""
        if not text.strip():
            continue
        if any(r.search(text) for r in health_re):
            continue
        loc = {"profile": probe.profile_name, "session_id": probe.session_id,
               "msg_id": probe.msg_id}
        base_conf = 0.7 if probe.position == "tail" else 0.55
        rules = (
            ("blocker", blocker_re, 0.8),
            ("next_action", next_re, 0.7),
            ("decision", decision_re, 0.7),
            ("goal", goal_re, 0.6),
        )
        for kind, pats, conf in rules:
            if any(r.search(text) for r in pats):
                out.append(EvidenceItem(tier=1, kind=kind,
                                        excerpt=_clip(text, excerpt_chars),
                                        locator=loc, confidence=conf))
                break
        for url in _URL_RE.findall(text)[:1]:
            out.append(EvidenceItem(tier=1, kind="link",
                                    excerpt=_clip(url, excerpt_chars),
                                    locator=loc, confidence=0.6))
    return out


def _tokenize(text: str, cfg: Config) -> Counter:
    """Extract canonical tokens (CS-3) from one text blob. Generic tokens are dropped (CR-2)."""
    stops = set(cfg.get("stopword_tokens") or [])
    toks: Counter = Counter()
    if not text:
        return toks
    for url in _URL_RE.findall(text):
        toks["url:" + url.rstrip("/.,;")] += 1
    for pr in _PR_RE.findall(text):
        toks["issue:" + pr] += 1
    for n in _HASH_ISSUE_RE.findall(text):
        toks["issue:#" + n] += 1
    for seg in _REPO_SEGMENT_RE.findall(text):
        toks["repo:" + seg.lower()] += 1
    for path in _PATH_RE.findall(text):
        if cfg.is_generic_path(path.strip()):
            continue
        if len(path) < 8:
            continue
        # CS-3 path tokens must be project-scoped (a wrk/work/working segment), otherwise a
        # shared ancestor directory (e.g. .../Documents/ai work/Hermes) merges unrelated work.
        if not re.search(r"/(?:wrk|working|work)/", path):
            continue
        toks["path:" + path.rstrip("/.")] += 1
    for w in _WORD_RE.findall(text):
        lw = w.lower()
        if lw in stops or len(lw) < 5 or lw.isdigit():
            continue
        toks["noun:" + lw] += 1
    return toks


def canonical_keys(facts: Sequence[SessionFact],
                   evidence: Sequence[EvidenceItem],
                   probes: Sequence[MessageProbe],
                   cfg: Config) -> Dict[str, List[str]]:
    """token -> [``profile/session_id`` ...] for tokens that satisfy the CS-3 gates.

    Gates: appears in >= token_min_sessions sessions; appears in <= token_max_frequency of all
    sessions (rare); never a generic token. Paths/urls/issues/repos are inherently strong.
    """
    by_session: Dict[Tuple[str, str], Counter] = defaultdict(Counter)
    for f in facts:
        key = (f.profile_name, f.session_id)
        by_session[key].update(_tokenize(f.title or "", cfg))
        if f.cwd and not cfg.is_generic_path(f.cwd):
            for seg in _REPO_SEGMENT_RE.findall(f.cwd):
                by_session[key]["repo:" + seg.lower()] += 1
    for p in probes:
        by_session[(p.profile_name, p.session_id)].update(_tokenize(p.content, cfg))
    for e in evidence:
        key = (str(e.locator.get("profile")), str(e.locator.get("session_id")))
        by_session[key].update(_tokenize(e.excerpt, cfg))

    total_sessions = max(1, len(facts))
    session_count: Dict[str, int] = Counter()
    token_sessions: Dict[str, set] = defaultdict(set)
    for (profile, sid), counter in by_session.items():
        for tok in counter:
            pair = (profile, sid)
            if pair not in token_sessions[tok]:
                token_sessions[tok].add(pair)
                session_count[tok] += 1

    min_sessions = int(cfg.get("token_min_sessions", 2) or 2)
    max_freq = float(cfg.get("token_max_frequency", 0.05) or 0.05)
    max_sessions = int(cfg.get("token_max_sessions", 40) or 40)

    keys: Dict[str, List[str]] = {}
    for tok, pairs in token_sessions.items():
        n = session_count[tok]
        if n < min_sessions:
            continue
        if n > max_sessions or n / total_sessions > max_freq:
            continue  # too common to be distinctive (CR-2)
        keys[tok] = sorted("{}/{}".format(profile, sid) for (profile, sid) in pairs)
    return keys


def is_strong_token(tok: str) -> bool:
    """CS-3 strength: repo ids, paths, URLs and issue ids are strong; bare nouns are weak."""
    return tok.split(":", 1)[0] in ("repo", "path", "url", "issue")
