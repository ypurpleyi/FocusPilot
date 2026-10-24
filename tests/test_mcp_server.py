import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytest.importorskip('mcp', reason='Install the optional MCP extra: pip install -e ".[dev,mcp]"')
import anyio
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from focuspilot.mcp_server import create_server
from focuspilot.storage import TaskStore


@pytest.fixture
def fixture_db(tmp_path):
    path = tmp_path / 'fixture.sqlite3'
    store = TaskStore(path)
    a = store.create('周报', '2026-10-24T17:00:00')
    b = store.create('周报', '2026-10-25T17:00:00')
    old = store.create('周报')
    store.complete(old.id)
    store.create('整理每周汇报')
    return path, store, {a.id, b.id}


def run(coroutine):
    async def bounded():
        with anyio.fail_after(8):
            return await coroutine
    return asyncio.run(bounded())


@pytest.mark.parametrize('mode', ['auto', 'legacy'])
def test_discovery_lists_only_two_tools_with_strict_contracts(fixture_db, mode):
    async def check():
        async with Client(create_server(fixture_db[0]), mode=mode) as client:
            listed = await client.list_tools()
            assert {tool.name for tool in listed.tools} == {'list_tasks', 'find_duplicate_tasks'}
            assert client.server_capabilities.resources is None
            assert client.server_capabilities.prompts is None
            for tool in listed.tools:
                assert tool.input_schema['additionalProperties'] is False
                assert tool.annotations.read_only_hint is True
                assert tool.annotations.destructive_hint is False
                assert tool.annotations.open_world_hint is False
    run(check())


def test_queries_return_structured_results_without_modifying_tasks(fixture_db):
    path, store, ids = fixture_db
    before = store.list_tasks()
    async def check():
        async with Client(create_server(path)) as client:
            result = await client.call_tool('find_duplicate_tasks', {'titles': [' 周报 ']})
            assert not result.is_error
            data = result.structured_content
            assert {row['id'] for row in data['tasks']} == ids
            assert data['truncated'] is False
            assert json.loads(result.content[0].text) == data
            limited = await client.call_tool('list_tasks', {'limit': 1})
            assert len(limited.structured_content['tasks']) == 1
            assert limited.structured_content['truncated'] is True
    run(check())
    assert store.list_tasks() == before


@pytest.mark.parametrize('tool,arguments', [
    ('complete_task', {'id': 'x'}),
    ('list_tasks', {'sql': 'DELETE FROM tasks'}),
    ('list_tasks', {'database_path': 'other.sqlite3'}),
    ('list_tasks', {'confirmed': True}),
    ('list_tasks', {'limit': True}),
    ('list_tasks', {'limit': '1'}),
    ('list_tasks', {'limit': 1.0}),
    ('list_tasks', {'limit': 0}),
    ('list_tasks', {'limit': 101}),
    ('find_duplicate_tasks', {}),
    ('find_duplicate_tasks', {'titles': []}),
    ('find_duplicate_tasks', {'titles': [' ']}),
    ('find_duplicate_tasks', {'titles': [123]}),
    ('find_duplicate_tasks', {'titles': ['x'], 'run_id': 'forged'}),
])
def test_untrusted_arguments_fail_closed(fixture_db, tool, arguments):
    path, store, _ = fixture_db
    before = store.list_tasks()
    async def check():
        async with Client(create_server(path)) as client:
            result = await client.call_tool(tool, arguments)
            assert result.is_error
            assert result.structured_content is None
    run(check())
    assert store.list_tasks() == before


def test_missing_database_is_explicit_error_without_path_disclosure(tmp_path):
    path = tmp_path / 'private-path-must-not-leak.sqlite3'
    async def check():
        async with Client(create_server(path)) as client:
            result = await client.call_tool('list_tasks', {})
            assert result.is_error
            text = result.content[0].text
            assert 'query_failed' in text
            assert str(path) not in text
            assert result.structured_content is None
    run(check())
    assert not path.exists()


def test_call_budget_is_enforced_before_database_access(fixture_db, monkeypatch):
    from focuspilot.rpc import ReadOnlyTaskTools
    calls = []
    original = ReadOnlyTaskTools.call
    def observed(self, name, arguments):
        calls.append(name)
        return original(self, name, arguments)
    monkeypatch.setattr(ReadOnlyTaskTools, 'call', observed)
    async def check():
        async with Client(create_server(fixture_db[0], max_calls=1)) as client:
            assert not (await client.call_tool('list_tasks', {})).is_error
            denied = await client.call_tool('list_tasks', {})
            assert denied.is_error and 'budget_exceeded' in denied.content[0].text
    run(check())
    assert calls == ['list_tasks']


def test_invalid_attempt_reaches_server_and_consumes_budget(fixture_db):
    async def check():
        async with Client(create_server(fixture_db[0], max_calls=1)) as client:
            invalid = await client.call_tool('list_tasks', {'sql': 'SELECT 1'})
            assert invalid.is_error and 'invalid_arguments' in invalid.content[0].text
            valid = await client.call_tool('list_tasks', {})
            assert valid.is_error and 'budget_exceeded' in valid.content[0].text
    run(check())


def test_no_candidate_is_success_not_a_query_failure(fixture_db):
    async def check():
        async with Client(create_server(fixture_db[0])) as client:
            result = await client.call_tool('find_duplicate_tasks', {'titles': ['不存在的虚构标题']})
            assert not result.is_error
            assert result.structured_content == {'tasks': [], 'truncated': False}
    run(check())


def test_large_result_is_error_not_unbounded_or_partial_success(fixture_db):
    fixture_db[1].create('x' * 70_000)
    async def check():
        async with Client(create_server(fixture_db[0])) as client:
            result = await client.call_tool('list_tasks', {})
            assert result.is_error and 'result_too_large' in result.content[0].text
            assert result.structured_content is None
    run(check())


def test_invalid_stored_fields_are_not_published_as_valid_results(fixture_db):
    path, store, _ = fixture_db
    with store._connect() as connection:
        connection.execute("UPDATE tasks SET importance=99 WHERE status='pending'")
    before = store.list_tasks()
    async def check():
        async with Client(create_server(path)) as client:
            result = await client.call_tool('list_tasks', {})
            assert result.is_error and 'invalid_result' in result.content[0].text
            assert result.structured_content is None
    run(check())
    assert store.list_tasks() == before


@pytest.mark.parametrize('mode', ['auto', 'legacy'])
def test_stdio_roundtrip_and_cleanup_with_official_client(fixture_db, mode):
    before = fixture_db[1].list_tasks()
    async def check():
        params = StdioServerParameters(
            command=sys.executable,
            args=['-u', '-m', 'focuspilot.mcp_server', '--database', str(fixture_db[0])],
            env={'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src'),
                 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'},
        )
        with anyio.fail_after(10):
            async with Client(params, mode=mode, read_timeout_seconds=3) as client:
                assert client.protocol_version
                assert {t.name for t in (await client.list_tools()).tools} == {
                    'list_tasks', 'find_duplicate_tasks'}
                result = await client.call_tool('find_duplicate_tasks', {'titles': ['周报']})
                assert not result.is_error and len(result.structured_content['tasks']) == 2
                denied = await client.call_tool('list_tasks', {'sql': 'SELECT * FROM app_settings'})
                assert denied.is_error
            # Exiting SDK context must complete within the same bounded deadline.
    run(check())
    assert fixture_db[1].list_tasks() == before


def test_cli_requires_explicit_database_and_keeps_stdout_clean(tmp_path):
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / 'src'))
    completed = subprocess.run([sys.executable, '-m', 'focuspilot.mcp_server'],
                               capture_output=True, timeout=10, env=env)
    assert completed.returncode != 0
    assert completed.stdout == b''
    assert b'--database' in completed.stderr
