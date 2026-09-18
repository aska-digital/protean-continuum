# Continuum — static snapshot export (PIN-gated)

This directory is the complete file set served as the project's GitHub Pages
site. It is a **static export**: no server, no API, no database — every read the
board performs hits a sibling `snapshot.*.json` file baked at build time.

## What you are looking at

- **Synthetic data only.** Every profile, project, message excerpt, and action-log
  row is generated fixture data produced by `server/fixtures/` (architecture D0/D2).
  Nothing here reflects real work.
- **Access gate.** `index.html` loads exactly one script, `pin-check.js`, which
  prompts for an access code, checks its SHA-256 against a committed hash constant,
  and only then appends `app.js`. See `PIN-GATE-SPEC.md`. The gate is honest
  friction over public synthetic data, not confidentiality.
- **Read-only.** The snapshot has no write surface: scan/review/undo/archive
  controls are not rendered and any mutation path short-circuits to a read-only
  notice (architecture D5). The 15-second liveness poll is disabled (G8);
  `snapshot.events.json` is a static marker stub.
- **Strict CSP.** `index.html` ships a same-origin-only Content-Security-Policy
  meta tag (D6); the page has zero external dependencies.

## Layout

Flat, all-relative (D10): `index.html`, `app.js`, `styles.css`,
`desktop/kanban-interaction.js`, the `snapshot.*.json` data files (board,
action log, attention, candidates, staleness, events, per-project detail and
pre-baked session panes), `snapshot.manifest.json` (build audit: sizes + SHA-256
per snapshot file), `manifest.sha256` (SHA-256 of every file in this tree except
itself), and the three gate/documentation files named above.

## Reproducing the build

From a checkout of the project's default branch:

```sh
python3 tools/build_static_snapshot.py --out static-export --now <epoch>
```

The script imports the repo's own fixture builder, runs the Continuum service
in-process over that throw-away fixture home with the clock pinned to `--now`,
and writes this exact tree to `static-export/pages-root/`. Two runs with the same
`--now` are byte-identical (`diff -r`). Python stdlib + PyYAML only. Publishing
this tree to Pages is a separate, gated step owned by the build/ops lane.
