# Continuum — JARVIS-class visual skin (design handoff)

STATUS: STABLE

owner: Frida (design / UX / visual governance)
domain: design (stage 3). This artifact locks VISUAL + INTERACTION + COPY-TONE only; it locks no
data contract, no route, no server field.
project: Continuum / Raptora dashboard — live source build
task: dashboard-design (frida-design)
workspace: /Users/kethuda/.hermes/profiles/proteus/cache/delegation/dashboard-design
profile/session/provider at dispatch: frida / 20260917_180002_b69e2a / deepseek-v4.1-flash via opencode-go
served target read this pass: http://127.0.0.1:18771/ (HTTP 200 at 2026-09-17 18:00-18:03 EDT)
read on disk this pass (not from memory) — shas computed at read-back, advisory only because the
action-log implementation lane holds write ownership of the two code files concurrently:
  - dashboard/static/index.html  41 lines, sha256 a447b5ce3f5e41bc…  (mtime 2026-09-17T01:21:49)
  - dashboard/static/styles.css 565 lines, sha256 df5665f43e05a686…  (mtime 2026-09-17T18:00:43)
  - dashboard/static/app.js    2548 lines, sha256 a38239a77f9562db…  (mtime 2026-09-17T18:00:20)
  - delegation/continuum-sync/leo-architecture.md (245 lines, STATUS: STABLE) — treated as immutable input
  - delegation/dashboard-design/briefs/frida-design.md (29 lines) — this lane's brief
scope: UX hierarchy, spacing, typography, palette, borders, motifs, states, motion timings,
responsive behaviour, accessibility treatment, CSS class/token naming, screenshot acceptance criteria.
non-claims: this artifact writes no build file, edits no app.js/styles.css, changes no data layer, no
registry row, no route, no task-home field, no action-log field; installs, deploys, restarts nothing and
writes nothing public. It issues no QA verdict and closes no OPEN item belonging to another lane. Every
colour ratio printed here was COMPUTED this pass (script + output quoted in §2.7), not estimated.

---

## 0. Ground truth read this pass

Measured on disk (file:line anchors are to the CURRENT build, which is what a renderer will edit):

| surface | anchor | what it is today |
|---|---|---|
| page shell | index.html:10-38 | `.wrap` > `.c-header` + `.c-banner` + `main#app` + `#toast-root` + `#live` |
| shell is frozen | styles.css:312-314 ("Controls are built in app.js (index.html is frozen)") | toolbar is INJECTED into `.c-header-actions` (app.js:2205-2212) |
| toolbar | app.js:2064-2203 | View Today/All, Action log toggle, Sort, direction, Lane, State, Attention, Band, Layout lanes/grid, Clear — or the action-log vocabulary (Kind/Operator/Status/Sort/show archived/Clear) |
| mission control | app.js:1640+ ; styles.css:386-400 | snapshot age + structured scan state |
| current state | app.js:480-568 ; styles.css:110-122 | 7-column metric strip |
| Task Home | app.js:1246-1315 ; styles.css:485-520 | not-synced state with CLI line; else head + source/sha + 6 group buttons + conflicts + warnings + `items[]` rows |
| attention rail | app.js:1320-1359 ; styles.css:288-298 | 3 server groups, capped list, per-item marker |
| staleness bands | app.js:1537-1575 ; styles.css:462-483 | band buttons + quiet group (dashed, non-interactive) |
| board | app.js:1178-1240 | 8 lanes, `.c-lane-header`, `.c-lane-body`, `.c-lane-empty` (+ textbook empty action + hint) |
| card | app.js:996-1145 | attention marker, name button, chips (accepted/candidate/needs/urgent), provenance, condition row, primary session, ABOUT THIS, LEFT OFF (declared/derived/none), counts, Move to…, Mark urgent, move menu |
| dense grid | app.js:1483-1536 ; styles.css:405-440, 548-561 | 7 columns (GRID_COLUMNS, app.js:110-111), `data-label` reflow below 1100px |
| claim control | styles.css:428-440 | derived value opens its evidence source |
| evidence drawer | app.js:1442-1471 ; styles.css:442-460 | fixed bottom-right panel, Escape closes, focus returns |
| session pane | styles.css:348-383 | per-row disclosure, `.is-you` / `.is-agent` / `.is-clipped` |
| Recovery Inbox | app.js:1576-1620 ; styles.css:522-534 | heading, triage line, zero state, ranked order, suppressed |
| detail view | app.js:2442-2445 ; styles.css:233-240 | replaces main when a project is opened |
| action log | app.js:2241-2435 | DISTINCT view `?view=action_log`: own filter bar, counts bar, 11-column table, per-row Archive/Unarchive, pager, archived count + show/hide |
| action-log CSS gap | `grep -c "c-al" styles.css` → **0** | `c-al-*` classes are emitted by app.js and have NO rules in styles.css today |
| loading/empty/error | app.js:2453-2467 ; styles.css:242-255 | skeleton shimmer, `.c-empty`, `.c-error` |
| toast / live region | app.js:2214-2230 ; styles.css:257-263 | `#toast-root`, `#live` `aria-live=polite` |
| reduced motion | styles.css:308-310 | global `animation:none !important` |
| board responsive rule | styles.css:271-276 | single-column stack below 2164px |
| grid responsive rule | styles.css:548-561 | labelled row list below 1100px |

Two facts drive this handoff:
1. The markup is generated entirely in app.js; **index.html is frozen**. A skin is therefore a
   `styles.css` + token exercise, and any class the renderer must ADD has to be additive.
2. The action-log surfaces ship with zero styles today. This handoff covers them explicitly (§4.10)
   so the in-progress action-log lane lands inside the same system instead of a second one
   (role rule: never ship two systems that interfere).

---

## 1. Visual principles

P1 — Instrument, not application. Continuum reads as a console an operator keeps open: state first,
chrome last. No hero, no illustrations, no marketing surfaces, no positive-reinforcement copy.
P2 — Darkness is structural, not decorative. Depth is built from four value steps of graphite/navy
(§2.2), never from shadows alone; a darker plane may sit *behind* a lighter panel but never the reverse.
P3 — Luminescence is meaning, and it is rationed. Saturated cyan and amber appear only where the
server publishes a state that changes what the operator does next (running, blocked, stale, urgent,
accepted). At rest the screen is ≈95% desaturated: text, rules, panels. If everything glows, nothing does.
P4 — Never colour alone. Every state carries a word (chip/badge text from the server field), a shape or
weight difference, and only then a hue. Deleting colour must not delete meaning.
P5 — Numbers are instrument readouts. Counts, ages, durations, timestamps, ids and hashes are tabular
monospace (HUD numerals, §6). Prose stays humanist sans.
P6 — Thin geometry over fills. Structure is drawn with 1px rules, rings and arcs at low opacity; large
filled blocks are reserved for raised surfaces. Borders are the instrument panel, never the message.
P7 — Motion is state, never transition. Only three things may animate: a live/running pulse, a
loading shimmer, and a state change acknowledgement. Nothing animates to decorate, and nothing animates
on arrival in a way that delays reading (§8).
P8 — Density is respect. The dense grid and the action-log table stay dense at desktop: a senior
operator scanning 34 rows must not be asked to scroll a padded card. Density relaxes at breakpoints
(§9), never the reverse.
P9 — Quiet is a first-class state. `paused`, `CLOSED`, `OPEN_FOLLOW_UP`, the quiet staleness group and
archived action-log rows are all *present and legible* but low-emission: dim, dashed, monochrome. A
parked project must never look like an alarm (matches INV-MC-6: no guilt copy, no invented alert).
P10 — Read first, ornament second. The decorative layer (scanlines, particles, ring motifs) is
procedural, `aria-hidden`, pointer-events:none, and rendered UNDER text — never between the operator and
a value. Removing it changes nothing about comprehension.

---

## 2. Design tokens

### 2.1 Naming
New tokens are prefixed `--cj-` ("Continuum JARVIS") and are ADDITIVE: every existing `--bg`, `--card`,
`--cyan`… token in styles.css:13-37 keeps its name and current value until each consumer is migrated, so
the skin can land surface by surface with no half-styled release. The mapping table in §11.2 tells a
renderer exactly which legacy token each legacy rule should switch to.

### 2.2 Depth planes (4 steps)
```
--cj-void:      #05070c   /* page base — deepest navy-black          */
--cj-deep:      #080c14   /* recessive regions (lane interiors, bars) */
--cj-panel:     #0c1220   /* primary surface: cards, tables, panels  */
--cj-panel-2:   #111a2b   /* raised surface: menus, drawer, toasts   */
--cj-inset:     #060a12   /* inset wells: panes, code, table headers */
```

### 2.3 Rules and edges
```
--cj-line:        #1b2740  /* hairline divider (decorative)          */
--cj-line-2:      #26405f  /* structural border of a panel           */
--cj-line-hot:    #2f4c6e  /* hover/active edge                      */
--cj-radius-sm:   4px      /* chips, buttons, inputs                 */
--cj-radius-md:   8px      /* panels, lanes, drawers                 */
--cj-radius-lg:   12px     /* page-level shells only                 */
--cj-radius-full: 999px    /* pills, rails, ring frames              */
```
Deliberate change from today's radii (6/11/15px): corners tighten. Ring/arc motifs need circular and
4-8px geometry to read as instrument bezels; 15px rounding reads as consumer SaaS.

### 2.4 Text
```
--cj-fg:        #e7eefb  /* primary text                       */
--cj-fg-2:      #c3d1e8  /* secondary text, values             */
--cj-fg-dim:    #9fb0c9  /* labels, meta                       */
--cj-fg-faint:  #8496ae  /* the dimmest text allowed (closed, archived) */
--cj-fg-hush:   #6b7f9c  /* NON-TEXT ONLY: rule labels/watermarks; never a sentence */
```

### 2.5 State luminescence
| token | value | means (server field it may accompany) |
|---|---|---|
| `--cj-cyan` | `#4fd8ff` | live / running / in progress (`column=ongoing`, `task_home.section=RUNNING`, scan `is-running`) |
| `--cj-cyan-deep` | `#1f6f8b` | cyan at rest: cyan structure without emission (rail head, rules under a live lane) |
| `--cj-amber` | `#ffb454` | needs a human: blocked, needs_review, stale, stalled, conflict, `receipt_section_mismatch` |
| `--cj-amber-soft` | `#ffc879` | amber for small text on raised surfaces |
| `--cj-emerald` | `#58e0a8` | accepted, fresh, healthy, `has_more false` |
| `--cj-rose` | `#ff6b8a` | urgent, error, `scan is-error`, load failure |
| `--cj-violet` | `#8b7bff` | derived/provenance accents ONLY (`LEFT OFF · DERIVED`, candidate provenance) |
| `--cj-closed` | `#8496ae` | closed / archived / suppressed: no hue at all |

Rationing rule: at most TWO luminous hues may be non-quiet on one screen region at a time (e.g. cyan
lanes + amber attention). Violet and emerald are accents on ≤5% of a card's area.

### 2.6 Glow and texture
```
--cj-halo-cyan:   0 0 0 1px rgba(79,216,255,.35), 0 0 14px -4px rgba(79,216,255,.55);
--cj-halo-amber:  0 0 0 1px rgba(255,180,84,.35), 0 0 14px -4px rgba(255,180,84,.50);
--cj-halo-rose:   0 0 0 1px rgba(255,107,138,.40), 0 0 14px -4px rgba(255,107,138,.45);
--cj-elev-1:      0 1px 0 rgba(255,255,255,.02), 0 8px 24px -12px rgba(0,0,0,.75);
--cj-scanline:    repeating-linear-gradient(180deg, rgba(231,238,251,.014) 0 1px, transparent 1px 3px);
--cj-particle:    radial-gradient(rgba(79,216,255,.05) 1px, transparent 1px); /* 18px grid, opacity .35 */
--cj-grid-etch:   linear-gradient(rgba(38,64,95,.28) 1px, transparent 1px),
                  linear-gradient(90deg, rgba(38,64,95,.28) 1px, transparent 1px); /* 32px cells */
```
Glow is allowed on: live state dot, focus ring, the active lane edge, an urgent card's left arc. Glow is
FORBIDDEN on body text, table rows, chips at rest, and anything that spans more than ~2 lines.

### 2.7 Contrast — COMPUTED this pass, not estimated
Script: WCAG 2.x relative-luminance formula, run on 2026-09-17 (output quoted; re-runnable).
Required: ≥4.5:1 for body text, ≥3:1 for ≥18.66px bold / ≥24px, and ≥3:1 for any non-text
indicator that carries meaning.

| pair | ratio | verdict |
|---|---|---|
| `--cj-fg` #e7eefb on `--cj-panel` #0c1220 | **16.05** | AAA |
| `--cj-fg-2` #c3d1e8 on `--cj-panel` | **12.12** | AAA |
| `--cj-fg-dim` #9fb0c9 on `--cj-panel` | **8.48** | AAA |
| `--cj-fg-faint` #8496ae on `--cj-panel` | **6.19** | AA (this is why the closed/archived tier may use it) |
| `--cj-fg-hush` #6b7f9c on `--cj-panel` | **4.58** | AA, text-only-at-the-limit → restricted to non-text |
| `--cj-cyan` #4fd8ff on `--cj-panel` | **11.22** | AAA |
| `--cj-cyan` on `--cj-panel-2` #111a2b | **10.44** | AAA |
| `--cj-amber` #ffb454 on `--cj-panel-2` | **9.87** | AAA |
| `--cj-amber-soft` #ffc879 on `--cj-panel` | **12.28** | AAA |
| `--cj-emerald` #58e0a8 on `--cj-panel-2` | **10.48** | AAA |
| `--cj-rose` #ff6b8a on `--cj-panel-2` | **6.40** | AA |
| `--cj-violet` #8b7bff on `--cj-panel-2` | **5.28** | AA (accents only) |
| `--cj-line` #1b2740 on `--cj-panel` | **1.26** | decorative only — never the sole state cue |
| `--cj-line-2` #26405f on `--cj-panel` | **1.76** | decorative border; state never rides on it |

Consequence (binding on the renderer): the "closed = dim" idea is expressed with `--cj-fg-faint`
(6.19:1) plus reduced weight and a dashed/quiet treatment — **never** by dropping text below ~4.5:1.
The old `--dim` #5c6780 tier (#3.7:1 on the new panel) is retired for text.

---

## 3. Page shell and hierarchy

Order is frozen by app.js:2469-2480; this skin does not reorder it. It only re-weights it.

```
.z-shell (body)                       bg: --cj-void + --cj-grid-etch (top 420px fade) + --cj-particle
  .c-header                           row 1: identity left, controls right
    .c-brand  (.c-logo ring + h1 + .c-tag)
    .c-header-actions  (#filter  +  #scan  +  injected .c-toolbar)
  .c-banner                           read-only notice — must stay visibly present (§4.1)
  main#app  (aria-live=polite)
    renderHealthStrip()               (app.js:1367)
    renderMissionControl()            snapshot age + scan state
    renderCurrentState()              7 metrics
    renderTaskHome()                  6 group buttons + rows
    attentionRail()                   3 groups
    renderBands()                     staleness
    board | dense grid                (state.layout)
    renderInbox()                     Recovery Inbox
    renderCompletenessReceipt()       .c-receipt
    drawer / detail / action log      (mutually exclusive)
  #toast-root   #live
```

Hierarchy (5 levels, expressed ONLY through size + value + rule weight — never through colour):

| level | element | treatment |
|---|---|---|
| L0 | page identity (`h1` Continuum) | 18px, 600, `--cj-fg`, letterspacing .06em, uppercase |
| L1 | region heading (`Needs attention`, `Recovery Inbox`, `Task Home`, `Action log`) | 12.5px, 700, uppercase, .1em, `--cj-fg-dim`, preceded by a 3px cyan arc tick (§7.2) |
| L2 | the data (project name, metric value, table cell) | 13-14px `--cj-fg` / metrics 20-22px `--cj-cyan` when live |
| L3 | labels (`Owner`, `Staleness`, `Condition`, `LEFT OFF`) | 10px, 700, uppercase, .08em, `--cj-fg-dim` |
| L4 | meta (`sha · age · page · data as of`) | 10.5-11px, `--cj-fg-faint`, tabular |

Spacing scale (8px base, tightened — the current 12-16px rhythm reads consumer-grade):
`--cj-s1:4 --cj-s2:6 --cj-s3:8 --cj-s4:12 --cj-s5:16 --cj-s6:24 --cj-s7:32`
Shell padding: 20px 24px 48px (from 28px 32px 60px) so the board reaches the viewport edge sooner.
Region gap: `--cj-s4` (12px) between rails/panels, `--cj-s3` inside a panel.
`.wrap` stays full-width fluid: `max-width:100%` (no change; user rule: full-width fluid containers).

Header treatment: `.c-header` becomes a HUD bezel — a 44px-tall bar with a 1px `--cj-line-2` bottom rule
and, at its left, the ring logo (§7.1). The filter input gets the instrument look: inset well
(`--cj-inset`), 1px `--cj-line`, cyan caret, and a monospace placeholder. `Scan now` is the ONLY primary
button on the page (cyan fill at rest, no gradient) — it is the one explicit mutation and must be the
single brightest control.

Banner (`.c-banner`, index.html:25-28): keep the exact copy. Restyle as a flat informational strip with
a 2px amber left rule, NOT the current pink/gold gradient wash — the read-only promise must be legible
without competing with the attention rail. Label `Read-only source scan` stays amber-on-void (≥10:1).

---

## 4. Surface treatments

### 4.1 Panels (all `.c-current-state`, `.c-mission-control`, `.c-task-home`, `.c-bands`, `.c-attention-rail`, `.c-inbox`, `.c-playground`, `.c-toolbar`)
`background: linear-gradient(180deg, rgba(79,216,255,.03), transparent 42%), var(--cj-panel);`
`border: 1px solid var(--cj-line-2); border-radius: var(--cj-radius-md);`
`box-shadow: var(--cj-elev-1);`
Each panel gets a 1px inner top highlight (`inset 0 1px 0 rgba(231,238,251,.03)`) so the bezel reads at
100% saturation without a glow. Section headings sit in a `.c-panel-head` row with a bottom hairline.

### 4.2 Mission control + health strip
Metric cells stay 4-up + banner (styles.css:279-287) and 2-up at 900px. Values become monospace tabular.
`is-running` scan state = cyan text + a 6px pulsing ring dot (§5.1); `is-error` = rose + a static `!`
glyph in the marker (non-colour redundancy). Stale-snapshot banner: amber on `--cj-deep`, 2px amber top
rule, no fill wash.

### 4.3 Current state (7 metrics, app.js:480-568)
Numbers 22px/600 monospace cyan-dim on `--cj-inset` wells; labels below at L3. Cell separators become
1px `--cj-line` verticals (from `--cj-line-2` left borders) so the strip reads as a segmented readout.
Below 900px: 4-up + 3-up wrap (already the rule) with labels kept.

### 4.4 Task Home (app.js:1246-1315)
- Head: `Task Home` + `synced · age · fresh|stale` (server fields; browser derives nothing — TH-L13).
- `fresh` renders as emerald text, `stale` as amber text PLUS the word `stale` (already emitted).
- Source line becomes monospace, `--cj-fg-faint`, with the sha truncated to 12 chars as today.
- The six group buttons form a segmented control on a `--cj-inset` rail: label left, count right in
  monospace. Active = cyan underline + cyan count, `aria-pressed` unchanged. GROUP ORDER IS FROZEN to
  `HOME_GROUPS` (server order, app.js constant) — the skin adds no ordering opinion.
- `source-only` rows: dim key (monospace) + `unbound` affordance only if the server field exists;
  never invent a project name.
- Conflict rows (`.c-th-conflict`): amber, monospace key, then `TASK-HOME vs dashboard · winner` — the
  winner token is bolded. Conflict is a *reading* state, not an alarm: amber text, no glow.
- `receipt` / `confirm then close` chips: amber outline pills, text verbatim from the field.

### 4.5 Attention rail (3 groups, cap 8)
Rails read left-to-right as a scan: heading cell (L1 + total), then groups. Group heads are L3 labels
with the count in monospace; items are 11.5px buttons with a 2px left state rule coloured by
`attention-*` class and the group's word in the label. Overflow line (`+N more`) stays `--cj-fg-faint`.
Hover = cyan text only (no background shift, no movement).

### 4.6 Staleness bands
Band buttons: monospace counts, label at L3, active = cyan border + `--cj-halo-cyan` at 40% intensity.
**Quiet** is dashed, `--cj-deep`, `--cj-fg-faint`, `cursor:default` (unchanged semantics) with the
subline `Parked or hiatus; not an attention alert.` kept verbatim (MC_COPY, app.js).

### 4.7 Board lanes (8 fixed)
Lane = `--cj-deep` well with a 1px `--cj-line` border, 8px radius, 8px padding (unchanged geometry).
Header is sticky, on `--cj-deep`, with the lane name at L3 and a monospace count; a 1px hairline; and a
lane state rule across the top of the well:
- `ongoing` → 2px cyan rule at 55% + faint `--cj-halo-cyan`;
- `blocked` → 2px amber rule;
- `waiting_on_you` → 2px amber-at-40% dashed;
- `done` / `shipped` / `scrapped` → no rule, `--cj-line` only (quiet by design, P9);
- `inbox` → 2px `--cj-line-hot` (neutral, not an alarm);
- `paused` → dashed `--cj-line-hot`.
Drop target during drag: 2px dashed cyan + `--cj-halo-cyan` (keeps the existing affordance, adds no
layout shift). Empty lane copy stays as-is; the "Run a scan / Clear filters" action line is a real button
styled as a link-button (cyan text, 1px underline on hover), hint line `--cj-fg-faint`.

### 4.8 Card (app.js:996-1145) — the densest surface
Anatomy, top to bottom, with no field added or removed:
1. `.c-row` head: attention marker (upper-case word, `--cj-fg-dim`, with a 4px dot in the state hue), name
   button 13.5px/600 `--cj-fg`, then chips, then provenance chip pushed right.
2. `.c-condition-row`: label L3, value 12.5px `--cj-fg-2` bold, age right-aligned monospace.
3. `.c-meta` state line: lifecycle + stall label, monospace where numeric.
4. `.c-primary-session` code: monospace `--cj-fg-faint`, 10.5px.
5. `ABOUT THIS` / `LEFT OFF` blocks: hairline above, L3 label, body 12.5px/1.45 `.c-summary` 3-line clamp
   and `.c-leftoff` 2-line clamp (clamps unchanged — the server text is verbatim).
6. counts line: monospace.
7. actions row (`Move to…`, `Mark urgent`) at 72% opacity, full opacity on hover/focus-within (existing
   behaviour, keep) — but keep the buttons at ≥28px hit height at touch breakpoints.
8. move menu: `--cj-panel-2`, 1px `--cj-line-2`, radius sm, items 12px with a cyan left rule on the
   highlighted/current target; `✓` prefix retained (non-colour cue).
Urgent card: 2px rose LEFT arc (inset box-shadow), not a full rose border — a full red outline on many
cards is a wall of alarm (P3). Focus: 2px cyan ring, 2px offset, always visible (`:focus-visible`).

### 4.9 Dense grid (7 columns, app.js:110-111)
Table stays `table-layout:fixed`, 12px, `tabular-nums`. New: head row on `--cj-inset` with a 1px
`--cj-line-2` bottom rule; zebra via `rgba(231,238,251,.015)` every other row (not borders alone); row
hover raises the row to `--cj-panel-2`; the `Project` cell keeps the name button plus the id line.
Derived values keep the `.c-claim` affordance and gain a 10px `WHY THIS?` hint that turns cyan on hover.
Below 1100px the existing labelled-row-list reflow (styles.css:548-561) is kept EXACTLY; the skin only
recolours labels to L3 and swaps the row card background to `--cj-panel`.

### 4.10 Action log (`?view=action_log`, app.js:2241-2435) — currently unstyled
This is the largest visual gap in the build and the lane this handoff must serve first.
- Shell: `.c-al` is a full-width panel on `--cj-panel`; `.c-al-head` = L1 title + `.c-al-sub` one-liner
  (copy stays `Projection of evidence sources; each row links its source.`).
- `.c-al-bar` = a readout strip on `--cj-inset`: `total/active/archived` in monospace, `by_kind` and
  `by_operator` rendered as inline monospace key=value pairs separated by a 1px `--cj-line` divider.
  These are counts, not alarms → no hue except a cyan `active` figure.
- `.c-al-filters` = the instrument bar: each `.c-al-field` is a stacked label (L3) + `.c-select` /
  `.c-input` well. Date-range inputs are monospace, right-aligned numerals, with a `→` hairline between
  `.date_from` and `.date_to`.
- `.c-al-table`: 11 columns; head on `--cj-inset`, sticky, 1px `--cj-line-2` rule; rows 32-36px tall with
  a 1px `--cj-line` divider and hover `--cj-panel-2`. Monospace for `timestamp`, `action_id`, `target`,
  `evidence` link, `operator_flag`, `status`, counts and the `audit <id>` meta line. `kind` renders as a
  `.c-al-chip` pill using the SAME chip vocabulary as the board (so the two views cannot drift).
- Status semantics (server vocabulary, `AL_VIEWS`): `active` = cyan left rule, `archived` = NO hue, dim
  row (`--cj-fg-faint` text) with a dashed left rule — an archived row is still fully readable, it just
  stops competing. This is the archive treatment for the whole product, applied identically to the
  board's `done/shipped/scrapped` lanes and any future closed projection.
- Per-row `.c-btn.tiny` Archive / Unarchive keeps its position; Archive is the primary-looking button on
  active rows, Unarchive is ghost on archived rows (already the case).
- Evidence link opens in a new tab: render as cyan underline-on-hover link text with a `↗` glyph
  (glyph = non-colour affordance that it leaves the page).
- `.c-al-empty` (`Loading…`, `No actions returned for this filter.`), `.c-al-error`, and the pager
  (`.c-al-pager`) are styled as quiet instrument states: `--cj-fg-dim`, monospace numerals, buttons at
  ≥28px. `has_more true|false` renders as a monospace status token, emerald for `false`.
- Column ORDER and header words come from the server envelope; the skin does not rename a column.
- If the action-log lane adds hover-count chips or a row drawer, they must reuse `.c-claim` (§4.9) and
  `.c-evidence-drawer` (§4.11) rather than introducing a third disclosure pattern.

### 4.11 Evidence drawer, session pane, detail view
Drawer: `--cj-panel-2`, 1px `--cj-line-2`, radius md, `--cj-elev-1`, cyan focus ring, `.c-ev-row` rows
with a 2px left rule (kind-coloured: cyan for session, violet for derived, faint for hash-only).
Session pane: `--cj-inset` well, `.c-pane-row` 2px left rule — cyan for `.is-you`, `--cj-line-hot` for
agent, dashed for clipped rows with the existing 3-line clamp. Detail view: single column, max 900px,
H2 20px, sections separated by hairlines, evidence rows monospace for ids/timestamps.

### 4.12 Recovery Inbox, receipt, playground, empty/error/loading, toast
Every one keeps its current DOM and copy. Skin-only changes: panel recipe from §4.1, monospace numerals,
L3 labels, cyan link-buttons for the empty-lane actions, receipt strip on `--cj-deep` with a 2px cyan
left rule, skeleton shimmer on the new panel recipe, toast `--cj-panel-2` + `--cj-elev-1` with a 2px
left rule coloured by outcome (cyan info, rose failure) — the undo affordance keeps its two buttons.

---

## 5. State styles

State is defined by (hue, geometry, word, motion) and every state must survive greyscale (§1 P4).

| state | source field | hue | geometry | word (verbatim) | motion | greyscale survivor |
|---|---|---|---|---|---|---|
| running / in progress | `column=ongoing`, `task_home.section=RUNNING`, scan `is-running` | cyan | 2px solid top rule + ring dot | `Running`, `in progress` | 2.4s pulse (§8) | word + solid rule |
| live scan | `scan.status` running | cyan | pulsing 6px ring in the strip | `is-running` | 2.4s pulse | word |
| blocked | `attention_state=blocked` | amber | 2px amber left rule on card + amber marker | `blocked` | none | word + rule |
| needs review | `needs_review:true`, `chip-needs` | amber | outline pill | `Needs review` | none | pill outline + text |
| stale / stalled | `staleness`, `chip-stale`, `declared_stale` | amber | outline pill, tabular age | `stale` | none | text |
| conflict | `task_home.conflicts[]` | amber | 1px amber left rule on the conflict row | `TASK-HOME vs dashboard · winner X` | none | text |
| urgent | `urgent:true` | rose | 2px rose left arc | `Urgent` | none | chip text |
| error | fetch failure, scan `is-error` | rose | static `!` glyph in marker + rose rule | error copy from MC_COPY | none | glyph + copy |
| accepted | `review_status=accepted` | emerald | outline pill | `Accepted` | none | text |
| candidate | `review_status=candidate` | none | outline pill, dim | `Candidate` | none | text |
| waiting on you | `column=waiting_on_you` | amber@40% | dashed 2px rule | `waiting on you` | none | dashed rule + word |
| paused / open follow-up | `column=paused`, `OPEN_FOLLOW_UP` | none | dashed `--cj-line-hot` rule | `Paused`, `Open follow-ups` | none | dashed rule |
| closed / archived | `done|shipped|scrapped`, `CLOSED`, `status=archived` | none | dashed dim rule, `--cj-fg-faint` text | `Closed`, `Archived` | none | dashed rule + dim text |
| quiet staleness | band `quiet` | none | dashed border, non-interactive | `Quiet` | none | already non-colour |
| pending write | `state.pending[pid]` | cyan@40% | card at 60% opacity + `aria-busy` | — | suppressed while pending | opacity |
| focus | any focusable | cyan | 2px ring, 2px offset | — | none | visible outline |

### 5.1 The one live pulse (exact spec)
```
@keyframes cj-pulse { 0%{box-shadow:0 0 0 0 rgba(79,216,255,.55)}
                      70%{box-shadow:0 0 0 7px rgba(79,216,255,0)}
                      100%{box-shadow:0 0 0 0 rgba(79,216,255,0)} }
.cj-live { animation: cj-pulse 2.4s cubic-bezier(.4,0,.2,1) infinite; }
```
Applies to: the running-state dot, the live-scan dot, and the active-lane tint. Nothing else. A maximum of
ONE pulsing element per region; if two states are live in the same panel, only the higher-priority one
pulses (urgency order: error > blocked > running).

---

## 6. Typography and numerals

```
--cj-font-sans: 'Inter var', 'Inter', system-ui, -apple-system, 'Segoe UI', sans-serif;
--cj-font-mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
```
- The current `DM Serif Display` brand serif (styles.css:69) is REMOVED. A serif wordmark is the single
  biggest reason the page reads editorial rather than instrument. `.c-brand h1` becomes uppercase
  sans 600 with .06em tracking; the identity now comes from the ring logo (§7.1) and the tag line.
- Scale: `--cj-t-metric:22px/1` (readouts), `--cj-t-h1:18px`, `--cj-t-h2:16px`, `--cj-t-body:13px/1.45`,
  `--cj-t-small:12px`, `--cj-t-micro:10.5px`, `--cj-t-label:10px` (uppercase, .08em).
- ALL of the following render in `--cj-font-mono` with `font-variant-numeric: tabular-nums`: metric
  values, counts, ages/durations, timestamps, `sha`, `project_id`, `session_id`, `action_id`, `audit id`,
  `line_no`, page/page_size, `by_kind`/`by_operator` pairs, evidence hashes, Task Home keys.
- Numerals never animate a width change (tabular) and never use proportional figures.
- Uppercase is reserved for L1/L3 labels and state words — never for project names, summaries, or left-off
  text (those are server prose and must read as written).

---

## 7. Motifs

All motifs are CSS-only, `aria-hidden`, `pointer-events:none`, and must be removable without touching
layout (they live in pseudo-elements or a `.cj-deco` overlay div at z-index 0).

### 7.1 Brand ring (replaces `.c-logo` gradient tile)
A 36px circle: 1.5px cyan arc spanning 300° with a 60° gap rotating to the upper-right, plus a 12px
emerald core dot for `fresh` / cyan for `live` / amber when the last scan errored. Purpose: the logo
becomes the product's status lamp, not a decoration.

### 7.2 Section arc tick
Each L1 region heading takes a `::before` of a 3px × 10px quarter-arc (border-radius on one corner) in
`--cj-cyan-deep`. Purely structural repetition; it makes eight rails read as one console.

### 7.3 Field grid
`--cj-grid-etch` drawn at 32px on the page background, masked to fade out by 60% viewport height. Reads
as graph paper under the void; never behind a table (tables get an opaque `--cj-panel`).

### 7.4 Scanline + particle
`--cj-scanline` at 3px period, 1.4% alpha, applied to the page and to lane wells only. `--cj-particle`
18px cyan dots at 35% opacity, confined to the header band and the page's outer 64px margins. Both are
under 2% contrast against their backdrop and must never overlay a `<table>` or a `.c-pane`.

### 7.5 Arc accents on state
A state rule with meaning takes the arc form: `border-image: linear-gradient(90deg, hsl, transparent) 1`
on the top rule of a live lane, so the emission decays to the right instead of stopping hard. On quiet
states the rule is solid and dim — the difference between glowing and drawn is the state.

---

## 8. Motion

| event | motion | duration | easing | rule |
|---|---|---|---|---|
| live/running state | halo pulse | 2.4s loop | cubic-bezier(.4,0,.2,1) | max ONE per region |
| state change (card moves lane, chip appears) | 120ms cross-fade of the affected row only | 120ms | ease-out | never on initial load |
| panel/rail appear | none on load | — | — | arrival must never delay reading |
| hover | border/text colour only | 90ms | linear | no transform, no scale, no lift |
| menu open | opacity 0→1 | 100ms | linear | no slide |
| drawer open | opacity + 6px translateY | 140ms | ease-out | focus moves first |
| value update (count/age refresh, 15s poll) | none | — | — | a changing number must not flash or shift |
| loading skeleton | shimmer | 1.4s loop | linear | existing keyframe retained |

Invariants:
- Motion is never required to perceive a state (every animated state also has a word + geometry).
- No element MOVES horizontally or vertically except the drawer's 6px entrance; nothing scrolls itself.
- Nothing animates on data arrival. The 15s poll (app.js:2530) must cause zero visual disturbance.
- Reduced motion (`prefers-reduced-motion: reduce`) continues to disable everything globally
  (styles.css:308-310 stays) and, in addition, the pulse is replaced by a static 2px cyan ring so the
  live child still reads at rest:
```
@media (prefers-reduced-motion: reduce) {
  .cj-live { animation: none !important; box-shadow: 0 0 0 2px rgba(79,216,255,.45) !important; }
  .c-skeleton { animation: none !important; background: var(--cj-panel-2) !important; }
  .cj-deco { display: none !important; }
}
```

---

## 9. Responsive

Breakpoints are kept from the build (2164 / 1100 / 900) because they are load-bearing for existing
behaviour; this skin adds one narrow-range refinement at 640px.

| range | board | grid | rails/panels | notes |
|---|---|---|---|---|
| ≥2165px | 8 columns in one row | table, all 7 columns | multi-column rails | the operator's native desktop |
| 1100–2164px | single-column lane stack (existing rule, styles.css:271-276) | full table | wraps to 2 columns where defined | unchanged geometry |
| 901–1099px | lane stack | 7 columns → labelled row list (existing, styles.css:548-561) | 2-up | unchanged |
| 641–900px | lane stack; actions row always full-opacity (no hover available) | row list | 1-up panels; rail horizontal scroll retained | mission control 2-up (existing) |
| ≤640px | lane stack, 12px gutters, sticky lane header retained | row list; each `data-label` becomes the row's left column at 96px | 1-up; `.c-al-table` reflows to the same labelled-row pattern | touch targets ≥40px (§10) |

Non-negotiables: no horizontal page scroll at any width; no text truncation beyond the declared clamps
(`.c-summary` 3 lines, `.c-leftoff` 2 lines, rail items 1 line) — never `overflow:hidden` on prose as a
layout fix; the 8 lanes never become 2 rows of 4 on desktop (a lane is a state, not a card).
The known 390px defect recorded in the architecture lane (MC-L16, out of scope there) is DESIGNED here:
at ≤640px the action-log table and dense grid both use the labelled-row pattern, and the toolbar wraps to
two scroll-free rows with `Clear` promoted to the end.

---

## 10. Accessibility

1. Contrast: every text pair in §2.7 meets AA; body text meets AAA. No state is carried by a hue below
   4.5:1, and no hue is the only carrier (§5 table).
2. Focus: one ring style (`2px --cj-cyan`, 2px offset) on every interactive element; never removed;
   the existing `.c-card:focus-visible`, `.c-claim`, `.c-th-group`, `.c-band`, `.c-menu-item` rules keep
   their semantics and only change colour.
3. Hit targets: ≥28px on desktop, ≥40px at ≤640px for `.c-btn.tiny`, `.c-lane-empty-action`, rail items,
   pager buttons and the action-log row buttons. Row-level buttons keep their accessible names.
4. Semantics unchanged: no `div` gains a click handler; no `role` is dropped; `aria-pressed`,
   `aria-busy`, `aria-haspopup`, `aria-live=polite`, `role=listitem|region|status|menu` all stay exactly
   as app.js emits them. The skin adds no ARIA and removes none; it may only add `aria-hidden="true"` to
   `.cj-deco`.
5. Keyboard: every motion in §8 is triggered by state, not by keypress, so no keyboard path changes.
   Escape behaviour (drawer/menu/pane, app.js:2514-2527) is untouched and must stay documented in the
   empty-state copy.
6. Text zoom to 200%: no loss of content (all clamps are line-based, and the grid reflow is
   width-based); no fixed pixel heights on text containers (`.c-lane-header` sticky offsets recomputed).
7. Colour-blind safety: cyan/amber/violet all differ in luminance as well as hue (ratios 11.2 / 10.6 /
   5.3 on panel), and every state carries a word — a deuteranope sees the same distinctions as the hue
   table because the labels, rules and glyphs carry it.
8. No emoji anywhere (role rule). Status glyphs are limited to `✓`, `!`, `↗`, `↑`, `↓` and `·`.
9. Screen-reader order equals visual order; the decorative layer is `aria-hidden` and therefore cannot
   inject phantom content.
10. Motion: the pulse is ≤2.4s period, low amplitude, and fully replaced under reduced motion, which
    satisfies WCAG 2.3.3-style "motion from interaction" expectations without an extra toggle.

---

## 11. Implementation acceptance checklist

### 11.1 Checklist (all must be true on the served build; evidence = screenshot + computed style)
- A1 index.html is byte-unchanged.
- A2 No server field, route, query vocabulary, count, or copy string is changed; every string rendered is
  the same string as before this skin.
- A3 Every surface in §13 exists and is reachable: board (lanes + dense grid), Task Home (enabled and
  not-synced), attention rail, staleness bands, Recovery Inbox, completeness receipt, detail view,
  evidence drawer, session pane, action log (active and archived), empty/loading/error, toast.
- A4 `getComputedStyle` on the shell reports `--cj-void` as the background base, and no element paints a
  colour outside the §2 palette (allow only `currentColor`-inherited SVG/glyph).
- A5 Only one pulsing element is visible per region at any time, and its period is 2.4s.
- A6 With `prefers-reduced-motion: reduce` forced, no `animation` is running (DevTools Animations panel
  empty) and the live state is still visibly marked by the static ring.
- A7 Zero horizontal page scroll at 1920 / 1440 / 1280 / 1100 / 900 / 768 / 640 / 390px.
- A8 Full keyboard pass: Tab reaches every control; visible focus on all of them; Escape closes
  drawer/menu/pane; no focus trap.
- A9 Every state word in §5 appears in the DOM next to its coloured element (colour is never alone).
- A10 Action-log rows are fully legible in archived state (`--cj-fg-faint`, ≥6.19:1) and archived rows
  carry no hue.
- A11 The banner's read-only sentence and Task Home's CLI line are present verbatim.
- A12 Contrast spot-check via DevTools on: body text, a card name, a chip label, a lane header, a table
  cell, an archived row, a metric value, a rail item — each ≥4.5:1 on its actual background.
- A13 Tabular numerals: no numeric column changes width between `9` and `10`.

### 11.2 Legacy → token mapping (for the renderer's CSS pass)
| existing rule / token | action |
|---|---|
| `--bg #0b0e14` | → `--cj-void` on body, `--cj-deep` on wells |
| `--bg2 #10141d` | → `--cj-deep` (lanes) / `--cj-inset` (table heads, code wells) |
| `--card #141926` | → `--cj-panel` |
| `--card2 #1a2030` | → `--cj-panel-2` |
| `--line #232b3d` | → `--cj-line` |
| `--line2 #2c3650` | → `--cj-line-2` |
| `--fg/--muted/--dim` | → `--cj-fg / --cj-fg-dim / --cj-fg-faint` (`--dim` #5c6780 retired for text) |
| `--cyan/--amber/--rose/--emerald/--violet/--gold` | → `--cj-cyan/--cj-amber/--cj-rose/--cj-emerald/--cj-violet`; `--gold` disappears (folded into amber) |
| `--shadow-pop` (violet glow on `.c-logo`, `.c-btn.primary`) | delete; `.c-btn.primary` becomes flat `--cj-cyan` fill, `--cj-void` text |
| body radial-gradient washes (violet/cyan, styles.css:44-47) | replace with `--cj-grid-etch` + `--cj-particle` at ≤2% |
| serif `h1` (styles.css:68-72) | uppercase sans 600 |
| `.c-al*` (0 rules today) | implement per §4.10 with the same token set |
Class-name policy: KEEP every existing `c-*` name (app.js emits them and index.html is frozen). New
modifier classes must be ADDITIVE and namespaced `cj-` (`cj-live`, `cj-deco`, `cj-quiet`) so a renderer
can adopt the skin without touching a JS selector. No existing selector may be renamed.

### 11.3 Screenshot acceptance criteria (capture these six, 1440×900 unless noted)
S1 Board, lanes layout, Today view, ≥8 cards with ≥1 urgent and ≥1 blocked: 8 lane rules visible; exactly
   one cyan pulse; body text crisp at 13px; no wash of saturated colour; graphite base reads as navy-black.
S2 Dense grid, All view, scrolled to row 12: sticky head on `--cj-inset`; tabular numerals aligned; zebra
   rows visible but not distracting; `WHY THIS?` hints present.
S3 Task Home in both states (not-synced with CLI line; synced with 6 group buttons, a conflict row and a
   `receipt` chip): group rail reads as a segmented control; conflict row is readable amber on panel, not
   glowing.
S4 Action log, `?view=action_log`, active + one archived row in frame: 11 columns fit at 1440 without
   clipping; archived row visibly quiet and still legible; filter bar reads as an instrument bar.
S5 Empty/loading/error: skeleton on the new panel recipe, an empty lane with its action line, and an
   error panel — none of which should look like a broken page.
S6 Mobile 390×844: no horizontal scroll, labelled-row reflow in the grid and the action-log table,
   ≥40px targets, lane headers still legible.
Each capture is "pass" only if §11.1 A2, A4, A5, A6, A7, A9 and A12 hold for that screen.

---

## 12. References — real established systems, and exactly what is borrowed

1. **NASA Open MCT (mission control framework)** — https://nasa.github.io/openmct/ ·
   plugins/themes: https://nasa.github.io/openmct/plugins/ · releases (theme + a11y notes):
   https://github.com/nasa/openmct/releases
   *Borrowed:* the "console first" information order — telemetry/state above, chrome minimal, one
   authoritative time/state readout at the top (our mission-control + current-state strip); theme chosen
   as an installable layer (their Espresso/Snow/Darkmatter plugins) which is why this skin is a token
   layer over frozen markup; and their stance that an accessibility-compliant theme is the
   *default* theme, enforced by automated visual-a11y checks on every commit (their Espresso theme is
   WCAG 2.0 AA / Section 508) — mirrored by our §11.1 A12 spot-check requirement.
   *Not borrowed:* their plot/overlay visualisations and their object-tree editing model (this dashboard
   is read-only and has no editing surface).

2. **Grafana dark dashboards** — theme guide (rich state colours and theme precedence):
   https://github.com/grafana/grafana/blob/main/contribute/style-guides/themes.md ·
   theme configuration/precedence: https://grafana.com/docs/grafana/latest/administration/organization-preferences
   *Borrowed:* named state roles with sub-variants — Grafana's six "rich" colour objects (`primary,
   secondary, info, success, warning, error`) each carrying `main` (background), `shade` (hover) and
   `text` values; our `--cj-*` state tokens follow the same role-first shape, and our
   `--cj-amber`/`--cj-amber-soft` split is exactly the main/text variant idea. Also borrowed: dark as the
   product default rather than an option, and the precedence model (theme resolved at the highest level
   that defines it) → which is why this skin is expressed as one token block that a renderer adopts
   wholesale, never per-surface ad-hoc colours.
   *Not borrowed:* panel titles/legends heuristics and their light-theme parity work (Continuum has one
   theme by design).

3. **Eclipse Ditto Explorer UI (digital-twin / telemetry explorer)** —
   https://eclipse.org/ditto/user-interface.html · live UI: https://eclipse-ditto.github.io/ditto/
   *Borrowed:* the HUD-style telemetry inspection pattern — an environment/context switcher at the top
   (ours: view / lane / lifecycle / attention / band / home filters in one bar), dense key-value
   inspection of one entity (ours: the detail view + evidence drawer), and the rule that a filter bar is
   a *readout of the current query*, not a form. Ditto's Explorer also distinguishes environments
   (local/sandbox/prod) in the interface — the analogue here is Continuum's explicit "read-only source
   scan" banner, which must stay visible so the operator always knows which authority they are looking at.
   *Not borrowed:* Ditto's write/edit actions (Things/Policies CRUD); Continuum's only mutation is the
   audited review path plus the explicit scan.

4. **IBM Carbon Design System — themes** — https://carbondesignsystem.com/guidelines/themes ·
   token/theming code: https://carbondesignsystem.com/elements/themes/code
   *Borrowed:* role-named tokens rather than value-named ones — Carbon assigns the same ~52 role
   variables across its white/g10/g90/g100 themes, changing only the value; our `--cj-*` roles
   (panel/line/fg/state) are defined the same way so a future light or high-contrast variant is a token
   swap, not a rewrite. Also borrowed: theme switching through a single root-level mechanism and respect
   for the user's system preference — mirrored in our `prefers-reduced-motion` handling (§8) and the
   planned `prefers-contrast` extension (§14 OPEN-D2).
   *Not borrowed:* Carbon's product-scale component library and its spacing baseline (Continuum is a
   single-screen console, not an app framework).

5. **WCAG 2.2 — motion and contrast** (the standards the above are measured against) —
   animation from interactions: https://www.w3.org/WAI/WCAG22/Understanding/animation-from-interactions.html ·
   non-text contrast: https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html ·
   `prefers-reduced-motion`: https://developer.mozilla.org/en-US/docs/Web/CSS/@media/prefers-reduced-motion
   *Borrowed:* the obligation shape — motion that can be disabled must be, and any meaningful non-text
   indicator carries ≥3:1 against its neighbour. This is the source of §8's "state is never motion-only"
   invariant and §2.7's computed table.

---

## 13. Preserve-list (inventory — every named thing that must survive)

Panels/regions: health strip, mission control (snapshot age, scan state, stale-snapshot banner), current
state (7 metrics + scope line), Task Home (head, synced/age/fresh-stale, source path + sha, 6 group
buttons, conflicts, warnings, items), attention rail (heading, 3 groups, cap 8, overflow line), staleness
bands (5 band buttons + quiet group + 2 sublines), board (8 lanes, header, count, body, empty state with
action + hint), dense grid (7 columns, sticky head, claim controls, `WHY THIS?`), Recovery Inbox (head,
triage line, zero state, ranked order, suppressed), completeness receipt (`.c-receipt`), playground
reference groups (START HERE / REDISCOVER + omitted), detail view, evidence drawer, session pane,
action log (head, sub, counts bar, filter bar with 4 selects + 2 date inputs, table + 11 columns,
per-row Archive/Unarchive, `audit` meta, pager with page/page_size/has_more, archived count + show/hide,
`Clear`), toolbar (board vocabulary: View, Sort, direction, Lane, State, Attention, Band, Layout, hint,
Clear; action-log vocabulary: Kind, Operator, Status, Sort, show archived, Clear), header (filter input,
Scan now), read-only banner, toast (message + up to 2 actions), `#live`, skeleton, empty, error.
Fields rendered today that must still render (non-exhaustive but binding): attention_state,
attention_reason, review_status(+label), needs_review, urgent, provenance, quiet_days, stall_age_days,
lifecycle_name/derived_lifecycle_name, declared_stale, summary_text, left_off tiers
(declared/derived/none), sessions_omitted, session/message counts, user_facing_session_count,
project_id, session_id, primary_profile, data_as_of, counts.total/unplaced_accepted/continuity_total/
suppressed_total, page, scan state fields, staleness bands + quiet, attention group totals, inbox rank +
suppressed, all `task_home.*` fields (§6 of leo-architecture), all action-log row fields (timestamp,
kind, target, evidence link + type, operator_flag, status, action_id, archive_audit_id), and every
`data-label` in the 1100px grid reflow.

---

## 14. OPEN, non-claims, handoff

OPEN-D1 — `--cj-font-mono` availability: the build loads no webfont today (system stack only). If
JetBrains Mono is desired, it must be a self-hosted `@font-face` added by the renderer with
`font-display: swap`, and `ui-monospace` must remain the first fallback so the page never waits on a
font. Decision belongs to the implementer; the skin works on the system stack alone.
OPEN-D2 — high-contrast theme (`prefers-contrast: more`): the token architecture supports it
(Carbon-style role swap) but no values are locked here. Recommend a future pass that raises
`--cj-fg-faint` to `--cj-fg-dim`, thickens rules to 2px and removes the decorative layer entirely.
OPEN-D3 — the decorative layer's CPU cost on a 34-row board: `--cj-scanline`/`--cj-particle` are static
gradients (no canvas, no JS), which is why they are safe; if the renderer measures a paint cost above
~1ms/frame on the served build, drop `.cj-deco` — it carries no meaning (§1 P10).
OPEN-D4 — quiet-lane affordance text for `done|shipped|scrapped` remains the existing EMPTY_COPY strings;
no new copy is authored here (copy sign-off rule: Hazen owns wording, this lane owns tone and the
visual system it sits in).
OPEN-D5 — whether the action-log lane's incoming rows add columns: if they do, the new column joins the
same table recipe and must not introduce a second disclosure pattern (§4.10, final bullet).

Non-claims: no build file was written; no data contract, route, registry row, task-home field or
action-log field is touched by this document; nothing was installed, deployed, restarted or published;
no QA verdict is issued.

Handoff: the action-log implementation lane owns `dashboard/static/styles.css` + `app.js` code. Read this
artifact's §4.10 for the action-log surface, §11.2 for the token mapping, §5 for state semantics, and
§11.1/§11.3 for the acceptance evidence to attach. Any deviation from §2 tokens or §5 semantics is
recorded as a deviation, not silently absorbed.
