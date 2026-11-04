"""Synthetic-only acceptance tests for the opt-in private ledger."""
import json
import multiprocessing
import os
from pathlib import Path

import pytest

from continuum import commitments as work


def create(ledger, n=1, **kwargs):
    return ledger.create(source=f'https://github.com/example/synthetic/issues/{n}',
                         title=f'Task {n}', next_action='Assign an owner', actor='test', key=f'create-{n}', **kwargs)


def move(ledger, item, state, *, key=None, **kwargs):
    return ledger.update(item['id'], expected_revision=item['revision'], state=state,
                         owner='synthetic-owner', next_action=None if state in work.TERMINAL else 'Next action',
                         actor='test', key=key or f"{item['id']}-{item['revision']}-{state}", **kwargs)


def test_missing_snapshot_read_has_no_side_effects(tmp_path):
    ledger = work.Ledger(tmp_path / 'missing' / 'ledger.json')
    assert ledger.outstanding() == []
    assert ledger.snapshot()['version'] == 1
    assert not ledger.path.parent.exists()


def test_restart_identity_and_complete_lifecycle(tmp_path):
    path = tmp_path / 'ledger.json'
    ledger = work.Ledger(path)
    item = create(ledger, now=1)
    assert create(work.Ledger(path), now=2) == item  # ambiguous outcome replay
    item = move(ledger, item, 'queued', now=2)
    item = move(ledger, item, 'active', now=3,
                evidence={'reference':'https://example.com/acceptance/1', 'summary':'Worker accepted; work incomplete'})
    assert len(work.Ledger(path).outstanding()) == 1
    item = move(ledger, item, 'blocked', blocker='Wait for dependency release', now=4)
    item = move(ledger, item, 'active', now=5)
    item = move(ledger, item, 'review', now=6)
    item = move(ledger, item, 'done', now=7,
                evidence={'reference':'https://example.com/results/1', 'summary':'Observed agreed outcome'},
                completion={'verified_by':'independent-reviewer', 'criterion':'Synthetic acceptance criterion passed'})
    assert work.Ledger(path).outstanding() == []
    assert len(ledger.snapshot()['events']) == 7
    assert item['last_checked_at'] == 7
    assert os.stat(path).st_mode & 0o777 == 0o600
    reopened = move(ledger, item, 'ready', now=8)
    assert reopened['completion'] is None
    assert len(reopened['evidence']) == 2
    assert len(ledger.outstanding()) == 1


def test_source_canonicalization_and_replay_conflicts(tmp_path):
    ledger = work.Ledger(tmp_path / 'ledger.json')
    item = create(ledger)
    assert work.identity(item['source'] + '/') == item['id']
    with pytest.raises(work.CommitmentError, match='already tracked'):
        ledger.create(source=item['source'], title='Duplicate', next_action='Assign', actor='test', key='other')
    with pytest.raises(work.CommitmentError, match='different request'):
        ledger.create(source=item['source'], title='Changed', next_action='Assign', actor='test', key='create-1')
    assert len(ledger.snapshot()['events']) == 1


@pytest.mark.parametrize('url', ['http://example.com/a', 'https://user:secret@example.com/a',
                               'https://example.com/a?token=secret', 'https://example.com/a#private'])
def test_reject_credentials_query_fragment_and_plain_http(url):
    with pytest.raises(work.CommitmentError):
        work.identity(url)


def test_optimistic_revision_and_invalid_transition_leave_state_unchanged(tmp_path):
    ledger = work.Ledger(tmp_path / 'ledger.json')
    item = create(ledger)
    queued = move(ledger, item, 'queued')
    with pytest.raises(work.CommitmentError, match='revision changed'):
        move(ledger, item, 'ready')
    with pytest.raises(work.CommitmentError, match='invalid lifecycle'):
        move(ledger, queued, 'done', completion={'verified_by':'reviewer','criterion':'test'},
             evidence={'reference':'https://example.com/result','summary':'result'})
    assert ledger.snapshot()['items'][item['id']] == queued


def test_owner_blocker_and_completion_evidence_required(tmp_path):
    ledger = work.Ledger(tmp_path / 'ledger.json')
    item = create(ledger)
    with pytest.raises(work.CommitmentError, match='owner'):
        ledger.update(item['id'], expected_revision=1, state='ready', owner=None,
                      next_action='Next', actor='test', key='no-owner')
    with pytest.raises(work.CommitmentError, match='blocker'):
        move(ledger, item, 'blocked')
    item = move(ledger, item, 'ready')
    item = move(ledger, item, 'active')
    item = move(ledger, item, 'review')
    with pytest.raises(work.CommitmentError, match='completion requires'):
        move(ledger, item, 'done')
    assert len(ledger.outstanding()) == 1


def test_dependencies_are_existing_acyclic_and_gate_readiness(tmp_path):
    ledger = work.Ledger(tmp_path / 'ledger.json')
    prerequisite = create(ledger)
    dependent = create(ledger, 2, dependencies=[prerequisite['id']])
    with pytest.raises(work.CommitmentError, match='dependencies are not complete'):
        move(ledger, dependent, 'ready')
    with pytest.raises(work.CommitmentError, match='already exist'):
        create(ledger, 3, dependencies=['missing'])
    for state in ['ready', 'active', 'review']:
        prerequisite = move(ledger, prerequisite, state)
    move(ledger, prerequisite, 'done', completion={'verified_by':'reviewer','criterion':'passed'},
         evidence={'reference':'https://example.com/proof','summary':'Observed pass'})
    assert move(ledger, dependent, 'ready')['state'] == 'ready'


def test_failed_replace_preserves_previous_document_and_retry(tmp_path, monkeypatch):
    ledger = work.Ledger(tmp_path / 'ledger.json')
    item = create(ledger)
    original = ledger.path.read_bytes()
    replace = work.os.replace
    def fail(*args):
        raise OSError('synthetic disk failure')
    monkeypatch.setattr(work.os, 'replace', fail)
    with pytest.raises(OSError):
        move(ledger, item, 'queued')
    assert ledger.path.read_bytes() == original
    monkeypatch.setattr(work.os, 'replace', replace)
    assert move(ledger, item, 'queued')['revision'] == 2
    assert len(ledger.snapshot()['events']) == 2


def _concurrent_create(path, n):
    create(work.Ledger(path), n)


def test_multiple_processes_do_not_lose_updates(tmp_path):
    path = str(tmp_path / 'ledger.json')
    context = multiprocessing.get_context('spawn')
    children = [context.Process(target=_concurrent_create, args=(path,n)) for n in range(6)]
    for process in children:
        process.start()
    for process in children:
        process.join(10)
        assert process.exitcode == 0
    doc = work.Ledger(path).snapshot()
    assert len(doc['items']) == len(doc['events']) == len(doc['operations']) == 6


def test_capacity_and_corruption_fail_closed(tmp_path, monkeypatch):
    ledger = work.Ledger(tmp_path / 'ledger.json')
    item = create(ledger)
    original = ledger.path.read_bytes()
    monkeypatch.setattr(work, 'MAX_EVENTS', 1)
    with pytest.raises(work.CommitmentError, match='capacity'):
        move(ledger, item, 'queued')
    assert ledger.path.read_bytes() == original
    ledger.path.write_text('{bad json')
    with pytest.raises(work.CommitmentError, match='corrupt'):
        create(ledger, 2)
    assert ledger.path.read_text() == '{bad json'


def test_recovery_preview_deduplicates_without_trusting_status_or_copying_context():
    packet = {'action_id':'dec-1', 'status':'dispatched', 'note':'PRIVATE NOTE',
              'item':{'url':'https://github.com/example/synthetic/issues/1', 'title':'Synthetic task',
                      'context':'PRIVATE CONTEXT', 'author':'unverified-owner'}}
    previews = work.recovery_proposals([packet, {**packet, 'action_id':'dec-2'}])
    assert len(previews) == 1
    assert previews[0]['action_ids'] == ['dec-1','dec-2']
    assert previews[0]['state'] == 'proposed' and previews[0]['owner'] is None
    assert 'PRIVATE' not in json.dumps(previews)
    assert 'unverified-owner' not in json.dumps(previews)


@pytest.mark.parametrize('damage', ['missing-fields', 'event-null', 'audit-mismatch', 'replay-mismatch', 'missing-owner'])
def test_valid_json_corruption_is_rejected_before_read_or_mutation(tmp_path, damage):
    ledger = work.Ledger(tmp_path / 'ledger.json')
    item = create(ledger)
    doc = ledger.snapshot()
    if damage == 'missing-fields':
        doc['items'][item['id']] = {'state':'done'}
    elif damage == 'event-null':
        doc['events'][0] = None
    elif damage == 'audit-mismatch':
        doc['items'][item['id']]['title'] = 'Unrecorded change'
    elif damage == 'replay-mismatch':
        doc['operations']['create-1']['result']['revision'] = 9
    else:
        for target in [doc['items'][item['id']], doc['events'][0]['after'], doc['operations']['create-1']['result']]:
            target['state'] = 'ready'
    ledger.path.write_text(json.dumps(doc))
    original = ledger.path.read_bytes()
    with pytest.raises(work.CommitmentError, match='invalid ledger'):
        ledger.outstanding()
    with pytest.raises(work.CommitmentError, match='invalid ledger'):
        create(ledger, 2)
    assert ledger.path.read_bytes() == original


def test_failure_after_replace_replays_the_committed_operation(tmp_path, monkeypatch):
    ledger = work.Ledger(tmp_path / 'ledger.json')
    item = create(ledger)
    fsync = work.os.fsync
    count = []
    def fail_directory(fd):
        count.append(fd)
        if len(count) == 2:
            raise OSError('synthetic directory fsync failure after replace')
        fsync(fd)
    monkeypatch.setattr(work.os, 'fsync', fail_directory)
    with pytest.raises(OSError):
        move(ledger, item, 'queued')
    monkeypatch.setattr(work.os, 'fsync', fsync)
    result = move(work.Ledger(ledger.path), item, 'queued')
    assert result['revision'] == 2
    assert len(ledger.snapshot()['events']) == 2


def _competing_update(path, item, key, results):
    try:
        move(work.Ledger(path), item, 'queued', key=key)
        results.put('committed')
    except work.CommitmentError:
        results.put('conflict')


def test_competing_revisions_allow_one_commit(tmp_path):
    ledger = work.Ledger(tmp_path / 'ledger.json')
    item = create(ledger)
    context = multiprocessing.get_context('spawn')
    results = context.Queue()
    children = [context.Process(target=_competing_update, args=(str(ledger.path), item, f'writer-{n}', results)) for n in range(2)]
    for process in children:
        process.start()
    for process in children:
        process.join(10)
        assert process.exitcode == 0
    assert sorted(results.get(timeout=1) for _ in children) == ['committed','conflict']
    assert len(ledger.snapshot()['events']) == 2


@pytest.mark.parametrize('value', [True, float('nan'), float('inf'), -1])
def test_bad_timestamp_never_creates_state(tmp_path, value):
    ledger = work.Ledger(tmp_path / 'ledger.json')
    with pytest.raises(work.CommitmentError, match='timestamp'):
        create(ledger, now=value)
    assert not ledger.path.exists()
