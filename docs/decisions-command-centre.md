# Decisions command centre

One queue for everything waiting on a maintainer decision, across every repo in
the organisation. The feed is the org-wide GitHub label `needs decision`
("Waiting on a maintainer decision"). Any seat can put work in the queue by
applying that label to an issue or pull request. Ion does it automatically:
its proposal issues carry the label while they await approval, and the pull
requests it opens carry it while they wait for a merge.

## Run it

```bash
cd server
~/.hermes/hermes-agent/venv/bin/python -m continuum.cli decisions sync   # pull the queue from GitHub
~/.hermes/hermes-agent/venv/bin/python -m dashboard.standalone           # loopback server on 8765
```

Open `http://127.0.0.1:8765/decisions.html`. The board at `/` links to it.
Sync also runs from the page's Sync GitHub button. State lives under
`server/data/` (`decisions.json`, `decision_events.jsonl`,
`decision_dispatches/`), all git-ignored. The page is loopback only by
default, and real decision titles name private repos, so never publish the
snapshot to a public surface.

## How a decision item gets its panels

Each labelled item becomes one card: the question, a short context excerpt,
and up to four tappable option panels, oldest first. Panels come from the
item itself, in this order:

1. A `## Options` (or Decision/Choices) section in the body or a comment.
   One bullet per option. A bold lead becomes the label, the rest is the
   detail, and a trailing `Cost: ...` is shown as the cost.
2. An explicit either/or sentence, or a "compares three ways: A; B; or C"
   sentence.
3. Standard panels for the kind: merge, salvage, send back, close for a pull
   request; go ahead, dispatch an agent, send back, close for an issue.

A stated preference ("Preferred: ...", "recommends ...") marks the matching
panel Recommended. Authors who want good panels should write the `## Options`
section when they apply the label.

## Acting on a decision

Pick a panel, optionally add a note, then choose how the ruling travels:

- **Comment the ruling.** One `## Decision` comment is posted on the item
  with the chosen option and the note, and `needs decision` comes off. The
  author proceeds from the comment. A hold keeps the label on.
- **Dispatch an agent.** A dispatch packet (JSON plus a markdown brief) is
  written under `data/decision_dispatches/`, the ruling is announced on the
  item, and the label comes off. If `decisions_dispatch_command` is configured
  (an argv list, never a shell string), the brief is fed to it on stdin, for
  example a Divan Bus post wrapper that hands the work to a seat. With no
  command configured the packet queues on disk and the card shows `queued`.
  The packet instructs the agent to re-verify the item, keep the original
  author's commits and attribution on a salvage, never force-push, and report
  back on the item.
- **Ion proposals record by label.** The tracker is decided by label, never by
  comment, so approve, hold and reject apply `ion:approved`, `ion:hold` or
  `ion:rejected` for the exact plan on display, and clear `needs decision`.

Safety properties hold for every mode. An action re-fetches the item and
refuses with 409 if it changed since it was shown, lost the label, or the
chosen option is gone. Actions are idempotent on a client key and carry an
HTML-comment marker, so a crash or a double tap never posts twice. Sync and
act require the per-process session token the page fetches from
`/decisions/session`, because act writes to GitHub. Every action is appended
to `decision_events.jsonl` (fsync'd) before the response returns.

## Configuration

Keys live in `server/config.yaml`: `decisions_org`, `decisions_label`, the
three state paths, `decisions_ion_tracker_repo`,
`decisions_stale_after_seconds`, and `decisions_dispatch_command`.
