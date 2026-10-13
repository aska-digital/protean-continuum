"""Undo restores project rows and the project set, as well as link overlays."""
import json
import pytest
from continuum.config import load_config
from continuum.service import Service
from fixtures.make_fixture import build_fixture_tree


@pytest.fixture
def svc(tmp_path):
    home = build_fixture_tree(str(tmp_path / "home"))
    cfg = load_config(hermes_home=home)
    cfg.bundle["registry_path"] = str(tmp_path / "registry.db")
    service = Service(cfg)
    service.scan()
    yield service
    service.registry.close()


def _target(svc):
    return sorted(item["project_id"] for item in svc.inbox()["items"])[0]


@pytest.mark.parametrize("action,payload,column", [
    ("accept", {"name": "Renamed project"}, "name"),
    ("set_owner", {"owner": "new-owner"}, "owner_profile"),
    ("drive_expected", {"value": True}, "drive_expected"),
])
def test_undo_restores_full_project_snapshot_and_public_column(svc, action, payload, column):
    target = _target(svc)
    before = svc.registry.snapshot([target])
    api_before = svc.project_detail(target)["project"][column]
    changed = svc.review(action, target, payload)
    assert svc.project_detail(target)["project"][column] != api_before
    svc.review("undo", "", {"audit_id": changed["audit_id"]})
    assert svc.registry.snapshot([target]) == before
    assert svc.project_detail(target)["project"][column] == api_before


def test_split_undo_and_undo_of_undo_restore_exact_project_sets(svc):
    target = next(row["project_id"] for row in svc.registry.conn.execute(
        "SELECT project_id FROM project ORDER BY project_id")
        if len(svc.registry.links_for(row["project_id"])) >= 2)
    link = svc.registry.links_for(target)[0]
    original_ids = {row["project_id"] for row in svc.registry.conn.execute("SELECT project_id FROM project")}
    before = svc.registry.snapshot(sorted(original_ids))
    split = svc.review("split", target, {
        "session_refs": [f'{link["profile_name"]}/{link["session_id"]}'],
        "name": "Split project",
    })
    split_ids = {row["project_id"] for row in svc.registry.conn.execute("SELECT project_id FROM project")}
    created, = split_ids - original_ids
    after = svc.registry.snapshot(sorted(split_ids))
    undo = svc.review("undo", "", {"audit_id": split["audit_id"]})
    assert svc.registry.get_project(created) is None
    assert svc.registry.snapshot(sorted(original_ids)) == before
    assert not svc.registry.links_for(created)
    assert {row["project_id"] for row in svc.registry.conn.execute("SELECT project_id FROM project")} == original_ids
    svc.review("undo", "", {"audit_id": undo["audit_id"]})
    assert svc.registry.snapshot(sorted(split_ids)) == after


def test_restore_preserves_unrelated_projects_and_existing_foreign_key_references(svc):
    target = _target(svc)
    db = svc.registry.conn
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("CREATE TABLE project_reference (project_id TEXT REFERENCES project(project_id) ON DELETE CASCADE)")
    db.execute("INSERT INTO project_reference VALUES (?)", (target,))
    db.commit()
    changed = svc.review("set_owner", target, {"owner": "new-owner"})
    db.execute("INSERT INTO project(project_id, name) VALUES ('later-project', 'Unrelated')")
    db.execute("INSERT INTO project_signature VALUES ('later-signature', 'later-project', 1)")
    db.execute("INSERT INTO evidence(evidence_id, project_id, tier, kind) VALUES ('later-evidence', 'later-project', 1, 'test')")
    db.commit()
    untouched = svc.registry.snapshot(["later-project"])
    svc.review("undo", "", {"audit_id": changed["audit_id"]})
    assert svc.registry.snapshot(["later-project"]) == untouched
    assert db.execute("SELECT project_id FROM project_reference").fetchone()[0] == target
    assert db.execute("SELECT project_id FROM project_signature WHERE signature='later-signature'").fetchone()[0] == "later-project"
    assert db.execute("SELECT project_id FROM evidence WHERE evidence_id='later-evidence'").fetchone()[0] == "later-project"


def test_legacy_split_audit_without_absent_marker_remains_reversible(svc):
    target = next(row["project_id"] for row in svc.registry.conn.execute(
        "SELECT project_id FROM project ORDER BY project_id")
        if len(svc.registry.links_for(row["project_id"])) >= 2)
    link = svc.registry.links_for(target)[0]
    original = {row["project_id"] for row in svc.registry.conn.execute("SELECT project_id FROM project")}
    split = svc.review("split", target, {"session_refs": [f'{link["profile_name"]}/{link["session_id"]}']})
    audit = svc.registry.get_review_event(split["audit_id"])
    before = json.loads(audit["before_json"])
    # Reconstruct the audit shape emitted before the absent marker was added.
    before["projects"] = {pid: row for pid, row in before["projects"].items() if row is not None}
    svc.registry.conn.execute("UPDATE review_event SET before_json=? WHERE event_id=?", (json.dumps(before), split["audit_id"]))
    svc.registry.conn.commit()
    svc.review("undo", "", {"audit_id": split["audit_id"]})
    assert {row["project_id"] for row in svc.registry.conn.execute("SELECT project_id FROM project")} == original
