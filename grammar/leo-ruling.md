# Continuum TASK-HOME Key-Grammar Ruling

STATUS: STABLE
owner: Leo (stage 2, architecture)
domain: architecture (OWNERSHIP-MATRIX.md read at dispatch: yes)
task: continuum-grammar (leo-ruling)
upstream_approval: continuum-rebind BLOCKED receipt (0/32 mapping gate, registry 812008f2…, source b5cee132… intact)
  + locked continuum-sync/leo-architecture.md (STABLE, TH-L1..TH-L16)
  + Orda goal (grammar ruling)

files_read:
  - /Users/kethuda/.hermes/profiles/orda/TASK-HOME.md (sha256 b5cee132…, 66 bullets, 0 keyed)
  - /Users/kethuda/.hermes/profiles/proteus/cache/delegation/continuum-rebind/mozi-rebind-receipt.md (§2 R1–R6, §6 findings)
  - /Users/kethuda/.hermes/profiles/proteus/cache/delegation/continuum-sync/leo-architecture.md (STABLE, v1 contract)
  - /Users/kethuda/.hermes/profiles/proteus/cache/session-project-indexer/build/continuum/task_home.py (L148 regex, L202 valid_key)

files_written:
  - /Users/kethuda/.hermes/profiles/proteus/cache/delegation/continuum-grammar/leo-ruling.md (this file)
  - /Users/kethuda/.hermes/profiles/proteus/cache/delegation/continuum-grammar/leo-receipt.md

---

## 1. Verdict

(b) AMEND GRAMMAR — versioned amendment to the continuum-sync contract accepting `- slug: text` as keyed with key=slug, proc optional. (a) RE-KEY BOARD is REJECTED.

---

## 2. Reasons (stability vs parser authority)

1. TH-L3 already settles the primary-key question. The locked contract's own rationale column reads
   "proc ids rotate and are absent on 23/34 bullets" — the architecture itself declared slug-as-primary
   when it locked TH-L3. The current parser regex `_KEYED_RE` at line 148 of task_home.py requires
   `slug (proc_hex):` for keyed recognition, which contradicts TH-L3's law that slug alone IS the
   identity key and proc is secondary. Option (b) is not a concession to the board; it is a
   consistency correction toward the locked §2/§4 law.

2. Option (a) inverts TH-L3. Re-keying all 66 bullets to `- slug (proc_hex): text` makes proc a
   REQUIRED syntactic element of the primary key line. That is architecturally backwards: the contract
   explicitly designed proc as secondary because it rotates. Minting 49-57 fabricated hex tokens for
   bullets that have no real proc creates K2 pollution (data that looks real but is invented), and
   every subsequent relane by Orda forces a board re-edit to update the rotating token — exactly the
   recurring churn pattern that produced this incident.

3. Board stability wins over parser purity. The board's own header already declares "Every bullet
   carries a stable slug key (required by the Continuum sync identity contract)." The slugs ARE the
   stable keys. Forcing 66 bullets through a regex that the contract's own law says is unnecessary
   produces zero benefit and recurring cost. The parser serves the contract; the contract does not
   serve the parser.

4. The fallback order (§4) is unchanged. Slug-only bullets already satisfy step 1 of the binding
   fallback order (exact `slug ==` project name). They do not need proc to be keyed. The unnamed
   fallback (TH-L4) remains for genuinely keyless bullets — bullets without a slug at all. Option
   (b) removes the false "keyless" classification of 66 real-slug bullets without touching TH-L4's
   purpose for actual keyless lines.

5. Dual-notation cost is negligible. Accepting both `- slug (proc_hex):` and `- slug: text` as
   keyed adds one regex branch. The existing `_KEYED_RE` continues to match the proc-bearing form
   for backward compatibility; a new or modified rule handles the slug-only form. No new syntax
   marker is needed; no OPEN-4 `[key: <slug>]` adoption is required for this fix.

---

## 3. Exact grammar spec

### Current rule (task_home.py line 148, to be preserved for backward compat):

```python
_KEYED_RE = re.compile(r"^(?P<slug>[^\s(]+)\s*\((?P<proc>proc_[0-9a-fA-F]+)\)\s*:")
```

A bullet is keyed ONLY in `- slug (proc_hex): text` form.

### New rule (amendment, additive — _KEYED_RE unchanged, new _SLUG_ONLY_RE added):

```python
_SLUG_ONLY_RE = re.compile(r"^(?P<slug>[^\s(:][^\s(]*[^\s(]|[^\s(:])\s*:")
```

This matches `- slug: text` where slug contains no whitespace, `(`, `)`, or `:` — the same
character class as `_KEY_RE` (`^[^\s():]+$`), anchored at the start and terminated by `:`.

### Parse logic change (task_home.py, inside the bullet-processing block, around lines 385-397):

Replace the current `if m is not None: ... else:` block with:

```python
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
```

A bullet with a valid slug (no whitespace/parens/colon) followed by `:` is now keyed with
key=slug and proc=None. Only bullets that match NEITHER `_KEYED_RE` nor `_SLUG_ONLY_RE` fall
to the unnamed fallback.

### What stays valid:

| Form | Result | Example |
|---|---|---|
| `- slug (proc_hex): text` | keyed, key=slug, proc=proc_hex | `- evopet-pointer-fix (proc_5347043a30e4): overlay fix` |
| `- slug: text` | keyed, key=slug, proc=None | `- continuum-apply-3: proc_10922f9410b9 (server live...)` |
| `- no-slug-line without colon` | keyless, key=unnamed:<section>:<ordinal> | any prose line with no slug |
| `- malformed` (no space after dash) | keyless + malformed_bullet warning | `-malformed` |
| `[key: <slug>]` marker | still warns `unknown_key_marker` (OPEN-4 not adopted) | future option |

### valid_key() (line ~202): NO CHANGE needed — already accepts a literal slug via `KEY_RE`.

---

## 4. Contract amendment text + version

### Version bump

The continuum-sync contract (`leo-architecture.md`) version goes from v1 to v2.

### Amendment

Add to §4 Identity keys, after the K1–K4 table:

> **v2 amendment (2026-09-17, continuum-grammar ruling):**
> The parser recognizes two keyed forms:
> 1. `- slug (proc_hex): text` — keyed, key=slug, proc=proc_hex (original v1 form)
> 2. `- slug: text` — keyed, key=slug, proc=None (v2 addition)
>
> Both are keyed; neither falls to the TH-L4 unnamed fallback. A slug-only bullet carries
> proc=None and participates in the §4 binding fallback order starting at step 1 (exact slug match).
> The unnamed fallback remains for bullets that have no slug at all.
>
> TH-L3 is unchanged: slug is the primary identity key; proc is a secondary cross-check.

Add to §9 OPEN:

> **CLOSED-4:** A stable key marker on keyless bullets. Superseded by the v2 grammar amendment:
> `- slug: text` is now keyed directly; no marker syntax is needed for bullets that already carry a
> slug. OPEN-4 remains open for genuinely keyless bullets (those without any slug) if a future
> decision wants to adopt `[key: <slug>]` for them.

---

## 5. Disposition of 32 orphaned human binds

**DISSOLVE** via the audited unbind route (TH-L9).

Reasons:

1. No lineage exists. Mozi's receipt (§2 R1–R6) proves the 32 old keys were forward-looking
   proposals never present in any TASK-HOME version. The current bullets are 2026-09-17 task
   lanes. There is no 1:1 mapping to discover.

2. Rotting absent_from_source rows serve no one. Keeping them means every sync pass carries 32
   dead `absent_from_source` items in the ledger and wire, polluting the counts and confusing
   anyone reading the dashboard.

3. The undo route is proven. Mozi's receipt records the 33rd bind+undo pair as an audited test.
   TH-L9 provides audit + undo. The dissolution is a deliberate, reversible action.

4. Future binds are a clean import pass. After dissolution, the first sync run under the v2
   grammar will parse all 66 bullets as keyed (0 unnamed), and the §4 fallback order will attempt
   binding. New human binds can be made deliberately through `POST /projects/{id}/review` with the
   actual current slug keys.

Human-owned fields (urgent ×2, placement, lifecycle_override, accepted, project_name, dismissed,
merged_into) are OUT OF BOUNDS — dissolution touches only the 32 `task_home_binding=human` +
`task_home_key=<old-key>` rows, not any other declared_field on those projects.

---

## 6. Zero-unnamed proof definition

A sync pass achieves "zero-unnamed" when the ledger report shows:

- `item_count`: 66 (all bullets parsed)
- `unnamed keys`: 0 / 66 (no item has key starting with `unnamed:`)
- `keyed`: 66 / 66 (every item has a literal slug key)
- `conflicts`: [] (empty)
- `expected exit code`: 0 (EXIT_OK)

**Acceptable degraded exit (exit code 5)**: ONLY if pre-existing W-1-class warnings are present
that do NOT affect keying:

| Warning | Class | Meaning | Acceptable in zero-unnamed proof |
|---|---|---|---|
| `compound_bullet` | INFORMATIONAL | bullet bundles multiple asks with `+` | YES — informational, not degraded |
| `heading_unrecognized` | DEGRADED | section heading not in `SECTION_HEADING_KEYS` (e.g. `## PAUSED`) | YES — pre-existing, documented in Mozi receipt §4 |
| `receipt_section_mismatch` | INFORMATIONAL | RUNNING bullet claims CLOSED receipt | YES — informational |

Warnings that would BREAK the zero-unnamed proof (none should appear after v2 amendment):

| Warning | Why it breaks proof |
|---|---|
| `duplicate_slug` | two bullets share a slug → identity collision |
| `unsectioned_bullet` | bullet outside any section → unparseable |
| `malformed_bullet` | no `- ` prefix → unparseable |

The recommended verification: run the sync pass, confirm the ledger `items` array has 66 entries,
none with key matching `^unnamed:`, and exit code ∈ {0, 5} with only the acceptable warnings above.

---

## 7. Migration order + rollback

### Migration (Mozi implements)

1. **Contract amendment**: Update `leo-architecture.md` to v2 per §4 above. This is a documentation
   change to the contract artifact; no code change yet.

2. **Parser code change**: Modify `task_home.py` per §3 — add `_SLUG_ONLY_RE`, modify the
   bullet-processing block. No other files change in this step.

3. **Test update**: Add test cases to the existing test suite for:
   - `- slug: text` → keyed, key=slug, proc=None
   - `- slug (proc_hex): text` → keyed (existing behavior preserved)
   - mixed file with both forms → correct keyed count
   - `valid_key()` still accepts bare slugs (no change)
   Do NOT modify existing tests; add new test functions.

4. **Full test suite run**: 369 existing + new tests all green.

5. **Sync pass on live TASK-HOME**: Run `python -m continuum.cli task-home sync --dry-run` against
   the current `b5cee132…` source. Confirm zero-unnamed proof per §6.

6. **Orphan dissolution**: Via `POST /projects/{id}/review` with `action=unbind_task_home` for each
   of the 32 projects. This uses the existing TH-L9 audited route. Record the 32 unbind events in
   the review_event table.

7. **Full sync pass (non-dry-run)**: Run against live TASK-HOME. Confirm 66 keyed items, 0 unnamed,
   exit 0 or 5 with only acceptable warnings.

### Rollback

- **Parser rollback**: Revert `task_home.py` to pre-amendment state. The `_KEYED_RE` regex is
  unchanged in both versions, so the only rollback target is the new `_SLUG_ONLY_RE` branch.
  Existing tests continue to pass (they test v1 behavior which is a subset of v2).

- **Contract rollback**: Revert `leo-architecture.md` version from v2 to v1. This is a documentation
  artifact only; no runtime state depends on the version number.

- **Orphan dissolution rollback**: The TH-L9 `undo` route reverses each unbind. The 32 projects
  can be re-bound to their old keys via `POST /projects/{id}/review` with `action=bind_task_home`.
  However, since the old keys have no source match, re-binding them restores the absent_from_source
  state — only useful if a future decision creates a mapping.

---

## 8. Acceptance criteria for Mozi

| ID | Criterion | Verification |
|---|---|---|
| TH-GA1 | `_SLUG_ONLY_RE` matches `- evopet-pointer-fix: overlay fix VERIFIED live` | test: key="evopet-pointer-fix", proc=None, keyless=False |
| TH-GA2 | `_KEYED_RE` still matches `- evopet-pointer-fix (proc_5347043a30e4): overlay fix` | test: key="evopet-pointer-fix", proc="proc_5347043a30e4" |
| TH-GA3 | Mixed file: 18 proc-bearing + 48 slug-only = 66 keyed, 0 keyless | parse test against frozen fixture |
| TH-GA4 | `valid_key("evopet-pointer-fix")` returns True | unit test (already passes, verify no regression) |
| TH-GA5 | `valid_key("unnamed:RUNNING:1")` returns True | unit test (already passes, verify no regression) |
| TH-GA6 | Existing 369 tests pass with zero failures | full suite run |
| TH-GA7 | New tests for slug-only form: at least 4 new test functions | code review |
| TH-GA8 | Dry-run sync on `b5cee132…`: exit 0 or 5, items 66, unnamed 0, keyed 66 | CLI dry-run output |
| TH-GA9 | Non-dry-run sync after orphan dissolution: exit 0 or 5, items 66, unnamed 0, bound ≥ 0 | CLI output + ledger |
| TH-GA10 | `_KEYED_RE` regex unchanged from v1 (byte-identical) | code review |
| TH-GA11 | `_PROC_SHAPED_RE` still triggers `missing_proc` for `(proc_invalid)` form | test |
| TH-GA12 | `[key: slug]` marker still warns `unknown_key_marker` | test |
| TH-GA13 | Wrapped/continuation lines still warn `wrapped_line` | test |
| TH-GA14 | CRLF file still normalizes and warns `crlf_normalized` | test |
| TH-GA15 | 32 orphan dissolution via TH-L9: review_event count increases by 32, all with `unbind_task_home` | registry query |

---

## 9. G-8 gate note

This ruling is option (b) — a LOCKED contract change. Per the control-plane §A major-decision
rule:

- **G-8 HTML decision report (Frida) MUST exist and be linked in this delegation directory
  BEFORE the parser code change lands.** The report names this ruling, the v2 amendment, and
  the migration order. Frida produces it; Mozi waits for it.

- If Proteus determines the grammar amendment is minor enough to waive G-8 (the change is a
  one-regex additive branch with no wire/schema/DDL impact), Proteus records the waiver in the
  dispatch ledger. The architecture ruling itself is LOCKED either way.

---

## 10. Ownership receipt

```
ownership_matrix: /Users/kethuda/.hermes/team-skills/ops/OWNERSHIP-MATRIX.md
matrix_read_at_dispatch: yes
owner: leo
domain: architecture
upstream_approval: continuum-rebind BLOCKED receipt + Orda goal (grammar ruling)
```

LOCKED