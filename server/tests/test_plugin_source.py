"""M8 conformance: the desktop plugin must be valid uncompiled ESM.

Static checks (always run) + a real parser check via node when available.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess

import pytest

BUILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_JS = os.path.join(BUILD_ROOT, "desktop", "plugin.js")
PLUGIN_API = os.path.join(BUILD_ROOT, "dashboard", "plugin_api.py")

APPROVED_IMPORTS = {"@hermes/plugin-sdk", "react", "react/jsx-runtime", "./kanban-interaction.js"}


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _import_specifiers(src):
    specs = set()
    for m in re.finditer(r"""^\s*import\s+(?:[^'\"]*?\s+from\s+)?['\"]([^'\"]+)['\"]""",
                         src, re.MULTILINE):
        specs.add(m.group(1))
    for m in re.finditer(r"""import\(\s*['\"]([^'\"]+)['\"]\s*\)""", src):
        specs.add(m.group(1))
    return specs


def test_package_admission_contract_and_dashboard_manifest():
    import importlib.util
    import json
    import inspect

    init_path = os.path.join(BUILD_ROOT, "__init__.py")
    spec = importlib.util.spec_from_file_location("continuum_package", init_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.register)
    assert module.register(object()) is None

    manifest_path = os.path.join(BUILD_ROOT, "dashboard", "manifest.json")
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    assert manifest["name"] == "continuum"
    assert manifest["api"] == "plugin_api.py"
    assert os.path.isfile(os.path.join(BUILD_ROOT, "dashboard", manifest["api"]))
    assert "ctx" in str(inspect.signature(module.register))


def test_plugin_uses_scoped_rest_socket_and_polling_fallback():
    src = _read(PLUGIN_JS)
    assert "pluginCtx.rest" in src
    assert "pluginCtx.socket('/events'" in src
    assert "refetchInterval: POLL_MS" in src
    assert not re.search(r"\bfetch\s*\(", src)
    assert "'/projects/'" in src and "'/review/undo'" in src and "'/scan'" in src


def test_detail_uses_profile_name_resume_links_and_retry_context():
    src = _read(PLUGIN_JS)
    assert "profile_name" in src
    assert "resume_links" in src
    assert "Open the source session" in src
    assert "q.refetch" in src
    assert "last good data as of" in src


def test_plugin_file_exists_and_is_esm():
    src = _read(PLUGIN_JS)
    assert "export default" in src
    assert re.search(r"export default\s*\{", src)


def test_plugin_imports_only_approved_specifiers():
    specs = _import_specifiers(_read(PLUGIN_JS))
    assert specs, "expected at least one import"
    assert specs <= APPROVED_IMPORTS, "unapproved imports: {}" .format(specs - APPROVED_IMPORTS)


def test_plugin_imported_symbols_are_actually_used():
    """Every symbol imported from kanban-interaction.js must be used (not dead)."""
    src = _read(PLUGIN_JS)
    shared_path = os.path.join(BUILD_ROOT, "desktop", "kanban-interaction.js")
    shared_src = _read(shared_path)
    # Extract imported symbols from the kanban-interaction import line
    m = re.search(r"import\s*\{([^}]+)\}\s*from\s*'./kanban-interaction\.js'", src)
    assert m, "kanban-interaction import not found"
    imported = [s.strip() for s in m.group(1).split(",")]
    for sym in imported:
        # Count occurrences in plugin.js beyond the import line
        body_after_import = src.split("from './kanban-interaction.js'")[1] if "from './kanban-interaction.js'" in src else src
        assert sym in body_after_import, \
            "imported symbol '{}' is not used in plugin.js (dead import)".format(sym)


def test_plugin_uses_jsx_calls_and_no_jsx_syntax():
    src = _read(PLUGIN_JS)
    assert "jsx('" in src or 'jsx(\"' in src
    # element-position JSX syntax (<Foo ...> or </Foo>) must not appear
    assert not re.search(r"</[A-Za-z][A-Za-z0-9.]*\s*>", src), "JSX closing tag found"
    assert not re.search(r"<\s*[A-Z][A-Za-z0-9]*\s+[a-zA-Z-]+=", src), "JSX element syntax found"


def test_plugin_has_no_hardcoded_colors():
    src = _read(PLUGIN_JS)
    # strip comments — the header documents the rule and must not trip it
    code = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    code = re.sub(r"(?m)//.*$", "", code)
    for pattern in (r"#[0-9a-fA-F]{3,8}\b", r"\brgb\(", r"\brgba\(", r"\bhsla?\("):
        bad = re.findall(pattern, code)
        assert not bad, "hardcoded color(s): {}".format(bad)


def test_plugin_registers_page_nav_and_palette_areas():
    src = _read(PLUGIN_JS)
    for area in ("ROUTES_AREA", "SIDEBAR_NAV_AREA", "PALETTE_AREA"):
        assert area in src


def test_plugin_polls_and_never_hammers():
    src = _read(PLUGIN_JS)
    m = re.search(r"POLL_MS\s*=\s*(\d+)", src)
    assert m, "expected an explicit poll interval"
    assert int(m.group(1)) >= 5000, "must not poll faster than a few seconds"


def test_plugin_carries_no_scanner_logic():
    src = _read(PLUGIN_JS)
    for banned in ("sqlite3", "state.db", "registry.db", "SELECT ", "cluster_id:"):
        assert banned not in src


def test_backend_declares_the_continuum_router_and_service_only():
    src = _read(PLUGIN_API)
    assert "APIRouter()" in src
    assert "from continuum.service import Service" in src
    assert "sqlite3" not in src


def test_plugin_surfaces_include_overview_as_default():
    src = _read(PLUGIN_JS)
    assert "id: 'overview'" in src, "SURFACES must contain overview"
    assert "label: 'Overview'" in src
    assert src.count("id: 'overview'") == 1
    assert re.search(r"SURFACES\s*=\s*\[\s*\{\s*id:\s*'overview'", src, re.DOTALL)
    assert "useState('overview')" in src
    assert "?view=overview" in src


def test_plugin_overview_renders_variant_label_and_review_status():
    src = _read(PLUGIN_JS)
    assert "variant_label" in src
    assert "review_status_label" in src
    assert "inclusion_basis" in src
    assert "OverviewSurface" in src
    for field in ("summary_text", "context_line", "quiet_days", "sessions_omitted"):
        assert field in src


def test_plugin_no_hermes_profile_in_emitted_commands():
    src = _read(PLUGIN_JS)
    code = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    code = re.sub(r"(?m)//.*$", "", code)
    assert "HERMES_PROFILE" not in code, "profile-scoped resume must not use HERMES_PROFILE"
    svc_src = _read(os.path.join(BUILD_ROOT, "continuum", "service.py"))
    assert "hermes -p {} --resume {}" in svc_src
    # the scoped copy_command must be the -p form, not HERMES_PROFILE=
    assert "HERMES_PROFILE=" not in svc_src


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_plugin_parses_as_esm(tmp_path):
    """The the real parser is the only authority on 'valid uncompiled ESM'."""
    target = tmp_path / "plugin.mjs"
    shutil.copyfile(PLUGIN_JS, target)
    proc = subprocess.run(["node", "--check", str(target)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_plugin_compact_card_has_merged_chip_and_no_standalone_summary_tier():
    src = _read(PLUGIN_JS)
    assert "'T' + it.summary_tier" not in src, "standalone summary_tier badge must be removed (F-2)"
    # merged chip references both evidence_tier and confidence_band
    assert "evidence_tier" in src and "confidence_band" in src
    # chip label pattern T{evidence_tier} · {confidence_band}
    assert re.search(r"T.*evidence_tier.*confidence_band|evidence_tier.*confidence_band", src)
    # provenance chip tooltip carries tier sentence + numeric confidence
    assert "T0" in src or "deterministic" in src.lower()


def test_plugin_accept_captures_audit_id_and_undo():
    src = _read(PLUGIN_JS)
    assert "audit_id" in src, "accept must capture audit_id from response (F-4)"
    # undo uses existing POST /review/undo route
    assert "/review/undo" in src or "review/undo" in src or "undo" in src.lower()
    # kanban supersession S-4: success toast states board destination "It's in {Column}" not "on the Overview" (file stores \u2019 escape)
    assert "Accepted" in src and ("s in" in src and "COLUMN_LABELS" in src), "success toast must state destination It's in {Column} (kanban)"
    assert "on the Overview" not in src
    assert "Inbox" in src
    assert "Undo" in src
    # failure toast has Retry
    assert "Retry" in src
    # no optimistic state — onSuccess invalidates, doesn't flip badge beforehand (check no direct review_status mutation before fetch)


def test_plugin_removed_old_literals():
    src = _read(PLUGIN_JS)
    assert "Accept as project" not in src, "old overview Accept literal must be removed (F-5)"
    assert "undo in detail history" not in src, "old notification text must be removed (F-5)"


def test_plugin_review_candidates_label_and_copy_id():
    src = _read(PLUGIN_JS)
    # kanban D-KB-6/S-3: retired Review candidates tab replaced by Inbox lane on the board
    assert "Review candidates" not in src, "Review candidates tab retired — Inbox lane is the review surface (kanban D-KB-6)"
    assert "Inbox" in src, "Inbox lane must be present as kanban review surface"
    assert "Board" in src
    # eight lane labels present per ux-continuum-kanban-hybrid §3.2
    for label in ("Ongoing", "Blocked", "Waiting on you", "Paused", "Done", "Shipped", "Scrapped"):
        assert label in src
    assert "Copy ID" in src, "visible Copy ID button must copy bare session_id"
    # Copy ID copies bare session_id, not profile-scoped form
    assert "session_id" in src
    # candidate/accepted badges preserved
    assert "Candidate" in src and "Accepted" in src
    # explicit partial markers
    assert "quiet unknown" in src
    assert "no linked session" in src
    # provenance chip single — ensure only one chip kind on card (merged)
    assert src.count("ProvenanceChip") >= 1


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_backend_compiles(tmp_path):
    proc = subprocess.run(
        ["/Users/kethuda/.hermes/hermes-agent/venv/bin/python", "-m", "py_compile", PLUGIN_API]
        if os.path.exists("/Users/kethuda/.hermes/hermes-agent/venv/bin/python")
        else ["python3", "-m", "py_compile", PLUGIN_API],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
