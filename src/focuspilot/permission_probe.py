"""Offline control experiment: a read-only connection is not a process sandbox.

Intentionally changes ONLY an internally created disposable fictional database.
Never accepts a database path, invokes Pi, reads credentials, or calls a model.
"""

import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory

from .pi_local_probe import isolated_environment
from .rpc import ReadOnlyTaskTools
from .storage import TaskStore


CHILD = '''
import json, pathlib, sqlite3, sys
root = pathlib.Path(sys.argv[1]).resolve()
database = (root / 'fixture.sqlite3').resolve()
sentinel = (root / 'outside-tool-scope' / 'sentinel.txt').resolve()
assert database.is_relative_to(root) and sentinel.is_relative_to(root)
with sqlite3.connect(database) as connection:
    connection.execute("UPDATE tasks SET title=?", ('changed-by-controlled-child',))
can_read = sentinel.read_text(encoding='utf-8') == 'fictional-control-sentinel'
print(json.dumps({'same_user_child_read_control_file': can_read}))
'''


def probe():
    with TemporaryDirectory(prefix='focuspilot-permission-') as directory:
        root = Path(directory)
        database = root / 'fixture.sqlite3'
        store = TaskStore(database)
        store.create('fictional-permission-task')
        sentinel = root / 'outside-tool-scope' / 'sentinel.txt'
        sentinel.parent.mkdir()
        sentinel.write_text('fictional-control-sentinel', encoding='utf-8')
        gateway = ReadOnlyTaskTools(database)
        report = {}
        try:
            gateway.call('delete_task', {})
        except ValueError:
            report['write_tool_rejected'] = True
        try:
            gateway.call('list_tasks', {'sql': 'DELETE FROM tasks'})
        except ValueError:
            report['sql_argument_rejected'] = True
        try:
            with gateway.connect() as connection:
                connection.execute('UPDATE tasks SET title=?', ('must-not-be-written',))
        except sqlite3.OperationalError as error:
            if error.sqlite_errorcode != sqlite3.SQLITE_READONLY:
                raise
            report['readonly_connection_rejected_write'] = True
        if store.list_tasks()[0].title != 'fictional-permission-task':
            raise RuntimeError('Read-only boundary did not preserve the fixture')
        child = subprocess.run(
            [sys.executable, '-I', '-c', CHILD, str(root)], cwd=root,
            env=isolated_environment(root), capture_output=True, timeout=10,
            shell=False, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        if child.returncode:
            raise RuntimeError('Controlled child did not finish; no sandbox conclusion drawn')
        report.update(json.loads(child.stdout))
        report['same_user_child_wrote_fixture'] = (
            store.list_tasks()[0].title == 'changed-by-controlled-child')
        expected = ('write_tool_rejected', 'sql_argument_rejected',
                    'readonly_connection_rejected_write',
                    'same_user_child_read_control_file', 'same_user_child_wrote_fixture')
        if not all(report.get(key) is True for key in expected):
            raise RuntimeError('Control experiment did not match expected conditions')
        report.update(os_sandbox_verified=False, model_called=False,
                      production_database_used=False)
        return report


def main():
    print(json.dumps(probe(), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
