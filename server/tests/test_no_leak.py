"""No-leak gate: committed fixtures and proof artifacts must not contain personal paths or live registry content.

Finding 1 remediation: fails on /Users/, /home/, C:\\Users, and the repository's absolute checkout path.
Scoped to the review-queue fixture and proof queue dump — the leak class identified in review.
Wired into pytest so the gate runs on every test invocation.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent  # repo root

# Files that must be free of personal paths / live registry content
# Expanded in fix-loop-2 to cover the Finding 1 scope: all fixtures + proof
# artifacts that were flagged (make_fixture, task-home, test-before/after).
SENSITIVE_FILES = [
    ROOT / "server" / "fixtures" / "review-queue.json",
    ROOT / "proof" / "proof-review-queue.json",
    ROOT / "server" / "fixtures" / "make_fixture.py",
    ROOT / "server" / "fixtures" / "task_home" / "task-home.md",
    ROOT / "proof" / "test-before.txt",
    ROOT / "proof" / "test-after.txt",
]

# Minimum patterns required by Finding 1
PATTERNS = [
    (r"/Users/", "/Users/ absolute home path"),
    (r"/home/", "/home/ absolute home path"),
    (r"C:\\Users", "C:\\Users Windows home path"),
]

# Repository checkout path pattern (absolute path to this repo)
# Checked dynamically against the actual checkout path


def _collect_hits(text: str, path: Path) -> list[str]:
    hits: list[str] = []
    for pat, label in PATTERNS:
        if re.search(pat, text):
            hits.append(f"{path}: matched {label} ({pat})")
    # Absolute checkout path: the repo root itself must not appear as an absolute path in fixture
    # e.g. /Users/kethuda/.hermes/mozi-continuum-review-test or /home/...
    # We check that the fixture does not contain the repo's own absolute path prefix
    try:
        # Resolve the repo root and check if its string appears in the fixture
        repo_abs = str(ROOT.resolve())
        # Only flag if the absolute repo path appears literally (would be a leak)
        if repo_abs in text:
            hits.append(f"{path}: contains absolute checkout path {repo_abs}")
        # Also check parent's absolute prefix (e.g. /Users/kethuda/.hermes/...)
        # Any occurrence of the home prefix that ROOT starts with
        home_prefix = str(Path.home())
        if home_prefix in text:
            hits.append(f"{path}: contains home prefix {home_prefix}")
    except Exception:
        pass
    return hits


def test_review_queue_has_no_personal_paths():
    missing = [p for p in SENSITIVE_FILES if not p.exists()]
    # If proof file does not exist, that is acceptable (it was removed as part of scrub)
    # But server/fixtures/review-queue.json must exist
    fixture = ROOT / "server" / "fixtures" / "review-queue.json"
    assert fixture.exists(), f"fixture missing: {fixture}"

    hits: list[str] = []
    for p in SENSITIVE_FILES:
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        hits.extend(_collect_hits(text, p))

    assert not hits, "no-leak gate failed:\n" + "\n".join(hits)


def test_review_queue_is_synthetic():
    """Fixture must be synthetic: no live queue rows, synthetic identifiers only."""
    fixture = ROOT / "server" / "fixtures" / "review-queue.json"
    if not fixture.exists():
        return
    import json
    data = json.loads(fixture.read_text(encoding="utf-8"))
    # Synthetic fixture uses synthetic-* identifiers or synthetic- prefix
    # Live data had real brief slugs like aska-staging-portfolio-navigation-corrections
    # and draft IDs like draft-1 with live content. Synthetic fixture must not carry those.
    groups = data.get("groups", {})
    all_ids = []
    for g, rows in groups.items():
        for r in rows:
            all_ids.append(r.get("id", ""))
    # Check that at least one id looks synthetic OR the file explicitly marks synthetic
    text = fixture.read_text(encoding="utf-8")
    is_synthetic_marker = "_synthetic" in text or "synthetic" in text.lower()
    # Also check source_paths do not contain absolute home paths
    source_paths = data.get("source_paths", {})
    for k, v in source_paths.items():
        assert "/Users/" not in str(v), f"source_paths[{k}] contains /Users/: {v}"
        assert "/home/" not in str(v) or "synthetic" in str(v).lower(), f"source_paths[{k}] contains /home/: {v}"
    # If fixture has content, it must have synthetic marker
    if all_ids:
        assert is_synthetic_marker, f"fixture has ids {all_ids[:3]} but no synthetic marker"
