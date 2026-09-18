"""M5 — continuum.classify.

Lifecycle + confidence scoring. Deterministic rules first. The bounded model step (S3) is a
disabled stub (OD2 / INV-4): ``model_enabled: false`` by default and no network code exists.

Lifecycle vocabulary is verbatim from research LS-1..LS-9 (architecture §8).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .cluster import Cluster, Ref, split_ref
from .config import Config
from .evidence import EvidenceItem
from .scanner import MessageProbe, SessionFact

LIFECYCLES = ("LS-1", "LS-2", "LS-3", "LS-4", "LS-5", "LS-6", "LS-7", "LS-8", "LS-9")
LIFECYCLE_NAMES = {
    "LS-1": "proposed", "LS-2": "active", "LS-3": "blocked", "LS-4": "awaiting-user",
    "LS-5": "stalled", "LS-6": "parked", "LS-7": "complete", "LS-8": "abandoned-candidate",
    "LS-9": "unknown",
}
PHASE_BY_LIFECYCLE = {
    "LS-1": "proposed", "LS-2": "in-progress", "LS-3": "blocked", "LS-4": "awaiting-review",
    "LS-5": "idle", "LS-6": "parked", "LS-7": "complete", "LS-8": "abandoned",
    "LS-9": "unknown",
}

DAY = 86400.0


@dataclass
class Classification:
    cluster_id: str
    lifecycle: str
    lifecycle_name: str
    confidence: float
    band: str
    evidence_tier: int
    signals_fired: List[str]
    member_refs: List[Ref]
    summary_tier2: Optional[str] = None
    model_used: bool = False
    phase: str = "unknown"
    last_substantive_activity: Optional[float] = None
    stall_age_days: Optional[float] = None
    session_count: int = 0
    next_action_derived: Optional[str] = None
    blockers: List[str] = field(default_factory=list)
    build_reason: List[str] = field(default_factory=list)
    proposed_name: Optional[str] = None


def _compile(pats) -> List[re.Pattern]:
    out = []
    for p in pats or []:
        try:
            out.append(re.compile(p))
        except re.error:
            continue
    return out


def substantive_activity(fact: SessionFact,
                         probes: Sequence[MessageProbe],
                         cfg: Config) -> Optional[float]:
    """max timestamp of a non-noise user/assistant message with content (ET-0/Tier-1 rule).

    Falls back to the session's ``last_activity_at`` when no probe qualifies.
    """
    health = _compile(cfg.get("health_ack_content_patterns"))
    best: Optional[float] = None
    for p in probes:
        if p.role not in ("user", "assistant"):
            continue
        text = (p.content or "").strip()
        if not text:
            continue
        if any(r.search(text) for r in health):
            continue
        if p.timestamp is not None and (best is None or p.timestamp > best):
            best = p.timestamp
    if best is None:
        best = fact.last_activity_at
    return best


def classify(clusters: Sequence[Cluster],
             facts_by_ref: Dict[Ref, SessionFact],
             probes_by_ref: Dict[Ref, List[MessageProbe]],
             evidence_by_cluster: Dict[str, List[EvidenceItem]],
             cfg: Config,
             *,
             now: Optional[float] = None,
             model=None) -> List[Classification]:
    now = now if now is not None else time.time()
    band_scores = cfg.get("band_scores") or {}
    blocker_re = _compile(cfg.get("blocker_patterns"))
    awaiting_re = _compile(cfg.get("awaiting_user_patterns"))
    next_re = _compile(cfg.get("next_action_patterns"))
    stall_days = float(cfg.get("stall_days", 7) or 7)
    abandoned_days = float(cfg.get("abandoned_days", 60) or 60)

    out: List[Classification] = []
    for c in clusters:
        members = c.member_refs
        last_substantive: Optional[float] = None
        total_msgs = 0
        total_tools = 0
        for m in members:
            f = facts_by_ref.get(m)
            if not f:
                continue
            total_msgs += f.message_count
            total_tools += f.tool_call_count
            ts = substantive_activity(f, probes_by_ref.get(m, []), cfg)
            if ts is not None and (last_substantive is None or ts > last_substantive):
                last_substantive = ts
        stall_age = None
        if last_substantive is not None:
            stall_age = max(0.0, (now - last_substantive) / DAY)

        blockers: List[str] = []
        awaiting = False
        for m in members:
            for p in probes_by_ref.get(m, []):
                if p.position != "tail" or p.role not in ("user", "assistant"):
                    continue
                text = p.content or ""
                if any(r.search(text) for r in blocker_re):
                    blockers.append(text[:120])
                if p.role == "assistant" and any(r.search(text) for r in awaiting_re):
                    awaiting = True
        next_action_derived = None
        for m in members:
            for p in probes_by_ref.get(m, []):
                if p.position == "tail" and any(r.search(p.content or "") for r in next_re):
                    next_action_derived = " ".join((p.content or "").split())[:160]
                    break
            if next_action_derived:
                break

        evidence = evidence_by_cluster.get(c.cluster_id, [])
        evidence_tier = 1 if evidence else 0
        fired = sorted(set(c.strong_keys and [k.split(":", 1)[0] for k in c.strong_keys] or []))

        if blockers:
            lifecycle = "LS-3"
        elif awaiting:
            lifecycle = "LS-4"
        elif c.confidence_band == "unknown":
            lifecycle = "LS-9"
        elif stall_age is not None and stall_age > abandoned_days:
            lifecycle = "LS-8"
        elif stall_age is not None and stall_age > stall_days:
            lifecycle = "LS-5"
        elif total_msgs <= 4 and total_tools == 0:
            lifecycle = "LS-1"
        else:
            lifecycle = "LS-2"

        fired += [lifecycle]
        if blockers:
            fired.append("blocker-evidence")
        if awaiting:
            fired.append("awaiting-user-evidence")

        base = float(band_scores.get(c.confidence_band, 0.15))
        confidence = min(0.95, base + 0.02 * max(0, len(c.strong_keys) - 1))
        confidence = round(confidence, 3)

        out.append(Classification(
            cluster_id=c.cluster_id,
            lifecycle=lifecycle,
            lifecycle_name=LIFECYCLE_NAMES[lifecycle],
            confidence=confidence,
            band=c.confidence_band,
            evidence_tier=evidence_tier,
            signals_fired=fired,
            member_refs=list(members),
            phase=PHASE_BY_LIFECYCLE[lifecycle],
            last_substantive_activity=last_substantive,
            stall_age_days=None if stall_age is None else round(stall_age, 2),
            session_count=len(members),
            next_action_derived=next_action_derived,
            blockers=blockers,
            build_reason=list(c.build_reason),
            proposed_name=c.proposed_name,
        ))
    return out
