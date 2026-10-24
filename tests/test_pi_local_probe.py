import json

import pytest

from focuspilot.pi_local_probe import check_discovery, check_state, isolated_environment


def state_record(**changes):
    record = {'id': 'local-state', 'type': 'response', 'command': 'get_state',
              'success': True, 'data': {'isStreaming': False, 'messageCount': 0,
                                      'pendingMessageCount': 0}}
    record.update(changes)
    return (json.dumps(record) + '\n').encode()


@pytest.mark.parametrize('changes', [
    {'id': 'old-run'}, {'success': False}, {'command': 'prompt'},
    {'data': {'isStreaming': True, 'messageCount': 0, 'pendingMessageCount': 0}},
    {'data': {'isStreaming': False, 'messageCount': 1, 'pendingMessageCount': 0}},
])
def test_state_rejects_unrelated_failed_or_nonempty_session(changes):
    with pytest.raises(RuntimeError):
        check_state(state_record(**changes))


def test_state_rejects_duplicate_acknowledgements():
    with pytest.raises(RuntimeError):
        check_state(state_record() + state_record())


def test_environment_does_not_inherit_credentials_or_node_injection(tmp_path, monkeypatch):
    for key in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'NODE_OPTIONS',
                'PI_CODING_AGENT_DIR', 'PYTHONPATH'):
        monkeypatch.setenv(key, 'untrusted-inherited-value')
    env = isolated_environment(tmp_path)
    assert 'OPENAI_API_KEY' not in env and 'ANTHROPIC_API_KEY' not in env
    assert 'NODE_OPTIONS' not in env
    assert env['PI_CODING_AGENT_DIR'] == str(tmp_path / 'agent')
    assert env['PI_OFFLINE'] == '1'
    assert 'untrusted-inherited-value' not in env.values()


@pytest.mark.parametrize('report', [
    {'servers': [], 'errors': []},
    {'servers': [{'name': 'focuspilot', 'state': 'failed', 'tools': []}]},
    {'servers': [{'name': 'focuspilot', 'state': 'connected',
                  'tools': ['list_tasks', 'find_duplicate_tasks', 'write']}]},
])
def test_discovery_rejects_missing_failed_or_extra_tools(report):
    with pytest.raises(RuntimeError):
        check_discovery(report)


def test_successful_local_checks():
    assert check_state(state_record())['messageCount'] == 0
    result = check_discovery({'servers': [{
        'name': 'focuspilot', 'state': 'connected',
        'tools': ['list_tasks', 'find_duplicate_tasks'],
        'source': 'not-in-report',
    }], 'errors': []})
    assert result['state'] == 'connected'
    assert 'source' not in result
