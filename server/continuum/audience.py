"""M1b — continuum.audience.

Derived, total audience classification for every source session (architecture decision
``continuum-azaraki-user-session-filter`` D-US-1 .. D-US-9).

Closed vocabulary: ``USER_FACING`` | ``DELEGATED`` | ``AUTOMATED`` | ``UNKNOWN``.

Deterministic and database-first:
  * no model call, no randomness, no ordering dependence;
  * a fixed set of source rows plus a fixed brief-file index always yields the same value
    byte-for-byte;
  * every rule is data-driven from ``config`` so the rule set is falsifiable by mutation.

Read-only: this module never opens a source database. The scanner hands it text (the
untruncated first user message) and the delegator id set; the module returns values only.
The one filesystem read it performs is the read-only dispatch-brief index.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .config import Config

AUDIENCES: Tuple[str, ...] = ("USER_FACING", "DELEGATED", "AUTOMATED", "UNKNOWN")

# Rule A — automated source (exact, from the `source` column).
_SOURCE_CODES: Dict[str, str] = {"cron": "A1", "subagent": "A2", "kanban": "A3"}

# C2 bounded head (architecture fixes 4 096 chars).
ENVELOPE_HEAD_CHARS = 4096

# C2 envelope line: a single leading lowercase char, an optional space (the live corpus
# contains ``t eam6_agent: azaraki``), then the rest of the key.
_ENVELOPE_LINE_RE = re.compile(r"^([a-z])[ ]?([a-z0-9_]{1,30})\s*:\s*(\S.*)$")
# C3 ownership preflight header.
_PREFLIGHT_MATRIX_RE = re.compile(r"^ownership_matrix\s*:", re.MULTILINE)
_PREFLIGHT_READ_RE = re.compile(r"^matrix_read_at_dispatch\s*:\s*yes\b",
                                re.MULTILINE | re.IGNORECASE)
# C4 dispatch / assignment headline.
_HEADLINE_HASH_RE = re.compile(
    r"^#{1,4}\s*(?:Brief|Team6 dispatch|Team6 brief|Assignment|Dispatch)\b")
_HEADLINE_LABEL_RE = re.compile(r"^(?:GOAL|PROJECT|TASK)\s*[:\u2014-]")
# C5 role declaration (corroborating only — never standalone; fact 10).
_ROLE_DECL_RE = re.compile(r"(?im)^\s*-?\s*\**role:\**\s*\S")

_GROUP_MESSAGE_PREFIX = "[Group chat:"
_NO_USER_SESSION_LINE = "No user-facing conversation found for this project."

# Evidence-ref convention for non-file rules (auditable, never raw content).
NONE_REASON = "NONE"


def no_user_session_line() -> str:
    """The exact architecture copy for a project with no user-facing conversation (D-US-6)."""
    return _NO_USER_SESSION_LINE


# --------------------------------------------------------------------------- normalization

def normalize(text: Optional[str]) -> str:
    """BOM stripped, ``\\r\\n``/``\\r`` -> ``\\n``, whitespace runs collapsed, then stripped."""
    if text is None:
        return ""
    s = text
    if s.startswith("\ufeff"):
        s = s[1:]
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def digest(text: Optional[str]) -> str:
    """sha256 of the normalized text — the exact C1 comparison key."""
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- C1 brief index

def build_brief_index(cfg: Config) -> Dict[str, str]:
    """Read-only index of dispatch brief files: ``digest(normalize(text)) -> abs path``.

    Deterministic: roots are walked in sorted order and a digest keeps the first path seen.
    Oversized files and files that are not valid UTF-8 are skipped, never guessed at.
    """
    roots = cfg.get("audience_brief_roots") or []
    exts = {str(e).lower() for e in (cfg.get("audience_brief_exts") or [".md", ".txt"])}
    max_bytes = int(cfg.get("audience_brief_max_bytes", 2_000_000) or 2_000_000)
    home = os.path.expanduser(str(getattr(cfg, "hermes_home", "") or ""))
    index: Dict[str, str] = {}
    for root in roots:
        # ``{hermes_home}`` keeps a fixture home from reading the live brief corpus.
        rp = Path(os.path.expanduser(str(root).replace("{hermes_home}", home)))
        if not rp.is_dir():
            continue
        try:
            candidates = sorted(p for p in rp.rglob("*") if p.is_file())
        except OSError:
            continue
        for p in candidates:
            if p.suffix.lower() not in exts:
                continue
            try:
                if p.stat().st_size > max_bytes:
                    continue
                text = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            index.setdefault(digest(text), str(p))
    return index


# --------------------------------------------------------------------------- U-DELEGATOR

def build_delegator_index(profile_rows: Any) -> set:
    """Union of every session id named by an ``async_delegations`` linkage column.

    ``profile_rows`` is either ``{profile_name: [id, ...]}`` (the scanner's shape) or an
    iterable of ``(profile_name, [id, ...])`` pairs. ``async_delegations`` never names the
    delegated child (fact 5), so this is ONLY the positive ``U-DELEGATOR`` signal (D-US-3/D-US-4).
    """
    out: set = set()
    if profile_rows is None:
        return out
    items = profile_rows.items() if isinstance(profile_rows, Mapping) else profile_rows
    for _profile, ids in items:
        if ids is None:
            continue
        if isinstance(ids, str):
            out.add(ids)
            continue
        for i in ids:
            if i:
                out.add(str(i))
    return out


# --------------------------------------------------------------------------- helpers

def _get(fact: Any, key: str, default: Any = None) -> Any:
    if fact is None:
        return default
    if isinstance(fact, Mapping):
        return fact.get(key, default)
    return getattr(fact, key, default)


def _s(value: Any) -> str:
    return value if isinstance(value, str) else ("" if value is None else str(value))


def _dispatch_keys(cfg: Config) -> set:
    return {str(k) for k in (cfg.get("dispatch_keys") or [])}


def _dispatch_targets(cfg: Config) -> List[str]:
    return [str(p) for p in (cfg.get("dispatch_target_profiles") or [])]


def match_envelope(head: str, cfg: Config) -> Optional[str]:
    """C2 — bounded dispatch envelope. Returns the matched dispatch-key list or ``None``.

    Reads consecutive matching lines from the bounded head, skipping one optional leading
    ``---`` line, until a ``---`` line, a blank line, or a non-matching line. At least two
    distinct keys must be in ``dispatch_keys``.
    """
    if not head:
        return None
    lines = head[:ENVELOPE_HEAD_CHARS].split("\n")
    i = 0
    if lines and lines[0].strip() == "---":
        i = 1
    keys: List[str] = []
    for raw in lines[i:]:
        line = raw.rstrip("\r")
        if line.strip() == "---" or line.strip() == "":
            break
        m = _ENVELOPE_LINE_RE.match(line)
        if not m:
            break
        keys.append(m.group(1) + m.group(2))
    allowed = _dispatch_keys(cfg)
    hits = sorted({k for k in keys if k in allowed})
    return ",".join(hits) if len(hits) >= 2 else None


def matches_preflight(head: str) -> bool:
    """C3 — the ownership-preflight header (``ownership_matrix:`` + ``matrix_read_at_dispatch: yes``)."""
    if not head:
        return False
    bounded = head[:ENVELOPE_HEAD_CHARS]
    return bool(_PREFLIGHT_MATRIX_RE.search(bounded) and _PREFLIGHT_READ_RE.search(bounded))


def matches_headline(head: str) -> Optional[str]:
    """C4 — a dispatch/assignment headline on the first non-empty line."""
    if not head:
        return None
    for line in head[:ENVELOPE_HEAD_CHARS].split("\n"):
        s = line.strip()
        if not s:
            continue
        if _HEADLINE_HASH_RE.match(s):
            return s[:60]
        if _HEADLINE_LABEL_RE.match(s):
            return s[:60]
        return None
    return None


def role_declaration(head: str, cfg: Config) -> bool:
    """C5 — role declaration. Corroborating only: never sufficient on its own (fact 10)."""
    if not head:
        return False
    if _ROLE_DECL_RE.search(head):
        return True
    targets = _dispatch_targets(cfg)
    if not targets:
        return False
    alt = "|".join(re.escape(t) for t in targets)
    try:
        return re.search(r"\bYou are (?:" + alt + r")\b", head) is not None
    except re.error:
        return False


def _is_group_relay(fact: Any, head: str) -> bool:
    if _s(_get(fact, "chat_type")).strip().lower() == "group":
        return True
    if _s(_get(fact, "display_name")).startswith("Group:"):
        return True
    if _s(_get(fact, "title")).startswith("Group:"):
        return True
    if (head or "").lstrip().startswith(_GROUP_MESSAGE_PREFIX):
        return True
    return False


def _user_facing(fact: Any, head: str) -> bool:
    """U1..U6. U1/U2 (not AUTOMATED / not DELEGATED) hold by the time this is called."""
    if _get(fact, "parent_session_id"):
        return False                                   # U3
    if _is_group_relay(fact, head):                    # U4
        return False
    if not (head or "").strip():                       # U5
        return False
    if int(_get(fact, "hidden", 0) or 0) != 0:         # U6
        return False
    return True


# --------------------------------------------------------------------------- classification

def classify_audience(fact: Any,
                      first_turn_text: Optional[str],
                      brief_index: Optional[Mapping[str, str]],
                      delegators: Optional[Iterable[str]],
                      cfg: Config) -> Tuple[str, str, Optional[str]]:
    """Return ``(audience, reason_code, evidence_ref)`` for one session.

    Rules are evaluated in order and the FIRST hit wins; its code is stored verbatim in
    ``audience_reason``. ``A`` automated source -> ``B`` lineage -> ``C`` dispatch provenance
    -> ``U1..U6`` retention -> ``UNKNOWN`` residual.
    """
    delegator_set = set(delegators or ())

    # --- Rule A: automated source (exact, from the `source` column). Never used to include.
    src = _s(_get(fact, "source")).strip().lower()
    if src in _SOURCE_CODES:
        return "AUTOMATED", _SOURCE_CODES[src], None

    # --- Rule B: lineage (exact, from a session column).
    if _get(fact, "parent_session_id"):
        return "DELEGATED", "B1", None

    head = first_turn_text or ""

    # --- Rule C: dispatch provenance.
    if head:
        d = digest(head)
        idx = brief_index or {}
        if d in idx:
            return "DELEGATED", "C1", idx[d]                     # C1 exact brief-file digest
        env = match_envelope(head, cfg)
        if env is not None:
            return "DELEGATED", "C2", env                        # C2 structural envelope
        if matches_preflight(head):
            return "DELEGATED", "C3", "ownership_matrix"         # C3 preflight header
        headline = matches_headline(head)
        if headline is not None:
            return "DELEGATED", "C4", headline                   # C4 dispatch headline
        # C5 corroborates C2/C3/C4 only: it can never make a DELEGATED verdict on its own,
        # so it is deliberately not an exit here (fact 10 — a real user turn can name an agent).
        _ = role_declaration(head, cfg)

    # --- Retention: U1..U6 (no profile filter; a direct session in any profile is USER_FACING).
    rid = _s(_get(fact, "session_id"))
    if _user_facing(fact, head):
        if rid and rid in delegator_set:
            return "USER_FACING", "U-DELEGATOR", "async_delegations"
        return "USER_FACING", "U", None
    return "UNKNOWN", "UNKNOWN", None


# --------------------------------------------------------------------------- primary ranking

def _rank_tuple(row: Mapping[str, Any]) -> Tuple[int, int, float, str, str]:
    reason = _s(row.get("audience_reason"))
    accepted = int(row.get("accepted") or 0)
    last = row.get("last_activity_at")
    try:
        last_f = float(last) if last is not None else float("-inf")
    except (TypeError, ValueError):
        last_f = float("-inf")
    return (0 if reason == "U-DELEGATOR" else 1,
            0 if accepted else 1,
            -last_f,
            _s(row.get("profile_name")),
            _s(row.get("session_id")))


def select_primary(member_rows: Sequence[Mapping[str, Any]],
                   audiences: Sequence[str] = ("USER_FACING",)) -> Optional[Mapping[str, Any]]:
    """Deterministic primary selection over the allowed audiences (D-US-6 P1/P2).

    Ranking (total order, no tie broken by wording): ``U-DELEGATOR`` first, then
    ``accepted=1``, then ``last_activity_at`` descending, then ``profile_name`` ascending,
    then ``session_id`` ascending. Returns the winning member row, or ``None``.
    """
    wanted = set(audiences)
    pool = [r for r in member_rows if _s(r.get("audience")) in wanted]
    if not pool:
        return None
    return sorted(pool, key=_rank_tuple)[0]


# --------------------------------------------------------------------------- anchor

def anchor_token(name: Optional[str], cfg: Config) -> Optional[str]:
    """Longest ``-``/``_`` separated name component that is >= anchor_min_token_len and not a stopword."""
    min_len = int(cfg.get("anchor_min_token_len", 5) or 5)
    stops = {str(t).lower() for t in (cfg.get("stopword_tokens") or [])}
    parts = [p for p in re.split(r"[-_/\s]+", _s(name)) if p]
    cands = [p for p in parts if len(p) >= min_len and p.lower() not in stops]
    if not cands:
        return None
    cands.sort(key=lambda s: (-len(s), s))
    return cands[0]


def _word_match(token: str, text: Optional[str]) -> bool:
    if not token or not text:
        return False
    return re.search(r"(?<!\w)" + re.escape(token) + r"(?!\w)", text, re.IGNORECASE) is not None


def resolve_anchor(cluster_members: Any,
                   user_facing_rows: Sequence[Mapping[str, Any]],
                   cfg: Config,
                   *,
                   delegations: Optional[Sequence[Mapping[str, Any]]] = None
                   ) -> Tuple[Optional[str], str]:
    """Resolve a project's user-facing conversation by project-name token (D-US-6 step 5-8).

    ``cluster_members`` is a cluster/classification object exposing ``proposed_name`` (or a
    mapping with ``name``). ``user_facing_rows`` are candidate session rows (profile_name,
    session_id, title, first_turn_text, accepted, last_activity_at, audience_reason).
    ``delegations`` is a list of ``{origin_session, text}`` for the delegation-origin route.
    Returns ``(profile/session_id | None, 'ANCHOR-NAME' | 'NONE')``.
    """
    name = None
    if cluster_members is not None:
        if isinstance(cluster_members, Mapping):
            name = cluster_members.get("proposed_name") or cluster_members.get("name")
        else:
            name = getattr(cluster_members, "proposed_name", None)
    token = anchor_token(name, cfg)
    if not token:
        return None, NONE_REASON

    deleg_text: Dict[str, str] = {}
    for d in delegations or ():
        origin = _s(d.get("origin_session"))
        if origin:
            deleg_text[origin] = (deleg_text.get(origin, "") + " " + _s(d.get("text"))).strip()

    survivors: List[Tuple[Tuple[int, int, int, float, str, str], str]] = []
    for r in user_facing_rows:
        title = _s(r.get("title"))
        first = _s(r.get("first_turn_text"))
        strong = _word_match(token, title)
        weak = _word_match(token, first)
        origin_text = deleg_text.get(_s(r.get("session_id")), "")
        via_delegation = _word_match(token, origin_text)
        if not (strong or weak or via_delegation):
            continue
        strength = 0 if strong else 1
        ref = "{}/{}".format(_s(r.get("profile_name")), _s(r.get("session_id")))
        base = _rank_tuple(r)
        survivors.append(((base[0], base[1], strength, base[2], base[3], base[4]), ref))
    if not survivors:
        return None, NONE_REASON
    survivors.sort(key=lambda t: t[0])
    return survivors[0][1], "ANCHOR-NAME"


# --------------------------------------------------------------------------- invariant guard

def assert_no_resume_on_delegated(cards: Sequence[Mapping[str, Any]]) -> List[str]:
    """D-US-6 step-4 invariant: no DELEGATED/AUTOMATED member is a resume target.

    Inspects every resume affordance on every card in ``cards`` — the ``resume_links``
    entries, and any ``sessions`` entry that carries a resume command. A plain supporting
    evidence row (no copy/cli command) is not a resume target and is not a violation.
    Returns a list of violations (empty == clean).
    """
    violations: List[str] = []
    _resume_keys = ("copy_command", "copy_command_profile_scoped", "cli_resume")
    for card in cards or ():
        pid = _s(card.get("project_id")) or _s(card.get("cluster_id")) or "?"
        for key in ("resume_links", "sessions"):
            for entry in card.get(key) or ():
                if not isinstance(entry, Mapping):
                    continue
                if not any(k in entry for k in _resume_keys):
                    continue
                aud = _s(entry.get("audience"))
                if aud in ("DELEGATED", "AUTOMATED"):
                    ref = "{}/{}".format(_s(entry.get("profile") or entry.get("profile_name")),
                                         _s(entry.get("session_id")))
                    violations.append("{} {} {} audience={}".format(pid, key, ref, aud))
    return violations
