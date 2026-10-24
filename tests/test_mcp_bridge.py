import json
import pytest

pytest.importorskip('mcp')
from focuspilot.mcp_bridge import BridgePolicy, Frames, BridgeRejected


def request(method, params=None, ident=1):
    record = {'jsonrpc': '2.0', 'method': method, 'id': ident}
    if params is not None:
        record['params'] = params
    return record


def ready(policy):
    policy.accept(request('initialize', {'protocolVersion': '2025-11-25',
        'capabilities': {}, 'clientInfo': {'name': 'fixture', 'version': '1'}}), now=0)
    policy.accept({'jsonrpc': '2.0', 'method': 'notifications/initialized'}, now=0)


def test_bridge_protocol_and_exact_tool_contract():
    policy = BridgePolicy(now=0)
    ready(policy)
    policy.accept(request('tools/list', ident=2), now=1)
    policy.accept(request('tools/call', {'name': 'list_tasks', 'arguments': {'limit': 5}}, 3), now=2)
    policy.accept(request('tools/call', {'name': 'find_duplicate_tasks',
        'arguments': {'titles': ['fictional']}}, 4), now=2)
    assert policy.attempts == 2


@pytest.mark.parametrize('params', [
    {'name': 'delete_task', 'arguments': {'id': 'x'}},
    {'name': 'list_tasks', 'arguments': {'sql': 'UPDATE tasks SET status="done"'}},
    {'name': 'list_tasks', 'arguments': {'limit': True}},
    {'name': 'list_tasks', 'arguments': {}, 'run_id': 'forged'},
    {'name': ['list_tasks'], 'arguments': {}},
])
def test_rejected_calls_count_and_revoke_session(params):
    policy = BridgePolicy(now=0)
    ready(policy)
    with pytest.raises(BridgeRejected):
        policy.accept(request('tools/call', params, 2), now=1)
    assert policy.attempts == 1 and not policy.active
    with pytest.raises(BridgeRejected):
        policy.accept(request('tools/list', ident=3), now=1)


def test_budget_cancel_deadline_and_replayed_ids():
    policy = BridgePolicy(now=0, max_calls=1)
    ready(policy)
    policy.accept(request('tools/call', {'name': 'list_tasks'}, 2), now=1)
    with pytest.raises(BridgeRejected):
        policy.accept(request('tools/call', {'name': 'list_tasks'}, 3), now=2)
    for action in ('cancel', 'deadline', 'replay'):
        policy = BridgePolicy(now=0)
        ready(policy)
        if action == 'cancel':
            policy.cancel()
        with pytest.raises(BridgeRejected):
            policy.accept(request('tools/list', ident=1 if action == 'replay' else 2),
                          now=60 if action == 'deadline' else 1)


@pytest.mark.parametrize('method', ['resources/read', 'prompts/get', 'notifications/progress', 'sampling/createMessage'])
def test_nonquery_methods_fail_closed(method):
    policy = BridgePolicy(now=0)
    ready(policy)
    with pytest.raises(BridgeRejected):
        policy.accept(request(method, ident=2), now=1)


def test_mcp_frames_fragmentation_limits_and_invalid_json():
    frames = Frames(1000)
    raw = (json.dumps(request('tools/list'), ensure_ascii=False) + '\n').encode()
    assert frames.feed(raw[:5]) == []
    assert frames.feed(raw[5:]) == [request('tools/list')]
    for raw in (b'x' * 1001, b'{"jsonrpc":"2.0","id":NaN}\n', b'[]\n',
                b'{"id":1,"id":2}\n', ('{"x":' + '[' * 66 + '0' + ']' * 66 + '}\n').encode()):
        with pytest.raises(BridgeRejected):
            Frames(1000).feed(raw)
    frames.feed(b'{')
    with pytest.raises(BridgeRejected):
        frames.finish()


def test_responses_require_live_pending_identity_and_tool_success():
    for scenario in ('cancel', 'unknown-id', 'server-error', 'tool-error'):
        policy = BridgePolicy(now=0)
        ready(policy)
        if scenario == 'cancel':
            policy.cancel()
        record = {'jsonrpc': '2.0', 'id': 2 if scenario == 'unknown-id' else 1,
                  'result': {'isError': scenario == 'tool-error'}}
        if scenario == 'server-error':
            record.pop('result')
            record['error'] = {'code': -32000, 'message': 'fictional'}
        with pytest.raises(BridgeRejected):
            policy.response(record, now=1)


def test_response_replay_rejected():
    policy = BridgePolicy(now=0)
    ready(policy)
    result = {'jsonrpc': '2.0', 'id': 1, 'result': {}}
    policy.response(result, now=1)
    with pytest.raises(BridgeRejected):
        policy.response(result, now=2)


def test_bridge_cli_requires_optin_before_inventory_read(tmp_path):
    import subprocess
    import sys
    result = subprocess.run([sys.executable, '-m', 'focuspilot.sandbox_mcp_probe',
        '--inventory', str(tmp_path / 'missing.json')], capture_output=True, timeout=10)
    assert result.returncode == 2 and b'--approve-offline-bridge' in result.stderr
    assert b'Traceback' not in result.stderr


@pytest.mark.parametrize('identity,in_job', [
    ((True, 0, 'wrong-sid'), True), ((True, 1, 'expected-sid'), True),
    ((True, 0, 'expected-sid'), False), ((True, 0, 'expected-sid'), True),
])
def test_peer_requires_os_sid_zero_capabilities_and_job(monkeypatch, identity, in_job):
    from types import SimpleNamespace
    import win32api
    import win32job
    import focuspilot.appcontainer_probe as native_module
    from focuspilot.mcp_bridge import PipeRelay
    closed = []
    class Handle:
        def __int__(self):
            return 123
        def Close(self):
            closed.append(True)
    def get_pid(pipe, pointer):
        pointer._obj.value = 42
        return True
    api = SimpleNamespace(kernel=SimpleNamespace(GetNamedPipeClientProcessId=get_pid),
        require=lambda value, label: None, token=lambda handle: identity)
    monkeypatch.setattr(native_module, 'Native', lambda: api)
    monkeypatch.setattr(win32api, 'OpenProcess', lambda *args: Handle())
    monkeypatch.setattr(win32job, 'IsProcessInJob', lambda *args: in_job)
    relay = object.__new__(PipeRelay)
    relay.pipe, relay.sid, relay.job, relay.report = 11, 'expected-sid', 22, {}
    if identity == (True, 0, 'expected-sid') and in_job:
        relay.verify_peer()
        assert relay.report['identity_verified']
    else:
        with pytest.raises(BridgeRejected):
            relay.verify_peer()
        assert not relay.report
    assert closed == [True]
