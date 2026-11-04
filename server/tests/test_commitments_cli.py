import io
import json

import pytest

from continuum.commitments_cli import attention, main
from continuum.commitments import Ledger


def call(tmp_path, command, request=None):
    output, error = io.StringIO(), io.StringIO()
    status = main([command, '--path', str(tmp_path / 'ledger.json')],
                  stdin=io.StringIO(json.dumps(request)), stdout=output, stderr=error)
    assert status == 0, error.getvalue()
    return json.loads(output.getvalue())


def test_cli_intake_to_evidenced_closure(tmp_path):
    item = call(tmp_path, 'create', dict(source='https://example.com/work/1', title='Synthetic work',
                next_action='Assign', actor='operator', key='intake'))
    rows = call(tmp_path, 'attention')
    assert rows[0]['reasons'] == ['owner_missing', 'evidence_stale']
    for state in ['ready', 'active', 'blocked', 'active', 'review', 'done']:
        request = dict(item_id=item['id'], expected_revision=item['revision'], state=state,
                       owner='operator', next_action=None if state == 'done' else 'Verify next step',
                       actor='operator', key=f'{state}-{item["revision"]}')
        if state == 'blocked':
            request['blocker'] = 'Wait for synthetic prerequisite'
        if state == 'done':
            request['completion'] = {'verified_by':'reviewer','criterion':'Outcome observed'}
            request['evidence'] = {'reference':'https://example.com/proof/1','summary':'Verified synthetic result'}
        item = call(tmp_path, 'update', request)
    assert call(tmp_path, 'outstanding') == []
    assert len(call(tmp_path, 'snapshot')['events']) == 7


def test_cli_read_only_missing_path_and_recovery_preview(tmp_path):
    assert call(tmp_path, 'outstanding') == []
    assert not (tmp_path / 'ledger.json').exists()
    output = io.StringIO()
    assert main(['recovery-preview'], stdin=io.StringIO('[]'), stdout=output) == 0
    assert output.getvalue().strip() == '[]'


def test_cli_rejection_does_not_echo_private_request(tmp_path):
    output, error = io.StringIO(), io.StringIO()
    assert main(['create', '--path', str(tmp_path / 'ledger.json')],
                stdin=io.StringIO('{"private":"SECRET-EXAMPLE"}'), stdout=output, stderr=error) == 2
    assert 'SECRET-EXAMPLE' not in error.getvalue()
    assert not (tmp_path / 'ledger.json').exists()


def test_attention_rejects_nonfinite_interval(tmp_path):
    output, error = io.StringIO(), io.StringIO()
    assert main(['attention', '--path', str(tmp_path / 'ledger.json'), '--stale-after', 'nan'],
                stdout=output, stderr=error) == 2
    assert not (tmp_path / 'ledger.json').exists()


@pytest.mark.parametrize('item', [None, []])
def test_recovery_preview_rejects_malformed_item_without_traceback(item):
    output, error = io.StringIO(), io.StringIO()
    assert main(['recovery-preview'], stdin=io.StringIO(json.dumps([{'item':item}])),
                stdout=output, stderr=error) == 2
    assert not output.getvalue()
    assert 'Traceback' not in error.getvalue()
