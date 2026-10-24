import json
import sqlite3

import pytest

from focuspilot.rpc import JsonLines, ProtocolError, ReadOnlyTaskTools, RunPolicy, RunState
from focuspilot.storage import TaskStore


def encoded(record):
    return json.dumps(record, ensure_ascii=False).encode('utf-8') + b'\n'


def ack(run, disposition='started', **extra):
    return {'type': 'response', 'id': run.command_id, 'command': 'prompt',
            'success': True, 'data': {'disposition': disposition}, **extra}


def finish(run, text='只读分析结果'):
    run.receive({'type': 'message_end', 'message': {
        'role': 'assistant', 'content': [{'type': 'text', 'text': text}], 'stopReason': 'stop'}})
    run.receive({'type': 'agent_settled'})


def test_jsonl_handles_fragmented_utf8_crlf_and_unicode_separators():
    parser = JsonLines()
    data = encoded({'type': 'message', 'text': '中文\u2028仍在同一条记录'})
    split = data.index('中'.encode()) + 1
    assert parser.feed(data[:split]) == []
    assert parser.feed(data[split:-1] + b'\r\n' + encoded({'type': 'agent_settled'})) == [
        {'type': 'message', 'text': '中文\u2028仍在同一条记录'}, {'type': 'agent_settled'}]
    parser.finish()


@pytest.mark.parametrize('data', [b'[]\n', b'{bad}\n', b'{"type":"x","n":NaN}\n', b'\xff\n'])
def test_jsonl_rejects_invalid_records(data):
    with pytest.raises(ProtocolError):
        JsonLines().feed(data)


def test_jsonl_bounds_and_truncated_eof():
    with pytest.raises(ProtocolError):
        JsonLines(max_record_bytes=16).feed(b'x' * 17)
    parser = JsonLines()
    parser.feed(b'{"type":')
    with pytest.raises(ProtocolError):
        parser.finish()


def test_excessive_json_nesting_is_a_protocol_error():
    with pytest.raises(ProtocolError):
        JsonLines().feed(b'{"type":"x","data":' + b'[' * 2000 + b'0' + b']' * 2000 + b'}\n')


def test_acceptance_and_agent_end_are_not_completion():
    run = RunState('input', now=10)
    run.receive(ack(run))
    run.receive({'type': 'agent_end'})
    assert run.state == 'running'
    assert not run.terminal and run.result is None
    finish(run)
    assert run.state == 'succeeded'


def test_fast_completion_waits_for_correct_ack_and_never_regresses():
    run = RunState('input', now=0)
    finish(run)
    assert not run.terminal
    run.receive({**ack(run), 'id': 'other'})
    assert not run.terminal
    run.receive(ack(run))
    assert run.state == 'succeeded'
    run.receive({'type': 'agent_start'})
    assert run.state == 'succeeded'


@pytest.mark.parametrize('disposition', ['handled', 'queued', 'unknown'])
def test_unsupported_prompt_dispositions_fail_without_waiting_forever(disposition):
    run = RunState('input', now=0)
    run.receive(ack(run, disposition))
    assert run.state == 'failed'


def test_rejected_prompt_and_settled_without_result_fail():
    run = RunState('input', now=0)
    run.receive(ack(run, success=False, error='rejected'))
    assert run.state == 'failed'
    other = RunState('input', now=0)
    other.receive(ack(other))
    other.receive({'type': 'agent_settled'})
    assert other.state == 'failed'


def test_retry_can_recover_but_final_error_is_not_success():
    run = RunState('input', now=0)
    run.receive(ack(run))
    run.receive({'type': 'message_end', 'message': {
        'role': 'assistant', 'stopReason': 'error', 'errorMessage': 'provider failed'}})
    run.receive({'type': 'agent_end'})
    assert not run.terminal
    run.receive({'type': 'auto_retry_start'})
    run.receive({'type': 'auto_retry_end', 'success': True})
    finish(run)
    assert run.state == 'succeeded'
    bad = RunState('input', now=0)
    bad.receive(ack(bad))
    bad.receive({'type': 'message_end', 'message': {
        'role': 'assistant', 'stopReason': 'error', 'errorMessage': 'failed'}})
    bad.receive({'type': 'agent_settled'})
    assert bad.state == 'failed'


def test_cancel_and_deadline_are_terminal_even_with_late_results():
    run = RunState('original', now=10, policy=RunPolicy(timeout_seconds=5))
    run.receive(ack(run))
    run.cancel()
    finish(run)
    assert run.state == 'cancelled' and run.result is None and run.text == 'original'
    timed = RunState('original', now=10, policy=RunPolicy(timeout_seconds=5))
    timed.receive(ack(timed))
    timed.receive({'type': 'message_update'})
    timed.check_deadline(15)
    finish(timed)
    assert timed.state == 'timed_out' and timed.result is None


def test_tool_events_do_not_authorize_writes_and_budget_is_bounded():
    run = RunState('input', now=0, policy=RunPolicy(max_tool_calls=1))
    run.receive(ack(run))
    run.receive({'type': 'tool_execution_start', 'toolName': 'list_tasks', 'toolCallId': 't1'})
    run.receive({'type': 'tool_execution_start', 'toolName': 'list_tasks', 'toolCallId': 't2'})
    assert run.state == 'failed'
    bad = RunState('input', now=0)
    bad.receive({'type': 'tool_execution_start', 'toolName': 'bash', 'toolCallId': 't1'})
    assert bad.state == 'failed'


def test_recovered_tool_failure_and_nonfinal_assistant_text():
    run = RunState('input', now=0)
    run.receive(ack(run))
    run.receive({'type': 'tool_execution_end', 'toolName': 'list_tasks', 'isError': True})
    run.receive({'type': 'tool_execution_end', 'toolName': 'list_tasks', 'isError': False})
    finish(run)
    assert run.state == 'succeeded'
    unfinished = RunState('input', now=0)
    unfinished.receive(ack(unfinished))
    unfinished.receive({'type': 'message_end', 'message': {'role': 'assistant',
                        'stopReason': 'toolUse', 'content': [{'type': 'text', 'text': '正在查询'}]}})
    unfinished.receive({'type': 'agent_settled'})
    assert unfinished.state == 'failed'


@pytest.mark.parametrize('name,allowed', [
    ('mcp__focuspilot__list_tasks', True),
    ('mcp__other__list_tasks', False),
    ('mcp__focuspilot__complete_task', False),
])
def test_mcp_event_aliases_are_exact_and_readonly(name, allowed):
    run = RunState('input', now=0)
    run.receive(ack(run))
    run.receive({'type': 'tool_execution_start', 'toolName': name, 'toolCallId': 'mcp-1'})
    assert (run.state == 'running') is allowed


def test_readonly_tools_bound_fields_pending_scope_and_same_title(tmp_path):
    path = tmp_path / 'tasks.sqlite3'
    store = TaskStore(path)
    a = store.create('周报', '2026-10-24T17:00:00')
    b = store.create('周报', '2026-10-25T17:00:00')
    old = store.create('周报')
    store.complete(old.id)
    store.create('整理每周汇报')
    before = store.list_tasks()
    tools = ReadOnlyTaskTools(path)
    matched = tools.call('find_duplicate_tasks', {'titles': [' 周报 ']})
    assert {t['id'] for t in matched['tasks']} == {a.id, b.id}
    assert not matched['truncated']
    assert set(matched['tasks'][0]) == {'id', 'title', 'due_at', 'importance', 'estimated_minutes'}
    assert len(tools.call('list_tasks', {'limit': 1})['tasks']) == 1
    assert tools.call('list_tasks', {'limit': 1})['truncated']
    assert store.list_tasks() == before
    with tools.connect() as connection:
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute('DELETE FROM tasks')


@pytest.mark.parametrize('name,args', [
    ('complete_task', {'id': 'x'}), ('list_tasks', {'sql': 'DELETE FROM tasks'}),
    ('list_tasks', {'limit': True}), ('list_tasks', {'limit': 101}),
    ('find_duplicate_tasks', {'titles': ['']}),
    ('find_duplicate_tasks', {'titles': ['x'], 'confirmed': True}),
])
def test_tool_contract_rejects_unsafe_arguments(tmp_path, name, args):
    with pytest.raises(ValueError):
        ReadOnlyTaskTools(tmp_path / 'missing.sqlite3').call(name, args)


def test_missing_database_is_failure_not_empty_or_auto_created(tmp_path):
    path = tmp_path / 'missing.sqlite3'
    with pytest.raises(sqlite3.Error):
        ReadOnlyTaskTools(path).call('list_tasks', {})
    assert not path.exists()
