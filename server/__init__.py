"""Continuum plugin package root.

The unified Hermes plugin package layout is:

    $HERMES_HOME/plugins/continuum/
      plugin.yaml                 # this manifest ("api: dashboard/plugin_api.py")
      __init__.py                 # this file
      dashboard/plugin_api.py     # FastAPI half -> /api/plugins/continuum/*
      desktop/plugin.js           # M8 renderer half (opt-in; copied to desktop-plugins/)
      continuum/                  # M1-M7 Python package + C1 config
      config.yaml                 # C1 config surface
      data/registry.db            # M2 store (local, gitignored)

Nothing here writes to a Hermes source database.
"""

__version__ = "0.1.0"


def register(ctx) -> None:
    """Satisfy the directory-plugin load contract (plugins_loader.py:310-317).

    Continuum's Python surface is the dashboard backend (dashboard/plugin_api.py,
    mounted at /api/plugins/continuum/) and the desktop half (desktop/plugin.js).
    Neither surface is registered through PluginContext: the api file is imported
    directly by the web server by file path, and the desktop half by the
    renderer. This plugin subscribes to no lifecycle hooks, so there is nothing
    for register() to add.

    Hermes enables a plugin only when __init__.py exposes a module-level
    register(); without it the loader records "no register() function", leaves
    the plugin disabled, and `hermes plugins doctor` reports a registration
    registration failure. A no-op register() is therefore the correct and complete
    registration for this package.
    """
    return None
