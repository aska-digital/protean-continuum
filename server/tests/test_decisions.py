"""Decisions command centre: the org-wide `needs decision` label as a live queue.

All GitHub traffic is faked at the runner seam. These tests pin the contract the
dashboard relies on: option panels are parsed from the item when it states them,
an action is idempotent and stale-checked, a comment ruling unlabels the item,
an ion proposal is decided by label (never by comment), and a dispatch writes a
durable packet an agent can pick up.
"""
from __future__ import annotations

import json

import pytest

from continuum import decisions as dec


def _item(repo, number, title, body, *, pr=False, labels=("needs decision",),
          updated="2026-10-13T10:00:00Z", comments=0):
    repo_url = "https://api.github.com/repos/aska-digital/" + repo
    it = {
        "number": number, "title": title, "body": body, "state": "open",
        "html_url": "https://github.com/aska-digital/{}/issues/{}".format(repo, number),
        "repository_url": repo_url, "comments": comments,
        "created_at": "2026-10-10T09:00:00Z", "updated_at": updated,
        "user": {"login": "abu-aska"},
        "labels": [{"name": name} for name in labels],
    }
    if pr:
        it["pull_request"] = {"url": repo_url + "/pulls/{}".format(number)}
        it["html_url"] = it["html_url"].replace("/issues/", "/pull/")
    return it


PR_BODY = """Staging only, not for merge as-is.

## Options
- **Merge as-is**: Ships today. Cost: the known wart in the middle ships too.
- **Salvage the good commits**: Cherry-pick onto a clean branch, keep authorship. Cost: an agent hour.
- **Close it**: Nothing ships. Cost: the work is redone later.
"""

ION_BODY = "## Proposal\n\nMerge a into b. plan-sha256: abc123\n"


class FakeGH:
    def __init__(self, items):
        self.items = items          # search results
        self.calls = []             # (argv, stdin)
        self.comments = {}          # (repo, number) -> [comment dicts]

    def __call__(self, argv, stdin=None):
        self.calls.append((argv, stdin))
        joined = " ".join(argv)
        if "search/issues" in joined:
            page = 1
            for part in argv:
                if "page=" in part:
                    page = int(part.rsplit("page=", 1)[1].split("&")[0])
            return json.dumps({"total_count": len(self.items),
                               "items": self.items if page == 1 else []})
        if "/comments" in joined and "--method" not in argv:
            parts = argv[2].split("/")
            repo, number = parts[2], int(parts[4].split("?")[0])
            return json.dumps(self.comments.get((repo, number), []))
        if "--method" in argv and "POST" in argv and "/comments" in joined:
            return json.dumps({"id": 999})
        if "--method" in argv:
            return "{}"
        if "/pulls/" in joined:
            return json.dumps({"draft": False, "mergeable": True, "changed_files": 3,
                               "additions": 40, "deletions": 5,
                               "head": {"sha": "headsha", "ref": "feat"},
                               "base": {"ref": "main"}})
        if "/issues/" in joined:     # single-item refetch
            parts = argv[2].split("/")
            repo, number = parts[2], int(parts[4].split("?")[0])
            for it in self.items:
                if it["number"] == number and it["repository_url"].endswith("/" + repo):
                    return json.dumps(it)
            return json.dumps({})
        return "{}"

    def posts(self, needle):
        return [(a, s) for a, s in self.calls
                if needle in " ".join(a) and "POST" in a]


def cfg(tmp_path):
    return {
        "decisions_org": "aska-digital",
        "decisions_label": "needs decision",
        "decisions_snapshot_path": str(tmp_path / "snap.json"),
        "decisions_events_path": str(tmp_path / "events.jsonl"),
        "decisions_dispatch_dir": str(tmp_path / "dispatch"),
        "decisions_ion_tracker_repo": "ion-tracker",
    }


def _sync(tmp_path, gh):
    return dec.sync(cfg(tmp_path), runner=gh, now=1760000000.0)


def test_sync_parses_explicit_panels_and_ion_choices(tmp_path):
    gh = FakeGH([
        _item("exo", 7, "Ship the thing", PR_BODY, pr=True),
        _item("ion-tracker", 3, "[P-0007] merge a into b", ION_BODY,
              labels=("needs decision", "ion:proposal")),
    ])
    snap = _sync(tmp_path, gh)
    assert snap["counts"]["total"] == 2
    pr = next(i for i in snap["items"] if i["repo"] == "exo")
    assert [o["label"] for o in pr["options"]][:3] == [
        "Merge as-is", "Salvage the good commits", "Close it"]
    assert pr["options"][0]["intent"] == "merge"
    assert pr["options"][1]["intent"] == "salvage"
    assert pr["options"][0]["source"] == "explicit"
    assert pr["head_sha"] == "headsha" and pr["changed_files"] == 3
    ion = next(i for i in snap["items"] if i["repo"] == "ion-tracker")
    assert ion["source"] == "ion" and ion["ion_proposal"] == "P-0007"
    assert [o["intent"] for o in ion["options"]] == ["approve", "hold", "reject"]


def test_either_or_sentence_becomes_two_panels():
    opts = dec.parse_options(
        "Pick either a per-seat credential during onboarding, or locking the "
        "surface off until a maintainer adds one. Shipping the current shared "
        "state is the one outcome to avoid.", [], kind="issue", source="github")
    assert len(opts) >= 2
    assert "credential" in opts[0]["label"].lower() or "credential" in opts[0]["detail"].lower()
    assert "locking" in (opts[1]["label"] + opts[1]["detail"]).lower()


def test_comment_action_posts_once_and_unlabels(tmp_path):
    gh = FakeGH([_item("exo", 7, "Ship the thing", PR_BODY, pr=True)])
    snap = _sync(tmp_path, gh)
    item = snap["items"][0]
    ev = dec.act(cfg(tmp_path), item["id"], item["options"][0]["id"], "comment",
                 note="Ship it.", expected_fingerprint=item["fingerprint"],
                 idempotency_key="k1", runner=gh, now=1760000100.0)
    assert ev["status"] == "done" and ev["mode"] == "comment"
    assert len(gh.posts("/comments")) == 1
    unlabels = [a for a, _ in gh.calls if "DELETE" in a]
    assert unlabels, "the needs decision label must come off"
    again = dec.act(cfg(tmp_path), item["id"], item["options"][0]["id"], "comment",
                    note="Ship it.", expected_fingerprint=item["fingerprint"],
                    idempotency_key="k1", runner=gh, now=1760000200.0)
    assert again["action_id"] == ev["action_id"]
    assert len(gh.posts("/comments")) == 1, "a replayed action must not comment twice"


def test_stale_fingerprint_is_refused(tmp_path):
    gh = FakeGH([_item("exo", 7, "Ship the thing", PR_BODY, pr=True)])
    snap = _sync(tmp_path, gh)
    item = snap["items"][0]
    with pytest.raises(dec.DecisionsError) as exc:
        dec.act(cfg(tmp_path), item["id"], item["options"][0]["id"], "comment",
                note="", expected_fingerprint="stale",
                idempotency_key="k2", runner=gh, now=1760000100.0)
    assert getattr(exc.value, "status_code", None) == 409


def test_ion_action_records_labels_and_never_comments(tmp_path):
    gh = FakeGH([_item("ion-tracker", 3, "[P-0007] merge a into b", ION_BODY,
                       labels=("needs decision", "ion:proposal"))])
    snap = _sync(tmp_path, gh)
    item = snap["items"][0]
    approve = next(o for o in item["options"] if o["intent"] == "approve")
    ev = dec.act(cfg(tmp_path), item["id"], approve["id"], "comment",
                 note="", expected_fingerprint=item["fingerprint"],
                 idempotency_key="k3", runner=gh, now=1760000100.0)
    assert ev["status"] == "done"
    bodies = [s for _, s in gh.calls if s]
    assert any("ion:approved" in (s or "") for s in bodies)
    assert not [a for a, _ in gh.calls if "/comments" in " ".join(a) and "POST" in a]


def test_dispatch_writes_a_packet_and_announces_it(tmp_path):
    gh = FakeGH([_item("exo", 7, "Ship the thing", PR_BODY, pr=True)])
    snap = _sync(tmp_path, gh)
    item = snap["items"][0]
    salvage = next(o for o in item["options"] if o["intent"] == "salvage")
    ev = dec.act(cfg(tmp_path), item["id"], salvage["id"], "dispatch",
                 note="Keep authorship.", expected_fingerprint=item["fingerprint"],
                 idempotency_key="k4", runner=gh, now=1760000100.0)
    assert ev["status"] in ("dispatched", "queued")
    packet = tmp_path / "dispatch" / (ev["action_id"] + ".md")
    assert packet.exists() and "Salvage" in packet.read_text(encoding="utf-8")
    shown = dec.show(cfg(tmp_path))
    assert shown["items"][0]["local_status"] == ev["status"]


def test_decisions_routes_serve_page_and_guard_actions():
    from fastapi.testclient import TestClient

    from dashboard import standalone

    client = TestClient(standalone.create_app())
    page = client.get("/decisions.html")
    assert page.status_code == 200 and "Decisions" in page.text
    listing = client.get("/api/plugins/continuum/decisions")
    assert listing.status_code == 200 and "counts" in listing.json()
    guarded = client.post("/api/plugins/continuum/decisions/sync", json={})
    assert guarded.status_code == 403, "sync writes a snapshot and must carry the session token"
