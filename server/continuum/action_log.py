"""Orda action-log — evidence collection, projection, sync engine, archive (AL-L1..AL-L16).

The action log is a PROJECTION of evidence sources (AL-L1): INFLIGHT claim rows,
DISPATCH-LEDGER dispatch rows, and delegation receipts. It stores its own audited JSON ledger
plus an append-only JSONL event sidecar (AL-L2) and never writes to the registry, TASK-HOME.md,
or any source database (AL-L12).

Pure module: injected clock/paths, no HTTP, no network, no registry handle.

Writes (exhaustive, AL §4):
  data/action_log.json          ledger   (tmp + os.replace)
  data/action_log_events.jsonl  events   (append-only)
  data/action_log.lock          lock     (exclusive advisory flock; never written to)

Exit codes (AL-L8): 0 changed, 1 no changes, 3 LOCKED, 4 SOURCE_MISSING, 5 PARSE_DEGRADED.
"""
from __future__ import annotations

import calendar
import fcntl
import glob as globmod
import hashlib
import json
import os
import re
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------- locked vocabulary

#: AL-L3/§2. Exactly six kinds, six target types, three evidence types, five sources, two states.
KINDS = ("dispatch", "direct-edit", "post", "merge", "close", "retraction")
TARGET_TYPES = ("delegation", "file", "url", "commit", "pr", "task-home")
EVIDENCE_TYPES = ("receipt-path", "post-url", "commit-sha")
OPERATOR_FLAGS = ("operator", "agent")
SOURCES = ("inflight", "dispatch-ledger", "receipt", "task-home", "direct-log")
STATUSES = ("active", "archived")

#: §2 field caps.
TARGET_MAX = 512
EVIDENCE_MAX = 1024
ACTION_ID_MAX = 64

#: AL-L2 — schema 1; the registry's own SCHEMA_VERSION is untouched (stays 3).
LEDGER_SCHEMA = 1

#: §5/AL-L7 — the additive playground view.
VIEW = "action_log"

#: §2 ledger shape — the two source hashes the body carries.
SOURCE_HASH_KEYS = ("inflight_md5", "dispatch_ledger_md5")

#: AL-L8/§5 exit codes.
EXIT_OK = 0
EXIT_NO_CHANGES = 1
EXIT_LOCKED = 3
EXIT_SOURCE_MISSING = 4
EXIT_PARSE_DEGRADED = 5

#: AL-L13 query vocabulary for ``view=action_log``.
QUERY_SORT_FIELDS = ("timestamp", "kind", "target", "operator_flag")
PAGE_SIZE_DEFAULT = 50
PAGE_SIZE_MAX = 200

#: Orda directive 2026-09-17 — supersedes ONLY the default-content clause of AL-L14: the
#: default view of the action log is the EXTERNAL filter; ``scope=all`` is the full log.
#: Purely additive on top of AL-L13 (never replaces kind=/operator=/status=...).
SCOPES = ("external", "all")
DEFAULT_SCOPE = "external"

#: Ruling clause 1: kinds whose every row is external-facing by nature.
EXTERNAL_KINDS = ("post", "merge", "retraction")
#: Ruling clause 3: kinds that stay internal (visible under scope=all only).
INTERNAL_KINDS = ("dispatch", "close")
#: Ruling clause 2: the internal Hermes tree. Paths under it are NOT public artifacts.
#: Resolved from the home of the serving user; on this machine it is
#: ``/Users/kethuda/.hermes/`` exactly as the ruling states.
INTERNAL_PATH_ROOT = os.path.expanduser("~/.hermes/")

#: Warnings that mean attribution is incomplete -> exit 5 (the pass still runs, nothing dropped).
WARNING_DEGRADED = frozenset((
    "inflight_row_drift",          # a claim row does not carry the 11 documented columns
    "dispatch_row_drift",          # a dispatch row matches neither locked schema exactly
    "dispatch_row_malformed",      # a dispatch row has no usable id cell
    "receipt_kind_default",        # no OPEN-1 keyword matched; kind fell back to direct-edit
    "timestamp_missing",           # the source carried no usable time at all
    "action_id_truncated",         # a source id exceeded the 64-char cap
    "duplicate_source_row_id",     # two source rows claim the same id; the first is kept
    "decode_replaced",             # undecodable bytes were replaced, not dropped
))

#: Warnings that are reported, never fatal, and never a degraded exit.
WARNING_INFORMATIONAL = frozenset((
    "crlf_normalized",
    "ledger_prose_row",            # a pipe wrapped a prose note, not a row
    "inflight_prose_row",          # a pipe row outside the documented lease table
    "timestamp_mtime_fallback",    # §2's documented fallback: the source file mtime was used
    "no_claim_rows",               # INFLIGHT present but carries no claim row
    "no_dispatch_rows",            # DISPATCH-LEDGER present but carries no dispatch row
    "no_receipt_files",            # the receipts glob matched nothing
    "receipt_no_columns",          # a receipt has no ``:``-separated column
    "source_row_absent",           # a previously projected source row is no longer in the source
    "source_superseded",           # a dispatch row is superseded by an INFLIGHT claim
    "empty_source_placeholder",    # the documented "(empty state)" placeholder row
))

#: §3 source precedence note: INFLIGHT supersedes DISPATCH-LEDGER for the same action.
DEFAULT_SOURCES = (
    "~/.hermes/team-skills/ops/INFLIGHT.md",
    "~/.hermes/team-skills/ops/DISPATCH-LEDGER.md",
    "~/.hermes/profiles/*/cache/delegation/**/*-receipt.md",
)

RECEIPT_READ_CAP = 4 * 1024 * 1024      # a receipt larger than this is truncated for matching
RENDER_TRUNCATE_INFORMATIONAL = True

_ISO_RE = re.compile(r"(?P<date>\d{4}-\d{2}-\d{2})T(?P<time>\d{2}:\d{2}(?::\d{2})?)"
                     r"(?:\.\d+)?(?:Z|(?P<off>[+-]\d{2}:?\d{2}))?")
#: Source classification anchors — the file's own title line, never a passing mention.
_INFLIGHT_HEAD_RE = re.compile(r"^#\s*INFLIGHT\b", re.MULTILINE)
_LEDGER_HEAD_RE = re.compile(r"^#\s*DISPATCH-LEDGER\b", re.MULTILINE)
_SESSION_ID_RE = re.compile(r"(?<!\d)(?P<date>\d{8})_(?P<time>\d{6})(?!\d)")
_SEP_RE = re.compile(r"^:?-{2,}:?$")
#: A row's id cell is one whitespace-free token; a documentation label never is.
_ID_TOKEN_RE = re.compile(r"^[^\s/|]+$")
#: Banded row-shape guards: a documentation table row never carries this many cells.
INFLIGHT_MIN_CELLS = 6
DISPATCH_MIN_CELLS = 7
_URL_RE = re.compile(r"https?://[^\s\)\]\>,;\"'`]+")
_SHA40_RE = re.compile(r"(?<![0-9a-fA-F])(?P<sha>[0-9a-f]{40})(?![0-9a-fA-F])")
_SHA_SHORT_RE = re.compile(r"(?<![0-9a-fA-F])(?P<sha>[0-9a-f]{7,40})(?![0-9a-fA-F])")
_RECEIPT_PATH_RE = re.compile(r"(?:~|/)[^\s\)\]\>,;\"'`|]+\.(?:md|json|txt|log|py)")

#: OPEN-1 resolution — the keyword map of §3 plus two deterministic guards.
#:
#: (1) PRECEDENCE (most-specific terminal outcome first): ``retract`` and ``merge`` are terminal
#:     outcomes, then a post, then the uppercase status word ``BLOCKED``, then close, then the
#:     direct-edit fallback. ORDER NOTE: ``BLOCKED`` sits below post/merge because it is a status
#:     line present in many receipts and must not outrank an observed merge or post.
#: (2) NEGATION AND COMPOUND SKIPPING: a match preceded by ``no/not/never/without/n't`` or glued
#:     into a compound (``fail-closed``, ``MERGE-GATE``) is not evidence of the action. Without
#:     this, a receipt that says "no merge" would be projected as ``merge`` — a row that
#:     contradicts its own evidence source, which AL-L1 forbids outright.
_KIND_RULES: Tuple[Tuple[str, str], ...] = (
    ("retraction", r"retract(?:s|ed|ion|ions|ing)?"),
    ("merge", r"merg(?:e|es|ed|ing)"),
    ("post", r"(?:post|posts|posted|posting|comment|comments|commented|commenting)"),
    ("retraction", r"BLOCKED"),
    ("close", r"(?:close|closes|closed|closing|COMPLETE)"),
    ("direct-edit", r"(?:edit|edits|edited|editing|patch|patches|patched|write|writes|"
                    r"written|writing|direct-edit)"),
)

#: ``no merge`` / ``not a merge`` / ``never posted`` — a negated mention is not the action.
#: Deliberately TIGHT: only a negation immediately in front (optionally one short filler word)
#: counts, so "found no defects; merged PR #5" is still a merge.
_NEGATION_RE = re.compile(r"(?:(?:no|not|never|without|n't)\s+(?:[a-z]{1,4}\s+)?)$", re.IGNORECASE)
_NEGATION_WINDOW = 20
_COMPOUND_GUARD = r"(?<![\w-])(?:{})(?![\w-])"


def _kind_pattern(body: str) -> "re.Pattern[str]":
    return re.compile(_COMPOUND_GUARD.format(body), re.IGNORECASE)


#: Compiled once per kind, in §3 precedence order (dict insertion order).
_KIND_COMPILED: Dict[str, List[Any]] = {}
for _rule_kind, _rule_pattern in _KIND_RULES:
    _KIND_COMPILED.setdefault(_rule_kind, []).append(_kind_pattern(_rule_pattern))


def _kind_hits(body: str, kind: str) -> List[int]:
    """Offsets of non-negated, non-compound mentions of ``kind`` in ``body`` (in order)."""
    out: List[int] = []
    for pattern in _KIND_COMPILED.get(kind, ()):
        for match in pattern.finditer(body or ""):
            prefix = (body or "")[max(0, match.start() - _NEGATION_WINDOW):match.start()]
            if _NEGATION_RE.search(prefix):
                continue
            out.append(match.start())
    return sorted(out)


# --------------------------------------------------------------------------- errors

class SourceMissing(IOError):
    """A configured non-glob evidence source does not exist (AL-L8 exit 4)."""


class SyncLocked(RuntimeError):
    """Another pass holds the ledger lock (AL-L8 exit 3 / AL-I4)."""


# --------------------------------------------------------------------------- small helpers

def _now() -> float:
    return time.time()


def _norm_ws(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _clamp(text: str, limit: int) -> Tuple[str, bool]:
    text = str(text or "")
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def _is_separator(cells: Sequence[str]) -> bool:
    return bool(cells) and all(_SEP_RE.match(c or "") for c in cells)


def _is_header(cells: Sequence[str]) -> bool:
    if not cells:
        return False
    first = _norm_ws(cells[0]).lower()
    return first in ("claim id", "task id", "column", "---")


def _read_text(path: str) -> Tuple[str, List[str]]:
    """Read a source file; CRLF is normalised, undecodable bytes are replaced (never dropped)."""
    warnings: List[str] = []
    with open(path, "rb") as fh:
        raw = fh.read()
    if b"\r\n" in raw:
        raw = raw.replace(b"\r\n", b"\n")
        warnings.append("crlf_normalized")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", "replace")
        warnings.append("decode_replaced")
    return text, warnings


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def md5_file(path: str) -> Optional[str]:
    try:
        with open(path, "rb") as fh:
            return hashlib.md5(fh.read()).hexdigest()
    except OSError:
        return None


def iso_to_epoch(value: Any) -> Optional[float]:
    """Parse an ISO-8601 stamp to a Unix epoch (UTC). None when unusable."""
    if value is None:
        return None
    text = str(value).strip()
    for match in _ISO_RE.finditer(text):
        date, clock = match.group("date"), match.group("time")
        pattern = "%Y-%m-%dT%H:%M:%S" if clock.count(":") == 2 else "%Y-%m-%dT%H:%M"
        try:
            struct = time.strptime("{}T{}".format(date, clock), pattern)
        except ValueError:
            continue
        epoch = calendar.timegm(struct)
        off = match.group("off")
        if off:
            sign = 1 if off[0] == "+" else -1
            off = off[1:].replace(":", "")
            epoch -= sign * (int(off[:2]) * 3600 + int(off[2:4] or 0) * 60)
        return float(epoch)
    return None


def session_id_to_epoch(value: Any) -> Optional[float]:
    """``YYYYMMDD_HHMMSS_xxxxxx`` (a launch session id) -> epoch. None when unusable."""
    if value is None:
        return None
    match = _SESSION_ID_RE.search(str(value))
    if match is None:
        return None
    try:
        struct = time.strptime("{}{}".format(match.group("date"), match.group("time")),
                               "%Y%m%d%H%M%S")
    except ValueError:
        return None
    return float(calendar.timegm(struct))


def _mtime(path: str) -> Optional[float]:
    try:
        return float(os.stat(path).st_mtime)
    except OSError:
        return None


def evidence_of(text: str, fallback: str) -> Tuple[str, str]:
    """Best evidence triple for a source row: (evidence_type, evidence_link).

    A row's own richer evidence wins (post URL, commit SHA, receipt path); otherwise the source
    file and the line/row anchor is the evidence (AL-L1: every row links its evidence source).
    """
    url = _URL_RE.search(text or "")
    if url:
        link = url.group(0).rstrip(".")
        return "post-url", link[:EVIDENCE_MAX]
    sha = _SHA40_RE.search(text or "")
    if sha:
        return "commit-sha", sha.group("sha")
    path = _RECEIPT_PATH_RE.search(text or "")
    if path:
        return "receipt-path", path.group(0)[:EVIDENCE_MAX]
    return "receipt-path", fallback[:EVIDENCE_MAX]


def target_type_for(evidence_type: str, kind: str) -> str:
    if evidence_type == "post-url":
        return "url"
    if evidence_type == "commit-sha":
        return "commit" if kind != "merge" else "commit"
    return "delegation"


def action_id_for(kind: str, source: str, source_row_id: Optional[str],
                  source_path: Optional[str] = None) -> Tuple[str, List[str]]:
    """Deterministic action-id per AL-L4. Never random: a re-scan reproduces it byte-for-byte."""
    warnings: List[str] = []
    if source in ("inflight", "dispatch-ledger") and source_row_id:
        raw = "al-{}".format(source_row_id)
    elif source == "receipt":
        digest = hashlib.sha256("{}\n{}".format(source_path or "", kind).encode("utf-8"))
        raw = "al-{}".format(digest.hexdigest()[:16])
    else:
        raw = "al-{}".format(hashlib.sha256(
            "{}\n{}\n{}".format(source, source_row_id or "", kind).encode("utf-8")).hexdigest()[:16])
    if len(raw) > ACTION_ID_MAX:
        raw = raw[:ACTION_ID_MAX]
        warnings.append("action_id_truncated")
    return raw, warnings


# --------------------------------------------------------------------------- source parsing

def _operator_flag_for(text: str, source: str) -> str:
    """§3 operator-flag derivation. INFLIGHT: a direct-records-action row is operator-initiated."""
    if source == "inflight":
        if re.search(r"direct[- ]records[- ]action", text or "", re.IGNORECASE):
            return "operator"
        return "agent"
    if source == "dispatch-ledger":
        return "agent"
    if re.search(r"\b(operator|manual)\b", text or "", re.IGNORECASE):
        return "operator"
    return "agent"


def parse_inflight(text: str, path: str) -> Tuple[List[Dict[str, Any]], List[str], List[str]]:
    """One row per claim (§3). Drift is warned about and best-effort parsed — never dropped.

    The claim table is armed by its documented ``| claim id | ... |`` header row and stays armed
    to end-of-file (the live file breaks the table into blocks separated by blank lines, so a
    non-table line must NOT disarm it). Row SHAPE decides membership: a claim row carries at
    least ``INFLIGHT_MIN_CELLS`` cells and a whitespace-free id cell, so the two documentation
    tables above the claim table can never be mistaken for claims.
    """
    rows: List[Dict[str, Any]] = []
    warnings: List[str] = []
    seen: Dict[str, int] = {}
    armed = False
    for line_no, line in enumerate(text.split("\n"), 1):
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if _is_separator(cells):
            continue
        if _is_header(cells):
            if _norm_ws(cells[0]).lower() == "claim id":
                armed = True
            continue
        if not armed:
            warnings.append("inflight_prose_row")
            continue
        claim_id = next((cell for cell in cells if cell), "")
        if len(cells) < INFLIGHT_MIN_CELLS or not _ID_TOKEN_RE.match(claim_id):
            warnings.append("inflight_prose_row")
            continue
        if claim_id.startswith("(") and "empty state" in claim_id:
            warnings.append("empty_source_placeholder")
            continue
        row_warnings: List[str] = []
        if len(cells) != 11:
            row_warnings.append("inflight_row_drift")
        blob = stripped
        stamp = None
        for cell in cells:
            stamp = iso_to_epoch(cell)
            if stamp is not None:
                break
        if stamp is None:
            stamp = _mtime(path)
            row_warnings.append("timestamp_mtime_fallback" if stamp is not None
                                else "timestamp_missing")
        if claim_id in seen:
            row_warnings.append("duplicate_source_row_id")
            warnings.extend(row_warnings + ["duplicate_source_row_id:{}".format(claim_id)])
            continue
        seen[claim_id] = line_no
        kind = "dispatch"
        action_id, id_warnings = action_id_for(kind, "inflight", claim_id)
        row_warnings.extend(id_warnings)
        evidence_type, evidence_link = evidence_of(blob, "{}#L{}".format(path, line_no))
        rows.append({
            "source": "inflight", "source_row_id": claim_id, "kind": kind,
            "timestamp": stamp, "target": claim_id[:TARGET_MAX],
            "target_type": "delegation", "evidence_type": evidence_type,
            "evidence_link": evidence_link,
            "operator_flag": _operator_flag_for(blob, "inflight"),
            "action_id": action_id, "warnings": sorted(set(row_warnings)),
            "line_no": line_no,
        })
        warnings.extend(row_warnings)
    if not rows:
        warnings.append("no_claim_rows")
    return rows, warnings, sorted(seen)


def parse_dispatch_ledger(text: str, path: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    """One row per dispatch (§3): the historical 10-column schema and the current 15-column one.

    Armed by a ``task id`` header row and sticky to end-of-file (the file interleaves blank lines
    and prose between blocks). Row SHAPE decides membership: a dispatch row carries at least
    ``DISPATCH_MIN_CELLS`` cells and a whitespace-free id cell, so the two
    ``column | requirement`` documentation tables are never rows.
    """
    rows: List[Dict[str, Any]] = []
    warnings: List[str] = []
    seen: Dict[str, int] = {}
    armed = False
    for line_no, line in enumerate(text.split("\n"), 1):
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if _is_separator(cells):
            continue
        if _is_header(cells):
            if _norm_ws(cells[0]).lower() == "task id":
                armed = True
            continue
        if not armed:
            warnings.append("ledger_prose_row")
            continue
        task_id = next((cell for cell in cells if cell), "")
        if len(cells) < DISPATCH_MIN_CELLS or not _ID_TOKEN_RE.match(task_id):
            warnings.append("ledger_prose_row")
            continue
        row_warnings: List[str] = []
        era = "current" if len(cells) > 10 else "historical"
        if len(cells) not in (10, 15):
            row_warnings.append("dispatch_row_drift")
        blob = stripped
        stamp = None
        for cell in cells[:4]:
            stamp = iso_to_epoch(cell)
            if stamp is not None:
                break
        if stamp is None:
            for cell in cells:
                stamp = session_id_to_epoch(cell)
                if stamp is not None:
                    break
        if stamp is None:
            stamp = _mtime(path)
            row_warnings.append("timestamp_mtime_fallback" if stamp is not None
                                else "timestamp_missing")
        if task_id in seen:
            row_warnings.append("duplicate_source_row_id")
            warnings.extend(row_warnings + ["duplicate_source_row_id:{}".format(task_id)])
            continue
        seen[task_id] = line_no
        kind = "dispatch"
        action_id, id_warnings = action_id_for(kind, "dispatch-ledger", task_id)
        row_warnings.extend(id_warnings)
        evidence_type, evidence_link = evidence_of(blob, "{}#L{}".format(path, line_no))
        rows.append({
            "source": "dispatch-ledger", "source_row_id": task_id, "kind": kind,
            "timestamp": stamp, "target": task_id[:TARGET_MAX],
            "target_type": "delegation", "evidence_type": evidence_type,
            "evidence_link": evidence_link,
            "operator_flag": _operator_flag_for(blob, "dispatch-ledger"),
            "action_id": action_id, "warnings": sorted(set(row_warnings)),
            "era": era, "line_no": line_no,
        })
        warnings.extend(row_warnings)
    if not rows:
        warnings.append("no_dispatch_rows")
    return rows, warnings


def receipt_kind(text: str) -> Optional[str]:
    """OPEN-1 keyword mapping (§3). None when no non-negated keyword matched."""
    body = text or ""
    for kind in _KIND_COMPILED:
        if _kind_hits(body, kind):
            return kind
    return None


def receipt_kind_line(body: str, kind: str, limit: int = 4000) -> str:
    """The first line carrying a non-negated mention of ``kind`` (evidence enrichment)."""
    for candidate in body.split("\n")[:limit]:
        if _kind_hits(candidate, kind):
            return candidate
    return ""


def parse_receipt(path: str, text: str, mtime: Optional[float] = None) -> Tuple[Dict[str, Any], List[str]]:
    """One row per receipt (§3): kind from the OPEN-1 keyword map, evidence from the receipt."""
    warnings: List[str] = []
    body = text[:RECEIPT_READ_CAP]
    kind = receipt_kind(body)
    if kind is None:
        kind = "direct-edit"
        warnings.append("receipt_kind_default")
    if ":" not in body:
        warnings.append("receipt_no_columns")
    line = receipt_kind_line(body, kind)
    action_id, id_warnings = action_id_for(kind, "receipt", None, source_path=path)
    warnings.extend(id_warnings)
    evidence_type, evidence_link = evidence_of(line or body, path)
    stamp = mtime if mtime is not None else _mtime(path)
    if stamp is None:
        stamp = 0.0
        warnings.append("timestamp_unparsable")
    row = {
        "source": "receipt", "source_row_id": path, "kind": kind, "timestamp": float(stamp),
        "target": _receipt_target(kind, path, evidence_type, evidence_link)[0],
        "target_type": _receipt_target(kind, path, evidence_type, evidence_link)[1],
        "evidence_type": evidence_type, "evidence_link": evidence_link,
        "operator_flag": _operator_flag_for(body, "receipt"),
        "action_id": action_id, "warnings": sorted(set(warnings)), "path": path,
    }
    return row, warnings


def _receipt_target(kind: str, path: str, evidence_type: str,
                    evidence_link: str) -> Tuple[str, str]:
    """§2 target semantics: the action's own object, else the receipt file itself.

    ``post`` with a post URL targets that URL; ``merge`` with a commit SHA targets the commit;
    every other receipt targets the receipt file (``file``).
    """
    if kind == "post" and evidence_type == "post-url":
        return evidence_link, "url"
    if kind == "merge" and evidence_type == "commit-sha":
        return evidence_link, "commit"
    return path, "file"


def classify_source(path: str, head: str) -> str:
    """Classification is STRICT: only the two documented ops tables are tables.

    A receipt that merely mentions ``DISPATCH-LEDGER.md`` in its evidence table must stay a
    receipt — sniffing the whole head for the word misclassified 8 real receipts on the live
    tree and corrupted the source-hash slot. The file's own title line (or its exact name)
    decides; everything else is a receipt.
    """
    top = "\n".join((head or "").split("\n")[:8])
    base = os.path.basename(path)
    if _INFLIGHT_HEAD_RE.search(top) or base == "INFLIGHT.md":
        return "inflight"
    if _LEDGER_HEAD_RE.search(top) or base == "DISPATCH-LEDGER.md":
        return "dispatch-ledger"
    return "receipt"


# --------------------------------------------------------------------------- paths (§2/AL-L2)

def plugin_dir() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve(cfg: Any, key: str, default: str) -> str:
    value = str((cfg.get(key) if cfg is not None else None) or default)
    value = os.path.expanduser(value)
    if os.path.isabs(value):
        return value
    return os.path.join(plugin_dir(), value)


def ledger_path_for(cfg: Any = None) -> str:
    return _resolve(cfg, "action_log_ledger_path", "data/action_log.json")


def events_path_for(cfg: Any = None) -> str:
    return os.path.splitext(ledger_path_for(cfg))[0] + "_events.jsonl"


def lock_path_for(cfg: Any = None) -> str:
    return os.path.splitext(ledger_path_for(cfg))[0] + ".lock"


def source_paths_for(cfg: Any = None) -> List[str]:
    raw = cfg.get("action_log_source_paths") if cfg is not None else None
    values = raw if isinstance(raw, (list, tuple)) and raw else DEFAULT_SOURCES
    return [os.path.expanduser(str(v)) for v in values if str(v or "").strip()]


def stale_after_for(cfg: Any = None) -> int:
    return int((cfg.get("action_log_stale_after_seconds") if cfg is not None else None) or 900)


def enabled_for(cfg: Any = None) -> bool:
    return bool(cfg.get("action_log_enabled")) if cfg is not None else False


# --------------------------------------------------------------------------- ledger / events

def load_ledger(path: str) -> Optional[Dict[str, Any]]:
    """Read the ledger. Write-free, lock-free: a GET path may call this (AL-L8)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != LEDGER_SCHEMA:
        return None
    data.setdefault("items", [])
    data.setdefault("last_source_hashes", {})
    data.setdefault("last_sync_at", None)
    return data


def write_ledger(path: str, ledger: Dict[str, Any]) -> None:
    """Atomic write: tmp + ``os.replace`` (AL-L2)."""
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = "{}.tmp.{}".format(path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(ledger, fh, indent=2, ensure_ascii=False, sort_keys=False)
        fh.write("\n")
    os.replace(tmp, path)


def read_events(path: str) -> List[Dict[str, Any]]:
    """Append-only sidecar read. A missing file is an empty log, never an error."""
    events: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    return events


def append_event(path: str, event: Dict[str, Any]) -> None:
    """Append one JSON object as one line (AL-I3: monotone, never rewritten)."""
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False, sort_keys=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


class _LedgerLock:
    """Exclusive advisory lock on the ledger lock file (AL-I4). Never writes to it."""

    def __init__(self, path: str):
        self.path = path
        self._fh = None

    def __enter__(self) -> "_LedgerLock":
        parent = os.path.dirname(os.path.abspath(self.path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._fh = open(self.path, "a+")
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._fh.close()
            self._fh = None
            raise SyncLocked(self.path)
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._fh is not None:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            self._fh.close()
            self._fh = None


# --------------------------------------------------------------------------- rows

ROW_FIELDS = ("action_id", "timestamp", "kind", "target", "target_type", "evidence_type",
              "evidence_link", "operator_flag", "source", "source_row_id", "status",
              "archived_at", "archive_audit_id")

#: Fields a re-sync may write. The archive lifecycle is owned by archive/unarchive only.
CONTENT_FIELDS = ("timestamp", "kind", "target", "target_type", "evidence_type", "evidence_link",
                  "operator_flag", "source", "source_row_id")

ARCHIVE_FIELDS = ("status", "archived_at", "archive_audit_id")


def normalize_row(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Project a parsed source row into the locked 13-field row shape (§2)."""
    kind = raw.get("kind") if raw.get("kind") in KINDS else "direct-edit"
    evidence_type = raw.get("evidence_type") if raw.get("evidence_type") in EVIDENCE_TYPES \
        else "receipt-path"
    target_type = raw.get("target_type") if raw.get("target_type") in TARGET_TYPES \
        else target_type_for(evidence_type, kind)
    source = raw.get("source") if raw.get("source") in SOURCES else "direct-log"
    operator_flag = "operator" if raw.get("operator_flag") == "operator" else "agent"
    target, target_cut = _clamp(raw.get("target") or "", TARGET_MAX)
    link, link_cut = _clamp(raw.get("evidence_link") or "", EVIDENCE_MAX)
    if not link:
        link = "unknown"
    return {
        "action_id": str(raw.get("action_id") or ""),
        "timestamp": float(raw.get("timestamp") or 0.0),
        "kind": kind,
        "target": target,
        "target_type": target_type,
        "evidence_type": evidence_type,
        "evidence_link": link,
        "operator_flag": operator_flag,
        "source": source,
        "source_row_id": raw.get("source_row_id"),
        "status": "active",
        "archived_at": None,
        "archive_audit_id": None,
        "_warnings": sorted(set(list(raw.get("warnings") or [])
                                + (["target_truncated"] if target_cut else [])
                                + (["evidence_truncated"] if link_cut else []))),
    }


def row_content_hash(row: Dict[str, Any]) -> str:
    payload = {field: row.get(field) for field in CONTENT_FIELDS}
    return sha256_text(json.dumps(payload, sort_keys=True, ensure_ascii=False))


def ledger_bytes(ledger: Dict[str, Any]) -> str:
    return json.dumps(ledger, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def ledger_sha256(path: str) -> Optional[str]:
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None


def counts_by(items: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    by_kind = {kind: 0 for kind in KINDS}
    by_operator = {"operator": 0, "agent": 0}
    by_status = {state: 0 for state in STATUSES}
    for row in items:
        by_kind[row.get("kind")] = by_kind.get(row.get("kind"), 0) + 1
        by_operator[row.get("operator_flag")] = by_operator.get(row.get("operator_flag"), 0) + 1
        by_status[row.get("status")] = by_status.get(row.get("status"), 0) + 1
    return {"by_kind": by_kind, "by_operator": by_operator, "by_status": by_status}


# --------------------------------------------------------------------------- collect

def collect(cfg: Any = None) -> Dict[str, Any]:
    """Read every configured evidence source (READ-ONLY) and project it into source rows."""
    paths = source_paths_for(cfg)
    rows: List[Dict[str, Any]] = []
    warnings: List[str] = []
    sources: List[Dict[str, Any]] = []
    hashes: Dict[str, Optional[str]] = {key: None for key in SOURCE_HASH_KEYS}
    missing: List[str] = []

    for spec in paths:
        matches = sorted(globmod.glob(spec, recursive=True)) if globmod.has_magic(spec) else [spec]
        if not matches:
            if globmod.has_magic(spec):
                warnings.append("no_receipt_files")
            else:
                missing.append(spec)
            sources.append({"spec": spec, "kind": "missing" if not globmod.has_magic(spec)
                            else "empty-glob", "files": 0, "rows": 0})
            continue
        for path in matches:
            if not os.path.isfile(path):
                if globmod.has_magic(spec):
                    continue
                missing.append(path)
                continue
            text, read_warnings = _read_text(path)
            warnings.extend(read_warnings)
            kind = classify_source(path, text)
            before = len(rows)
            if kind == "inflight":
                parsed, row_warnings, _ids = parse_inflight(text, path)
                hashes["inflight_md5"] = md5_file(path)
            elif kind == "dispatch-ledger":
                parsed, row_warnings = parse_dispatch_ledger(text, path)
                hashes["dispatch_ledger_md5"] = md5_file(path)
            else:
                row, row_warnings = parse_receipt(path, text, mtime=_mtime(path))
                parsed = [row]
            warnings.extend(row_warnings)
            for raw in parsed:
                raw.setdefault("source_path", path)
                rows.append(raw)
            sources.append({"spec": spec, "kind": kind, "path": path,
                            "rows": len(rows) - before})

    if missing:
        raise SourceMissing(", ".join(missing))

    # §3 source precedence: an INFLIGHT claim supersedes a dispatch row carrying the same id.
    claim_ids = {r["source_row_id"] for r in rows if r["source"] == "inflight"}
    kept: List[Dict[str, Any]] = []
    for raw in rows:
        if raw["source"] == "dispatch-ledger" and raw["source_row_id"] in claim_ids:
            warnings.append("source_superseded:{}".format(raw["source_row_id"]))
            continue
        kept.append(normalize_row(raw))

    kept.sort(key=lambda r: r["action_id"])
    return {"rows": kept, "warnings": warnings, "sources": sources, "source_hashes": hashes}


# --------------------------------------------------------------------------- sync

def _event(action: str, actor: str, target_id: str, before: Optional[Dict[str, Any]],
           after: Optional[Dict[str, Any]], rev: int, ts: float,
           event_id: str) -> Dict[str, Any]:
    """One audit event matching the ReviewEvent schema (§2).

    ``event_id`` is passed in, never generated here: the caller returns it as ``audit_id`` and
    stores it in the row's ``archive_audit_id``, so a locally generated id would leave the row
    pointing at an event that does not exist (undo by audit_id would then be impossible).
    """
    return {
        "event_id": event_id,
        "ts": ts,
        "actor": actor,
        "action": action,
        "target_id": target_id,
        "before_json": json.dumps(before, sort_keys=True, ensure_ascii=False) if before is not None else None,
        "after_json": json.dumps(after, sort_keys=True, ensure_ascii=False) if after is not None else None,
        "rev": rev,
    }


def _public_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {field: row.get(field) for field in ROW_FIELDS}


def sync(cfg: Any = None, *, dry_run: bool = False, now: Optional[float] = None,
         use_lock: bool = True, run_id: Optional[str] = None) -> Dict[str, Any]:
    """One explicit, idempotent, content-gated sync pass (AL-L8/AL-L9).

    Never raises for content: LOCKED and SOURCE_MISSING raise for the CLI to map onto exit
    codes 3/4; everything else lands in the report (exit 0/1/5).
    """
    now = _now() if now is None else float(now)
    run_id = run_id or uuid.uuid4().hex
    ledger_path = ledger_path_for(cfg)
    events_path = events_path_for(cfg)
    lock_path = lock_path_for(cfg)

    lock = _LedgerLock(lock_path) if use_lock else None
    if lock is not None:
        lock.__enter__()
    try:
        collected = collect(cfg)
        rows = collected["rows"]
        previous = load_ledger(ledger_path)
        prev_items = {row["action_id"]: row for row in (previous or {}).get("items", [])}
        prev_hashes = dict((previous or {}).get("last_source_hashes") or {})
        before_sha = ledger_sha256(ledger_path)

        items: List[Dict[str, Any]] = []
        added = changed = unchanged = 0
        archived_preserved = 0
        warnings = list(collected["warnings"])
        for row in rows:
            prior = prev_items.get(row["action_id"])
            out = dict(row)
            if prior is None:
                added += 1
            else:
                if row_content_hash(prior) != row_content_hash(row):
                    changed += 1
                else:
                    unchanged += 1
                # The archive lifecycle survives a re-sync: only archive/unarchive own it.
                for field in ARCHIVE_FIELDS:
                    out[field] = prior.get(field)
                if out.get("status") == "archived":
                    archived_preserved += 1
            items.append(out)

        absent = sorted(set(prev_items) - {row["action_id"] for row in items})
        for action_id in absent:
            # OPEN-3 (keep all, no pruning): a row whose source left the tree is KEPT with its
            # archive state and audit id — dropping it would destroy the audit trail.
            items.append(dict(prev_items[action_id]))
            warnings.append("source_row_absent:{}".format(action_id))
        items.sort(key=lambda r: r["action_id"])

        source_hashes = collected["source_hashes"]
        hashes_moved = any(source_hashes.get(key) != prev_hashes.get(key)
                           for key in SOURCE_HASH_KEYS)
        content_moved = bool(added or changed or absent)
        will_write = bool(content_moved or hashes_moved)

        ledger_out = {
            "schema": LEDGER_SCHEMA,
            "last_sync_at": now if will_write else (previous or {}).get("last_sync_at"),
            "last_source_hashes": source_hashes,
            "items": [{field: row.get(field) for field in ROW_FIELDS} for row in items],
        }
        if will_write and not dry_run:
            # AL-L9: the body carries no wall-clock value on an unchanged pass — an unchanged
            # pass writes nothing at all, so the ledger hash stays byte-identical.
            write_ledger(ledger_path, ledger_out)

        degraded = sorted({w for w in warnings if w.split(":")[0] in WARNING_DEGRADED})
        informational = sorted({w for w in warnings if w.split(":")[0] in WARNING_INFORMATIONAL})
        unknown = sorted({w for w in warnings
                          if w.split(":")[0] not in WARNING_DEGRADED
                          and w.split(":")[0] not in WARNING_INFORMATIONAL})
        if degraded:
            exit_code = EXIT_PARSE_DEGRADED
        elif will_write:
            exit_code = EXIT_OK
        else:
            exit_code = EXIT_NO_CHANGES
        after_sha = ledger_sha256(ledger_path)
        counts = counts_by(items)
        return {
            "ok": True, "exit_code": exit_code, "run_id": run_id, "dry_run": bool(dry_run),
            "ledger_path": ledger_path, "events_path": events_path, "lock_path": lock_path,
            "written": bool(will_write and not dry_run),
            "changed": changed, "added": added, "unchanged": unchanged, "absent": len(absent),
            "archived_preserved": archived_preserved,
            "row_count": len(items), "source_count": len(collected["sources"]),
            "sources": collected["sources"], "source_hashes": source_hashes,
            "source_hashes_moved": hashes_moved,
            "ledger_sha256_before": before_sha, "ledger_sha256_after": after_sha,
            "counts": {"total": len(items),
                       "active": counts["by_status"].get("active", 0),
                       "archived": counts["by_status"].get("archived", 0),
                       "by_kind": counts["by_kind"], "by_operator": counts["by_operator"]},
            "warnings": sorted(set(warnings)), "degraded": degraded,
            "informational": informational, "unclassified": unknown,
            "row_warnings": {row["action_id"]: row.get("_warnings") for row in rows
                             if row.get("_warnings")},
            "items": [{field: row.get(field) for field in ROW_FIELDS} for row in items],
        }
    finally:
        if lock is not None:
            lock.__exit__(None, None, None)


# --------------------------------------------------------------------------- read model (§5)

def _target_is_public(target: Any) -> bool:
    """Ruling clause 2: a URL target, or a path OUTSIDE the internal Hermes tree."""
    text = str(target or "")
    if not text:
        return False
    if text.startswith(("http://", "https://")):
        return True
    return not text.startswith(INTERNAL_PATH_ROOT)


def is_external_action(row: Dict[str, Any]) -> bool:
    """Orda ruling 2026-09-17 (single named audit point, per ruling clause 4): does this
    action touch an external-facing artifact needing hindsight review?

    - Clause 1: ``kind`` in EXTERNAL_KINDS (post, merge, retraction) -> always external.
    - Clause 2: ``direct-edit`` -> external only on public evidence: an http(s) target, a
      path outside ``INTERNAL_PATH_ROOT``, or ``evidence_type == 'post-url'``.
    - Clause 3: ``dispatch`` / ``close`` -> internal (still fully visible under scope=all).
    - Clause 4 fail-safe: any FUTURE/UNKNOWN kind defaults to internal unless its own
      evidence says public (same public-evidence test as direct-edit).
    """
    kind = row.get("kind")
    if kind in EXTERNAL_KINDS:
        return True
    if kind in INTERNAL_KINDS:
        return False
    # direct-edit rows and unknown kinds (fail-safe): the evidence decides.
    return row.get("evidence_type") == "post-url" or _target_is_public(row.get("target"))


def _number(value: Any, label: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError("invalid {}: {!r}".format(label, value))


def parse_query(provided: Dict[str, Any], *, page_size_default: int = PAGE_SIZE_DEFAULT,
                page_size_max: int = PAGE_SIZE_MAX) -> Dict[str, Any]:
    """AL-L13 vocabulary. ValueError -> HTTP 400 on the existing playground path."""
    def multi(key: str) -> List[str]:
        raw = provided.get(key)
        if raw is None:
            return []
        values = raw if isinstance(raw, (list, tuple)) else [raw]
        out: List[str] = []
        for value in values:
            for part in str(value).split(","):
                part = part.strip()
                if part:
                    out.append(part)
        return out

    kinds = multi("kind")
    for value in kinds:
        if value not in KINDS:
            raise ValueError("invalid kind: {!r}; expected one of: {}".format(value, ", ".join(KINDS)))
    operators = multi("operator")
    for value in operators:
        if value not in OPERATOR_FLAGS:
            raise ValueError("invalid operator: {!r}; expected one of: {}".format(
                value, ", ".join(OPERATOR_FLAGS)))
    statuses = multi("status")
    for value in statuses:
        if value not in STATUSES:
            raise ValueError("invalid status: {!r}; expected one of: {}".format(
                value, ", ".join(STATUSES)))

    # Orda directive 2026-09-17: additive `scope=`. ABSENT means `external` (the new default
    # view); `all` is today's full log. Any other value is a ValueError -> HTTP 400 (AL-L13).
    raw_scope = provided.get("scope")
    if raw_scope in (None, ""):
        scope = DEFAULT_SCOPE
    else:
        scope = str(raw_scope)
        if scope not in SCOPES:
            raise ValueError("invalid scope: {!r}; expected one of: {}".format(
                scope, ", ".join(SCOPES)))

    date_from = date_to = None
    if provided.get("date_from") is not None and provided.get("date_from") != "":
        date_from = _number(provided["date_from"], "date_from")
    if provided.get("date_to") is not None and provided.get("date_to") != "":
        date_to = _number(provided["date_to"], "date_to")
    if date_from is not None and date_to is not None and date_from > date_to:
        raise ValueError("invalid date range: date_from {} is after date_to {}".format(
            date_from, date_to))

    sort = provided.get("sort")
    sort = "timestamp" if sort in (None, "") else str(sort)
    if sort not in QUERY_SORT_FIELDS:
        raise ValueError("invalid sort: {!r}; expected one of: {}".format(
            sort, ", ".join(QUERY_SORT_FIELDS)))

    if provided.get("page") not in (None, ""):
        try:
            page = int(provided["page"])
        except (TypeError, ValueError):
            raise ValueError("invalid page: {!r}".format(provided.get("page")))
    else:
        page = 1
    if page < 1:
        raise ValueError("invalid page: {!r}; must be >= 1".format(page))

    if provided.get("page_size") not in (None, ""):
        try:
            page_size = int(provided["page_size"])
        except (TypeError, ValueError):
            raise ValueError("invalid page_size: {!r}".format(provided.get("page_size")))
    else:
        page_size = int(page_size_default)
    if page_size < 1 or page_size > int(page_size_max):
        raise ValueError("invalid page_size: {!r}; must be 1..{}".format(page_size, page_size_max))

    return {"view": VIEW, "kinds": kinds, "operators": operators, "statuses": statuses,
            "scope": scope, "date_from": date_from, "date_to": date_to, "sort": sort,
            "page": page, "page_size": page_size}


def _matches(row: Dict[str, Any], q: Dict[str, Any]) -> bool:
    if q["kinds"] and row.get("kind") not in q["kinds"]:
        return False
    if q["operators"] and row.get("operator_flag") not in q["operators"]:
        return False
    if q["statuses"]:
        if row.get("status") not in q["statuses"]:
            return False
    elif row.get("status") != "active":      # AL-L14: the default view is active only
        return False
    # Orda directive 2026-09-17: the default SCOPE is external-only; a missing/absent scope
    # in a hand-built query dict means the same default (fail-safe toward the ruling).
    if q.get("scope", DEFAULT_SCOPE) == "external" and not is_external_action(row):
        return False
    stamp = float(row.get("timestamp") or 0.0)
    if q["date_from"] is not None and stamp < q["date_from"]:
        return False
    if q["date_to"] is not None and stamp > q["date_to"]:
        return False
    return True


def view(cfg: Any = None, query: Optional[Dict[str, Any]] = None,
         ledger_path: Optional[str] = None) -> Dict[str, Any]:
    """The §5 wire envelope for ``view=action_log``. Read-only: writes nothing, locks nothing."""
    q = query or parse_query({})
    path = ledger_path or ledger_path_for(cfg)
    ledger = load_ledger(path)
    items = list((ledger or {}).get("items") or [])
    counts = counts_by(items)
    ring = {"dispatch": 0, "direct-edit": 0, "post": 0, "merge": 0, "close": 0, "retraction": 0}
    ring.update(counts["by_kind"])
    filtered = [row for row in items if _matches(row, q)]
    # Orda directive 2026-09-17: additive scope reconciliation counts over the WHOLE ledger
    # (like every other counts key: ledger facts, not page facts). external_total counts the
    # ACTIVE external rows — exactly the size of the default view — so
    # external_total + internal_total == active always holds.
    external_total = sum(1 for row in items
                         if row.get("status") == "active" and is_external_action(row))
    active_total = counts["by_status"].get("active", 0)
    key = {"timestamp": lambda r: (-float(r.get("timestamp") or 0.0), str(r.get("action_id") or "")),
           "kind": lambda r: (str(r.get("kind") or ""), str(r.get("action_id") or "")),
           "target": lambda r: (str(r.get("target") or ""), str(r.get("action_id") or "")),
           "operator_flag": lambda r: (str(r.get("operator_flag") or ""),
                                       str(r.get("action_id") or ""))}[q["sort"]]
    filtered.sort(key=key)
    start = (q["page"] - 1) * q["page_size"]
    page_rows = filtered[start:start + q["page_size"]]
    return {
        "view": VIEW,
        "counts": {
            "total": len(items),
            "active": active_total,
            "archived": counts["by_status"].get("archived", 0),
            "by_kind": ring,
            "by_operator": counts["by_operator"],
            # ADDITIVE (Orda directive 2026-09-17): the effective scope, and the ledger-wide
            # active-row split QA reconciles with. Existing keys keep name, type and position.
            "scope": q.get("scope", DEFAULT_SCOPE),
            "external_total": external_total,
            "internal_total": active_total - external_total,
        },
        "actions": [{field: row.get(field) for field in ROW_FIELDS} for row in page_rows],
        "page": q["page"],
        "page_size": q["page_size"],
        "has_more": (start + len(page_rows)) < len(filtered),
    }


def show(cfg: Any = None, *, ledger_path: Optional[str] = None) -> Dict[str, Any]:
    """Read-only ledger view for ``action-log show``. Writes nothing, locks nothing."""
    path = ledger_path or ledger_path_for(cfg)
    events = events_path_for(cfg) if ledger_path is None else \
        os.path.splitext(path)[0] + "_events.jsonl"
    ledger = load_ledger(path)
    items = list((ledger or {}).get("items") or [])
    counts = counts_by(items)
    event_log = read_events(events)
    return {
        "ok": True, "ledger_path": path, "events_path": events,
        "lock_path": lock_path_for(cfg) if ledger_path is None
        else os.path.splitext(path)[0] + ".lock",
        "ledger_present": ledger is not None,
        "last_sync_at": (ledger or {}).get("last_sync_at"),
        "last_source_hashes": (ledger or {}).get("last_source_hashes") or {},
        "row_count": len(items),
        "counts": {"total": len(items),
                   "active": counts["by_status"].get("active", 0),
                   "archived": counts["by_status"].get("archived", 0),
                   "by_kind": counts["by_kind"], "by_operator": counts["by_operator"]},
        "items": [{field: row.get(field) for field in ROW_FIELDS} for row in items],
        "events": len(event_log),
        "event_log": event_log,
    }


# --------------------------------------------------------------------------- archive (AL-L5)

def _find_row(ledger: Dict[str, Any], action_id: str) -> Dict[str, Any]:
    for row in ledger.get("items") or []:
        if row.get("action_id") == action_id:
            return row
    raise ValueError("unknown action_id: {}".format(action_id))


def _next_rev(events_path: str) -> int:
    events = read_events(events_path)
    return int(max([int(e.get("rev") or 0) for e in events] + [0])) + 1


def _mutate(cfg: Any, action_id: str, actor: str, action: str,
            mutate_fn) -> Dict[str, Any]:
    """AL-L5: snapshot -> mutate -> audit append -> atomic ledger write, all under the lock."""
    ledger_path = ledger_path_for(cfg)
    events_path = events_path_for(cfg)
    lock_path = lock_path_for(cfg)
    action_id = str(action_id or "")
    if not action_id:
        raise ValueError("{} requires action_id".format(action))
    with _LedgerLock(lock_path):
        ledger = load_ledger(ledger_path)
        if ledger is None:
            raise ValueError("action-log ledger is absent: {}".format(ledger_path))
        row = _find_row(ledger, action_id)
        before = _public_row(row)
        if action == "archive_action_log" and row.get("status") == "archived":
            # AL-I2: a double archive is a no-op — no duplicate event, existing audit id.
            return {"ok": True, "audit_id": row.get("archive_audit_id"), "action": action,
                    "target_id": action_id, "noop": True,
                    "rev": _next_rev(events_path) - 1}
        if action == "unarchive_action_log" and row.get("status") != "archived":
            return {"ok": True, "audit_id": row.get("archive_audit_id"), "action": action,
                    "target_id": action_id, "noop": True,
                    "rev": _next_rev(events_path) - 1}
        event_id = uuid.uuid4().hex
        ts = _now()
        new_fields = mutate_fn(row, event_id, ts)
        row.update(new_fields)
        after = _public_row(row)
        rev = _next_rev(events_path)
        append_event(events_path, _event(action, actor, action_id, before, after, rev, ts,
                                        event_id))
        write_ledger(ledger_path, ledger)
        return {"ok": True, "audit_id": event_id, "action": action, "target_id": action_id,
                "noop": False, "rev": rev, "before": before, "after": after}


def archive(cfg: Any, action_id: str, actor: str = "local") -> Dict[str, Any]:
    """Archive one row (AL-A5): status/archived_at/archive_audit_id + an audit event."""
    return _mutate(cfg, action_id, actor, "archive_action_log",
                   lambda row, event_id, ts: {"status": "archived", "archived_at": ts,
                                              "archive_audit_id": event_id})


def unarchive(cfg: Any, action_id: str, actor: str = "local") -> Dict[str, Any]:
    """Unarchive one row (AL-A6): the three archive fields clear and the row is active again."""
    return _mutate(cfg, action_id, actor, "unarchive_action_log",
                   lambda row, event_id, ts: {"status": "active", "archived_at": None,
                                              "archive_audit_id": None})


def undo(cfg: Any, audit_id: str, actor: str = "local") -> Dict[str, Any]:
    """Undo an archive/unarchive event by restoring its ``before_json`` (AL-A7)."""
    if not audit_id:
        raise ValueError("undo requires audit_id")
    ledger_path = ledger_path_for(cfg)
    events_path = events_path_for(cfg)
    lock_path = lock_path_for(cfg)
    with _LedgerLock(lock_path):
        events = read_events(events_path)
        target = None
        for event in events:
            if event.get("event_id") == audit_id:
                target = event
                break
        if target is None:
            raise ValueError("unknown audit_id: {}".format(audit_id))
        ledger = load_ledger(ledger_path)
        if ledger is None:
            raise ValueError("action-log ledger is absent: {}".format(ledger_path))
        row = _find_row(ledger, str(target.get("target_id") or ""))
        before = _public_row(row)
        restored = json.loads(target.get("before_json") or "{}")
        if not isinstance(restored, dict) or not restored.get("action_id"):
            raise ValueError("audit event {} carries no restorable before_json".format(audit_id))
        row.update(restored)
        after = _public_row(row)
        ts = _now()
        event_id = uuid.uuid4().hex
        rev = _next_rev(events_path)
        append_event(events_path, _event("undo", actor, str(target.get("target_id") or ""),
                                        before, after, rev, ts, event_id))
        write_ledger(ledger_path, ledger)
        return {"ok": True, "audit_id": event_id, "undoes": audit_id,
                "action": "undo", "target_id": target.get("target_id"), "rev": rev,
                "restored": after}
