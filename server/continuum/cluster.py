"""M4 — continuum.cluster.

Deterministic candidate generation (S0 noise pre-filter + S1 strong-key clustering).

Order of signal application is CS-1 -> CS-2 -> CS-3 -> CS-4 -> CS-5 (research contract; CS-6 is
the optional S3 refinement and is NOT implemented here — it can never create a cluster).

HARD GUARD (CR-2 / INV-M4-A / INV-M4-B): weak (non-canonical) tokens register no union
key at all while weak_token_clustering is false — no CS-3w entry may appear in
Cluster.strong_keys, Cluster.strength_breakdown or Cluster.build_reason. A token key
(CS-3 / CS-3w) is registered only for sessions already connected by a CS-1/CS-2/CS-4
signal: token edges never create and never bridge a component, so the CS-3 pass
annotates and corroborates only. Every cluster must cite at least one A (CS-1/CS-2),
B (CS-3 strong token) or D (CS-4) signal.

No model calls. No source reads. No lifecycle assignment (that is M5).
"""
from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .config import Config
from .evidence import is_strong_token
from .scanner import MessageProbe, SessionFact

Ref = str  # "profile/session_id"


@dataclass
class Cluster:
    cluster_id: str
    member_refs: List[Ref]
    strong_keys: List[str]
    strength_breakdown: Dict[str, int]
    build_reason: List[str]
    confidence_band: str          # high|medium|low|unknown
    proposed_name: Optional[str] = None


def ref_of(fact_or_profile, session_id: Optional[str] = None) -> Ref:
    if session_id is None:
        return "{}/{}".format(fact_or_profile.profile_name, fact_or_profile.session_id)
    return "{}/{}".format(fact_or_profile, session_id)


def split_ref(ref: Ref) -> Tuple[str, str]:
    profile, _, sid = ref.partition("/")
    return profile, sid


# --------------------------------------------------------------------------- S0 noise

def classify_noise(facts: Sequence[SessionFact],
                   probes: Sequence[MessageProbe],
                   cfg: Config) -> Dict[Ref, Tuple[int, str]]:
    """Return ``ref -> (is_noise, noise_class)``. Down-ranked and bucketed, never deleted.

    NS-1 cron | NS-2 subagent child | NS-3 recurring series | NS-4 test/handshake |
    NS-5 group relay | NS-6 untitled fragment | NS-7 model eval.
    """
    recurring = re.compile(cfg.get("recurring_title_pattern") or r"$^")
    tests = [re.compile(p) for p in (cfg.get("test_title_patterns") or []) if _ok(p)]
    evals = [re.compile(p) for p in (cfg.get("eval_title_patterns") or []) if _ok(p)]

    probe_text: Dict[Ref, str] = {}
    for p in probes:
        probe_text.setdefault(ref_of(p.profile_name, p.session_id), "")
        probe_text[ref_of(p.profile_name, p.session_id)] += " " + (p.content or "")

    out: Dict[Ref, Tuple[int, str]] = {}
    for f in facts:
        ref = ref_of(f)
        title = (f.title or "").strip()
        src = (f.source or "").lower()
        cls = None
        if src == "cron":
            cls = "NS-1"
        elif f.parent_session_id and src == "subagent":
            cls = "NS-2"
        elif not title and not (f.title_source or "").strip():
            cls = "NS-6"
        elif title and recurring.search(title):
            cls = "NS-3"
        elif (f.chat_type or "").lower() == "group" or (
                f.display_name or "").startswith("Group:") or title.startswith("Group:"):
            cls = "NS-5"
        elif title and any(r.search(title) for r in tests):
            cls = "NS-4"
        elif title and any(r.search(title) for r in evals):
            cls = "NS-7"
        out[ref] = (1, cls) if cls else (0, "")
    return out


def _ok(p: str) -> bool:
    try:
        re.compile(p)
        return True
    except re.error:
        return False


# --------------------------------------------------------------------------- S1 clustering

class _UnionFind:
    def __init__(self) -> None:
        self.parent: Dict[Ref, Ref] = {}

    def find(self, x: Ref) -> Ref:
        self.parent.setdefault(x, x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: Ref, b: Ref) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)  # deterministic root


def build_clusters(facts: Sequence[SessionFact],
                   keys: Dict[str, List[str]],
                   cfg: Config,
                   noise: Optional[Dict[Ref, Tuple[int, str]]] = None) -> List[Cluster]:
    """Union-find over strong keys. Noise sessions are never seeds (AV-2)."""
    noise = noise or {}
    by_ref: Dict[Ref, SessionFact] = {ref_of(f): f for f in facts}
    seeds = [r for r in by_ref if not noise.get(r, (0, ""))[0]]
    seed_set = set(seeds)

    uf = _UnionFind()
    reasons: Dict[Ref, set] = {}
    key_owner: Dict[str, str] = {}

    def note(ref: Ref, reason: str) -> None:
        reasons.setdefault(ref, set()).add(reason)

    # --- CS-1 git_repo_root / CS-2 workspace_root (signal A) ---------------
    for group_key, signal in (("git_repo_root", "CS-1"), ("workspace_root", "CS-2")):
        buckets: Dict[str, List[Ref]] = {}
        for ref in seeds:
            value = getattr(by_ref[ref], group_key)
            if not value:
                continue
            if signal == "CS-2" and cfg.is_generic_path(value):
                continue  # AV-4: generic roots are context, never keys
            buckets.setdefault(value, []).append(ref)
        for value, refs in buckets.items():
            if len(refs) < 2:
                continue
            anchor = refs[0]
            for r in refs[1:]:
                uf.union(anchor, r)
            kname = "{}:{}".format(signal, value)
            key_owner[kname] = anchor
            for r in refs:
                note(r, "{} shared {}".format(signal, value))

    # --- CS-3 canonical tokens (annotation only; never membership) ---------
    # INV-M4-B: a token key is registered for a component only when >=2 of its holders already
    # sit in that component. The bucketing is keyed on the current union-find root, so every
    # pair it sees is already connected: the CS-3 pass annotates and corroborates, and it can
    # never create or bridge a component. (A union call here would be dead code.)
    # INV-M4-A: weak tokens register no key at all while weak_token_clustering is false.
    weak_ok = bool(cfg.get("weak_token_clustering", False))
    for token, refs in keys.items():
        present = [r for r in refs if r in seed_set]
        if len(present) < 2:
            continue
        strong = is_strong_token(token)
        if not strong and not weak_ok:
            continue
        signal = "CS-3" if strong else "CS-3w"
        buckets: Dict[Ref, List[Ref]] = {}
        for r in present:
            buckets.setdefault(uf.find(r), []).append(r)
        for bucket in buckets.values():
            if len(bucket) < 2:
                continue
            key_owner["{}:{}".format(signal, token)] = bucket[0]
            for r in bucket:
                note(r, "{} shared token {}".format(signal, token))

    # --- CS-4 parent/child fold (signal D) ---------------------------------
    for ref in list(by_ref):
        fact = by_ref[ref]
        parent = fact.parent_session_id
        if not parent:
            continue
        pref = ref_of(fact.profile_name, parent)
        if pref not in by_ref:
            continue
        is_noise_child = bool(noise.get(ref, (0, ""))[0])
        if ref in seed_set and pref in seed_set:
            uf.union(pref, ref)
            note(ref, "CS-4 child of {}".format(parent))
            note(pref, "CS-4 parent of {}".format(fact.session_id))
        elif is_noise_child and pref in seed_set:
            # AV-7: a delegated child (NS-2) is folded into its parent's project — it never
            # spawns a standalone cluster and it never seeds one.
            uf.find(pref)
            uf.union(pref, ref)
            note(ref, "CS-4 child of {}".format(parent))

    # --- assemble ----------------------------------------------------------
    groups: Dict[Ref, List[Ref]] = {}
    for ref in list(uf.parent.keys()):
        groups.setdefault(uf.find(ref), []).append(ref)

    clusters: List[Cluster] = []
    for root, members in groups.items():
        if len(members) < 2:
            continue  # a single session is not a cross-session cluster
        member_keys: List[str] = []
        cs: Dict[str, int] = {}
        for kname, owner in key_owner.items():
            if uf.find(owner) != root:
                continue
            member_keys.append(kname)
            cs[kname.split(":", 1)[0]] = cs.get(kname.split(":", 1)[0], 0) + 1
        build_reason = sorted({r for m in members for r in reasons.get(m, set())})
        band = _band(cs, build_reason)
        member_keys.sort()
        name = _proposed_name(members, by_ref)
        clusters.append(Cluster(
            cluster_id=str(uuid.uuid5(uuid.NAMESPACE_URL,
                                      "continuum:{}".format("|".join(sorted(member_keys)) or root))),
            member_refs=sorted(members),
            strong_keys=member_keys,
            strength_breakdown=cs,
            build_reason=build_reason,
            confidence_band=band,
            proposed_name=name,
        ))
    clusters.sort(key=lambda c: (c.confidence_band != "high", -len(c.member_refs), c.cluster_id))
    return clusters


def _band(cs: Dict[str, int], build_reason: Sequence[str]) -> str:
    """CR-1: HIGH = shared CS-1 (or human-declared); MEDIUM = CS-2..CS-4 with continuity;
    LOW = single weak signal (CS-6-only / weak token)."""
    if cs.get("CS-1"):
        return "high"
    if cs.get("CS-2") or cs.get("CS-3"):
        return "medium"
    if cs.get("CS-4"):
        return "medium" if any("CS-2" in r or "CS-3" in r for r in build_reason) else "low"
    if cs.get("CS-3w"):
        return "low"
    return "unknown"


def _proposed_name(members: Sequence[Ref], by_ref: Dict[Ref, SessionFact]) -> Optional[str]:
    for m in members:
        f = by_ref.get(m)
        if f and f.workspace_root:
            return os.path.basename(f.workspace_root.rstrip("/")) or None
    for m in members:
        f = by_ref.get(m)
        if f and f.title:
            return f.title[:80]
    return None


def assert_no_generic_merge(clusters: Sequence[Cluster], cfg: Config) -> List[str]:
    """CR-2 hard guard. Returns a list of violations (empty == clean)."""
    violations: List[str] = []
    for c in clusters:
        has_strong = any(
            k.startswith(("CS-1:", "CS-2:", "CS-3:", "CS-4:")) for k in c.strong_keys
        )
        if not has_strong:
            violations.append("cluster {} merged without an A/B/D signal".format(c.cluster_id))
        for k in c.strong_keys:
            _, _, value = k.partition(":")
            if k.startswith(("CS-2:", "CS-1:")) and cfg.is_generic_path(value):
                violations.append("cluster {} merged on generic path {}".format(c.cluster_id, value))
    return violations
