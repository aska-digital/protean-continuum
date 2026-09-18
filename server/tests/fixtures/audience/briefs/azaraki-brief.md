---
goal: write the creator-facing levels-and-evolution contract for EvoPet pets (one doc, no code)
team6_agent: azaraki
from: lugia
---

## GOAL

Write `/Users/kethuda/evopet-pet/docs/evopet/levels-and-evolution.md`: the document a **pet
creator** reads to understand how levelling works in EvoPet and how they configure evolutions for
their own pet.

Write for a creator who is forking the pet catalogue (Petdex) and the pet generator (TamaCodex
lineage): they will hand us a `pet.json` plus a spritesheet, and they need to know exactly which
numbers are theirs to choose and which are fixed by the platform.

## CONTEXT — the mechanics, verified in source today

Everything below is already implemented and tested in `/Users/kethuda/evopet-pet`. Do not invent
mechanism; read `tamahermes/levels.py` and `tamahermes/state.py` to confirm before writing.

**The ladder is FIXED by EvoPet** — every pet in every package uses it, so a level means the same
thing everywhere:

    level 1    at 0 XP
    level 99   at 100,000 XP
    xp_for_level(L) = round(100_000 * ((L - 1) / 98) ** 2)
    fast early, slow late: the first level costs ~10 XP, the last ~2,000; half the cap is spent
    between level 50 (25,000 XP) and 99.

**Evolution is the CREATOR's choice** — how many evolutions, and at which levels, declared in the
pet's own manifest:

    "evopet": { "evolutionGates": [11, 23, 32, 45] }

A gate is a level the pet *reaches*. There is always exactly one more form than gate: the first
form is the one the pet starts in. The default pet ships `(11, 23, 32, 45)`, which on this curve is

    gate level 11 ->  1,041 XP -> egg -> hatchling
    gate level 23 ->  5,040 XP -> hatchling -> child
    gate level 32 -> 10,006 XP -> child -> teen
    gate level 45 -> 20,158 XP -> teen -> adult

Rules enforced in code (`levels.validate_gates`): 1 to 4 gates (the platform supports 5 forms),
each level 1-99, strictly increasing. A manifest with no `evopet` block gets the default gates.

**What the player sees:** the growth bar fills against the *current level* — floor to ceiling of
that level's XP band — so it fills about a hundred times per lifetime and resets on each level-up,
instead of filling once per evolution. The pet visibly changes form only at a gate.

Also worth stating plainly, because it is what makes the gates safe to tune: a gate level, once
reached, is a floor in the fixed ledger table (`levels.ledger_thresholds`), the terminal form is
absent from that table (nothing left to grow into), and dormancy is a condition, not a stage — a
sleeping pet still reports the progress it has genuinely made.

## CONSTRAINTS

- **One new file only:** `docs/evopet/levels-and-evolution.md`. Do not edit any `.py` file, do not
  touch `tests/`, do not touch `/Users/kethuda/TamaHermes` (another agent works there).
- Do not restate the other documents: `docs/evopet/W1-combined-ledger.md` covers the cross-agent
  ledger, and `PROVENANCE.md` covers the fork lineage. Link them, do not duplicate them.
- Every number you print must be verified: compute the table from the real formula and say what you
  ran (`uv run --quiet python -c "..."` from the repo root) so the reader can re-derive it.
- No invented features. If something is not implemented (for example a manifest field that does not
  exist yet), say so under a clearly-labelled "Not yet implemented" heading rather than describing
  it as if it works.
- External-writing discipline applies: no filler, no marketing voice, active sentences, concrete
  numbers. A creator should be able to configure a pet from this page alone.
- Include a short worked example: a creator who wants three evolutions at levels 20 / 40 / 60,
  with the resulting XP thresholds and the resulting form list.

## OUTPUT FORMAT

Markdown, roughly 80-150 lines, with these sections in order:

1. The ladder (fixed) — the formula, the shape of the curve, a small table of XP at meaningful
   levels, and the fact that the bar fills per level.
2. Evolution gates (yours) — the manifest block, what a gate means, the limits, what happens when
   the block is absent.
3. The default pet's gates — the four-gate table above with its XP and forms.
4. Choosing your own gates — the worked example, plus practical advice on gate spacing against the
   curve (early gates are cheap to reach; late gates need thousands of XP).
5. Not yet implemented — honest list.
6. Where the numbers live — file paths and function names in `tamahermes/levels.py`.

## DONE

- The file exists at the exact path, with the six sections in order.
- Every XP number in it is one you computed or read from source, not remembered.
- A reader who has never seen the code can add an `evopet` block to a `pet.json` and predict when
  their pet evolves.
- Report in your final answer: the commands you ran to verify numbers, and the file's line count.

## ENVIRONMENT

- Repo root `/Users/kethuda/evopet-pet`; Python via `uv run --quiet python`.
- `tamahermes/levels.py` is the source of truth (functions: `xp_for_level`, `level_for_xp`,
  `level_progress`, `validate_gates`, `gates_from_manifest`, `forms_for_gates`,
  `thresholds_for_gates`, `ledger_thresholds`, `stage_for_xp`, `gate_report`, `curve_report`).
- Sibling docs already present: `docs/evopet/W1-combined-ledger.md`, `PROVENANCE.md`.
- Another agent is editing `tests/` in the same repo right now — stay out of it.
