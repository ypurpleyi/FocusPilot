"""Approved offline MCP bridge probe. Disposable data only; no model prompt."""

import argparse
import ctypes as C
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
from tempfile import TemporaryDirectory

from .appcontainer_probe import Native, checked_target, profile_name, set_acl
from .mcp_bridge import PipeRelay
from .pi_local_probe import check_discovery
from .sandbox_runtime_probe import copy_program_tree, digest, is_reparse, run_command, ProbeFailure
from .storage import TaskStore


def probe(inventory_path, *, inprocess=False):
    import win32api
    import win32security as security
    inventory = json.loads(Path(inventory_path).read_text(encoding='utf-8'))
    source_node, source_pi = Path(inventory['node']['source']), Path(inventory['pi']['source'])
    source_entry = source_pi / 'dist/bundle/cli.js'
    if (digest(source_node) != inventory['node']['sha256']
            or digest(source_entry) != inventory['pi']['cli_sha256']):
        raise ValueError('Installed program fingerprint changed')
    sources = (source_node, source_node.parent, source_pi, source_entry)
    def acls():
        return [security.ConvertSecurityDescriptorToStringSecurityDescriptor(
            security.GetFileSecurity(str(path), 4), 1, 4) for path in sources]
    original_acls = acls()
    workspace = Path(__file__).resolve().parents[2]
    scratch = checked_target(workspace, workspace / '.tmp')
    scratch.mkdir(exist_ok=True)
    api = Native()
    report = {'model_called': False, 'credentials_read': False, 'production_data_used': False,
              'network_capability_count': 0, 'production_ready': False}
    failure = None
    with TemporaryDirectory(prefix='pi-mcp-bridge-', dir=scratch) as directory:
        root = checked_target(scratch, directory)
        name, sid, created, folder = profile_name(), C.c_void_p(), False, None
        try:
            api.hresult(api.CreateAppContainerProfile(name, name, 'FocusPilot offline MCP bridge',
                None, 0, C.byref(sid)), 'CreateAppContainerProfile')
            created = True
            report['profile'] = name
            sid_text = api.sid_string(sid)
            pointer = C.c_void_p()
            api.hresult(api.GetAppContainerFolderPath(sid_text, C.byref(pointer)), 'GetAppContainerFolderPath')
            try:
                folder = Path(C.wstring_at(pointer))
            finally:
                api.CoTaskMemFree(pointer)
            app_sid = security.ConvertStringSidToSid(sid_text)
            token = security.OpenProcessToken(win32api.GetCurrentProcess(), 8)
            try:
                user_sid = security.GetTokenInformation(token, security.TokenUser)[0]
            finally:
                token.Close()
            user_text = security.ConvertSidToStringSid(user_sid)
            set_acl(root, root, user_sid, app_sid, 0x1200a9)
            runtime, work, host = root / 'runtime', root / 'work', root / 'host'
            runtime.mkdir(); work.mkdir(); host.mkdir()
            set_acl(root, runtime, user_sid, app_sid, 0x1200a9, inherit=True)
            set_acl(root, work, user_sid, app_sid, 0x1f01ff, inherit=True)
            set_acl(root, host, user_sid, app_sid, inherit=True)
            label = subprocess.run([str(Path(os.environ['SystemRoot']) / 'System32/icacls.exe'),
                str(checked_target(root, work)), '/setintegritylevel', '(OI)(CI)L'],
                capture_output=True, timeout=10, creationflags=0x08000000)
            if label.returncode:
                raise RuntimeError('Workspace integrity label failed')
            fixture = host / 'fixture.sqlite3'
            store = TaskStore(fixture)
            expected = {store.create('fictional-report').id, store.create('fictional-report').id}
            old = store.create('fictional-report')
            store.complete(old.id)
            before = digest(fixture)
            node = runtime / 'node.exe'
            if is_reparse(source_node):
                raise ValueError('Node source is a reparse point')
            shutil.copyfile(source_node, node)
            if digest(node) != inventory['node']['sha256']:
                raise ValueError('Copied Node hash mismatch')
            for asset in ('mcp_pipe_proxy.cjs', 'mcp_pipe_fixture.cjs'):
                shutil.copyfile(Path(__file__).with_name(asset), runtime / asset)
            base = [node, '--preserve-symlinks', '--preserve-symlinks-main']

            def relay_command(label, command, *, timeout=20, expected_error=None, interrupt=None, ready_path=None, data=b''):
                relay = PipeRelay(fixture, sid_text, user_text)
                monitor = None
                def attach(job):
                    nonlocal monitor
                    cleanup = relay.attach(job)
                    if interrupt:
                        def control():
                            until = time.monotonic() + 5
                            while not relay.stop.is_set():
                                ready = relay.policy.phase == 'ready'
                                if ready_path is not None:
                                    try:
                                        ready = json.loads(Path(ready_path).read_text(encoding='utf-8')).get('ready') is True
                                    except (OSError, ValueError):
                                        ready = False
                                if ready:
                                    break
                                if time.monotonic() > until:
                                    return
                                time.sleep(.01)
                            time.sleep(.1)
                            if interrupt == 'cancel':
                                relay.policy.cancel()
                            else:
                                relay.policy.deadline = time.monotonic()
                        monitor = threading.Thread(target=control, daemon=True)
                        monitor.start()
                    return cleanup
                # Insert channel only into this trusted fixed command/configuration.
                try:
                    output, identity = run_command(api, command(relay.name), root, sid, sid_text,
                        label, data=data, timeout=timeout, on_verified=attach, cleanup_report=relay.report)
                except Exception:
                    if not relay.report.get('pipe_closed'):
                        relay.close()
                    report[label] = {**relay.report, 'error': relay.error}
                    if label == 'pi-discovery':
                        from .sandbox_runtime_probe import read_bounded
                        report['pi_discovery_diagnostic'] = {
                            suffix: read_bounded(work / (label + suffix))[-2000:].decode('utf-8', errors='replace')
                            for suffix in ('.out', '.err')}
                    if expected_error is None or relay.error != expected_error:
                        raise
                    if not all(relay.report.get(key) is True for key in ('child_reaped',
                            'host_job_empty', 'worker_threads_stopped', 'pipe_closed',
                            'container_job_empty_after_cleanup')):
                        raise RuntimeError('Failed bridge did not clean all owned resources')
                    return None
                finally:
                    if monitor:
                        monitor.join(6)
                report[label] = {**relay.report, 'process': identity, 'error': relay.error}
                if relay.error and relay.error != expected_error:
                    raise RuntimeError('Bridge failed: ' + relay.error)
                if not all(relay.report.get(key) is True for key in ('identity_verified', 'child_reaped',
                        'host_job_empty', 'worker_threads_stopped', 'pipe_closed',
                        'container_job_empty_after_cleanup')):
                    raise RuntimeError('Bridge identity or resource cleanup evidence missing')
                return output

            print('Checking sandbox-to-host MCP pipe with fixed fixture client...', flush=True)
            output = relay_command('fixture-query', lambda pipe: base + [runtime / 'mcp_pipe_fixture.cjs', pipe])
            result = json.loads(output)
            if result['tools'] != ['find_duplicate_tasks', 'list_tasks']:
                raise ValueError('Unexpected bridge tools')
            for key in ('pending', 'duplicates'):
                if result[key]['truncated'] is not False or {item['id'] for item in result[key]['tasks']} != expected:
                    raise ValueError('Fixture query evidence mismatch')
            report['pending_count'] = len(result['pending']['tasks'])
            report['duplicate_candidate_count'] = len(result['duplicates']['tasks'])
            # The host fixture directory must remain inaccessible to the peer.
            access = "const fs=require('node:fs');try{fs.readFileSync(process.argv[1]);process.exit(2)}catch(e){if(!['EACCES','EPERM'].includes(e.code))process.exit(3);console.log('denied')}"
            data, report['db_boundary_process'] = run_command(api, base + ['-e', access, fixture],
                root, sid, sid_text, 'fixture-file-denied')
            report['direct_database_read_denied'] = data.strip() == b'denied'
            relay_command('illegal-call', lambda pipe: base + [runtime / 'mcp_pipe_fixture.cjs', pipe, 'illegal'],
                          expected_error='invalid_tool_arguments')
            if report['illegal-call']['forwarded_calls'] != 0:
                raise ValueError('Illegal query reached server')
            for mode in ('cancel', 'timeout'):
                relay_command(mode, lambda pipe: base + [runtime / 'mcp_pipe_fixture.cjs', pipe, 'linger'],
                    interrupt=mode, expected_error='inactive_or_expired')
                if report[mode]['forwarded_calls'] != 0:
                    raise ValueError('Unexpected query during control test')
            # Ordinary user can reach the owner ACL, but cannot pass the peer
            # AppContainer identity check. No server child is started for it.
            import win32file
            relay = PipeRelay(fixture, sid_text, user_text)
            relay.attach(None)
            peer = None
            try:
                peer = win32file.CreateFile(relay.host_name, 0xc0000000, 0, None, 3, 0, None)
                relay.thread.join(3)
                if relay.thread.is_alive() or relay.error != 'peer_identity_mismatch':
                    raise ValueError('Ordinary peer was not rejected')
            finally:
                if peer:
                    peer.Close()
                relay.close()
            report['wrong-identity'] = {**relay.report, 'error': relay.error}
            if relay.report['identity_verified'] or relay.report['forwarded_calls']:
                raise ValueError('Untrusted peer reached queries')
            report['offline_bridge_checks_passed'] = True
            report['database_unchanged'] = digest(fixture) == before
            if inprocess:
                from .pi_inprocess_probe import check_inprocess
                check_inprocess(api, base, root, sid, sid_text, source_pi, inventory,
                                relay_command, expected, report)
                if digest(fixture) != before:
                    raise ValueError('Fixture changed during in-process checks')
            else:
                check_stdio(api, base, root, sid, sid_text, source_pi, inventory, relay_command, report)
            report['source_fingerprints_unchanged'] = (digest(source_node) == inventory['node']['sha256']
                and digest(source_entry) == inventory['pi']['cli_sha256'])
            report['source_acls_unchanged'] = acls() == original_acls
            if not all(report.get(key) is True for key in ('direct_database_read_denied',
                    'database_unchanged', 'source_fingerprints_unchanged', 'source_acls_unchanged')):
                raise ValueError('Bridge integrity boundary failed')
        except Exception as error:
            failure = error
            report['failure'] = {'type': type(error).__name__, 'detail': str(error)[-1800:]}
        finally:
            report['source_fingerprints_unchanged'] = (digest(source_node) == inventory['node']['sha256']
                and digest(source_entry) == inventory['pi']['cli_sha256'])
            report['source_acls_unchanged'] = acls() == original_acls
            if created:
                try:
                    api.hresult(api.DeleteAppContainerProfile(name), 'DeleteAppContainerProfile')
                    report['profile_deleted'] = folder is not None and not folder.exists()
                finally:
                    api.FreeSid(sid)
            checked_target(scratch, root)
    report['runtime_and_work_deleted'] = not root.exists()
    if failure or not report.get('profile_deleted') or not report['runtime_and_work_deleted']:
        raise ProbeFailure(report)
    return report


def check_stdio(api, base, root, sid, sid_text, source_pi, inventory, relay_command, report):
    work, runtime = root / 'work', root / 'runtime'
    spawn_test = "const c=require('node:child_process');console.error('before-spawn');const p=c.spawn(process.execPath,['--version'],{stdio:['ignore','pipe','pipe']});console.error('after-spawn');p.stdout.pipe(process.stdout);p.on('error',e=>{console.error(e.code);process.exit(9)});p.on('exit',n=>process.exit(n));"
    report['node_spawn_cleanup'] = {}
    try:
        data, report['node_spawn_process'] = run_command(api, base + ['-e', spawn_test],
            root, sid, sid_text, 'node-spawn', timeout=5, cleanup_report=report['node_spawn_cleanup'])
    except TimeoutError:
        from .sandbox_runtime_probe import read_bounded
        report['node_spawn_diagnostic'] = read_bounded(work / 'node-spawn.err').decode().strip()
        report['pi_mcp_discovery_verified'] = False
        raise RuntimeError('Installed Node child_process.spawn with stdio pipes timed out in AppContainer; stopped before Pi copy') from None
    if data.decode().strip() != inventory['node']['version']:
        raise ValueError('Node child process pipe incompatibility')
    print('Pipe verified; copying Pi program tree for real tool discovery...', flush=True)
    report['copy'] = copy_program_tree(source_pi, runtime / 'pi', root,
        progress=lambda count: print(f'Copied and verified {count} Pi files...', flush=True))
    entry = runtime / 'pi/dist/bundle/cli.js'
    if digest(entry) != inventory['pi']['cli_sha256']:
        raise ValueError('Copied Pi fingerprint mismatch')
    agent = work / 'agent'
    agent.mkdir()
    (agent / 'settings.json').write_text(json.dumps({'defaultProjectTrust': 'never',
        'enableAnalytics': False, 'packages': []}), encoding='utf-8')
    def discovery_command(pipe):
        (agent / 'mcp.json').write_text(json.dumps({'autoEnableCodemode': False,
            'mcpServers': {'focuspilot': {'command': str(base[0]), 'args': [
                '--preserve-symlinks', '--preserve-symlinks-main', str(runtime / 'mcp_pipe_proxy.cjs'), pipe],
                'timeout': 10, 'exposure': 'direct'}}}), encoding='utf-8')
        return base + [entry, 'mcp', 'list', '--json']
    output = relay_command('pi-discovery', discovery_command, timeout=30)
    report['pi_mcp_discovery'] = check_discovery(json.loads(output))
    report['pi_mcp_discovery_verified'] = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path,
        default=Path(__file__).resolve().parents[2] / 'docs/PI_RUNTIME_INVENTORY.json')
    parser.add_argument('--approve-offline-bridge', action='store_true')
    args = parser.parse_args()
    if not args.approve_offline_bridge:
        parser.error('Requires --approve-offline-bridge; no profile, copy or database opened')
    try:
        report = probe(args.inventory)
    except ProbeFailure as error:
        print(json.dumps(error.report, ensure_ascii=False, indent=2))
        parser.exit(1, 'Offline bridge failed; no privilege fallback.\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
