# Private commitment lifecycle foundation

`continuum.commitments.Ledger` is an opt-in, local-only store for commitments that
must survive decisions, handoffs, source-label removal and process restarts. It is
not wired to routes, startup, dispatch, scanning, static export or a scheduler.
Nothing imports historical runtime data automatically. No service deployment is
part of this change.

## Boundaries

The existing registry schema stays at version 3. Like the action/decision sidecars,
the new ledger is separate JSON state, conventionally
`server/data/commitments.json` (already gitignored). Caller chooses the path;
store it on a private local filesystem, not a public/static export directory or
network filesystem. Titles, source links, owners and evidence can be private.
New files are mode 0600; the store does not change existing directory permissions.
No telemetry, remote reads, comments, dispatches or credential handling occurs.

This module is storage and validation, not an authorization layer. A future caller
must check permissions for each operation. Evidence is a caller-verified
observation; this module cannot independently verify a remote link or reviewer.
Recording a decision, command exit, PR approval or merge never automatically marks
work complete or proves deployment.

## Model and invariants

- A canonical HTTPS source URL yields a stable `work-...` identity. Duplicate
  intake is rejected; replaying an identical idempotency key returns its original
  result. Credential-bearing, query-bearing and fragment-bearing source URLs are
  rejected to avoid accidental secret ingestion and unstable identity.
- States: proposed, ready, queued, active, blocked, review, done, deferred,
  cancelled. Explicit transitions only. Terminal work can be explicitly reopened
  to ready; old evidence and audit events remain.
- Ready/active/review/done require an accountable owner. Every outstanding item
  requires a concrete next action. Blocked requires a release condition.
- Completion requires review state, completed dependencies, an evidence reference
  and summary, a verifier and an acceptance criterion. Handoff receipt is not
  completion. The application, not this schema, decides whether supplied evidence
  actually satisfies the criterion and permission requirements.
- Dependencies must already exist and are fixed at intake, so cycles cannot be
  introduced. Readiness/active/review/completion transitions reject incomplete
  dependencies. Reopening a prerequisite does not silently mutate dependents;
  reconciliation must surface stale downstream assumptions separately.
- Writes require the expected revision; stale workers must refresh. Each state
  update and its replay result/audit event live in one atomically replaced,
  fsynced document under a process-safe advisory lock. The kernel releases locks
  after process exit. A retry after an uncertain write uses the same key.
- Reads never create files. Capacity is bounded at 1,000 items, 10,000 events and
  16 MiB. Exhaustion or malformed/unsupported state fails closed. No unfinished
  work or replay history is silently deleted; archival requires a separate design.
- `outstanding()` includes all nonterminal states, independent of any current
  GitHub decision label or session activity. `last_checked_at` only advances when
  evidence is explicitly supplied, not just because a file was read.

## Small local example (synthetic data)

```python
from continuum.commitments import Ledger

ledger = Ledger('/private/local/path/commitments.json')
item = ledger.create(
    source='https://github.com/example/synthetic/issues/1',
    title='Synthetic commitment', next_action='Assign accountable owner',
    actor='operator', key='intake-example-1')
item = ledger.update(
    item['id'], expected_revision=item['revision'], state='ready',
    owner='operator', next_action='Implement the agreed change',
    actor='operator', key='ready-example-1')
```

The path is illustrative, not a discovered runtime location. No code executes the
next action. `snapshot()` returns items/events and replay records;
`outstanding()` returns only unresolved items.

## Recovery integration (not activated)

`recovery_proposals(packets)` accepts explicitly supplied parsed decisions dispatch
packets. It groups source identities and preserves action IDs without importing
private context, notes or assumed owners. Even a packet saying `dispatched`
produces **proposed**, not active or done. This pure preview performs no IO.

Next integration sequence:

1. Identify the actual private runtime and take an authorized, consistent read-only
   copy of decision events/packets. Preserve originals; do not scan arbitrary homes.
2. Preview all historical packets, including ones no longer present in the labelled
   GitHub queue. Reconcile against existing commitments by stable source identity.
3. Re-verify source state and linked results. An open/closed issue, a lost label,
   or old dispatched string alone is insufficient completion evidence. Obtain an
   owner and next action for unresolved items, then explicitly import approved
   records. Keep ambiguous execution outcomes blocked; never blindly redispatch.
4. Add a read-only dashboard projection over this ledger and adapters that record
   accepted handoffs and later result verification, without making decisions or
   dispatches themselves. Keep this private and separate from synthetic Pages.
5. Add a separately authorized scheduler/monitor. Demonstrate a seeded commitment
   surviving handoff failure, restart, blocked state and independent closure.

PR #7 fixes immediate queued-label retention. This module is independent of that
patch and intentionally does not change its files. Neither alone constitutes an
operational end-to-end queue. Operator retry/resolution, automatic reconciliation,
UI/API adapters, current runtime access, deployment and scheduling remain work.

## Verification

All test inputs are synthetic. Tests cover restart and replay, lifecycle/owner/
evidence gates, stable identity, duplicate intake, optimistic conflicts,
dependencies, concurrent processes, failure before atomic replacement, corruption,
capacity and privacy-minimizing recovery previews. Run:

```sh
python -m pytest server/tests/test_commitments.py -q
```

## Explicit operator commands

A local CLI makes intake and reconciliation usable without starting a service:

```sh
cd server
python -m continuum.commitments_cli outstanding --path /private/local/path/commitments.json
python -m continuum.commitments_cli attention --path /private/local/path/commitments.json --stale-after 86400
python -m continuum.commitments_cli create --path /private/local/path/commitments.json < intake.json
python -m continuum.commitments_cli update --path /private/local/path/commitments.json < transition.json
python -m continuum.commitments_cli recovery-preview < explicitly-selected-packets.json
```

Mutation JSON fields match the `Ledger.create`/`Ledger.update` keyword arguments.
The path must be explicit: the command never guesses a runtime, scans a home, or
starts a dispatcher. `attention` flags unresolved ownership, blocked work,
unconfirmed handoffs, incomplete dependencies and stale evidence without changing
anything. Source closure or a missing label never implicitly closes a commitment.
Output may contain private information: keep stdout in the same private context.
A rejected command exits 2 without echoing the request or private path. If a write
outcome is uncertain, replay the same request and idempotency key before retrying
with new authority or a different revision. This CLI is not a scheduler.
