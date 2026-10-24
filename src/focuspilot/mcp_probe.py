"""Run an official MCP client against our stdio server using disposable fixtures.

No Pi, model, network endpoint, global configuration or formal database is used.
"""

import argparse
from importlib.metadata import version
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import anyio
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from .storage import TaskStore


async def probe(mode='auto'):
    with TemporaryDirectory(prefix='focuspilot-mcp-') as directory:
        path = Path(directory) / 'fixture.sqlite3'
        store = TaskStore(path)
        store.create('提交周报', '2026-10-24T17:00:00')
        store.create('提交周报', '2026-10-25T17:00:00')
        old = store.create('提交周报')
        store.complete(old.id)
        before = store.list_tasks()
        parameters = StdioServerParameters(
            command=sys.executable,
            args=['-u', '-m', 'focuspilot.mcp_server', '--database', str(path)],
            env={'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8',
                 'PYTHONPATH': str(Path(__file__).resolve().parents[1])},
        )
        with anyio.fail_after(15):
            async with Client(parameters, mode=mode, read_timeout_seconds=5) as client:
                discovered = await client.list_tools()
                pending = await client.call_tool('list_tasks', {'limit': 100})
                duplicate = await client.call_tool('find_duplicate_tasks', {'titles': ['提交周报']})
                extra = await client.call_tool('list_tasks', {'sql': 'SELECT * FROM tasks'})
                unknown = await client.call_tool('complete_task', {'id': old.id})
                if pending.is_error or duplicate.is_error:
                    raise RuntimeError('离线MCP只读检查失败')
                report = {
                    'sdk_version': version('mcp'), 'protocol_mode': mode,
                    'protocol_version': client.protocol_version,
                    'tools': sorted(tool.name for tool in discovered.tools),
                    'pending_count': len(pending.structured_content['tasks']),
                    'duplicate_candidate_count': len(duplicate.structured_content['tasks']),
                    'extra_argument_rejected': extra.is_error,
                    'write_tool_rejected': unknown.is_error,
                }
        report['database_unchanged'] = store.list_tasks() == before
        if (report['tools'] != ['find_duplicate_tasks', 'list_tasks']
                or report['pending_count'] != 2 or report['duplicate_candidate_count'] != 2
                or not all(report[key] for key in (
                    'extra_argument_rejected', 'write_tool_rejected', 'database_unchanged'))):
            raise RuntimeError('离线MCP验收条件未满足')
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--protocol-mode', choices=('auto', 'legacy'), default='auto')
    args = parser.parse_args()
    print(json.dumps(anyio.run(probe, args.protocol_mode), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
