"""C1 — continuum.config.

Loads the plugin's own config.yaml (C1) into a plain ``Config`` object. Pure data; holds no
secrets and performs no I/O beyond reading the one YAML file. Falls back to built-in defaults
when PyYAML or the file is unavailable, so the scanner is importable in any environment.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

PLUGIN_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = PLUGIN_DIR / "config.yaml"

_DEFAULTS: Dict[str, Any] = {
    "profiles_dir": "",
    "db_filename": "state.db",
    "db_default_profile": "~/.hermes/state.db",
    "include_default_db": False,
    "registry_path": "data/registry.db",
    "safety_lag_seconds": 5,
    "generic_paths": ["/Users/kethuda", "/Users/kethuda/", "/synthetic/home", "/synthetic/home/", ".", "./", "/", "/tmp", "/Users"],
    "workspace_patterns": [r"^(?P<repo>.*?/(?:wrk|working|work)/[^/]+)"],
    "workspace_git_walkup": True,
    "workspace_walkup_max_depth": 8,
    "stopword_tokens": [],
    "test_title_patterns": [],
    "eval_title_patterns": [],
    "recurring_title_pattern": r"·\s*\w{3}\s+\d{1,2}\s+\d{1,2}:\d{2}\s*$",
    "probe_head": 1,
    "probe_tail": 6,
    "excerpt_chars": 240,
    # --- audience (D-US-1..D-US-9): derived total audience classification ----------------
    # Approved dispatch-brief roots; C1 digest-matches a session's first user message
    # against every file under these roots. Read-only.
    "audience_brief_roots": [],
    "audience_brief_exts": [".md", ".txt"],
    "audience_brief_max_bytes": 2000000,
    # C2 envelope keys (>= 2 distinct keys => DELEGATED) and C5 role-declaration targets.
    "dispatch_keys": ["goal", "goals", "team6_agent", "from", "owner", "domain", "task_id",
                      "role", "ownership_matrix", "matrix_read_at_dispatch", "upstream_approval"],
    "dispatch_target_profiles": ["azaraki", "kodekoot", "halakukhan", "shayba",
                                 "sheikh-al-jabr", "lugia"],
    # Untruncated first-user-message cap (must hold a full brief: the EvoPet brief is 5 389 chars).
    "audience_probe_chars": 8192,
    "anchor_min_token_len": 5,
    "next_action_patterns": [],
    "blocker_patterns": [],
    "awaiting_user_patterns": [],
    "goal_patterns": [],
    "decision_patterns": [],
    "health_ack_content_patterns": [],
    "t_accept": 0.75,
    "t_review": 0.45,
    "t_noise": 0.20,
    "band_scores": {"high": 0.85, "medium": 0.60, "low": 0.35, "unknown": 0.15},
    "recent_days": 3,
    "stall_days": 7,
    "abandoned_days": 60,
    "ttl_next_action_days": 14,
    "ttl_lifecycle_days": 30,
    "ttl_drive_expected_days": 90,
    "model_enabled": False,
    "model_provider": "",
    "token_min_sessions": 2,
    "token_max_frequency": 0.02,
    "token_max_sessions": 40,
    "weak_token_clustering": False,
    # --- M4 Continuity Playground (data-only defaults; invalid values fail explicitly) -----
    # API default for an omitted ?view= is `all` (compat); the standalone browser
    # explicitly requests `today` (§5.1, §7.1).
    "board_default_view": "all",
    "board_default_sort_today": "attention",
    "board_default_sort_all": "quiet",
    "board_page_size_default": 100,
    "board_page_size_max": 200,
    # Bounded Today reference groups (§7.1). A cap is always reported as an omitted count.
    "playground_group_cap": 5,
    # Periodic full-sweep cadence; 0 disables age-forced sweeps (§8.1 step 6).
    "scan_full_sweep_seconds": 21600,
    # Bounded session-pane capture: rows exposed per block (presentation splits them further).
    "pane_recent_cap": 7,
    "pane_user_cap": 3,
    # D-SP-9: the UX display cap is separate from the architectural response cap above.
    "pane_display_cap": 5,
    # --- MC-L3/MC-S4: staleness band edges (days), anchored on `last_user_worked_on` ---------
    # Exactly three strictly ascending positive cut-points == the locked bands
    # 0-3d / 3-7d / 7-30d / 30d+. An invalid value fails config load loudly (never a default).
    "staleness_band_edges": [3, 7, 30],
    # --- TH-L7/TH-L13: TASK-HOME ↔ Continuum sync (non-secret data only) ----------------------
    # The feature switch only controls the READ projection (shipped default false per §7 — the
    # operator flips it to surface the panel). The sync itself is always an explicit CLI pass
    # (no daemon, no watcher, no write on any GET path). Paths are relative to the plugin dir
    # when not absolute. The source is READ-ONLY in both directions (TH-L1).
    "task_home_enabled": False,
    "task_home_source_path": "/Users/kethuda/.hermes/profiles/orda/TASK-HOME.md",
    "task_home_ledger_path": "data/task_home_sync.json",
    "task_home_export_dir": "review/out",
    "task_home_stale_after_seconds": 900,
    # --- AL-L10: Orda action-log (non-secret data only) ---------------------------------------
    # The switch controls the READ projection only. The log itself is a PROJECTION of evidence
    # sources (AL-L1) populated by an explicit CLI pass (`python -m continuum.cli action-log
    # sync`) — no daemon, no watcher, no write on any GET path. Paths are relative to the plugin
    # dir when not absolute; a glob entry is expanded recursively (receipts).
    "action_log_enabled": False,
    "action_log_source_paths": [
        "~/.hermes/team-skills/ops/INFLIGHT.md",
        "~/.hermes/team-skills/ops/DISPATCH-LEDGER.md",
        "~/.hermes/profiles/*/cache/delegation/**/*-receipt.md",
    ],
    "action_log_stale_after_seconds": 900,
}

# Alias used by the architecture contract's prose.
generic_path_list = "generic_paths"


@dataclass
class Config:
    """C1 config object. Field names mirror the YAML keys one-for-one."""

    hermes_home: str = os.path.expanduser("~/.hermes")
    bundle: Dict[str, Any] = field(default_factory=dict)
    source_path: Optional[str] = None

    # --- accessors used by the modules -------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        if key in self.bundle:
            return self.bundle[key]
        return _DEFAULTS.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def __getattr__(self, item: str) -> Any:  # attribute-style access
        if item.startswith("_"):
            raise AttributeError(item)
        if item == "bundle":
            raise AttributeError(item)
        if item in self.__dict__.get("bundle", {}):
            return self.bundle[item]
        if item in _DEFAULTS:
            return _DEFAULTS[item]
        raise AttributeError(item)

    # --- derived paths ------------------------------------------------------
    @property
    def profiles_root(self) -> str:
        explicit = self.get("profiles_dir") or ""
        if explicit:
            return os.path.expanduser(explicit)
        return os.path.join(os.path.expanduser(self.hermes_home), "profiles")

    def registry_path_resolved(self) -> str:
        p = self.get("registry_path") or "data/registry.db"
        if os.path.isabs(p):
            return p
        return str((PLUGIN_DIR / p).resolve())

    def is_generic_path(self, cwd: Optional[str]) -> bool:
        if not cwd:
            return True
        norm = cwd.rstrip("/") or "/"
        for g in self.get("generic_paths") or []:
            gn = (g or "").rstrip("/") or "/"
            if norm == gn:
                return True
        return False


def _read_yaml(path: Path) -> Dict[str, Any]:
    try:
        import yaml  # type: ignore
    except Exception:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


_INT_BOUNDS = {
    "board_page_size_default": (1, 200),
    "board_page_size_max": (1, 200),
    "playground_group_cap": (1, 100),
    "scan_full_sweep_seconds": (0, 30 * 86400),
    "pane_recent_cap": (0, 50),
    "pane_user_cap": (0, 50),
    "pane_display_cap": (0, 50),
    "task_home_stale_after_seconds": (1, 86400),
    "action_log_stale_after_seconds": (1, 86400),
}
_VALID_VIEWS = ("today", "all")
_TASK_HOME_PATH_KEYS = ("task_home_source_path", "task_home_ledger_path", "task_home_export_dir")
_ACTION_LOG_PATH_LIST_KEY = "action_log_source_paths"


def _validate_bundle(bundle: Dict[str, Any]) -> None:
    """Fail explicitly on an invalid M4 value (never silently substitute a default)."""
    for key, (low, high) in _INT_BOUNDS.items():
        if key not in bundle:
            continue
        try:
            value = int(bundle[key])
        except (TypeError, ValueError):
            raise ValueError("invalid config {}: {!r} is not an integer".format(
                key, bundle[key]))
        if value < low or value > high:
            raise ValueError("invalid config {}: {!r} outside {}-{}".format(
                key, bundle[key], low, high))
    if "board_default_view" in bundle and bundle["board_default_view"] not in _VALID_VIEWS:
        raise ValueError("invalid config board_default_view: {!r}; expected one of {}".format(
            bundle["board_default_view"], ", ".join(_VALID_VIEWS)))
    # MC-A17: the staleness band edges must be three strictly ascending positive day cut-points.
    # An invalid value fails at load time — it is never silently replaced by the locked default.
    if "staleness_band_edges" in bundle:
        raw = bundle["staleness_band_edges"]
        if not isinstance(raw, (list, tuple)) or len(raw) != 3:
            raise ValueError(
                "invalid config staleness_band_edges: {!r}; expected exactly three ascending "
                "day cut-points".format(raw))
        edges = []
        for value in raw:
            try:
                edges.append(float(value))
            except (TypeError, ValueError):
                raise ValueError(
                    "invalid config staleness_band_edges: {!r} contains a non-number "
                    "{!r}".format(raw, value))
        if edges[0] <= 0 or any(edges[i] >= edges[i + 1] for i in range(len(edges) - 1)):
            raise ValueError(
                "invalid config staleness_band_edges: {!r} must be three strictly ascending "
                "positive day cut-points".format(raw))
    # §8.3: the transport cap is fixed at 200. The configured default may never exceed it.
    if "board_page_size_default" in bundle and "board_page_size_max" in bundle:
        default = int(bundle["board_page_size_default"])
        maximum = int(bundle["board_page_size_max"])
        if default > maximum:
            raise ValueError(
                "invalid config board_page_size_default: {!r} exceeds board_page_size_max"
                " {!r}".format(bundle["board_page_size_default"],
                               bundle["board_page_size_max"]))
    # TH-L7: the TASK-HOME switch and its three paths fail loudly when malformed — an invalid
    # value is never silently replaced by the default (same style as the M4 checks above).
    if "task_home_enabled" in bundle and not isinstance(bundle["task_home_enabled"], bool):
        raise ValueError(
            "invalid config task_home_enabled: {!r} is not a boolean".format(
                bundle["task_home_enabled"]))
    for key in _TASK_HOME_PATH_KEYS:
        if key not in bundle:
            continue
        value = bundle[key]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                "invalid config {}: {!r} must be a non-empty path string".format(key, value))
    if bundle.get("task_home_enabled") and not str(bundle.get("task_home_source_path") or "").strip():
        raise ValueError(
            "invalid config: task_home_enabled is true but task_home_source_path is empty")
    # AL-L10: the action-log switch and its source list fail loudly when malformed — an invalid
    # value is never silently replaced by the default (same style as the checks above).
    if "action_log_enabled" in bundle and not isinstance(bundle["action_log_enabled"], bool):
        raise ValueError(
            "invalid config action_log_enabled: {!r} is not a boolean".format(
                bundle["action_log_enabled"]))
    if _ACTION_LOG_PATH_LIST_KEY in bundle:
        raw = bundle[_ACTION_LOG_PATH_LIST_KEY]
        if not isinstance(raw, (list, tuple)) or not raw:
            raise ValueError(
                "invalid config {}: {!r} must be a non-empty list of paths or globs".format(
                    _ACTION_LOG_PATH_LIST_KEY, raw))
        for value in raw:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    "invalid config {}: {!r} contains an empty path entry".format(
                        _ACTION_LOG_PATH_LIST_KEY, raw))
    if bundle.get("action_log_enabled") and not bundle.get(_ACTION_LOG_PATH_LIST_KEY):
        raise ValueError(
            "invalid config: action_log_enabled is true but action_log_source_paths is empty")


def load_config(hermes_home: Optional[str] = None,
                path: Optional[str] = None) -> Config:
    """Load C1. ``hermes_home`` scopes profile paths; ``path`` overrides the YAML location."""
    cfg_path = Path(path) if path else CONFIG_PATH
    bundle = dict(_DEFAULTS)
    if cfg_path.exists():
        bundle.update(_read_yaml(cfg_path))
    _validate_bundle(bundle)
    home = hermes_home or os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
    return Config(hermes_home=home, bundle=bundle, source_path=str(cfg_path))
