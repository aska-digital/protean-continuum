"""Explicit local operator interface; never scans, dispatches or contacts services.

Run from server: python -m continuum.commitments_cli --help.
Mutation requests are JSON on stdin, never shell arguments containing private text.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time

from .commitments import CommitmentError, Ledger, MAX_BYTES, recovery_proposals


def attention(ledger, *, now=None, stale_after=86400):
    """Read-only review hints, not automated completion or ownership decisions."""
    if not math.isfinite(stale_after) or stale_after <= 0:
        raise CommitmentError('stale interval must be positive')
    ts = time.time() if now is None else now
    snapshot = ledger.snapshot()
    rows = []
    for item in snapshot['items'].values():
        if item['state'] in {'done', 'cancelled'}:
            continue
        reasons = []
        if not item['owner']:
            reasons.append('owner_missing')
        if item['state'] == 'blocked':
            reasons.append('blocked')
        if item['state'] == 'queued':
            reasons.append('handoff_unconfirmed')
        if item['last_checked_at'] is None or ts - item['last_checked_at'] >= stale_after:
            reasons.append('evidence_stale')
        if any(snapshot['items'][dep]['state'] != 'done' for dep in item['dependencies']):
            reasons.append('dependency_incomplete')
        rows.append({'id': item['id'], 'source': item['source'], 'state': item['state'],
                     'owner': item['owner'], 'next_action': item['next_action'],
                     'blocker': item['blocker'], 'last_checked_at': item['last_checked_at'],
                     'reasons': reasons})
    return rows


def _input(stream):
    raw = stream.read(MAX_BYTES + 1)
    if len(raw.encode('utf-8')) > MAX_BYTES:
        raise CommitmentError('request exceeds size limit')
    return json.loads(raw)


def main(argv=None, stdin=None, stdout=None, stderr=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['snapshot', 'outstanding', 'attention', 'create', 'update', 'recovery-preview'])
    parser.add_argument('--path', help='Private local commitment JSON path; required except for recovery-preview')
    parser.add_argument('--stale-after', type=float, default=86400)
    args = parser.parse_args(argv)
    stdin, stdout, stderr = stdin or sys.stdin, stdout or sys.stdout, stderr or sys.stderr
    try:
        if args.command == 'recovery-preview':
            packets = _input(stdin)
            if not isinstance(packets, list) or not all(isinstance(p, dict) and isinstance(p.get('item', {}), dict) for p in packets):
                raise CommitmentError('recovery preview requires a JSON array of packet objects')
            result = recovery_proposals(packets)
        else:
            if not args.path:
                raise CommitmentError('--path is required; no implicit runtime is selected')
            ledger = Ledger(args.path)
            if args.command == 'attention':
                result = attention(ledger, stale_after=args.stale_after)
            elif args.command in {'snapshot', 'outstanding'}:
                result = getattr(ledger, args.command)()
            else:
                request = _input(stdin)
                if not isinstance(request, dict):
                    raise CommitmentError('mutation requires a JSON request object')
                result = getattr(ledger, args.command)(**request)
        json.dump(result, stdout, sort_keys=True, allow_nan=False)
        stdout.write('\n')
        return 0
    except (CommitmentError, ValueError, TypeError, KeyError, OSError):
        # Do not echo a request, path, or private value into diagnostic output.
        stderr.write('Commitment operation rejected; check the request, revision, private path and ledger integrity. No dispatch was attempted.\n')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
