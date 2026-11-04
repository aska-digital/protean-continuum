"""Opt-in private commitment ledger. No network, dispatch, or background writes.

One atomically replaced JSON document keeps state, audit events and replay keys
consistent. Existing registry schema is untouched. Reads never create files.
"""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time
from urllib.parse import urlsplit, urlunsplit

VERSION = 1
MAX_ITEMS = 1000
MAX_EVENTS = 10000
MAX_BYTES = 16 * 1024 * 1024
STATES = {'proposed', 'ready', 'queued', 'active', 'blocked', 'review', 'done', 'deferred', 'cancelled'}
TERMINAL = {'done', 'cancelled'}
TRANSITIONS = {
    'proposed': {'ready', 'queued', 'blocked', 'deferred', 'cancelled'},
    'ready': {'queued', 'active', 'blocked', 'deferred', 'cancelled'},
    'queued': {'active', 'blocked', 'deferred', 'cancelled'},
    'active': {'blocked', 'review', 'cancelled'},
    'blocked': {'ready', 'queued', 'active', 'deferred', 'cancelled'},
    'review': {'active', 'blocked', 'done', 'cancelled'},
    'deferred': {'ready', 'queued', 'blocked', 'cancelled'},
    'done': {'ready'},  # explicit reopening, never inferred from a sync
    'cancelled': {'ready'},
}


class CommitmentError(ValueError):
    pass


def _text(value, name, limit=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise CommitmentError(f'{name} must be nonempty text of at most {limit} characters')
    return value.strip()


def source_url(value):
    """Canonical identity excludes credential/query/fragment leakage."""
    value = _text(value, 'source URL')
    parts = urlsplit(value)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise CommitmentError('source URL must be HTTPS without credentials, query or fragment')
    return urlunsplit(('https', parts.netloc.lower(), parts.path.rstrip('/'), '', ''))


def identity(url):
    return 'work-' + hashlib.sha256(source_url(url).encode()).hexdigest()[:24]


def _empty():
    return {'version': VERSION, 'items': {}, 'events': [], 'operations': {}}


def _validate_document(doc):
    """Reject malformed but parseable snapshots instead of hiding lost work."""
    def require(condition):
        if not condition:
            raise CommitmentError('invalid ledger records; preserve them for recovery')
    def timestamp(value):
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    def item_valid(item, key):
        fields = {'id', 'source', 'title', 'state', 'owner', 'next_action', 'dependencies',
                  'blocker', 'evidence', 'completion', 'revision', 'created_at', 'updated_at', 'last_checked_at'}
        require(isinstance(item, dict) and set(item) == fields)
        require(item['id'] == key == identity(item['source']))
        _text(item['title'], 'title', 500)
        require(item['state'] in STATES and type(item['revision']) is int and item['revision'] >= 1)
        require(timestamp(item['created_at']) and timestamp(item['updated_at']) and item['created_at'] <= item['updated_at'])
        deps = item['dependencies']
        require(isinstance(deps, list) and len(deps) <= 50 and all(isinstance(d, str) and d in doc['items'] and d != key for d in deps))
        require(len(deps) == len(set(deps)))
        if item['owner'] is not None:
            _text(item['owner'], 'owner', 200)
        require(item['state'] not in {'ready', 'active', 'review', 'done'} or item['owner'] is not None)
        if item['state'] in TERMINAL:
            require(item['next_action'] is None)
        else:
            _text(item['next_action'], 'next action')
        if item['state'] == 'blocked':
            _text(item['blocker'], 'blocker')
        else:
            require(item['blocker'] is None)
        evidence = item['evidence']
        require(isinstance(evidence, list))
        for observation in evidence:
            require(isinstance(observation, dict) and set(observation) == {'reference', 'summary', 'at', 'actor'})
            source_url(observation['reference'])
            _text(observation['summary'], 'summary')
            _text(observation['actor'], 'actor', 200)
            require(timestamp(observation['at']) and item['created_at'] <= observation['at'] <= item['updated_at'])
        require(item['last_checked_at'] == (evidence[-1]['at'] if evidence else None))
        if item['state'] == 'done':
            require(isinstance(item['completion'], dict) and set(item['completion']) == {'verified_by', 'criterion'} and bool(evidence))
            for name, value in item['completion'].items():
                _text(value, name)
        else:
            require(item['completion'] is None)
    require(len(doc['items']) <= MAX_ITEMS and len(doc['events']) <= MAX_EVENTS)
    require(len(doc['events']) == len(doc['operations']))
    previous = {}
    seen = set()
    for event in doc['events']:
        require(isinstance(event, dict) and set(event) == {'key', 'actor', 'at', 'item_id', 'before', 'after'})
        key = _text(event['key'], 'event key', 200)
        _text(event['actor'], 'actor', 200)
        require(key not in seen and timestamp(event['at']))
        seen.add(key)
        item_id = event['item_id']
        require(isinstance(item_id, str) and item_id in doc['items'])
        after = event['after']
        item_valid(after, item_id)
        require(after['updated_at'] == event['at'])
        require(event['before'] == previous.get(item_id))
        require(after['revision'] == (previous[item_id]['revision'] + 1 if item_id in previous else 1))
        require(all(dep in previous for dep in after['dependencies']))
        operation = doc['operations'].get(key)
        require(isinstance(operation, dict) and set(operation) == {'digest', 'result'})
        require(isinstance(operation['digest'], str) and len(operation['digest']) == 64 and all(c in '0123456789abcdef' for c in operation['digest']))
        require(operation['result'] == after)
        previous[item_id] = after
    require(previous == doc['items'])


class Ledger:
    """Caller supplies a private local path, normally server/data/commitments.json.

    Capacity exhaustion fails closed; unfinished work and replay history are never
    silently pruned. A separate reviewed archive policy is needed at the bounds.
    """
    def __init__(self, path):
        self.path = Path(path)

    def _read(self):
        try:
            with self.path.open('rb') as stream:
                raw = stream.read(MAX_BYTES + 1)
        except FileNotFoundError:
            return _empty()
        if len(raw) > MAX_BYTES:
            raise CommitmentError('ledger size limit exceeded')
        try:
            doc = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise CommitmentError('ledger is corrupt; preserve it for recovery') from exc
        if (not isinstance(doc, dict) or doc.get('version') != VERSION or
                not isinstance(doc.get('items'), dict) or not isinstance(doc.get('events'), list) or
                not isinstance(doc.get('operations'), dict)):
            raise CommitmentError('unsupported or invalid ledger; migration required')
        try:
            _validate_document(doc)
        except (KeyError, TypeError, ValueError) as exc:
            raise CommitmentError("invalid ledger records; preserve them for recovery") from exc
        return doc

    def snapshot(self):
        return self._read()

    def outstanding(self):
        return [x for x in self._read()['items'].values() if x['state'] not in TERMINAL]

    def _write(self, doc):
        data = (json.dumps(doc, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()
        if len(data) > MAX_BYTES:
            raise CommitmentError('ledger size limit exceeded')
        fd, name = tempfile.mkstemp(prefix=self.path.name + '.', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _mutate(self, key, request, actor, now, mutate):
        key = _text(key, 'idempotency key', 200)
        actor = _text(actor, 'actor', 200)
        ts = time.time() if now is None else now
        if type(ts) not in (int, float) or not math.isfinite(ts) or ts < 0:
            raise CommitmentError('invalid timestamp')
        digest = hashlib.sha256(json.dumps({'actor': actor, 'request': request}, sort_keys=True, allow_nan=False).encode()).hexdigest()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # flock is released by the kernel on process exit; no orphaned lock lease.
        fd = os.open(str(self.path) + '.lock', os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(fd, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            doc = self._read()
            old = doc['operations'].get(key)
            if old:
                if old['digest'] != digest:
                    raise CommitmentError('idempotency key reused for a different request')
                return copy.deepcopy(old['result'])
            if len(doc['events']) >= MAX_EVENTS:
                raise CommitmentError('event capacity reached; archive review required')
            result, before = mutate(doc, ts)
            event = {'key': key, 'actor': actor, 'at': ts, 'item_id': result['id'],
                     'before': before, 'after': copy.deepcopy(result)}
            doc['events'].append(event)
            doc['operations'][key] = {'digest': digest, 'result': copy.deepcopy(result)}
            self._write(doc)
            return result

    def create(self, *, source, title, next_action, actor, key, dependencies=(), now=None):
        source = source_url(source)
        title = _text(title, 'title', 500)
        next_action = _text(next_action, 'next action')
        dependencies = list(dependencies)
        if len(dependencies) > 50 or len(set(dependencies)) != len(dependencies):
            raise CommitmentError('dependencies must be unique and bounded')
        request = dict(action='create', source=source, title=title, next_action=next_action, dependencies=dependencies)
        def apply(doc, ts):
            item_id = identity(source)
            if item_id in doc['items']:
                raise CommitmentError('source already tracked; use its existing identity')
            if len(doc['items']) >= MAX_ITEMS:
                raise CommitmentError('item capacity reached; archive review required')
            if any(dep not in doc['items'] for dep in dependencies):
                raise CommitmentError('dependency must already exist')
            item = dict(id=item_id, source=source, title=title, state='proposed', owner=None,
                        next_action=next_action, dependencies=dependencies, blocker=None,
                        evidence=[], completion=None, revision=1, created_at=ts, updated_at=ts,
                        last_checked_at=None)
            doc['items'][item_id] = item
            return item, None
        return self._mutate(key, request, actor, now, apply)

    def update(self, item_id, *, expected_revision, state, owner, next_action, actor, key,
               blocker=None, evidence=None, completion=None, now=None):
        """Explicit transition; recording handoff evidence never implies completion.

        Evidence is a caller-verified observation, not something this store checks
        against a remote service. Done requires reviewer + evidence + criterion.
        Dependencies are fixed at intake (earlier items only, hence acyclic).
        """
        if state not in STATES:
            raise CommitmentError('unknown state')
        if owner is not None:
            owner = _text(owner, 'owner', 200)
        if state not in TERMINAL:
            next_action = _text(next_action, 'next action')
        elif next_action is not None:
            raise CommitmentError('terminal state has no next action')
        if state in {'ready', 'active', 'review', 'done'} and owner is None:
            raise CommitmentError('this state requires an accountable owner')
        if state == 'blocked':
            blocker = _text(blocker, 'blocker/release condition')
        elif blocker is not None:
            raise CommitmentError('blocker is only valid in blocked state')
        if evidence is not None:
            if not isinstance(evidence, dict) or set(evidence) != {'reference', 'summary'}:
                raise CommitmentError('evidence requires reference and summary')
            evidence = {'reference': source_url(evidence['reference']), 'summary': _text(evidence['summary'], 'evidence summary')}
        if state == 'done':
            if not isinstance(completion, dict) or set(completion) != {'verified_by', 'criterion'} or evidence is None:
                raise CommitmentError('completion requires verified_by, criterion and evidence')
            completion = {k: _text(v, k) for k, v in completion.items()}
        elif completion is not None:
            raise CommitmentError('completion is only valid in done state')
        request = dict(action='update', item_id=item_id, expected_revision=expected_revision, state=state,
                       owner=owner, next_action=next_action, blocker=blocker, evidence=evidence, completion=completion)
        def apply(doc, ts):
            item = doc['items'].get(item_id)
            if item is None:
                raise CommitmentError('unknown commitment')
            if item['revision'] != expected_revision:
                raise CommitmentError('revision changed; refresh before updating')
            if state != item['state'] and state not in TRANSITIONS[item['state']]:
                raise CommitmentError('invalid lifecycle transition')
            if state in {'ready', 'active', 'review', 'done'} and any(doc['items'][d]['state'] != 'done' for d in item['dependencies']):
                raise CommitmentError('dependencies are not complete')
            if ts < item['updated_at']:
                raise CommitmentError('timestamp predates the current revision')
            before = copy.deepcopy(item)
            item.update(state=state, owner=owner, next_action=next_action, blocker=blocker,
                        completion=completion, revision=item['revision'] + 1, updated_at=ts)
            if evidence:
                item['evidence'].append({**evidence, 'at': ts, 'actor': actor})
                item['last_checked_at'] = ts
            return item, before
        return self._mutate(key, request, actor, now, apply)


def recovery_proposals(packets):
    """Pure preview over explicitly supplied existing decision packet dictionaries.

    No file discovery, copying of context/note/body, writes, or execution. Historical
    packet status does not prove an accepted handoff, let alone completed work.
    """
    proposals = {}
    if len(packets) > MAX_ITEMS:
        raise CommitmentError('recovery batch exceeds capacity')
    for packet in packets:
        item = packet.get('item', {})
        url = source_url(item.get('url'))
        item_id = identity(url)
        proposal = proposals.setdefault(item_id, {
            'id': item_id, 'source': url, 'title': _text(item.get('title'), 'title', 500),
            'state': 'proposed', 'owner': None, 'action_ids': [],
            'next_action': 'Verify the existing dispatch packet and source outcome before assigning or retrying.',
        })
        action_id = _text(packet.get('action_id'), 'action ID', 200)
        if action_id not in proposal['action_ids']:
            proposal['action_ids'].append(action_id)
    return list(proposals.values())
