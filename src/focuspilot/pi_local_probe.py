"""Check an installed Pi's local RPC/MCP without prompts or model credentials.

Uses disposable configuration and fictional data. This is not an OS sandbox,
model tool-calling test, or production desktop integration.
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

from .rpc import JsonLines
from .storage import TaskStore


TOOLS = ('mcp__focuspilot__list_tasks', 'mcp__focuspilot__find_duplicate_tasks')


def isolated_environment(root):
    # Do not inherit provider tokens, NODE_OPTIONS, or user Pi configuration.
    keep = {'systemroot', 'windir', 'comspec', 'path', 'pathext', 'temp', 'tmp'}
    env = {key: value for key, value in os.environ.items() if key.lower() in keep}
    env.update({
        'PI_CODING_AGENT_DIR': str(root / 'agent'),
        'PI_CODING_AGENT_SESSION_DIR': str(root / 'sessions'),
        'PI_OFFLINE': '1', 'PI_SKIP_VERSION_CHECK': '1', 'PI_TELEMETRY': '0',
        'HOME': str(root), 'USERPROFILE': str(root),
        'APPDATA': str(root / 'appdata'), 'LOCALAPPDATA': str(root / 'localappdata'),
        'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8',
        'PYTHONPATH': str(Path(__file__).resolve().parents[1]),
    })
    return env


def run_local(command, root, env, data=None):
    """Bound each child; communicate drains both streams and closes stdin."""
    with subprocess.Popen(
        command, cwd=root, env=env, shell=False, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    ) as child:
        try:
            stdout, stderr = child.communicate(data, timeout=30)
        except subprocess.TimeoutExpired:
            child.kill()
            child.communicate(timeout=5)
            raise RuntimeError('Pi local check timed out') from None
        if child.returncode:
            # No raw diagnostic dump: future versions may include environment details.
            raise RuntimeError(f'Pi local check exited with code {child.returncode}')
        return stdout


def check_state(output):
    parser = JsonLines()
    records = parser.feed(output)
    parser.finish()
    matches = [record for record in records
               if record.get('id') == 'local-state' and record.get('type') == 'response'
               and record.get('command') == 'get_state']
    if len(matches) != 1 or matches[0].get('success') is not True:
        raise RuntimeError('Pi did not acknowledge the state query')
    state = matches[0].get('data', {})
    if (state.get('isStreaming') is not False or state.get('messageCount') != 0
            or state.get('pendingMessageCount') != 0):
        raise RuntimeError('Expected an idle, empty Pi session')
    return state


def check_discovery(report):
    servers = report.get('servers', [])
    if report.get('errors') or len(servers) != 1:
        raise RuntimeError('Expected only the fixture MCP server without errors')
    server = servers[0]
    if (server.get('name') != 'focuspilot' or server.get('state') != 'connected'
            or sorted(server.get('tools', [])) != ['find_duplicate_tasks', 'list_tasks']):
        raise RuntimeError('Pi did not discover the two expected read-only tools')
    return {'server': 'focuspilot', 'state': 'connected', 'tools': server['tools']}


def probe(node, entry):
    node, entry = Path(node).resolve(), Path(entry).resolve()
    if not node.is_file() or not entry.is_file():
        raise ValueError('Explicit node executable and Pi CLI entry must exist')
    with TemporaryDirectory(prefix='focuspilot-pi-local-') as directory:
        root = Path(directory)
        env = isolated_environment(root)
        agent = root / 'agent'
        agent.mkdir()
        fixture = root / 'fixture.sqlite3'
        store = TaskStore(fixture)
        store.create('本地连接验证任务')
        before = fixture.read_bytes()
        (agent / 'settings.json').write_text(json.dumps({
            'defaultProjectTrust': 'never', 'packages': [],
            'enableAnalytics': False,
        }), encoding='utf-8')
        (agent / 'mcp.json').write_text(json.dumps({
            'autoEnableCodemode': False,
            'mcpServers': {'focuspilot': {
                'command': sys.executable,
                'args': ['-u', '-m', 'focuspilot.mcp_server', '--database', str(fixture)],
                'cwd': str(root), 'timeout': 10, 'exposure': 'direct',
                'env': {'PYTHONPATH': env['PYTHONPATH'], 'PYTHONUTF8': '1'},
            }},
        }), encoding='utf-8')
        base = [str(node), str(entry)]
        version = run_local(base + ['--version'], root, env).decode('utf-8').strip()
        mcp = check_discovery(json.loads(
            run_local(base + ['mcp', 'list', '--json'], root, env)))
        # Preserve no raw model/session/configuration information in the report.
        output = run_local(base + [
            '--mode', 'rpc', '--no-session', '--no-approve', '--no-extensions',
            '-e', 'builtin:mcp', '--no-skills', '--no-prompt-templates',
            '--no-themes', '--no-context-files', '--tools', ','.join(TOOLS),
        ], root, env, b'{"id":"local-state","type":"get_state"}\n')
        check_state(output)
        unchanged = fixture.read_bytes() == before
        if not unchanged:
            raise RuntimeError('Fixture database changed')
        return {'pi_version': version, 'rpc_idle_state_verified': True,
                'database_unchanged': unchanged, 'model_prompt_sent': False,
                'mcp_discovery': mcp}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node', default=shutil.which('node'))
    parser.add_argument('--pi-entry', required=True,
                        help='Installed package dist/bundle/cli.js; not a shell shim')
    args = parser.parse_args()
    if not args.node:
        parser.error('node executable not found; provide --node')
    print(json.dumps(probe(args.node, args.pi_entry), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
