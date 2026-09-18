# leo-receipt — Continuum TASK-HOME key-grammar ruling

VERDICT: (b) AMEND GRAMMAR. Versioned amendment to continuum-sync contract; `_SLUG_ONLY_RE`
accepts `- slug: text` as keyed with key=slug, proc optional. No board re-key. 32 orphans
dissolved via TH-L9 audited unbind. G-8 gate required before parser change lands.

## 0. Declarations
- role: stage 2 architecture; owner: leo
- matrix_read_at_dispatch: yes (OWNERSHIP-MATRIX.md Protean-era row "architecture | leo (2)")
- allowed_decisions exercised: grammar ruling, exact regex spec, contract v2 amendment text,
  orphan disposition, zero-unnamed proof definition, migration/rollback order, acceptance criteria
- forbidden_decisions honoured: no implementation, no TASK-HOME writes, no registry writes,
  no config edits, no ops rows beyond inflight claim + session-id fill + dispatch ledger fill

## 1. Files read
- `/Users/kethuda/.hermes/profiles/orda/TASK-HOME.md` — sha256 `b5cee132…`, 66 bullets,
  ALL in `- slug: text` form, 0 keyed bullets to current parser. Lines 8-24 (RUNNING),
  27-38 (AWAITING OWNER), 40-42 (PAUSED), 44-51 (OPEN FOLLOW-UPS), 53-81 (CLOSED).
  18 RUNNING bullets (9 with proc in body text, 9 without), 13 AWAITING OWNER, 2 PAUSED,
  7 OPEN FOLLOW-UPS, 27 CLOSED. Slugs appear unique (no duplicate_slug warning observed).
- `/Users/kethuda/.hermes/profiles/proteus/cache/delegation/continuum-rebind/mozi-rebind-receipt.md` —
  §2 (R1–R6 route failure, 0/32 mapping gate), §6 (findings). Registry sha256 `812008f2…`,
  source sha256 `b5cee132…` both intact. 32 human binds, zero source matches. The 33rd
  bind+undo pair on `1d0c7aca…` (TamaHermes) was the audited undo test.
- `/Users/kethuda/.hermes/profiles/proteus/cache/delegation/continuum-sync/leo-architecture.md` —
  STABLE v1 contract. TH-L3 (slug primary, proc secondary), §4 key table (K1–K4),
  TH-L4 (unnamed fallback), TH-L9 (bind/unbind via review route), §9 OPEN-4 (key marker,
  not adopted). Line 53: "proc ids rotate and are absent on 23/34 bullets".
- `/Users/kethuda/.hermes/profiles/proteus/cache/session-project-indexer/build/continuum/task_home.py` —
  L148: `_KEYED_RE = re.compile(r"^(?P<slug>[^\s(]+)\s*\((?P<proc>proc_[0-9a-fA-F]+)\)\s*:")`.
  L149: `_PROC_SHAPED_RE`. L153: `_KEY_RE = re.compile(r"^[^\s():]+$")`.
  L202-215: `valid_key()` accepts literal slug or unnamed form. L385-397: bullet processing
  block that falls to unnamed when `_KEYED_RE` does not match.

## 2. Analysis

The core tension: the locked contract (TH-L3) says slug is the primary key and proc is secondary.
The parser (L148) requires proc for keyed recognition. The board (TASK-HOME) has 66 bullets with
slugs but 0 in `slug (proc_hex):` form. The result: all 66 bullets get unnamed fallback keys,
making them unbindable by slug.

This is a parser-vs-contract mismatch, not a board problem. The contract's own law already says
slug alone IS the key. The parser is stricter than the contract requires. Option (b) brings the
parser into alignment with the locked contract.

Option (a) would make the board carry fabricated proc tokens on ~49-57 bullets, inverting TH-L3's
design rationale. It would also create recurring churn: every relane forces a board re-edit to
update the rotating proc token.

## 3. Ruling

(b) AMEND GRAMMAR. See `leo-ruling.md` for the full ruling, exact regex spec, contract amendment,
orphan disposition, zero-unnamed proof, migration/rollback, acceptance criteria, and G-8 gate note.

## 4. Files written
- `/Users/kethuda/.hermes/profiles/proteus/cache/delegation/continuum-grammar/leo-ruling.md` —
  the architecture ruling (this is the deliverable)
- `/Users/kethuda/.hermes/profiles/proteus/cache/delegation/continuum-grammar/leo-receipt.md` —
  this receipt

## 5. Ops claims
- `ops/INFLIGHT.md`: claim row appended for this session, released on exit
- `ops/DISPATCH-LEDGER.md`: session id filled in `continuum-grammar-leo-01` row (no new rows added)

## 6. Model receipt
- profile: leo
- config: nous / qwen/qwen3-coder-next (profiles/leo/config.yaml)
- actual session model: to be captured from state.db after run
- needs-decision: none identified this pass

STABLE