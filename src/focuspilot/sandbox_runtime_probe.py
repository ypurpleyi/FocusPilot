"""Approved offline Node/Pi AppContainer test using disposable runtime copies.

No model, MCP, credentials, existing installation ACL changes or formal data.
This file-backed stdio probe is not the production desktop transport.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import ctypes as C
from ctypes import wintypes as W
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import uuid

from .appcontainer_probe import (Native, PROCESS_INFORMATION, SECURITY_CAPABILITIES,
                                STARTUPINFOEX, checked_target, profile_name, set_acl)
from .pi_local_probe import check_state, isolated_environment


def is_reparse(path):
    return bool(getattr(Path(path).stat(follow_symlinks=False), 'st_file_attributes', 0) & 0x400)


def excluded(name):
    name = name.casefold()
    return name in {'.pi', '.git', 'auth.json', 'models.json', 'settings.json', '.npmrc', '.env'} or name.startswith('.env.')


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def copy_program_tree(source, destination, owned_root, progress=None, cancel_check=None):
    if is_reparse(source):
        raise ValueError('Program source must not be a reparse point')
    source = Path(source).resolve()
    if not source.is_dir():
        raise ValueError('Program source must be a directory')
    if Path(destination).exists() and is_reparse(destination):
        raise ValueError('Program destination must not be a reparse point')
    destination = checked_target(owned_root, destination)
    if destination.is_relative_to(source):
        raise ValueError('Copy destination must be outside program source')
    destination.mkdir(exist_ok=True)
    if any(destination.iterdir()):
        raise ValueError('Program destination must be empty')
    count = total = 0
    entries = []
    for directory, dirs, files in os.walk(source, followlinks=False):
        if cancel_check:
            cancel_check()
        current = Path(directory)
        # Fresh empty destination, not yet exposed to a sandbox process. Walk names
        # are filesystem basenames; reject source reparse entries before creating
        # their descendants. Resolving the same long prefix per file is unnecessary.
        target_dir = destination / current.relative_to(source)
        for name in list(dirs):
            if excluded(name):
                dirs.remove(name)
            elif is_reparse(current / name):
                raise ValueError('Program tree contains a reparse point')
            else:
                (target_dir / name).mkdir()
        for name in files:
            if excluded(name):
                continue
            original = current / name
            if is_reparse(original):
                raise ValueError('Program file is a reparse point')
            target = target_dir / name
            entries.append((original, target))
    def verified_copy(entry):
        original, target = entry
        original_hash = digest(original)
        shutil.copyfile(original, target)  # Destination ACL only; never copy source ACL.
        if digest(target) != original_hash:
            raise ValueError('Copied program hash mismatch')
        return target.stat().st_size
    executor = ThreadPoolExecutor(max_workers=8)
    try:
        for size in executor.map(verified_copy, entries):
            count += 1
            total += size
            if progress and count % 1000 == 0:
                progress(count)
    except BaseException:
        executor.shutdown(wait=True, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True)
    return {'files': count, 'bytes': total, 'hashes_verified': True}


def runtime_environment(root):
    root = Path(root)
    env = isolated_environment(root)
    env.pop('PYTHONPATH', None)
    work = root / 'work'
    env.update({
        'PATH': str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32'),
        'SystemDrive': Path(os.environ.get('SystemRoot', 'C:/Windows')).drive,
        'USERPROFILE': str(work), 'HOME': str(work), 'APPDATA': str(work),
        'LOCALAPPDATA': str(work), 'TEMP': str(work), 'TMP': str(work),
        'PI_CODING_AGENT_DIR': str(work / 'agent'),
        'PI_CODING_AGENT_SESSION_DIR': str(work / 'sessions'),
        'PI_PACKAGE_DIR': str(root / 'runtime' / 'pi'),
        'NO_COLOR': '1',
    })
    return env


def validate_versions(node, pi, inventory):
    if node.strip() != inventory['node']['version'] or pi.strip() != inventory['pi']['version']:
        raise ValueError('Sandbox versions differ from approved installation inventory')


def read_bounded(path, limit=1_048_576):
    with Path(path).open('rb') as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise ValueError('Runtime output exceeded bound')
    return content


def run_command(api, command, root, sid, sid_text, label, data=b'', timeout=20, *, on_verified=None,
                cleanup_report=None):
    """Only three explicit fixture handles are inherited; no network IPC."""
    import msvcrt
    import win32job as jobs
    work = root / 'work'
    input_path = checked_target(root, work / (label + '.in'))
    output_path = checked_target(root, work / (label + '.out'))
    error_path = checked_target(root, work / (label + '.err'))
    input_path.write_bytes(data)
    size = C.c_size_t()
    api.InitializeProcThreadAttributeList(None, 2, 0, C.byref(size))
    attributes = C.create_string_buffer(size.value)
    api.require(api.InitializeProcThreadAttributeList(attributes, 2, 0, C.byref(size)), 'InitializeAttributes')
    capabilities = SECURITY_CAPABILITIES(sid, None, 0, 0)
    process = PROCESS_INFORMATION()
    job = jobs.CreateJobObject(None, 'FocusPilot.Runtime.Job.' + uuid.uuid4().hex)
    streams = []
    stop_session = None
    try:
        limits = jobs.QueryInformationJobObject(job, jobs.JobObjectExtendedLimitInformation)
        limits['BasicLimitInformation']['LimitFlags'] |= jobs.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        jobs.SetInformationJobObject(job, jobs.JobObjectExtendedLimitInformation, limits)
        streams = [input_path.open('rb', buffering=0), output_path.open('wb', buffering=0),
                   error_path.open('wb', buffering=0)]
        handles = [msvcrt.get_osfhandle(stream.fileno()) for stream in streams]
        for handle in handles:
            os.set_handle_inheritable(handle, True)
        handle_list = (W.HANDLE * 3)(*handles)
        api.require(api.UpdateProcThreadAttribute(attributes, 0, 0x20002, handle_list,
            C.sizeof(handle_list), None, None), 'SetExplicitHandleList')
        api.require(api.UpdateProcThreadAttribute(attributes, 0, 0x20009, C.byref(capabilities),
            C.sizeof(capabilities), None, None), 'SetSecurityCapabilities')
        startup = STARTUPINFOEX()
        startup.StartupInfo.cb = C.sizeof(startup)
        startup.StartupInfo.dwFlags = 0x101  # STARTF_USESTDHANDLES | STARTF_USESHOWWINDOW
        startup.StartupInfo.hStdInput, startup.StartupInfo.hStdOutput, startup.StartupInfo.hStdError = handles
        startup.lpAttributeList = C.cast(attributes, C.c_void_p)
        line = C.create_unicode_buffer(subprocess.list2cmdline([str(part) for part in command]))
        env = runtime_environment(root)
        block = C.create_unicode_buffer('\0'.join(f'{key}={value}' for key, value in
            sorted(env.items(), key=lambda pair: pair[0].upper())) + '\0\0')
        api.require(api.CreateProcessW(str(command[0]), line, None, None, True,
            0x80000 | 0x400 | 0x4 | 0x08000000, block, str(work), C.byref(startup),
            C.byref(process)), 'CreateProcess-AppContainer-' + label)
        jobs.AssignProcessToJobObject(job, int(process.hProcess))
        identity, count, observed = api.token(process.hProcess)
        if not identity or count != 0 or observed != sid_text:
            raise RuntimeError('Runtime AppContainer token mismatch')
        if on_verified is not None:
            stop_session = on_verified(job)
        if api.ResumeThread(process.hThread) == 0xffffffff:
            api.require(False, 'ResumeThread')
        deadline = time.monotonic() + timeout
        while api.WaitForSingleObject(process.hProcess, 25) == 258:
            if time.monotonic() >= deadline:
                raise TimeoutError('Runtime command timed out: ' + label)
            if any(os.fstat(stream.fileno()).st_size > 1_048_576 for stream in streams[1:]):
                raise ValueError('Runtime output exceeded bound')
        code = W.DWORD()
        api.require(api.GetExitCodeProcess(process.hProcess, C.byref(code)), 'GetExitCodeProcess')
        # Close inherited standard handles in the host before reading their files.
        for stream in streams:
            stream.close()
        streams = []
        until = time.monotonic() + 2
        while jobs.QueryInformationJobObject(job, jobs.JobObjectBasicAccountingInformation)['ActiveProcesses']:
            if time.monotonic() >= until:
                raise TimeoutError('Runtime descendants did not exit')
            time.sleep(0.02)
        if code.value:
            diagnostic = read_bounded(error_path)[-2000:].decode('utf-8', errors='replace')
            raise RuntimeError(f'{label} exited {code.value}: {diagnostic}')
        read_bounded(error_path)
        return read_bounded(output_path), {'appcontainer_token_verified': True,
            'zero_capabilities': True, 'inherited_handle_count': 3, 'job_empty': True}
    finally:
        try:
            try:
                if stop_session is not None:
                    stop_session()
            finally:
                jobs.TerminateJobObject(job, 78)
                if cleanup_report is not None:
                    until = time.monotonic() + 2
                    while jobs.QueryInformationJobObject(job, jobs.JobObjectBasicAccountingInformation)['ActiveProcesses']:
                        if time.monotonic() >= until:
                            raise TimeoutError('Runtime job cleanup timed out')
                        time.sleep(.02)
                    cleanup_report['container_job_empty_after_cleanup'] = True
        finally:
            if process.hProcess:
                api.TerminateProcess(process.hProcess, 78)
                api.WaitForSingleObject(process.hProcess, 2000)
                api.CloseHandle(process.hProcess)
            if process.hThread:
                api.CloseHandle(process.hThread)
            for stream in streams:
                stream.close()
            job.Close()
            api.DeleteProcThreadAttributeList(attributes)


NODE_FILE_TEST = r'''
const fs = require('node:fs'), path = require('node:path');
const root = process.argv[1], runtime = path.join(root,'runtime'), work = path.join(root,'work');
function denied(fn) { try { fn(); return false; } catch(e) { return ['EACCES','EPERM'].includes(e.code); } }
const marker = path.join(runtime,'readonly.txt');
const result = {
  allowed_read: fs.readFileSync(marker,'utf8') === 'fictional-runtime-readonly',
  runtime_write_denied: denied(() => { const fd=fs.openSync(marker,'r+');fs.closeSync(fd); }),
  runtime_create_denied: denied(() => fs.writeFileSync(path.join(runtime,'must-not-create'),'x')),
  ungranted_read_denied: denied(() => fs.readFileSync(path.join(root,'ungranted.txt'))),
  ungranted_write_denied: denied(() => fs.writeFileSync(path.join(root,'ungranted.txt'),'x'))
};
fs.writeFileSync(path.join(work,'node-work.txt'),'fictional-workspace');
result.workspace_write = fs.readFileSync(path.join(work,'node-work.txt'),'utf8') === 'fictional-workspace';
console.log(JSON.stringify(result));
'''


class ProbeFailure(RuntimeError):
    def __init__(self, report):
        super().__init__('Sandbox runtime probe failed; no ordinary-user fallback')
        self.report = report


def probe(inventory_path):
    import win32api
    import win32security as security
    inventory = json.loads(Path(inventory_path).read_text(encoding='utf-8'))
    source_node = Path(inventory['node']['source'])
    source_pi = Path(inventory['pi']['source'])
    source_entry = source_pi / 'dist/bundle/cli.js'
    if digest(source_node) != inventory['node']['sha256'] or digest(source_entry) != inventory['pi']['cli_sha256']:
        raise ValueError('Installed program fingerprints changed; inventory must be reviewed')
    sources = (source_node, source_node.parent, source_pi, source_entry)
    def acls():
        return [security.ConvertSecurityDescriptorToStringSecurityDescriptor(
            security.GetFileSecurity(str(path), 4), 1, 4) for path in sources]
    original_acls = acls()
    api = Native()
    workspace = Path(__file__).resolve().parents[2]
    scratch = checked_target(workspace, workspace / '.tmp')
    scratch.mkdir(exist_ok=True)
    report = {'model_called': False, 'mcp_enabled': False, 'credentials_read': False,
              'production_data_used': False, 'network_capability_count': 0}
    failure = None
    with TemporaryDirectory(prefix='pi-runtime-', dir=scratch) as directory:
        root = checked_target(scratch, directory)
        name, sid = profile_name(), C.c_void_p()
        created, folder = False, None
        try:
            api.hresult(api.CreateAppContainerProfile(name, name, 'FocusPilot offline Node/Pi runtime test',
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
            set_acl(root, root, user_sid, app_sid, 0x1200a9)
            runtime, work = root / 'runtime', root / 'work'
            runtime.mkdir(); work.mkdir()
            set_acl(root, runtime, user_sid, app_sid, 0x1200a9, inherit=True)
            set_acl(root, work, user_sid, app_sid, 0x1f01ff, inherit=True)
            (root / 'ungranted.txt').write_text('fictional-ungranted', encoding='utf-8')
            set_acl(root, root / 'ungranted.txt', user_sid, app_sid)
            (runtime / 'readonly.txt').write_text('fictional-runtime-readonly', encoding='utf-8')
            label = subprocess.run([str(Path(os.environ['SystemRoot']) / 'System32/icacls.exe'),
                str(checked_target(root, work)), '/setintegritylevel', '(OI)(CI)L'],
                capture_output=True, timeout=10, creationflags=0x08000000)
            if label.returncode:
                raise RuntimeError('Workspace low integrity label could not be set')
            print('Copying approved program files into disposable runtime...', file=sys.stderr, flush=True)
            node = runtime / 'node.exe'
            if is_reparse(source_node):
                raise ValueError('Node executable must not be a reparse point')
            shutil.copyfile(source_node, node)
            if digest(node) != inventory['node']['sha256']:
                raise ValueError('Copied Node fingerprint mismatch')
            copy_report = copy_program_tree(source_pi, runtime / 'pi', root,
                progress=lambda count: print(f'Copied and verified {count} Pi files...',
                    file=sys.stderr, flush=True))
            report['copy'] = {**copy_report, 'bytes_with_node': copy_report['bytes'] + node.stat().st_size}
            entry = runtime / 'pi' / 'dist/bundle/cli.js'
            if digest(entry) != inventory['pi']['cli_sha256']:
                raise ValueError('Copied Pi entry fingerprint mismatch')
            (work / 'agent').mkdir()
            (work / 'agent' / 'settings.json').write_text(json.dumps({
                'defaultProjectTrust': 'never', 'enableAnalytics': False,
            }), encoding='utf-8')
            print('Running sandboxed Node/Pi versions, file boundaries and RPC state...', file=sys.stderr, flush=True)
            # Avoid Node walking denied drive/ancestor metadata to canonicalize
            # module paths. The copied tree rejects all reparse points; no OS
            # permissions are relaxed by these module-loader options.
            node_base = [node, '--preserve-symlinks', '--preserve-symlinks-main']
            node_output, report['node_process'] = run_command(api, node_base + ['--version'], root, sid, sid_text, 'node-version')
            pi_output, report['pi_process'] = run_command(api, node_base + [entry, '--version'], root, sid, sid_text, 'pi-version')
            validate_versions(node_output.decode('utf-8'), pi_output.decode('utf-8'), inventory)
            report['node_version'], report['pi_version'] = node_output.decode().strip(), pi_output.decode().strip()
            files, report['file_test_process'] = run_command(api, node_base + ['-e', NODE_FILE_TEST, root], root, sid, sid_text, 'node-files')
            report['node_file_checks'] = json.loads(files)
            expected = {'allowed_read', 'runtime_write_denied', 'runtime_create_denied',
                        'ungranted_read_denied', 'ungranted_write_denied', 'workspace_write'}
            if set(report['node_file_checks']) != expected or any(value is not True for value in report['node_file_checks'].values()):
                raise ValueError('Node file boundary checks failed')
            command = node_base + [entry, '--mode', 'rpc', '--no-session', '--no-approve',
                '--no-extensions', '--no-mcp', '--no-tools', '--no-skills', '--no-themes',
                '--no-prompt-templates', '--no-context-files']
            rpc, report['rpc_process'] = run_command(api, command, root, sid, sid_text, 'pi-state',
                b'{"id":"local-state","type":"get_state"}\n')
            check_state(rpc)
            report['rpc_idle_empty_state_verified'] = True
            report['source_fingerprints_unchanged'] = (digest(source_node) == inventory['node']['sha256']
                and digest(source_entry) == inventory['pi']['cli_sha256'])
            report['source_acls_unchanged'] = acls() == original_acls
            if not report['source_fingerprints_unchanged'] or not report['source_acls_unchanged']:
                raise ValueError('Original installation integrity check failed')
        except Exception as error:
            failure = error
            report['failure'] = {'type': type(error).__name__, 'detail': str(error)[-2400:]}
        finally:
            if created:
                try:
                    api.hresult(api.DeleteAppContainerProfile(name), 'DeleteAppContainerProfile-' + name)
                    report['profile_deleted'] = folder is not None and not folder.exists()
                finally:
                    api.FreeSid(sid)
            checked_target(scratch, root)
    report['runtime_and_work_deleted'] = not root.exists()
    report['production_ready'] = False
    if failure is not None or not report.get('profile_deleted') or not report['runtime_and_work_deleted']:
        raise ProbeFailure(report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path,
                        default=Path(__file__).resolve().parents[2] / 'docs/PI_RUNTIME_INVENTORY.json')
    parser.add_argument('--approve-runtime-copy', action='store_true')
    args = parser.parse_args()
    if not args.approve_runtime_copy:
        parser.error('Requires --approve-runtime-copy; no inventory read, program copied or profile created')
    try:
        report = probe(args.inventory)
    except ProbeFailure as error:
        print(json.dumps(error.report, ensure_ascii=False, indent=2))
        parser.exit(1, 'Sandbox runtime test failed; no ordinary-user fallback.\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
