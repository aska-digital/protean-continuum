"""M6 — continuum.model.

Reconciles derived fields (always regenerated) with declared fields (durable, TTL'd, re-applied
in declared_rev order). Materializes ``project``, ``project_session`` and ``evidence`` rows.

Guarantee (architecture §4): a rescan can never delete an accepted link or a declared next
action — at worst it flags a declared field ``expired``.

F1 stable identity: when a cluster's strong-key signature changes (e.g. a bridging session is
added), the project_id is preserved by looking up the existing project via member overlap.
Declared state is migrated from orphaned projects to the surviving project.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .classify import Classification
from .cluster import Cluster, Ref
from .config import Config
from .evidence import EvidenceItem
from .registry import LinkRow, ProjectRow, Registry
from . import audience


@dataclass
class ReconcileReport:
    materialized: int = 0
    links: int = 0
    declared_reapplied: int = 0
    expired_flagged: List[str] = field(default_factory=list)
    merged_suppressed: List[str] = field(default_factory=list)
    identity_migrated: List[str] = field(default_factory=list)
    orphans_pruned: int = 0
    errors: List[str] = field(default_factory=list)


def project_id_for(cluster: Cluster) -> str:
    """Deterministic local project id derived from the cluster's strong-key signature."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL,
                          "continuum:project:{}".format("|" .join(sorted(cluster.strong_keys)))))


def _cluster_signature(cluster: Cluster) -> str:
    """Canonical signature string for a cluster's strong keys."""
    return "|".join(sorted(cluster.strong_keys))


def reconcile(classifications: Sequence[Classification],
              clusters_by_id: Dict[str, Cluster],
              registry: Registry,
              cfg: Config,
              *,
              evidence_by_cluster: Optional[Dict[str, List[EvidenceItem]]] = None,
              now: Optional[float] = None,
              audience_ctx: Optional[Dict[str, object]] = None) -> ReconcileReport:
    now = now if now is not None else time.time()
    report = ReconcileReport()
    evidence_by_cluster = evidence_by_cluster or {}
    audience_ctx = audience_ctx or {}

    rows: List[ProjectRow] = []
    links: List[LinkRow] = []
    old_pids: Dict[str, str] = {}  # new_pid -> old_pid (for migration)

    for cl in classifications:
        cluster = clusters_by_id.get(cl.cluster_id)
        if cluster is None:
            pid = cl.cluster_id
        else:
            sig = _cluster_signature(cluster)
            # 1. Check signature table for exact match
            pid = registry.get_project_signature(sig)
            if pid is not None:
                pass  # exact match, reuse
            else:
                # 2. Find existing project via member overlap
                existing_pid = registry.current_project_for(cl.member_refs)
                if existing_pid:
                    pid = existing_pid
                    # Record migration if the project had a different signature
                    old_sig_row = registry.conn.execute(
                        "SELECT signature FROM project_signature WHERE project_id=?",
                        (pid,)).fetchone()
                    if old_sig_row and old_sig_row["signature"] != sig:
                        report.identity_migrated.append(pid)
                else:
                    # 3. Brand new cluster
                    pid = project_id_for(cluster)

                # Store/update signature mapping
                registry.set_project_signature(sig, pid)

        rows.append(ProjectRow(
            project_id=pid,
            name=cl.proposed_name,
            kind="project",
            phase=cl.phase,
            lifecycle=cl.lifecycle,
            confidence=cl.confidence,
            confidence_band=cl.band,
            evidence_tier=cl.evidence_tier,
            owner_profile=None,
            drive_expected=0,
            stall_age_days=cl.stall_age_days,
            last_substantive_activity=cl.last_substantive_activity,
            session_count=cl.session_count,
        ))
        for i, ref in enumerate(cl.member_refs):
            profile, _, sid = ref.partition("/")
            links.append(LinkRow(
                link_id=str(uuid.uuid5(uuid.NAMESPACE_URL,
                                       "continuum:link:{}:{}:{}".format(pid, profile, sid))),
                project_id=pid, profile_name=profile, session_id=sid,
                # D-US-6: the primary is chosen from audience below, never by sort order.
                role_in_project="supporting",
                link_confidence=cl.confidence,
                link_reason="; ".join(cl.build_reason[:4]),
                evidence_ref=None,
            ))
        if evidence_by_cluster.get(cl.cluster_id):
            registry.upsert_evidence(cl.cluster_id, pid, evidence_by_cluster[cl.cluster_id])

    # --- upsert projects and links FIRST so migration can find current links -----------
    registry.upsert_projects(rows)
    registry.upsert_links(links)
    report.materialized = len(rows)
    report.links = len(links)

    # --- F1: migrate declared state from orphaned projects -----------------------
    # Links now exist in project_session, so migration can resolve the survivor.
    current_pids = {r.project_id for r in rows}
    _migrate_orphaned_declared(registry, current_pids, now, report)

    # --- re-apply declared overlays (declared wins; never lost on rescan) ----
    for r in rows:
        pid = r.project_id
        declared = registry.effective_declared(pid, now=now)
        # R2: re-apply declared project name (declared wins over derived)
        declared_name = declared.get("project_name")
        if declared_name:
            registry.conn.execute("UPDATE project SET name=? WHERE project_id=?",
                                  (declared_name, pid))
        owner = declared.get("owner")
        drive = 1 if declared.get("drive_expected") == "1" else None
        if owner is not None or drive is not None:
            registry.update_project_declared(pid, owner_profile=owner or "",
                                             drive_expected=drive)
            report.declared_reapplied += 1
        if registry.is_declared_stale(pid, now=now):
            report.expired_flagged.append(pid)

    # --- audience: human override, primary selection, anchor (D-US-5/D-US-6) ----------
    apply_audience_overrides(registry, [r.project_id for r in rows], now=now)
    facts = facts_by_ref(registry)
    name_by_pid = {r.project_id: r.name for r in rows}
    for pid in name_by_pid:
        registry.set_primary(pid, primary_for(_member_rows(registry, pid, facts)))

    first_turn_by_ref = audience_ctx.get("first_turn_by_ref") or {}
    delegations = audience_ctx.get("delegations") or []
    accepted_by_ref = set()
    for row in registry.conn.execute(
            "SELECT profile_name, session_id FROM project_session WHERE accepted=1"):
        accepted_by_ref.add("{}/{}".format(row["profile_name"], row["session_id"]))
    user_facing_rows = []
    for ref, f in facts.items():
        if (f["audience"] or "UNKNOWN") != "USER_FACING":
            continue
        user_facing_rows.append({
            "profile_name": f["profile_name"], "session_id": f["session_id"],
            "title": f["title"], "first_turn_text": first_turn_by_ref.get(ref, ""),
            "accepted": 1 if ref in accepted_by_ref else 0,
            "last_activity_at": f["last_activity_at"],
            "audience_reason": f["audience_reason"] or "",
        })
    for pid, name in name_by_pid.items():
        anchor_ref, reason = audience.resolve_anchor(
            {"proposed_name": name}, user_facing_rows, cfg, delegations=delegations)
        registry.set_anchor(pid, anchor_ref, reason)
    return report


def _migrate_orphaned_declared(registry: Registry, current_pids: set,
                                now: float, report: ReconcileReport) -> None:
    """Find orphaned project rows that share members with current projects and migrate
    their declared state. Then prune the orphans.

    An orphan is a project_row whose project_id is NOT in current_pids but whose
    project_session rows overlap with a current project's members.

    MOVE semantics: declared fields, next actions, and accepted flags are transferred
    to the survivor and DELETED from the orphan. This ensures the orphan has no
    declared state remaining and can be pruned.
    """
    all_projects = registry.projects()
    orphan_pids = [dict(p)["project_id"] for p in all_projects
                   if dict(p)["project_id"] not in current_pids]

    migrated_pids: set = set()

    for old_pid in orphan_pids:
        # Find which current project shares the most members with this orphan
        old_links = registry.links_for(old_pid)
        if not old_links:
            continue

        best_new_pid = None
        best_overlap = 0
        for link in old_links:
            profile = link["profile_name"]
            sid = link["session_id"]
            # Find which current project this session belongs to
            row = registry.conn.execute(
                "SELECT project_id FROM project_session WHERE profile_name=? AND session_id=? "
                "AND project_id IN ({})".format(",".join("?" * len(current_pids))),
                (profile, sid, *current_pids)).fetchone()
            if row:
                new_pid = row["project_id"]
                # Count overlap
                overlap = sum(1 for l in old_links
                              if registry.conn.execute(
                                  "SELECT 1 FROM project_session WHERE project_id=? "
                                  "AND profile_name=? AND session_id=?",
                                  (new_pid, l["profile_name"], l["session_id"])).fetchone())
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_new_pid = new_pid

        if best_new_pid is None:
            continue  # orphan with no overlapping current project — leave it

        # MOVE declared fields: insert into survivor, then delete from orphan
        for df in registry.conn.execute(
                "SELECT * FROM declared_field WHERE project_id=?", (old_pid,)):
            existing = registry.conn.execute(
                "SELECT 1 FROM declared_field WHERE project_id=? AND field=?",
                (best_new_pid, df["field"])).fetchone()
            if not existing:
                registry.conn.execute(
                    """INSERT OR REPLACE INTO declared_field
                       (project_id, field, value, source, verified_at, expires_at, actor, declared_rev)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (best_new_pid, df["field"], df["value"], df["source"],
                     df["verified_at"], df["expires_at"], df["actor"], df["declared_rev"]))
        registry.conn.execute("DELETE FROM declared_field WHERE project_id=?", (old_pid,))

        # MOVE next actions: insert into survivor, then delete from orphan
        for na in registry.conn.execute(
                "SELECT * FROM next_action WHERE project_id=? AND state='open'", (old_pid,)):
            registry.conn.execute(
                """INSERT OR REPLACE INTO next_action
                   (action_id, project_id, text, state, source, verified_at, expires_at, declared_rev)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (na["action_id"], best_new_pid, na["text"], na["state"], na["source"],
                 na["verified_at"], na["expires_at"], na["declared_rev"]))
        registry.conn.execute("DELETE FROM next_action WHERE project_id=?", (old_pid,))

        # MOVE accepted flags: update survivor's links where orphan had accepted=1
        for link in old_links:
            if link["accepted"]:
                registry.conn.execute(
                    "UPDATE project_session SET accepted=1, declared_rev=declared_rev+1 "
                    "WHERE project_id=? AND profile_name=? AND session_id=?",
                    (best_new_pid, link["profile_name"], link["session_id"]))

        migrated_pids.add(old_pid)
        report.identity_migrated.append("{} -> {}".format(old_pid, best_new_pid))

    registry.commit()

    # Prune successfully-migrated orphans: all links, project row, signatures
    for old_pid in migrated_pids:
        registry.conn.execute("DELETE FROM project_session WHERE project_id=?", (old_pid,))
        registry.conn.execute("DELETE FROM project WHERE project_id=?", (old_pid,))
        registry.conn.execute("DELETE FROM project_signature WHERE project_id=?", (old_pid,))
        report.orphans_pruned += 1

    if migrated_pids:
        registry.commit()


# --------------------------------------------------------------------------- audience helpers

def facts_by_ref(registry: Registry) -> Dict[str, object]:
    """All present ``session_fact`` rows keyed by ``profile/session_id``."""
    return {"{}/{}".format(r["profile_name"], r["session_id"]): r
            for r in registry.session_facts()}


def _member_rows(registry: Registry, project_id: str, facts: Dict[str, object]
                 ) -> List[Dict[str, object]]:
    out: List[Dict[str, object]] = []
    for link in registry.links_for(project_id):
        ref = "{}/{}".format(link["profile_name"], link["session_id"])
        f = facts.get(ref)
        aud = ((f["audience"] if f is not None else None) or "UNKNOWN")
        out.append({
            "profile_name": link["profile_name"],
            "session_id": link["session_id"],
            "audience": aud,
            "audience_reason": ((f["audience_reason"] if f is not None else None) or aud),
            "accepted": int(link["accepted"] or 0),
            "last_activity_at": (f["last_activity_at"] if f is not None else None),
        })
    return out


def primary_for(member_rows: Sequence[Dict[str, object]]) -> Optional[str]:
    """D-US-6 / CP-5: the primary is a linked ``USER_FACING`` conversation only.

    ``UNKNOWN`` is visible supporting evidence with ``audience_unverified=true`` and is never
    promoted to primary or resume target; ``DELEGATED``/``AUTOMATED`` are supporting only.
    ``None`` means ``NO_USER_SESSION`` — the API returns no resume target in that state.
    """
    best = audience.select_primary(member_rows, ("USER_FACING",))
    if best is None:
        return None
    return "{}/{}".format(best["profile_name"], best["session_id"])


def apply_audience_overrides(registry: Registry, project_ids: Sequence[str],
                             now: Optional[float] = None) -> Dict[str, str]:
    """Re-apply the audited ``audience_override:<profile>/<session>`` declared fields (D-US-7).

    Returns ``{ref: audience}`` for what was applied. Declared fields have no TTL here, so
    they are re-applied on every reconcile exactly like the rest of the declared overlay.
    """
    applied: Dict[str, str] = {}
    for pid in project_ids:
        declared = registry.effective_declared(pid, now=now)
        for field, value in declared.items():
            if not field.startswith("audience_override:"):
                continue
            if value not in audience.AUDIENCES:
                continue
            ref = field.split(":", 1)[1]
            profile, _, sid = ref.partition("/")
            if not profile or not sid:
                continue
            registry.conn.execute(
                "UPDATE session_fact SET audience=?, audience_reason='OVERRIDE' "
                "WHERE profile_name=? AND session_id=?", (value, profile, sid))
            applied[ref] = value
    if applied:
        registry.commit()
    return applied
