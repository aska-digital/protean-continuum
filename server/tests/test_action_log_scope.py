"""Orda directive 2026-09-17 — action-log `scope=` (external default, full-log toggle).

The directive supersedes ONLY the default-content clause of AL-L14: the default view of
``view=action_log`` is the EXTERNAL filter (post / merge / retraction rows, plus direct-edit
rows whose target or evidence says PUBLIC); ``scope=all`` is the previous default exactly.
The inclusion test is the single named audit point ``is_external_action`` (ruling clause 4).

Coverage per the brief's SCOPE OF TESTS:
  (a) default/absent scope behaves as external          -> test_a_default_absent_scope_is_external
  (b) scope=all matches the previous default            -> test_b_scope_all_matches_previous_default
  (c) the direct-edit public/internal boundary          -> test_c_* (helper branches + ledger partition)
  (d) invalid scope -> ValueError / HTTP 400            -> test_d_invalid_scope_*
  (e) archive round-trip under BOTH scopes              -> test_e_archive_round_trip_under_both_scopes
                                                        + test_e_archive_route_round_trip_over_http
  (f) counts expose external_total and reconcile        -> test_f_counts_expose_external_total_and_reconcile

Fixtures are the FROZEN tree under ``fixtures/action_log/`` over a throw-away home — never the
live profile tree; the live ``data/action_log.json`` is untouchable by this lane.
"""
from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

from continuum import action_log as al
from continuum.config import load_config
from continuum.service import Service
from dashboard import plugin_api, standalone
from fixtures.make_fixture import build_fixture_tree

BUILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(BUILD_ROOT, "fixtures", "action_log")
RECEIPTS = os.path.join(FIX, "receipts")
CLEAN_RECEIPTS = ("post-receipt.md", "merge-receipt.md", "close-receipt.md",
                  "retract-receipt.md", "edit-receipt.md")

#: Expected external rows of the FROZEN fixture set (sanity anchor): the post, merge and
#: retraction receipts. Everything else — 6 dispatch rows, the close receipt, and the
#: direct-edit receipt (whose target is a fixture file under ~/.hermes/) — is internal.
FIXTURE_EXTERNAL = {"post", "merge", "retraction"}
FIXTURE_INTERNAL_COUNT = 8


def _sources():
    return [os.path.join(FIX, "INFLIGHT.md"), os.path.join(FIX, "DISPATCH-LEDGER.md")] + \
        [os.path.join(RECEIPTS, name) for name in CLEAN_RECEIPTS]


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Fixture home + temp registry + temp ledger, seeded from the FROZEN sources.

    Same pattern the ARCHIVE lane uses (and the crashed run worked out): a lazily-built
    Service — a SQLite connection from the test thread cannot be reused by TestClient's
    worker thread — monkey-patched into plugin_api, plus the real standalone app over HTTP.
    """
    home = build_fixture_tree(str(tmp_path / "hermes_home"))
    cfg = load_config(hermes_home=home)
    cfg.bundle["registry_path"] = str(tmp_path / "registry.db")
    cfg.bundle["action_log_enabled"] = True
    cfg.bundle["action_log_ledger_path"] = str(tmp_path / "action_log.json")
    cfg.bundle["action_log_source_paths"] = _sources()
    report = al.sync(cfg, now=1000.0)
    assert report["row_count"] == 11, report["counts"]
    holder = {}

    def service():
        if "svc" not in holder:
            holder["svc"] = Service(cfg)
        return holder["svc"]

    monkeypatch.setattr(plugin_api, "get_service", service)
    return {"cfg": cfg, "service": service, "report": report,
            "client": TestClient(standalone.create_app()), "tmp": tmp_path}


def _mk_row(action_id, kind, target, *, target_type="file", evidence_type="receipt-path",
            evidence_link="fixture-evidence", ts=1000.0, status="active"):
    """One row in the locked 13-field ledger shape (for a hand-built ledger)."""
    return {"action_id": action_id, "timestamp": ts, "kind": kind, "target": target,
            "target_type": target_type, "evidence_type": evidence_type,
            "evidence_link": evidence_link, "operator_flag": "agent",
            "source": "direct-log", "source_row_id": action_id, "status": status,
            "archived_at": None, "archive_audit_id": None}


def _ledger(tmp_path, rows):
    """Write a constructed ledger (schema 1) to a temp path; return the path."""
    path = str(tmp_path / "constructed_ledger.json")
    al.write_ledger(path, {"schema": al.LEDGER_SCHEMA, "last_sync_at": 1000.0,
                           "last_source_hashes": {}, "items": list(rows)})
    return path


def _kinds(rows):
    return {row["kind"] for row in rows}


def _ids(resp_or_rows):
    rows = resp_or_rows["actions"] if isinstance(resp_or_rows, dict) else resp_or_rows
    return {row["action_id"] for row in rows}


def _row_of(env_or_ledger_rows, kind):
    """One row of `kind` from the fixture ledger (list of row dicts)."""
    return [r for r in env_or_ledger_rows if r["kind"] == kind][0]


# --------------------------------------------------------------- (c) the named audit helper

def test_c_is_external_action_external_kinds_always_in():
    # Ruling clause 1: kind alone decides for post / merge / retraction.
    for kind in al.EXTERNAL_KINDS:
        assert al.is_external_action(_mk_row("a-" + kind, kind, "/whatever")) is True


def test_c_is_external_action_internal_kinds_always_out():
    # Ruling clause 3: dispatch/close stay internal EVEN with public-looking evidence —
    # the kind check precedes the evidence check, exactly as the ruling orders it.
    for kind in al.INTERNAL_KINDS:
        assert al.is_external_action(_mk_row("a-" + kind, kind, "/whatever")) is False
        assert al.is_external_action(_mk_row(
            "a-" + kind + "-url", kind, "https://example.com/x",
            target_type="url", evidence_type="post-url")) is False


def test_c_is_external_action_direct_edit_boundary():
    root = al.INTERNAL_PATH_ROOT
    # Clause 2, branch PUBLIC: a URL target ...
    assert al.is_external_action(_mk_row(
        "de-url", "direct-edit", "https://vercel.com/deploy/1",
        target_type="url", evidence_type="commit-sha")) is True
    # ... or a path OUTSIDE the internal Hermes tree.
    assert al.is_external_action(_mk_row(
        "de-outside", "direct-edit", "/Users/kethuda/Documents/team6/public/README.md")) is True
    # Clause 2, branch INTERNAL: under ~/.hermes/ with receipt-path evidence ...
    assert al.is_external_action(_mk_row(
        "de-inside", "direct-edit", root + "profiles/mozi/cache/x.md")) is False
    # ... but the SAME internal path flips to external on post-url evidence (clause 2, last).
    assert al.is_external_action(_mk_row(
        "de-inside-posturl", "direct-edit", root + "profiles/mozi/cache/x.md",
        evidence_type="post-url")) is True


def test_c_is_external_action_unknown_kind_fails_safe():
    # Clause 4 fail-safe: a FUTURE kind is internal unless its own evidence says public.
    assert al.is_external_action(_mk_row(
        "fut-in", "publish", al.INTERNAL_PATH_ROOT + "p.md")) is False
    assert al.is_external_action(_mk_row(
        "fut-url", "publish", al.INTERNAL_PATH_ROOT + "p.md",
        evidence_type="post-url")) is True
    assert al.is_external_action(_mk_row(
        "fut-out", "publish", "https://example.com/p", target_type="url")) is True


def test_c_direct_edit_target_is_public_helper():
    assert al._target_is_public("http://internal.lan/x") is True
    assert al._target_is_public("https://x") is True
    assert al._target_is_public(al.INTERNAL_PATH_ROOT + "a/b.md") is False
    assert al._target_is_public("/tmp/outside.md") is True
    assert al._target_is_public("") is False
    assert al._target_is_public(None) is False


# --------------------------------------------------------------- (c/d) query vocabulary

def test_d_parse_query_scope_vocabulary():
    assert al.parse_query({})["scope"] == al.DEFAULT_SCOPE == "external"   # absent = default
    assert al.parse_query({"scope": ""})["scope"] == "external"            # empty = default
    assert al.parse_query({"scope": "external"})["scope"] == "external"
    assert al.parse_query({"scope": "all"})["scope"] == "all"
    with pytest.raises(ValueError) as exc:
        al.parse_query({"scope": "bogus"})
    assert "invalid scope" in str(exc.value)
    # Purely additive: scope composes with the AL-L13 vocabulary.
    q = al.parse_query({"scope": "all", "kind": ["post"], "status": ["archived"]})
    assert q["scope"] == "all" and q["kinds"] == ["post"] and q["statuses"] == ["archived"]


# --------------------------------------------------------------- (a) default = external

def test_a_default_absent_scope_is_external(env):
    svc = env["service"]()
    default = svc.board(view="action_log")
    explicit = svc.board(view="action_log", scope="external")
    assert default == explicit                                  # absent behaves as external
    assert default["counts"]["scope"] == "external"
    ids = _ids(default)
    assert len(ids) == 3 and _kinds(default["actions"]) == FIXTURE_EXTERNAL
    by_id = {r["action_id"]: r for r in env["report"]["items"]}
    for row in default["actions"]:
        assert by_id[row["action_id"]]["kind"] in FIXTURE_EXTERNAL
        assert row["evidence_link"]                             # AL-L1: every row links evidence
    stamps = [row["timestamp"] for row in default["actions"]]
    assert stamps == sorted(stamps, reverse=True)               # newest first (AL-L14)
    # Internal rows are one click away, NOT gone: dispatch/close/internal direct-edit absent
    # by default, all present under scope=all.
    default_kinds = _kinds(env["report"]["items"]) - FIXTURE_EXTERNAL
    assert default_kinds == {"dispatch", "close", "direct-edit"}
    full = svc.board(view="action_log", scope="all", page_size=200)
    assert len(full["actions"]) == 11
    assert _kinds(full["actions"]) == default_kinds | FIXTURE_EXTERNAL


def test_a_external_filter_excludes_the_fixture_internal_direct_edit(env):
    # The frozen edit-receipt projects to a direct-edit row targeting a file UNDER ~/.hermes/
    # (the build tree lives there) — the public/internal boundary must classify it internal.
    svc = env["service"]()
    edit = _row_of(env["report"]["items"], "direct-edit")
    assert edit["target"].startswith(al.INTERNAL_PATH_ROOT)
    assert edit["evidence_type"] != "post-url"
    default = svc.board(view="action_log", page_size=200)
    assert edit["action_id"] not in _ids(default)
    assert edit["action_id"] in _ids(svc.board(view="action_log", scope="all", page_size=200))


def test_a_scope_composes_with_kind_filters(env):
    svc = env["service"]()
    assert svc.board(view="action_log", kind=["dispatch"])["actions"] == []
    all_dispatch = svc.board(view="action_log", scope="all", kind=["dispatch"], page_size=200)
    assert len(all_dispatch["actions"]) == 6
    assert svc.board(view="action_log", scope="all", kind=["close"])["actions"]  # internal kind
    assert _ids(svc.board(view="action_log", kind=["post"])) == \
        _ids(svc.board(view="action_log", scope="all", kind=["post"]))


# --------------------------------------------------------------- (b) scope=all = old default

def test_b_scope_all_matches_previous_default(env):
    svc = env["service"]()
    full = svc.board(view="action_log", scope="all", page_size=200)
    # Today's behavior exactly: every ACTIVE row, newest first, archived still hidden.
    active = [r for r in env["report"]["items"] if r["status"] == "active"]
    assert _ids(full) == {r["action_id"] for r in active}
    stamps = [row["timestamp"] for row in full["actions"]]
    assert stamps == sorted(stamps, reverse=True)
    assert full["counts"]["by_kind"] == env["report"]["counts"]["by_kind"]
    assert full["counts"]["total"] == 11 and full["counts"]["active"] == 11
    assert full["counts"]["archived"] == 0
    assert all(row["status"] == "active" for row in full["actions"])


def test_b_scope_all_preserves_paging_and_status_browse(env):
    cfg, svc = env["cfg"], env["service"]()
    al.archive(cfg, "al-inf-20260918-fixture-01", actor="qa")
    full = svc.board(view="action_log", scope="all", page_size=200)
    assert len(full["actions"]) == 10                          # active only, as before
    archived = svc.board(view="action_log", scope="all", status=["archived"])
    assert [r["action_id"] for r in archived["actions"]] == ["al-inf-20260918-fixture-01"]
    one = svc.board(view="action_log", scope="all", page="1", page_size="5")
    page = svc.board(view="action_log", scope="all", page="2", page_size="5")
    assert len(one["actions"]) == len(page["actions"]) == 5
    assert one["has_more"] is True and page["has_more"] is False
    assert _ids(one) != _ids(page)
    assert _ids(one) | _ids(page) == _ids(full)


# --------------------------------------------------------------- (c) ledger-level partition

def test_c_constructed_ledger_scope_partition(env, tmp_path):
    root = al.INTERNAL_PATH_ROOT
    rows = [
        _mk_row("x-post", "post", "https://github.com/o/r/issues/1#issuecomment-1",
                target_type="url", evidence_type="post-url", ts=1.0),
        _mk_row("x-merge", "merge", "deadbeef" * 5, target_type="commit",
                evidence_type="commit-sha", ts=2.0),
        _mk_row("x-retract", "retraction", root + "a.md", ts=3.0),   # kind decides, not path
        _mk_row("x-dispatch", "dispatch", "t-01", target_type="delegation", ts=4.0),
        _mk_row("x-close", "close", "https://example.com/issue/9",
                target_type="url", evidence_type="post-url", ts=5.0),  # clause 3: absolute
        _mk_row("x-de-public-url", "direct-edit", "https://api.example.com/thing",
                target_type="url", evidence_type="commit-sha", ts=6.0),
        _mk_row("x-de-outside", "direct-edit", "/srv/www/site/index.html", ts=7.0),
        _mk_row("x-de-inside", "direct-edit", root + "profiles/p/cache/n.md", ts=8.0),
        _mk_row("x-de-inside-posturl", "direct-edit", root + "profiles/p/cache/n.md",
                evidence_type="post-url", ts=9.0),
        _mk_row("x-future-internal", "publish", root + "p.md", ts=10.0),   # fail-safe: out
        _mk_row("x-future-public", "publish", "https://x.example/post",
                target_type="url", evidence_type="post-url", ts=11.0),     # fail-safe: in
    ]
    path = _ledger(tmp_path, rows)
    external = al.view(cfg=None, query=al.parse_query({}), ledger_path=path)
    assert _ids(external) == {"x-retract", "x-de-inside-posturl", "x-de-public-url",
                              "x-de-outside", "x-merge", "x-future-public", "x-post"}
    full = al.view(cfg=None, query=al.parse_query({"scope": "all"}), ledger_path=path)
    assert len(full["actions"]) == 11
    # Default view is newest-first among exactly those 7 external rows.
    expected_order = [r["action_id"] for r in
                      sorted((r for r in rows if r["action_id"] in _ids(external)),
                             key=lambda r: -r["timestamp"])]
    assert [r["action_id"] for r in external["actions"]] == expected_order
    c = external["counts"]
    assert (c["external_total"], c["internal_total"], c["active"]) == (7, 4, 11)
    assert c["external_total"] + c["internal_total"] == c["active"]


# --------------------------------------------------------------- (d) invalid scope -> 400

def test_d_invalid_scope_returns_http_400(env):
    client = env["client"]
    url = standalone.API_PREFIX + "/projects"
    bad = client.get(url, params={"view": "action_log", "scope": "bogus"})
    assert bad.status_code == 400
    assert "invalid scope" in bad.json()["detail"]
    # A bad scope must not leak as a silent external default on any combination.
    assert client.get(url, params={"view": "action_log", "scope": "External"}).status_code == 400
    assert client.get(url, params={"view": "action_log",
                                   "scope": "bogus", "kind": "post"}).status_code == 400
    for good in ("external", "all"):
        assert client.get(url, params={"view": "action_log",
                                       "scope": good}).status_code == 200


# --------------------------------------------------------------- (f) counts reconcile

def test_f_counts_expose_external_total_and_reconcile(env):
    svc = env["service"]()
    for scope in (None, "external", "all"):
        kwargs = {"view": "action_log", "page_size": 200}
        if scope is not None:
            kwargs["scope"] = scope
        c = svc.board(**kwargs)["counts"]
        # Existing keys keep name and type (AL-L7 additive wire rule).
        for key in ("total", "active", "archived", "by_kind", "by_operator"):
            assert key in c
        assert set(c["by_kind"]) == set(al.KINDS)
        # Additive keys present and correct on BOTH scopes.
        assert c["scope"] == (scope or "external")
        assert c["external_total"] == 3
        assert c["external_total"] + c["internal_total"] == c["active"]
        assert c["total"] == c["active"] + c["archived"] == 11


def test_f_external_total_tracks_the_archive_lifecycle(env):
    cfg, svc = env["cfg"], env["service"]()
    post_id = _row_of(env["report"]["items"], "post")["action_id"]
    assert svc.board(view="action_log")["counts"]["external_total"] == 3
    al.archive(cfg, post_id, actor="qa")
    c = svc.board(view="action_log")["counts"]
    assert c["external_total"] == 2 and c["active"] == 10
    assert c["external_total"] + c["internal_total"] == c["active"]
    al.unarchive(cfg, post_id, actor="qa")
    c = svc.board(view="action_log")["counts"]
    assert c["external_total"] == 3 and c["active"] == 11


# --------------------------------------------------------------- (e) archive round-trips

def test_e_archive_round_trip_under_both_scopes(env):
    cfg, svc = env["cfg"], env["service"]()
    post = _row_of(env["report"]["items"], "post")
    post_id = post["action_id"]
    original = json.dumps(post, sort_keys=True)

    def visible(scope, **over):
        kwargs = {"view": "action_log", "scope": scope, "page_size": 200}
        kwargs.update(over)
        return post_id in _ids(svc.board(**kwargs))

    # PRE: an external row is in the default view and in the full log.
    assert visible("external") and visible("all")
    first = al.archive(cfg, post_id, actor="qa")
    assert first["ok"] and not first["noop"]
    # Archived: absent from the ACTIVE view in both scopes; archived browse shows it in both
    # (AL-L14's active-only default + the status= browse survive the new filter).
    assert not visible("external") and not visible("all")
    assert visible("external", status=["archived"])
    assert visible("all", status=["archived"])
    # Double archive is a no-op with the SAME audit id and no second event (AL-I2).
    second = al.archive(cfg, post_id, actor="qa")
    assert second["noop"] is True and second["audit_id"] == first["audit_id"]
    assert len(al.read_events(al.events_path_for(cfg))) == 1
    # Unarchive: visible again in BOTH scopes, row restored byte-for-byte, two events.
    back = al.unarchive(cfg, post_id, actor="qa")
    assert back["ok"] and not back["noop"]
    assert visible("external") and visible("all")
    ledger = al.load_ledger(al.ledger_path_for(cfg))
    row = next(r for r in ledger["items"] if r["action_id"] == post_id)
    assert json.dumps(row, sort_keys=True) == original
    assert [e["action"] for e in al.read_events(al.events_path_for(cfg))] == \
        ["archive_action_log", "unarchive_action_log"]


def test_e_internal_row_round_trip_under_both_scopes(env):
    # Symmetry: archiving an INTERNAL row (a dispatch) — invisible in the default scope both
    # before and after, fully browsable and restorable under scope=all.
    cfg, svc = env["cfg"], env["service"]()
    dispatch_id = "al-inf-20260918-fixture-01"

    def visible_all(**over):
        kwargs = {"view": "action_log", "scope": "all", "page_size": 200}
        kwargs.update(over)
        return dispatch_id in _ids(svc.board(**kwargs))

    assert visible_all()
    assert dispatch_id not in _ids(svc.board(view="action_log", page_size=200))
    al.archive(cfg, dispatch_id, actor="qa")
    assert not visible_all()
    assert visible_all(status=["archived"])
    assert svc.board(view="action_log", status=["archived"])["actions"] == []
    al.unarchive(cfg, dispatch_id, actor="qa")
    assert visible_all()
    assert len(al.read_events(al.events_path_for(cfg))) == 2


def test_e_archive_route_round_trip_over_http(env):
    # The dashboard POST routes + GET under both scopes, over the real FastAPI app.
    client = env["client"]
    post_id = _row_of(env["report"]["items"], "post")["action_id"]
    base = "{}/action-log/{}".format(standalone.API_PREFIX, post_id)
    url = standalone.API_PREFIX + "/projects"
    assert client.get(url, params={"view": "action_log"}).json()["counts"]["external_total"] == 3
    arch = client.post(base + "/archive", json={"actor": "dashboard"})
    assert arch.status_code == 200 and arch.json()["action"] == "archive_action_log"
    ext = client.get(url, params={"view": "action_log"}).json()
    assert post_id not in _ids(ext) and ext["counts"]["external_total"] == 2
    full = client.get(url, params={"view": "action_log", "scope": "all"}).json()
    assert post_id not in _ids(full) and full["counts"]["active"] == 10
    browse = client.get(url, params={"view": "action_log", "status": "archived"}).json()
    assert [r["action_id"] for r in browse["actions"]] == [post_id]
    back = client.post(base + "/unarchive", json={"actor": "dashboard"})
    assert back.status_code == 200
    assert post_id in _ids(client.get(url, params={"view": "action_log"}).json())


# --------------------------------------------------------------- HTTP read-back (a)/(b)

def test_http_default_scope_external_and_toggle_all(env):
    client = env["client"]
    url = standalone.API_PREFIX + "/projects"
    default = client.get(url, params={"view": "action_log"})
    assert default.status_code == 200
    d = default.json()
    assert d["view"] == "action_log" and d["counts"]["scope"] == "external"
    assert _kinds(d["actions"]) == FIXTURE_EXTERNAL
    assert all(row["status"] == "active" for row in d["actions"])
    toggle = client.get(url, params={"view": "action_log", "scope": "all",
                                     "page_size": 200}).json()
    assert len(toggle["actions"]) == 11
    assert _ids(toggle) > _ids(d)                              # internal rows one click away
    # Envelope shape is unchanged and disjoint from the board (AL-L7/A8).
    assert set(d.keys()) == {"view", "counts", "actions", "page", "page_size", "has_more"}
    legacy = client.get(url).json()
    assert "actions" not in legacy and "items" in legacy
    today = client.get(url, params={"view": "today"}).json()
    assert "actions" not in today and "items" in today
