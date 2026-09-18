"""Continuum <-> Orda TASK-HOME sync — parser, keyer, mapper, sync engine, export renderer.

Implements the STABLE contract in
``profiles/proteus/cache/delegation/continuum-sync/leo-architecture.md`` (§2 locks TH-L1..TH-L16,
§4 keys, §5 mechanism/write boundary, §6 wire shape, §7 file boundary, §8 acceptance ids).

Properties this module is built to hold:
  * TASK-HOME.md is READ-ONLY in both directions (TH-L1). Nothing here opens it for write.
  * No HTTP, no network, no daemon, no thread, and no write on any read path (TH-L7). The sync
    is one explicit, idempotent, content-gated pass (TH-L8) invoked by the CLI.
  * The only writes are the reserved ``task_home_*`` declared rows (inside one
    ``registry.transaction()``), the ledger + lock under ``data/``, and the generated export
    under ``review/out/`` (§5 write boundary). ``project``/``project_session``/``evidence``/
    ``next_action``/``review_event``/``scan_run`` are never touched (TH-A12).
  * Every function that needs a clock, a path or a registry takes it as an argument, so a test
    can freeze all three and the export body stays free of wall-clock values (TH-I3).

Why the declared rows are written with raw SQL here instead of ``registry.set_declared``:
  1. TH-L5 requires ``source='task-home-sync'``; ``Registry.set_declared`` hardcodes
     ``source='declared'`` (registry.py:607-617).
  2. ``Registry.set_declared`` also bumps ``project.declared_rev`` (registry.py:618-619), which
     TH-A12 forbids ("pre/post sha256 of ``project`` ... unchanged").
  3. ``continuum/registry.py`` is not in §7's MAY WRITE list, so no new registry method was
     added; the pass writes through the registry connection it is given, inside
     ``registry.transaction()``, exactly as §5 prescribes.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------- constants (§3, §5)

#: §3 panel groups — six values, in the locked counts order. No lane is added (TH-L2).
PANEL_GROUPS = ("RUNNING", "AWAITING_OWNER", "OPEN_FOLLOW_UP", "CLOSED",
                "DASHBOARD_ONLY", "ABSENT")

#: §3 panel labels. Frida owns final copy (OPEN-1); these are the contract's own labels.
PANEL_GROUP_LABELS = {
    "RUNNING": "Running",
    "AWAITING_OWNER": "Awaiting owner",
    "OPEN_FOLLOW_UP": "Open follow-ups",
    "CLOSED": "Closed",
    "DASHBOARD_ONLY": "Dashboard only",
    "ABSENT": "Not in TASK-HOME",
}

#: §3 source sections, in source order: code -> (label, board lane, quiet).
SECTION_ORDER = ("RUNNING", "AWAITING_OWNER", "OPEN_FOLLOW_UP", "CLOSED")
SECTION_LABELS = {
    "RUNNING": "Running",
    "AWAITING_OWNER": "Awaiting owner",
    "OPEN_FOLLOW_UP": "Open follow-ups",
    "CLOSED": "Closed",
}
SECTION_LANES = {
    "RUNNING": "ongoing",
    "AWAITING_OWNER": "waiting_on_you",
    "OPEN_FOLLOW_UP": "paused",
    "CLOSED": "done",
}
SECTION_QUIET = {
    "RUNNING": False,
    "AWAITING_OWNER": False,
    "OPEN_FOLLOW_UP": True,
    "CLOSED": True,
}
ABSENT_SECTION_LABEL = PANEL_GROUP_LABELS["ABSENT"]

#: TH-A6 — a heading is matched on its leading words; a ``(date)``/parenthetical change orphans
#: nothing. The key is the text before the first ``(``, case-folded with ``-`` read as a space.
_SECTION_HEADING_KEYS = {
    "running": "RUNNING",
    "awaiting owner": "AWAITING_OWNER",
    "open follow ups": "OPEN_FOLLOW_UP",
    "closed": "CLOSED",
}

#: §5 write boundary — the reserved declared-field namespace, exhaustive.
RESERVED_FIELDS = (
    "task_home_key", "task_home_proc", "task_home_section", "task_home_section_label",
    "task_home_line", "task_home_line_no", "task_home_content_sha1", "task_home_receipt",
    "task_home_receipt_text", "task_home_binding", "task_home_source_sha256",
    "task_home_synced_at", "task_home_run_id",
)
#: The per-item identity fields. ``unbind`` clears exactly these (the pass provenance fields
#: stay so the row still says where it came from).
ITEM_FIELDS = (
    "task_home_key", "task_home_proc", "task_home_section", "task_home_section_label",
    "task_home_line", "task_home_line_no", "task_home_content_sha1", "task_home_receipt",
    "task_home_receipt_text", "task_home_binding",
)
#: Pass-provenance fields: written only when an item's identity row is (re)established
#: (added / section change / binding change). A content-only edit therefore moves exactly
#: ``task_home_line`` + ``task_home_content_sha1`` (TH-I2); the authoritative per-pass
#: metadata lives in the ledger (``last_run_id``/``last_synced_at``).
PROVENANCE_FIELDS = ("task_home_source_sha256", "task_home_synced_at", "task_home_run_id")

#: TH-L5: the import's own ``declared_field.source`` value.
SOURCE_KIND = "task-home-sync"
LEDGER_SCHEMA = 1

#: §5 exit codes.
EXIT_OK = 0
EXIT_LOCKED = 3
EXIT_SOURCE_MISSING = 4
EXIT_PARSE_DEGRADED = 5

#: Informational warnings — reported, never fatal, never a degraded exit.
WARNING_INFORMATIONAL = frozenset((
    "compound_bullet",            # §1: the bullet bundles more than one ask ('+')
    "long_line",                  # OPEN-5: single-line bullets, warned when long
    "receipt_section_mismatch",   # §3 rule 5 / TH-A11: a RUNNING bullet claiming a receipt
    "missing_proc",               # proc marker present but not a literal proc_<hex>
    "crlf_normalized",            # the source used CRLF; parsing normalised to LF
    "decode_replaced",            # undecodable bytes were replaced, not dropped
    "unknown_key_marker",         # OPEN-4 ``[key: <slug>]`` marker seen but not adopted
    "absent_from_source",         # a previously bound key is no longer in the source
))
#: Degraded warnings — attribution is incomplete. These set exit code 5 (PARSE_DEGRADED); the
#: pass still runs and never drops a bullet.
WARNING_DEGRADED = frozenset((
    "heading_unrecognized",
    "unsectioned_bullet",
    "malformed_bullet",
    "wrapped_line",
    "duplicate_slug",
    "duplicate_heading",
    "empty_section",
    "ambiguous_binding",
    "keyless_binding_shift",
))

#: §1's compound column reproduces exactly as "the bullet contains '+'".
COMPOUND_MARKER = "+"
#: OPEN-5's line-length warning threshold (documented constant, no config key).
LINE_LENGTH_WARN_CHARS = 300

_BULLET_RE = re.compile(r"^-\s")
_KEYED_RE = re.compile(r"^(?P<slug>[^\s(]+)\s*\((?P<proc>proc_[0-9a-fA-F]+)\)\s*:")
#: v2 amendment (continuum-grammar ruling §3): slug-only keyed form `- slug: text`.
_SLUG_ONLY_RE = re.compile(r"^(?P<slug>[^\s(:][^\s(]*[^\s(]|[^\s(:])\s*:")
_PROC_SHAPED_RE = re.compile(r"^(?P<slug>[^\s(]+)\s*\((?P<proc>[^)]*proc[^)]*)\)\s*:")
_RECEIPT_RE = re.compile(r"RECEIPT SAYS\b(?P<rest>.*)$")
_RECEIPT_STATUS_RE = re.compile(r"^\s*(CLOSED|COMPLETE)\b")
_PROC_TOKEN_RE = re.compile(r"proc_[0-9a-fA-F]+")
_KEY_RE = re.compile(r"^[^\s():]+$")
#: TH-L4's deterministic fallback key form (accepted by the audited bind route).
UNNAMED_KEY_RE = re.compile(r"^unnamed:(?:RUNNING|AWAITING_OWNER|OPEN_FOLLOW_UP|CLOSED):\d+$")
#: Public aliases used by the audited human-binding route (TH-L9).
KEY_RE = _KEY_RE
PROC_TOKEN_RE = _PROC_TOKEN_RE
_WS_RE = re.compile(r"\s+")
_UNNAMED_PREFIX = "unnamed:"
DASHBOARD_DERIVED_LABEL = "DASHBOARD-DERIVED (Continuum)"


class SourceMissing(IOError):
    """The configured TASK-HOME source does not exist -> exit 4."""


class SyncLocked(RuntimeError):
    """Another pass holds the ledger lock -> exit 3."""


# --------------------------------------------------------------------------- helpers

def _norm_ws(value: Any) -> str:
    """Collapse internal whitespace and strip — the only normalisation applied to a name."""
    if value is None:
        return ""
    return _WS_RE.sub(" ", str(value)).strip()


def _heading_key(heading: str) -> str:
    """TH-A6: the leading words of a heading, before any parenthetical."""
    text = str(heading or "")
    cut = text.find("(")
    if cut >= 0:
        text = text[:cut]
    return _norm_ws(text).casefold().replace("-", " ")


def _section_code(heading: str) -> Optional[str]:
    return _SECTION_HEADING_KEYS.get(_heading_key(heading))


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def valid_key(value: Any) -> bool:
    """A bindable TASK-HOME key, byte-verbatim (TH-L3).

    Two accepted forms:
      * a literal RUNNING slug — no whitespace, ``(``, ``)`` or ``:``;
      * the deterministic keyless fallback key ``unnamed:<SECTION>:<ordinal>`` (TH-L4), so a
        human can bind a bullet that carries no slug of its own.
    Nothing else is a key: no normalisation, no case folding.
    """
    if not isinstance(value, str):
        return False
    if KEY_RE.match(value):
        return True
    return bool(UNNAMED_KEY_RE.match(value))


def valid_proc(value: Any) -> bool:
    """``proc_<hex>`` or empty/null. The proc id is a secondary cross-check, never the key."""
    if value is None or value == "":
        return True
    return bool(PROC_TOKEN_RE.fullmatch(str(value)))


# --------------------------------------------------------------------------- parse (§1, §4)

@dataclass
class ParsedItem:
    """One TASK-HOME bullet. Line granularity — a bullet is never split (OPEN-9)."""

    key: str
    proc: Optional[str]
    section: str
    section_label: str
    heading: str
    line: str
    line_no: int
    content_sha1: str
    keyless: bool
    receipt: bool
    receipt_text: Optional[str]
    receipt_status: Optional[str]
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key, "proc": self.proc, "section": self.section,
            "section_label": self.section_label, "line": self.line, "line_no": self.line_no,
            "content_sha1": self.content_sha1, "keyless": self.keyless,
            "receipt": self.receipt, "receipt_text": self.receipt_text,
            "receipt_status": self.receipt_status, "warnings": list(self.warnings),
        }


@dataclass
class ParsedSource:
    path: Optional[str]
    source_sha256: str
    text: str
    items: List[ParsedItem]
    sections: List[Dict[str, Any]]
    warnings: List[str]
    preamble: List[str] = field(default_factory=list)
    mtime: Optional[float] = None
    size: Optional[int] = None

    # --- derived views used by the tests, the CLI report and the export -------------
    @property
    def degraded(self) -> List[str]:
        return sorted(set(w for w in self.warnings if w in WARNING_DEGRADED))

    @property
    def informational(self) -> List[str]:
        return sorted(set(w for w in self.warnings if w not in WARNING_DEGRADED))

    def section_counts(self) -> Dict[str, int]:
        out = {code: 0 for code in SECTION_ORDER}
        for it in self.items:
            out[it.section] = out.get(it.section, 0) + 1
        return out

    def keyed(self) -> List[ParsedItem]:
        return [it for it in self.items if not it.keyless]

    def keyless(self) -> List[ParsedItem]:
        return [it for it in self.items if it.keyless]

    def receipts(self) -> List[ParsedItem]:
        return [it for it in self.items if it.receipt]

    def compound(self) -> List[ParsedItem]:
        return [it for it in self.items if COMPOUND_MARKER in it.line]

    def by_key(self) -> Dict[str, ParsedItem]:
        out: Dict[str, ParsedItem] = {}
        for it in self.items:
            out.setdefault(it.key, it)
        return out


def parse_bytes(raw: bytes, *, path: Optional[str] = None,
                mtime: Optional[float] = None, size: Optional[int] = None) -> ParsedSource:
    """Parse TASK-HOME bytes. Never raises on malformed input — it warns (TH-G2)."""
    warnings: List[str] = []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
        warnings.append("decode_replaced")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if normalized != text:
        warnings.append("crlf_normalized")
    source_sha256 = sha256_bytes(raw)

    items: List[ParsedItem] = []
    sections: List[Dict[str, Any]] = []
    preamble: List[str] = []
    seen_codes: Dict[str, int] = {}
    current: Optional[Dict[str, Any]] = None
    keyless_ordinal = 0
    last_item: Optional[ParsedItem] = None

    for idx, line in enumerate(normalized.split("\n"), start=1):
        if line.startswith("## "):
            heading = line[3:].strip()
            code = _section_code(heading)
            entry = {
                "section": code or "UNRECOGNIZED",
                "label": SECTION_LABELS.get(code or "", heading),
                "heading": heading,
                "line": line,
                "line_no": idx,
                "lane": SECTION_LANES.get(code or ""),
                "quiet": SECTION_QUIET.get(code or ""),
                "items": [],
                "recognized": bool(code),
            }
            if code is None:
                warnings.append("heading_unrecognized")
            elif code in seen_codes:
                warnings.append("duplicate_heading")
                entry["duplicate_of_line_no"] = seen_codes[code]
            seen_codes.setdefault(code or heading, idx)
            sections.append(entry)
            current = entry
            keyless_ordinal = 0
            last_item = None
            continue

        if line.startswith("#"):
            if current is None:
                preamble.append(line)     # the document title line, kept verbatim
            continue                      # a non-``##`` heading is not a section

        if line == "" or line.strip() == "":
            if current is None:
                preamble.append(line)
            last_item = None
            continue

        if _BULLET_RE.match(line):
            body = line[2:]
        elif line.startswith("-"):
            body = line[1:].lstrip()
            warnings.append("malformed_bullet")
        else:
            if current is None:
                # document preamble prose (before the first section): nothing to attribute,
                # so it is kept verbatim and no warning is raised.
                preamble.append(line)
                continue
            # a continuation / wrapped line (OPEN-5): reported, never silently dropped
            warnings.append("wrapped_line")
            if last_item is not None and "wrapped_line" not in last_item.warnings:
                last_item.warnings.append("wrapped_line")
            continue

        section_code = current["section"] if current is not None else "UNSECTIONED"
        section_label = (current["label"] if current is not None
                         else PANEL_GROUP_LABELS["DASHBOARD_ONLY"])
        heading = current["heading"] if current is not None else ""
        if current is None:
            warnings.append("unsectioned_bullet")

        m = _KEYED_RE.match(body)
        proc: Optional[str] = None
        keyless = False
        if m is not None:
            key = m.group("slug")
            proc = m.group("proc")
        else:
            proc_shaped = _PROC_SHAPED_RE.match(body)
            if proc_shaped is not None:
                warnings.append("missing_proc")
            m2 = _SLUG_ONLY_RE.match(body)
            if m2 is not None:
                key = m2.group("slug")
                # proc stays None — this is a keyed bullet with no proc token
            else:
                keyless_ordinal += 1
                keyless = True
                key = "{}{}:{}".format(_UNNAMED_PREFIX, section_code, keyless_ordinal)

        if "[key:" in body:
            warnings.append("unknown_key_marker")   # OPEN-4 marker is not adopted in v1

        receipt = False
        receipt_text: Optional[str] = None
        receipt_status: Optional[str] = None
        rm = _RECEIPT_RE.search(body)
        if rm is not None:
            receipt = True
            rest = rm.group("rest")
            receipt_text = rest[1:] if rest.startswith(" ") else rest
            receipt_text = receipt_text.rstrip()
            status = _RECEIPT_STATUS_RE.match(receipt_text or "")
            receipt_status = status.group(1) if status is not None else None

        item_warnings: List[str] = []
        if COMPOUND_MARKER in line:
            item_warnings.append("compound_bullet")
        if len(line) > LINE_LENGTH_WARN_CHARS:
            item_warnings.append("long_line")
        if receipt and section_code == "RUNNING":
            item_warnings.append("receipt_section_mismatch")
        for code in item_warnings:
            warnings.append(code)

        item = ParsedItem(
            key=key, proc=proc, section=section_code, section_label=section_label,
            heading=heading, line=line, line_no=idx, content_sha1=sha1_text(line),
            keyless=keyless, receipt=receipt, receipt_text=receipt_text,
            receipt_status=receipt_status, warnings=item_warnings,
        )
        items.append(item)
        if current is not None:
            current["items"].append(idx)
        last_item = item

    for entry in sections:
        if not entry["items"]:
            warnings.append("empty_section")

    # TH-A2/TH-G2: a duplicated literal key is reported (and both bullets are kept verbatim).
    seen: Dict[str, int] = {}
    for item in items:
        if not item.keyless:
            seen[item.key] = seen.get(item.key, 0) + 1
    if any(count > 1 for count in seen.values()):
        warnings.append("duplicate_slug")

    return ParsedSource(path=path, source_sha256=source_sha256, text=normalized, items=items,
                        sections=sections, warnings=warnings, preamble=preamble,
                        mtime=mtime, size=size)


def parse_file(path: str) -> ParsedSource:
    """Read + parse the TASK-HOME source. READ-ONLY: the file is opened ``rb`` only."""
    if not os.path.isfile(path):
        raise SourceMissing(path)
    st = os.stat(path)
    with open(path, "rb") as fh:
        raw = fh.read()
    return parse_bytes(raw, path=path, mtime=st.st_mtime, size=st.st_size)


def render_document(parsed: ParsedSource) -> str:
    """Rebuild the source's EXISTING content from its parsed structure (TH-R1 renderer).

    Structure-driven, not a byte copy: the preamble verbatim, then each section's heading and
    its bullets verbatim, with one blank line between sections. For a source whose structure is
    the canonical one (no blank lines inside a section, no wrapped lines) this reproduces the
    LF-normalised source exactly, so ``parse(render(parse(x)))`` is a fixed point on items,
    sections, lanes AND line numbers. The export appends its proposal block separately, so this
    renderer never emits a proposed line (TH-R3).
    """
    out: List[str] = list(parsed.preamble)
    for position, entry in enumerate(parsed.sections):
        out.append(entry.get("line") or "## {}".format(entry["heading"]))
        for item in parsed.items:
            if item.line_no in entry["items"]:
                out.append(item.line)
        if position < len(parsed.sections) - 1:
            out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


# --------------------------------------------------------------------------- binding (§4)

def build_project_index(registry: Any) -> Dict[str, Any]:
    """Read-only index of the Continuum side used for the §4 fallback order.

    No fuzzy, substring, token-score or embedding matching exists anywhere (TH-L4/INV-MC-4).
    """
    declared: Dict[str, Dict[str, str]] = {}
    for row in registry.conn.execute(
            "SELECT project_id, field, value FROM declared_field WHERE field IN "
            "('project_name','placement','task_home_key','task_home_proc','task_home_binding',"
            "'next_action_verified','lifecycle_override','parked')"):
        declared.setdefault(row["project_id"], {})[row["field"]] = row["value"]

    rows: Dict[str, Dict[str, Any]] = {}
    for row in registry.projects():
        rows[row["project_id"]] = dict(row)

    titles: Dict[Tuple[str, str], str] = {}
    for row in registry.conn.execute("SELECT profile_name, session_id, title FROM session_fact"):
        titles[(row["profile_name"], row["session_id"])] = row["title"] or ""

    members: Dict[str, List[Tuple[str, str]]] = {}
    for row in registry.conn.execute(
            "SELECT project_id, profile_name, session_id FROM project_session"):
        members.setdefault(row["project_id"], []).append(
            (row["profile_name"], row["session_id"]))

    next_actions: Dict[str, List[str]] = {}
    for row in registry.conn.execute("SELECT project_id, text FROM next_action"):
        next_actions.setdefault(row["project_id"], []).append(row["text"] or "")

    by_name: Dict[str, List[str]] = {}
    proc_tokens: Dict[str, List[str]] = {}
    for pid, row in rows.items():
        for candidate in (row.get("name"), declared.get(pid, {}).get("project_name")):
            norm = _norm_ws(candidate)
            if norm:
                by_name.setdefault(norm, []).append(pid)
        blobs = [titles.get(ref, "") for ref in members.get(pid, [])]
        blobs += next_actions.get(pid, [])
        blobs.append(declared.get(pid, {}).get("next_action_verified") or "")
        for blob in blobs:
            for token in _PROC_TOKEN_RE.findall(blob or ""):
                if pid not in proc_tokens.setdefault(token, []):
                    proc_tokens[token].append(pid)
    for bucket in (by_name, proc_tokens):
        for key in bucket:
            bucket[key] = sorted(set(bucket[key]))
    return {"projects": rows, "declared": declared, "by_name": by_name,
            "proc_tokens": proc_tokens, "members": members}


def human_binding_for(index: Dict[str, Any], key: str) -> List[str]:
    """Projects whose audited human binding claims ``key`` (TH-L5: never silently overwritten)."""
    out = []
    for pid, fields in index["declared"].items():
        if fields.get("task_home_binding") == "human" and fields.get("task_home_key") == key:
            out.append(pid)
    return sorted(out)


def resolve_items(parsed: ParsedSource, index: Dict[str, Any],
                  ledger: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Apply §4 steps 1->5 to every parsed bullet. Returns the ledger item dicts."""
    prior_by_key: Dict[str, Dict[str, Any]] = {}
    prior_by_content: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for entry in ((ledger or {}).get("items") or []):
        prior_by_key.setdefault(entry.get("key"), entry)
        if str(entry.get("key") or "").startswith(_UNNAMED_PREFIX):
            prior_by_content.setdefault(
                (entry.get("section"), entry.get("content_sha1")), entry)

    resolved: List[Dict[str, Any]] = []
    ambiguous: List[Dict[str, Any]] = []
    for item in parsed.items:
        warnings = list(item.warnings)
        project_id: Optional[str] = None
        binding: Optional[str] = None
        prior = prior_by_key.get(item.key)

        humans = human_binding_for(index, item.key)
        if len(humans) == 1:
            project_id, binding = humans[0], "human"
        elif len(humans) > 1:
            warnings.append("ambiguous_binding")
            ambiguous.append({"key": item.key, "kind": "ambiguous_binding",
                              "task_home_value": item.key,
                              "dashboard_value": ",".join(humans),
                              "winner": "none", "line_no": item.line_no})
        else:
            names = index["by_name"].get(_norm_ws(item.key), []) if not item.keyless else []
            proc_matches = index["proc_tokens"].get(item.proc, []) if item.proc else []
            if len(names) == 1:
                # §4 step 1: exact slug == project name (byte compare of the normalised name)
                project_id, binding = names[0], "import"
            elif len(names) > 1:
                warnings.append("ambiguous_binding")
                ambiguous.append({"key": item.key, "kind": "ambiguous_binding",
                                  "task_home_value": item.key,
                                  "dashboard_value": ",".join(names),
                                  "winner": "none", "line_no": item.line_no})
            elif len(proc_matches) == 1:
                # §4 step 2: the literal proc id found verbatim in a member title / next action
                project_id, binding = proc_matches[0], "import"
            elif len(proc_matches) > 1:
                warnings.append("ambiguous_binding")
                ambiguous.append({"key": item.key, "kind": "ambiguous_binding",
                                  "task_home_value": item.key,
                                  "dashboard_value": ",".join(proc_matches),
                                  "winner": "none", "line_no": item.line_no})
            elif item.keyless:
                # §4 steps 3-4: content_sha1 within the same section, then the ordinal key.
                # TH-L8: a repeated pass updates the existing row instead of duplicating it.
                # A ledger binding to a project that no longer exists in THIS registry is
                # never re-created — the item falls through to source_only (no ghost rows).
                binding = "keyless"
                hit = prior_by_content.get((item.section, item.content_sha1))
                if hit is not None and hit.get("project_id") in index["projects"]:
                    project_id = hit.get("project_id")
                else:
                    prev_ordinal = prior_by_key.get(item.key)
                    if (prev_ordinal is not None and "ambiguous_binding" not in warnings
                            and prev_ordinal.get("project_id") in index["projects"]):
                        # TH-A3: the line was edited. Report the shift; the binding is kept and
                        # is never silently re-pointed at different content.
                        warnings.append("keyless_binding_shift")
                        project_id = prev_ordinal.get("project_id")
            else:
                prior = prior_by_key.get(item.key)
                if (prior is not None and prior.get("project_id")
                        and prior["project_id"] in index["projects"]):
                    project_id = prior.get("project_id")
                    binding = prior.get("binding") or "import"
        if "ambiguous_binding" in warnings:
            warnings = sorted(set(warnings))
        resolved.append({
            "key": item.key, "proc": item.proc, "section": item.section,
            "section_label": item.section_label, "line": item.line, "line_no": item.line_no,
            "content_sha1": item.content_sha1, "project_id": project_id, "binding": binding,
            "warnings": sorted(set(warnings)),
        })

    # TH-A5: at most one K1 -> K4 and one K4 -> K1. A violation is reported, never resolved.
    counts: Dict[str, List[Dict[str, Any]]] = {}
    for entry in resolved:
        if entry["project_id"]:
            counts.setdefault(entry["project_id"], []).append(entry)
    for pid, entries in counts.items():
        if len(entries) < 2:
            continue
        entries.sort(key=lambda e: (SECTION_ORDER.index(e["section"])
                                    if e["section"] in SECTION_ORDER else len(SECTION_ORDER),
                                    e["line_no"]))
        for loser in entries[1:]:
            loser["project_id"] = None
            if "ambiguous_binding" not in loser["warnings"]:
                loser["warnings"] = sorted(set(loser["warnings"] + ["ambiguous_binding"]))
            ambiguous.append({"key": loser["key"], "kind": "ambiguous_binding",
                              "task_home_value": loser["key"],
                              "dashboard_value": pid, "winner": entries[0]["key"],
                              "line_no": loser["line_no"]})
    resolved.sort(key=lambda e: (SECTION_ORDER.index(e["section"])
                                 if e["section"] in SECTION_ORDER else len(SECTION_ORDER),
                                 e["line_no"]))
    return resolved, ambiguous


def absent_items(ledger: Optional[Dict[str, Any]], present_keys: Iterable[str],
                 index: Dict[str, Any]) -> List[Dict[str, Any]]:
    """§3: previously bound keys that are no longer in the source -> ``ABSENT``."""
    present = set(present_keys)
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for entry in ((ledger or {}).get("items") or []):
        key = entry.get("key")
        if not entry.get("project_id") or key in present or key in seen:
            continue
        if entry.get("section") == "ABSENT" and entry.get("project_id") not in index["projects"]:
            continue
        seen.add(key)
        out.append({
            "key": key, "proc": entry.get("proc"), "section": "ABSENT",
            "section_label": ABSENT_SECTION_LABEL, "line": entry.get("line") or "",
            "line_no": entry.get("line_no"), "content_sha1": entry.get("content_sha1") or "",
            "project_id": entry.get("project_id"), "binding": entry.get("binding"),
            "warnings": ["absent_from_source"],
        })
    return out


def sync_conflicts(items: Sequence[Dict[str, Any]], index: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Sync-time conflict rows (§5 ledger shape): local human flags shadowed by TASK-HOME.

    TH-L6 P2: TASK-HOME outranks the dashboard on STATUS; the local human flag is preserved
    verbatim and reported here with both values and the winner.
    """
    out: List[Dict[str, Any]] = []
    for entry in items:
        pid = entry.get("project_id")
        lane = SECTION_LANES.get(entry.get("section") or "")
        if not pid or lane is None:
            continue
        placement = (index["declared"].get(pid) or {}).get("placement")
        if placement and placement != lane:
            out.append({"key": entry["key"], "kind": "placement_conflict",
                        "task_home_value": lane, "dashboard_value": placement,
                        "winner": "task_home", "line_no": entry.get("line_no")})
    out.sort(key=lambda c: (c["key"] or "", c["line_no"] or 0))
    return out


# --------------------------------------------------------------------------- paths / config

def plugin_dir() -> str:
    return str(Path(__file__).resolve().parent.parent)


def _resolve(cfg: Any, key: str, default: str) -> str:
    value = str(cfg.get(key) or default)
    if os.path.isabs(value):
        return value
    return os.path.join(plugin_dir(), value)


def source_path_for(cfg: Any) -> str:
    return os.path.expanduser(_resolve(cfg, "task_home_source_path",
                                       "/Users/kethuda/.hermes/profiles/orda/TASK-HOME.md"))


def ledger_path_for(cfg: Any) -> str:
    return _resolve(cfg, "task_home_ledger_path", "data/task_home_sync.json")


def lock_path_for(cfg: Any) -> str:
    return os.path.splitext(ledger_path_for(cfg))[0] + ".lock"


def export_dir_for(cfg: Any) -> str:
    return _resolve(cfg, "task_home_export_dir", "review/out")


def stale_after_for(cfg: Any) -> int:
    return int(cfg.get("task_home_stale_after_seconds", 900) or 900)


# --------------------------------------------------------------------------- ledger

def load_ledger(path: str) -> Optional[Dict[str, Any]]:
    """Read the ledger. SELECT-free, write-free: a GET path may call this (TH-L7)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != LEDGER_SCHEMA:
        return None
    data.setdefault("items", [])
    data.setdefault("conflicts", [])
    data.setdefault("runs", [])
    return data


def write_ledger(path: str, ledger: Dict[str, Any]) -> None:
    """Atomic write: tmp + ``os.replace`` (§5)."""
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = "{}.tmp.{}".format(path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(ledger, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


class _SyncLock:
    """Exclusive advisory lock on the ledger lock file (TH-I4). Never writes to it."""

    def __init__(self, path: str):
        self.path = path
        self._fh = None

    def __enter__(self) -> "_SyncLock":
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


# --------------------------------------------------------------------------- table hashes

#: §5: the tables the pass must leave byte-identical.
GUARDED_TABLES = ("project", "project_session", "evidence", "next_action", "review_event",
                  "scan_run")


def table_hashes(conn: Any, tables: Sequence[str] = GUARDED_TABLES,
                 declared_only: bool = False) -> Dict[str, str]:
    """Deterministic content hash of whole tables (TH-A12 / TH-I1 evidence).

    Rows are rendered in a stable order (every column, PK-sorted), so the hash changes if and
    only if a stored value changes.
    """
    out: Dict[str, str] = {}
    for table in tables:
        try:
            rows = list(conn.execute("SELECT * FROM {}".format(table)))
        except Exception:
            out[table] = "absent"
            continue
        names = sorted(rows[0].keys()) if rows else []
        parts = []
        for row in sorted(rows, key=lambda r: tuple(str(r[n]) for n in names)):
            parts.append("\x1f".join("{}={}".format(n, row[n]) for n in names))
        out[table] = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    if declared_only:
        rows = list(conn.execute(
            "SELECT project_id, field, value, source, verified_at, expires_at, actor, "
            "declared_rev FROM declared_field ORDER BY project_id, field"))
        parts = ["\x1f".join(str(row[n]) for n in rows[0].keys()) for row in rows] if rows else []
        out["declared_field"] = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    return out


# --------------------------------------------------------------------------- sync (§5)

_INSERT_DECLARED = (
    "INSERT INTO declared_field (project_id, field, value, source, verified_at, expires_at, "
    "actor, declared_rev) VALUES (?,?,?,?,?,NULL,?, COALESCE((SELECT declared_rev FROM "
    "declared_field WHERE project_id=? AND field=?), 0) + 1) "
    "ON CONFLICT(project_id, field) DO UPDATE SET value=excluded.value, "
    "source=excluded.source, verified_at=excluded.verified_at, expires_at=NULL, "
    "actor=excluded.actor, declared_rev=declared_field.declared_rev + 1"
)


def _declared_rows(registry: Any, project_ids: Sequence[str]) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    if not project_ids:
        return out
    placeholders = ",".join("?" * len(project_ids))
    rows = registry.conn.execute(
        "SELECT project_id, field, value FROM declared_field WHERE project_id IN ({})".format(
            placeholders), tuple(project_ids))
    for row in rows:
        out.setdefault(row["project_id"], {})[row["field"]] = row["value"]
    return out


def _desired_fields(entry: Dict[str, Any], source_sha256: str, run_id: str, now: float,
                    prev: Dict[str, str]) -> Dict[str, str]:
    desired = {
        "task_home_key": entry["key"],
        "task_home_proc": entry.get("proc") or "",
        "task_home_section": entry["section"],
        "task_home_section_label": entry["section_label"],
        "task_home_line": entry.get("line") or "",
        "task_home_line_no": "" if entry.get("line_no") is None else str(entry["line_no"]),
        "task_home_content_sha1": entry.get("content_sha1") or "",
        "task_home_receipt": "1" if entry.get("receipt") else "0",
        "task_home_receipt_text": entry.get("receipt_text") or "",
        "task_home_binding": entry.get("binding") or "",
    }
    identity_moved = (
        prev.get("task_home_key") != desired["task_home_key"]
        or prev.get("task_home_section") != desired["task_home_section"]
        or prev.get("task_home_binding") != desired["task_home_binding"]
    )
    if identity_moved or "task_home_synced_at" not in prev:
        desired["task_home_source_sha256"] = source_sha256
        desired["task_home_synced_at"] = "%.6f" % float(now)
        desired["task_home_run_id"] = run_id
    return desired


def sync(cfg: Any, *, source_path: Optional[str] = None, registry: Any = None,
         now: Optional[float] = None, run_id: Optional[str] = None, dry_run: bool = False,
         use_lock: bool = True) -> Dict[str, Any]:
    """One explicit idempotent pass (TH-L7/TH-L8). Returns a report; never raises for content."""
    now = time.time() if now is None else float(now)
    run_id = run_id or uuid.uuid4().hex
    started_at = now
    source = source_path or source_path_for(cfg)
    ledger_path = ledger_path_for(cfg)
    lock_path = lock_path_for(cfg)

    lock = _SyncLock(lock_path) if use_lock else None
    if lock is not None:
        lock.__enter__()
    try:
        parsed = parse_file(source)
        ledger = load_ledger(ledger_path)
        index = build_project_index(registry) if registry is not None else {
            "projects": {}, "declared": {}, "by_name": {}, "proc_tokens": {}, "members": {}}

        items, ambiguous = resolve_items(parsed, index, ledger)
        absent = absent_items(ledger, [it["key"] for it in items], index)
        conflicts = sync_conflicts(items, index) + ambiguous
        conflicts.sort(key=lambda c: (c.get("key") or "", c.get("line_no") or 0))

        guard_before = table_hashes(registry.conn, declared_only=True) if registry is not None else {}

        # ---- content-gated writes (TH-L8/TH-I1) ------------------------------------
        writes: List[Tuple[str, str, str]] = []
        added = changed = unchanged = 0
        bound = [it for it in items + absent if it.get("project_id")]
        prev_map = _declared_rows(registry, sorted({it["project_id"] for it in bound})) \
            if registry is not None else {}
        for entry in bound:
            pid = entry["project_id"]
            prev = prev_map.get(pid, {})
            desired = _desired_fields(entry, parsed.source_sha256, run_id, now, prev)
            diff = {k: v for k, v in desired.items() if prev.get(k) != v}
            if not prev.get("task_home_key"):
                added += 1
            if diff:
                changed += 1
                for key in RESERVED_FIELDS:
                    if key in diff:
                        writes.append((pid, key, diff[key]))
            else:
                unchanged += 1
        absent_now = {it["project_id"] for it in absent}

        # Parse-time AND resolve-time warnings both feed the report and the exit code: a
        # binding that had to shift is a degraded attribution even though the text parsed fine.
        resolve_warnings = sorted({w for it in items + absent for w in (it.get("warnings") or [])})
        warnings_all = sorted(set(parsed.warnings) | set(resolve_warnings))
        degraded = sorted(w for w in warnings_all if w in WARNING_DEGRADED)
        informational = sorted(w for w in warnings_all if w not in WARNING_DEGRADED)

        if writes and not dry_run:
            with registry.transaction():
                for pid, key, value in writes:
                    registry.conn.execute(_INSERT_DECLARED,
                                          (pid, key, value, SOURCE_KIND, now, SOURCE_KIND,
                                           pid, key))
        elif writes and dry_run:
            pass

        # ---- ledger ----------------------------------------------------------------
        ledger_out = {
            "schema": LEDGER_SCHEMA,
            "source_path": source,
            "source_sha256": parsed.source_sha256,
            "last_run_id": run_id,
            "last_synced_at": now,
            "items": [
                {"key": it["key"], "proc": it.get("proc"), "section": it["section"],
                 "section_label": it["section_label"], "line": it.get("line") or "",
                 "line_no": it.get("line_no"), "content_sha1": it.get("content_sha1") or "",
                 "project_id": it.get("project_id"), "binding": it.get("binding"),
                 "warnings": list(it.get("warnings") or [])}
                for it in items + absent
            ],
            "conflicts": conflicts,
            "runs": list((ledger or {}).get("runs") or []),
        }
        run_entry = {
            "run_id": run_id, "started_at": started_at, "ended_at": time.time(),
            "source_sha256": parsed.source_sha256, "added": added, "changed": changed,
            "unchanged": unchanged, "absent": len(absent),
            "warnings": len(degraded) + len(informational),
            "dry_run": bool(dry_run), "writes": len(writes),
        }
        ledger_out["runs"].append(run_entry)
        if not dry_run:
            write_ledger(ledger_path, ledger_out)

        guard_after = table_hashes(registry.conn, declared_only=True) if registry is not None else {}
        exit_code = EXIT_PARSE_DEGRADED if degraded else EXIT_OK
        return {
            "ok": True, "exit_code": exit_code, "run_id": run_id, "dry_run": bool(dry_run),
            "source_path": source, "source_sha256": parsed.source_sha256,
            "source_mtime": parsed.mtime, "ledger_path": ledger_path, "lock_path": lock_path,
            "added": added, "changed": changed, "unchanged": unchanged, "absent": len(absent),
            "absent_new": len(absent_now - {it["project_id"] for it in items
                                            if it.get("project_id")}),
            "writes": len(writes), "items": items, "absent_items": absent,
            "conflicts": conflicts, "warnings": warnings_all,
            "degraded": degraded, "informational": informational,
            "section_counts": parsed.section_counts(), "item_count": len(parsed.items),
            "table_hashes_before": guard_before, "table_hashes_after": guard_after,
            "item_fields_written": sorted({key for _pid, key, _v in writes}),
        }
    finally:
        if lock is not None:
            lock.__exit__(None, None, None)


def show(cfg: Any, *, source_path: Optional[str] = None) -> Dict[str, Any]:
    """Read-only ledger view for ``task-home show``. Writes nothing at all."""
    source = source_path or source_path_for(cfg)
    ledger_path = ledger_path_for(cfg)
    ledger = load_ledger(ledger_path)
    out: Dict[str, Any] = {
        "ok": True, "source_path": source, "ledger_path": ledger_path,
        "ledger_present": ledger is not None,
    }
    if os.path.isfile(source):
        st = os.stat(source)
        out["source_mtime"] = st.st_mtime
        out["source_size"] = st.st_size
    if ledger is not None:
        out["source_sha256"] = ledger.get("source_sha256")
        out["last_run_id"] = ledger.get("last_run_id")
        out["last_synced_at"] = ledger.get("last_synced_at")
        out["items"] = ledger.get("items") or []
        out["conflicts"] = ledger.get("conflicts") or []
        out["runs"] = ledger.get("runs") or []
        out["bindings"] = [
            {"key": it.get("key"), "project_id": it.get("project_id"),
             "binding": it.get("binding"), "section": it.get("section")}
            for it in (ledger.get("items") or []) if it.get("project_id")]
    return out


# --------------------------------------------------------------------------- export (§5)

def _proposed_key(project_id: str, name: Optional[str]) -> str:
    candidate = _norm_ws(name)
    if candidate and _KEY_RE.match(candidate):
        return candidate
    return "dashboard-{}".format(project_id[:12])


def _derived_block(row: Dict[str, Any]) -> Dict[str, Any]:
    """TH-C3: confidence/tier/session counts live ONLY here."""
    return {
        "confidence_band": row.get("confidence_band"),
        "evidence_tier": row.get("evidence_tier"),
        "session_count": row.get("session_count"),
        "last_subject_activity": row.get("last_substantive_activity"),
    }


def build_export(parsed: ParsedSource, ledger: Optional[Dict[str, Any]],
                 index: Dict[str, Any], *, source_sha256: Optional[str] = None
                 ) -> Dict[str, Any]:
    """The dashboard -> TASK-HOME proposal payload (§5). Generated, never applied (TH-L14)."""
    source_sha256 = source_sha256 or parsed.source_sha256
    items = list((ledger or {}).get("items") or [])
    by_pid: Dict[str, Dict[str, Any]] = {}
    for entry in items:
        if entry.get("project_id"):
            by_pid.setdefault(entry["project_id"], entry)

    sections: List[Dict[str, Any]] = []
    for code in SECTION_ORDER:
        entries = [it for it in items if it.get("section") == code]
        entries.sort(key=lambda e: (str(e.get("project_id") or ""), str(e.get("key") or "")))
        lines = []
        for entry in entries:
            pid = entry.get("project_id")
            row = index["projects"].get(pid) if pid else None
            lines.append({
                "key": entry.get("key"),
                "proposed": False,
                "existing_line_no": entry.get("line_no"),
                "lane": SECTION_LANES[code],
                "derived": _derived_block(row) if row else None,
                "proposed_line": entry.get("line") or "",
            })
        sections.append({"section": code, "label": SECTION_LABELS[code], "lines": lines})

    proposed_lines = []
    unbound = []
    for pid in sorted(index["projects"]):
        if pid in by_pid:
            continue
        row = index["projects"][pid]
        lane = _dashboard_lane(row, index["declared"].get(pid) or {})
        key = _proposed_key(pid, row.get("name"))
        proposed = {
            "key": key, "proposed": True, "existing_line_no": None, "lane": lane,
            "derived": _derived_block(row),
            "proposed_line": "- {}: {} (dashboard-derived, lane {}).".format(
                key, _norm_ws(row.get("name")) or pid, lane),
        }
        proposed_lines.append(proposed)
        unbound.append({"project_id": pid, "name": row.get("name"), "lane": lane,
                        "proposed_key": key})
    proposed_lines.sort(key=lambda e: (e["key"], e["proposed_line"]))

    confirm = [{"key": it.get("key"), "receipt_text": it.get("receipt_text"),
                "line_no": it.get("line_no")}
               for it in items if it.get("section") == "RUNNING"
               and "receipt_section_mismatch" in (it.get("warnings") or [])]
    confirm.sort(key=lambda c: (c.get("line_no") or 0, c.get("key") or ""))

    return {
        "schema": LEDGER_SCHEMA,
        "source_sha256": source_sha256,
        "sections": sections,
        "proposed_new_section": {
            "label": DASHBOARD_DERIVED_LABEL,
            "reason": ("Continuum projects with no TASK-HOME line. Proposed only; nothing in the "
                       "sync applies this and TASK-HOME.md is never written (TH-L1/TH-L14)."),
            "lines": proposed_lines,
        },
        "confirm_then_close": confirm,
        "unbound_projects": unbound,
        "conflicts": list((ledger or {}).get("conflicts") or []),
        "counts": {
            "sections": len(sections),
            "items": len(items),
            "bound": len(by_pid),
            "dashboard_only": len(unbound),
            "confirm_then_close": len(confirm),
            "conflicts": len((ledger or {}).get("conflicts") or []),
        },
    }


def _dashboard_lane(row: Dict[str, Any], declared: Dict[str, str]) -> str:
    """The dashboard's own lane for an unbound project (mirrors service._resolve_lane)."""
    placement = declared.get("placement")
    if placement:
        return placement
    if declared.get("parked") == "1":
        return "paused"
    return "ongoing"


def render_export_md(parsed: ParsedSource, payload: Dict[str, Any]) -> str:
    """The reviewable block (§5). LF endings, no wall-clock value anywhere (TH-I3)."""
    out: List[str] = [
        "# TASK-HOME ↔ Continuum export (generated proposal — nothing here was applied)",
        "",
        "source: {}".format(parsed.path or "(frozen fixture)"),
        "source_sha256: {}".format(payload["source_sha256"]),
        "sections: {} · items: {} · bound: {} · dashboard_only: {} · confirm_then_close: {}"
        " · conflicts: {}".format(
            payload["counts"]["sections"], payload["counts"]["items"],
            payload["counts"]["bound"], payload["counts"]["dashboard_only"],
            payload["counts"]["confirm_then_close"], payload["counts"]["conflicts"]),
        "",
        "Existing content is reproduced verbatim below (no line was modified). The proposal",
        "blocks that follow are additive and are for a human to apply to TASK-HOME.md by hand.",
        "",
        "---",
        "",
    ]
    out.append(render_document(parsed).rstrip("\n"))
    out += ["", "---", ""]
    section = payload["proposed_new_section"]
    out.append("## PROPOSED — {}".format(section["label"]))
    out.append("")
    out.append(section["reason"])
    out.append("")
    if section["lines"]:
        for line in section["lines"]:
            out.append(line["proposed_line"])
    else:
        out.append("(nothing to propose: every Continuum project already has a TASK-HOME line)")
    out += ["", "## CONFIRM THEN CLOSE (RUNNING bullets whose own line claims a receipt)", ""]
    if payload["confirm_then_close"]:
        for entry in payload["confirm_then_close"]:
            out.append("- {} (line {}): {}".format(
                entry["key"], entry["line_no"], entry["receipt_text"] or ""))
    else:
        out.append("(none)")
    out += ["", "## CONFLICTS (TASK-HOME wins on status; the local value is preserved)", ""]
    if payload["conflicts"]:
        for entry in payload["conflicts"]:
            out.append("- {}: task_home={} vs dashboard={} (winner {}, line {})".format(
                entry.get("key"), entry.get("task_home_value"), entry.get("dashboard_value"),
                entry.get("winner"), entry.get("line_no")))
    else:
        out.append("(none)")
    return "\n".join(out).rstrip("\n") + "\n"


def export(cfg: Any, registry: Any = None, *, out_dir: Optional[str] = None,
           source_path: Optional[str] = None, now: Optional[float] = None,
           run_id: Optional[str] = None) -> Dict[str, Any]:
    """Generate ``review/out/task-home-export.{md,json,meta.json}``. Never writes TASK-HOME."""
    now = time.time() if now is None else float(now)
    run_id = run_id or uuid.uuid4().hex
    source = source_path or source_path_for(cfg)
    parsed = parse_file(source)
    ledger = load_ledger(ledger_path_for(cfg))
    index = build_project_index(registry) if registry is not None else {
        "projects": {}, "declared": {}, "by_name": {}, "proc_tokens": {}, "members": {}}
    payload = build_export(parsed, ledger, index)
    body_md = render_export_md(parsed, payload)
    body_json = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n"

    target_dir = out_dir or export_dir_for(cfg)
    os.makedirs(target_dir, exist_ok=True)
    md_path = os.path.join(target_dir, "task-home-export.md")
    json_path = os.path.join(target_dir, "task-home-export.json")
    meta_path = os.path.join(target_dir, "task-home-export.meta.json")
    with open(md_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body_md)
    with open(json_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body_json)
    meta = {
        "run_id": run_id, "generated_at": now, "source_path": source,
        "source_sha256": parsed.source_sha256, "ledger_path": ledger_path_for(cfg),
        "counts": payload["counts"],
        "files": {"md": md_path, "json": json_path},
        "body_sha256": {"md": sha256_bytes(body_md.encode("utf-8")),
                        "json": sha256_bytes(body_json.encode("utf-8"))},
    }
    with open(meta_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(meta, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return {"ok": True, "exit_code": EXIT_OK, "payload": payload, "meta": meta,
            "paths": {"md": md_path, "json": json_path, "meta": meta_path},
            "body_md": body_md, "body_json": body_json,
            "source_sha256": parsed.source_sha256}


# --------------------------------------------------------------------------- projection (§6)

def _receipt_of(line: str) -> Tuple[bool, Optional[str]]:
    """Re-derive the receipt flag/text from a stored line (the ledger keeps no extra field).

    Deterministic and identical to the parse-time rule, so the projection re-reads the same
    answer from stored data instead of trusting a duplicated value.
    """
    match = _RECEIPT_RE.search(line or "")
    if match is None:
        return False, None
    rest = match.group("rest")
    text = rest[1:] if rest.startswith(" ") else rest
    return True, text.rstrip()


def _empty_counts() -> Dict[str, Any]:
    counts: Dict[str, Any] = {code: 0 for code in PANEL_GROUPS}
    counts["source_only"] = 0
    counts["conflicts"] = 0
    return counts


def projection_context(cfg: Any, *, now: Optional[float] = None) -> Dict[str, Any]:
    """The server-side TASK-HOME read model for the playground path (§6).

    Read-only: it loads the ledger file and the source's stat only. No registry, no write, so a
    GET can call it (TH-L7).
    """
    now = time.time() if now is None else float(now)
    source = source_path_for(cfg)
    ledger_path = ledger_path_for(cfg)
    stale_after = stale_after_for(cfg)
    ctx: Dict[str, Any] = {
        "enabled": False, "source_path": source, "source_sha256": None, "source_mtime": None,
        "synced_at": None, "age_seconds": None, "stale": False,
        "stale_after_seconds": stale_after, "run_id": None, "warnings": [], "conflicts": [],
        "items": [], "by_pid": {}, "by_key": {}, "counts": _empty_counts(), "source_only": 0,
        "ledger_path": ledger_path, "ledger_present": False,
    }
    if os.path.isfile(source):
        try:
            ctx["source_mtime"] = os.stat(source).st_mtime
        except OSError:
            ctx["source_mtime"] = None
    if not bool(cfg.get("task_home_enabled", False)):
        return ctx
    ledger = load_ledger(ledger_path)
    if ledger is None:
        return ctx                      # never synced yet -> enabled:false (§6)
    ctx["enabled"] = True
    ctx["ledger_present"] = True
    ctx["source_sha256"] = ledger.get("source_sha256")
    ctx["synced_at"] = ledger.get("last_synced_at")
    ctx["run_id"] = ledger.get("last_run_id")
    if ctx["synced_at"] is not None:
        ctx["age_seconds"] = max(0.0, now - float(ctx["synced_at"]))
        ctx["stale"] = bool(ctx["age_seconds"] > stale_after)
    items = []
    warnings: set = set()
    for entry in (ledger.get("items") or []):
        section = entry.get("section")
        receipt, receipt_text = _receipt_of(entry.get("line") or "")
        item = {
            "key": entry.get("key"), "proc": entry.get("proc"),
            "section": section, "lane": SECTION_LANES.get(section or ""),
            "line": entry.get("line") or "", "line_no": entry.get("line_no"),
            "project_id": entry.get("project_id"), "binding": entry.get("binding"),
            "receipt": receipt,
            "warnings": list(entry.get("warnings") or []),
        }
        items.append(item)
        warnings.update(item["warnings"])
        # internal (non-wire) enrichment the card block is built from
        enriched = dict(entry)
        enriched.update({
            "receipt": receipt, "receipt_text": receipt_text,
            "section_label": entry.get("section_label")
            or SECTION_LABELS.get(section or "", section or ""),
            "lane": item["lane"],
        })
        if item["key"] is not None:
            ctx["by_key"].setdefault(item["key"], enriched)
        if item["project_id"]:
            ctx["by_pid"].setdefault(item["project_id"], enriched)
    ctx["items"] = items
    ctx["warnings"] = sorted(warnings)
    ctx["ledger_conflicts"] = list(ledger.get("conflicts") or [])
    ctx["counts"]["source_only"] = sum(
        1 for entry in (ledger.get("items") or [])
        if not entry.get("project_id") and entry.get("section") in SECTION_ORDER)
    return ctx