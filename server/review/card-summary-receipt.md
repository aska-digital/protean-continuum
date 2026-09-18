# Continuum — Card Summary + Vertical Space Implementation Receipt (KodeKoot)

```
task_id:      continuum-kodekoot-card-summary-implementation
owner:        kodekoot (implementation)
domain:       implementation
upstream:     handoffs/ux-continuum-card-summary.md (Shayba, DECIDED D-CS-1..D-CS-6)
authority:    OWNERSHIP-MATRIX.md — "Implementation | KodeKoot | code and tests from a
              stable approved design; efficient implementation"
date:         2026-09-13
state:        IMPLEMENTED — awaiting Halakukhan independent QA gate
interpreter:  /Users/kethuda/.hermes/hermes-agent/venv/bin/python (3.11.15, fastapi 0.133.1);
              node v22.17.1
```

## 1. Outcome

The standalone browser board cards are taller and now state, in grounded terms, what the
project/session is about and where it left off:

- the state row is labelled `State: ` (was the semantically wrong `Waiting on: `);
- an `ABOUT THIS` block renders **only** when `card.summary_text` is a non-empty field value;
- an always-present `LEFT OFF` block renders exactly one of three grounded tiers —
  `LEFT OFF · DECLARED` (`card.next_action.text`, verbatim) → `LEFT OFF · DERIVED` (primary
  session title, verbatim) → the literal `Nothing recorded yet.`;
- cards gain a hairline-separated section rhythm and hard line clamps so eight lanes stay
  scannable.

Renderer-only: no service, schema, registry, scanner, `plugin_api.py`, `standalone.py`,
`index.html`, fixture, data, Raptora, or installed-tree file was touched.

## 2. Files

| file | change | lines | sha256 (prefix) |
|------|--------|-------|-----------------|
| `build/dashboard/static/app.js` | `renderCard` + left-off helpers only | 773 → **816** | `60be9606` |
| `build/dashboard/static/styles.css` | card rhythm/clamp rules | 238 → **257** | `403b91f9` |
| `build/tests/test_standalone.py` | +14 assertion-only tests (§9) | 344 → **498** | `71a7fa36` |
| `build/review/card-summary-receipt.md` | this file | — | — |

`build/data/registry.db` is byte-unchanged across the whole run (sha256 `94787bcc…`,
mtime `Sep 12 18:02`; live server issued GETs only). Forbidden files untouched.

## 3. App.js (renderCard) — what changed, exactly

New pure helpers immediately above `renderCard` (same file, same module):

```js
function primarySessionFor(card)            // first sessions[] with role === 'primary', else sessions[0]
function sessionTitleText(session)          // title verbatim, or 'untitled · ' + session_id.slice(0, 12)
function leftOffFor(card)                   // → { tier: 'declared'|'derived'|'none', text, session }
```

Tier priority in `leftOffFor`: `card.next_action && card.next_action.text` → declared;
else `sessions.length > 0 && session_count !== 0` → derived (role-aware selection);
else → `none`, text `'Nothing recorded yet.'`. No branch composes, paraphrases, or invents
prose; every value is a byte-for-byte field value.

`renderCard` body:

1. Identity row — **unchanged** (name button, Accepted/Candidate, Needs review, Urgent,
   provenance chip).
2. State row — prefix changed to `'State: '`; lifecycle name, age and the existing
   `· derived: X` suffix unchanged.
3. `if (card.summary_text) { <div.c-block><div.c-block-label>ABOUT THIS</div><div.c-summary>…</div></div> }`
   — the block is absent (whole block) when the field is absent.
4. LEFT OFF — one of:
   - declared: `.c-block` → `.c-block-label` (`LEFT OFF · DECLARED` + `chip-stale 'stale'`
     iff `card.declared_stale`) + `.c-leftoff` with `next_action.text` and `title=` tooltip;
   - derived: `.c-block` → `.c-block-label` (`LEFT OFF · DERIVED`) + `.c-session` sub-row
     [ `.c-link` = primary session title (tooltip + clamp) , `<code>profile/session_id</code> ,
     Copy ID button , `+N more` when `sessions_omitted > 0` ];
   - none: a single muted `.c-meta` line `Nothing recorded yet.`, no block label.
5. Action row — unchanged (Move to… / Mark urgent). Detail view (`renderDetail`) — unchanged.

Card `aria-label` (`cardAria`) is extended with the left-off tier text so screen readers get
the same left-off value as sighted users.

Byte-for-byte note: the derived-tier link value renders the session title **exactly** (the
former ` ▸` affordance was dropped from the value text so the value is the title and nothing
else). No whitespace is appended.

## 4. styles.css — vertical spacing contract

| rule | delivered |
|------|-----------|
| `.c-card` | `padding: 12px; margin-bottom: 12px;` |
| `.c-block` (new) | `margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--line);` |
| `.c-row + .c-row` | `margin-top: 6px;` |
| `.c-block-label` (new) | `font-size: 10px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: var(--dim);` (+ flex/gap to seat the `stale` chip) |
| `.c-summary` clamp | `-webkit-line-clamp: 3` |
| `.c-leftoff` (new) clamp | `-webkit-line-clamp: 2` |
| `.c-link` session title | `flex: 1; min-width: 0;` + `-webkit-line-clamp: 2` |
| lane | `--lane-min: 260px`, `.c-lane { min-height: 220px }` — unchanged |

`.c-chip.chip-stale` added (amber, matching the existing `chip-needs` vocabulary). No new
DOM id, lane, mutation, chip category beyond the specified `stale` chip, or detail change.

## 5. Tests added (`test_standalone.py` §9, standalone suite 22 → 36)

Assertion-only, served/static: the state-row label; the four contract labels
(`ABOUT THIS`, `LEFT OFF · DECLARED`, `LEFT OFF · DERIVED`, `Nothing recorded yet.`); the
`ABOUT THIS` iff `summary_text` guard (and no placeholder substitution); role-aware primary
selection with `sessions[0]` fallback; the `untitled · <12>` fallback pattern; the
conditional `stale` chip; the `sessions_omitted` `+N more` suffix; the aria extension; a
no-narrative-prose scan over the card render region's string literals; a no-backend-surface
check (fetch count 6, POST count 3, no `/evidence` route, unchanged card DOM id); the CSS
rhythm/clamp rules and the preserved lane dimensions; and served `/app.js` + `/styles.css`
byte-equality with the build files carrying the labels.

## 6. Gates (measured)

| gate | result |
|------|--------|
| Full Python suite | **187 passed** |
| Standalone suite (`test_standalone.py`) | **36 passed** (was 22) |
| Node behavioral (`test_kanban_helpers.mjs`) | **75 passed** |
| Shipped paths (`test_kanban_shipped_paths.mjs`) | **17 passed** |
| Structural/parity + drift (`mutation_evidence.js`) | **30 passed, 0 missed; 12/12 drift cases caught** |
| `node --check` all JS/mjs | exit 0 (app.js, kanban-interaction.js, plugin.js, kanban_helpers.js, mutation_evidence.js, both `.mjs`) |
| Plugin validation | `hermes plugins validate build` → **Validation passed** |

### 6.1 Served-vs-build bytes (live server, 127.0.0.1:8765)

```
GET /app.js     -> 200 text/javascript; sha256 60be9606 == build/dashboard/static/app.js 60be9606
GET /styles.css -> 200 text/css;         sha256 403b91f9 == build/dashboard/static/styles.css 403b91f9
```

Served bytes are byte-identical to the build files. Served `app.js` contains `ABOUT THIS`,
`LEFT OFF · DECLARED`, `LEFT OFF · DERIVED`, `Nothing recorded yet.`, `State: `, `chip-stale`;
served `styles.css` contains `padding: 12px`, `margin-bottom: 12px`, `margin-top: 8px`,
`border-top: 1px solid var(--line)`, `margin-top: 6px`, `-webkit-line-clamp: 3`,
`-webkit-line-clamp: 2`, `--lane-min: 260px`, `min-height: 220px`.

### 6.2 Rendered-tree verification (real `renderCard`, minimal DOM shim, Node)

The browser driver available in this session refuses private/loopback addresses, so the
rendered card tree was verified by importing the **actual** `app.js` (verification-only copy
in `/tmp`, import path repointed) under a ~30-line DOM shim and asserting on the produced
element tree. 23/23 synthetic cases pass (each tier, role-aware selection, fallback title,
`+N more`, About guard, honesty, aria, state label).

Then the **live payload** (`GET /api/plugins/continuum/projects`, 13 items) was rendered
through the same harness:

```
live items: 13
  all 13 render tier "derived" (next_action null on all; summary_text empty on all)
  omitted>0 on 2 items (40-session cluster -> +20 more); all 13 pass
Results: 13 passed, 0 failed
```

Each live card: label `LEFT OFF · DERIVED`, link value == the role-`primary` session title
(byte-for-byte), tooltip == full title, sub-row code == `profile/session_id`, `+N more` present
iff `sessions_omitted > 0`, About absent (no `summary_text`), and no *renderer-introduced*
`next`/`should`/`plan`/`must` prose. (One live session title legitimately contains the word
"plan" — it is shown verbatim as the contract requires; the renderer introduces nothing.)

## 7. Honesty invariants — status

1. Null `next_action` + sessions ⇒ only the two labels, no `next`/`should`/`plan`/imperative
   prose introduced. **Verified** (static literal scan + live-render diff excluding data).
2. About block present ⟺ `summary_text` non-empty; no placeholder. **Verified.**
3. Every LEFT OFF value is byte-for-byte `next_action.text` or the session title, plus its
   label. **Verified** (rendered-tree equality).
4. `Nothing recorded yet.` ⟺ no sessions / `session_count === 0`. **Verified.**
5. No new fetch, DOM id, lane, mutation, chip category, or detail-view change. **Verified**
   (fetch 6 / POST 3 unchanged; `renderDetail` untouched).

## 8. Flag for the architect/reviewer — tier-3 copy wording divergence

The dispatch brief's "Exact UX contract" (§4) and honesty invariants specify the tier-3 line
as the literal **`Nothing recorded yet.`**; upstream handoff D-CS-2 (`ux-continuum-card-summary.md`
line 44) specifies **`No sessions linked yet.`** for the same state. The two strings conflict.

Implemented: the dispatch brief's string, **`Nothing recorded yet.`** (it is the operative
spec and restates the wording twice). If the handoff's `No sessions linked yet.` is the
authoritative copy, this is a one-token change in `app.js` (`leftOffFor` return) plus the
matching string in `test_standalone.py`. Flagging rather than guessing — the conflict is
between two upstream artifacts, not an implementation choice.

Separately, the Copy ID button keeps using the shared `copyIdValue(card)` helper (bare
`session_id`, label `Copy ID`) as the single source of truth; `desktop/kanban-interaction.js`
is outside this task's allowed files, so it was not modified. The displayed `<code>` and the
link value use the role-aware primary session, which for the current feed is `sessions[0]`.

## 9. No install / enable / deploy / publication / readiness claim

Implementation receipt only. No install, enablement, deployment, publication, PR, or readiness
claim is made or authorized. Halakukhan's independent gate is the next step.
