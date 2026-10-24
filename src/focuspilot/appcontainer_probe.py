"""Explicitly approved, offline AppContainer feasibility experiment on Windows.

Creates a unique per-user profile and changes ACLs only on disposable fixtures.
No Pi, model requests, existing runtime ACL changes, or formal task data.
"""

import argparse
import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import socket
import subprocess
from tempfile import TemporaryDirectory
import time
import uuid


def profile_name():
    return 'FocusPilot.Prototype.' + uuid.uuid4().hex


def checked_target(root, target):
    root, target = Path(root).resolve(), Path(target).resolve()
    if not target.is_relative_to(root):
        raise ValueError('Security changes must stay within the disposable root')
    return target


class STARTUPINFO(C.Structure):
    _fields_ = [('cb', W.DWORD), ('lpReserved', W.LPWSTR), ('lpDesktop', W.LPWSTR),
                ('lpTitle', W.LPWSTR), ('dwX', W.DWORD), ('dwY', W.DWORD),
                ('dwXSize', W.DWORD), ('dwYSize', W.DWORD), ('dwXCountChars', W.DWORD),
                ('dwYCountChars', W.DWORD), ('dwFillAttribute', W.DWORD),
                ('dwFlags', W.DWORD), ('wShowWindow', W.WORD), ('cbReserved2', W.WORD),
                ('lpReserved2', C.c_void_p), ('hStdInput', W.HANDLE),
                ('hStdOutput', W.HANDLE), ('hStdError', W.HANDLE)]


class STARTUPINFOEX(C.Structure):
    _fields_ = [('StartupInfo', STARTUPINFO), ('lpAttributeList', C.c_void_p)]


class PROCESS_INFORMATION(C.Structure):
    _fields_ = [('hProcess', W.HANDLE), ('hThread', W.HANDLE),
                ('dwProcessId', W.DWORD), ('dwThreadId', W.DWORD)]


class SECURITY_CAPABILITIES(C.Structure):
    _fields_ = [('AppContainerSid', C.c_void_p), ('Capabilities', C.c_void_p),
                ('CapabilityCount', W.DWORD), ('Reserved', W.DWORD)]


class Native:
    def __init__(self):
        if os.name != 'nt':
            raise OSError('Windows required')
        self.kernel = C.WinDLL('kernel32', use_last_error=True)
        self.userenv = C.WinDLL('userenv', use_last_error=True)
        self.advapi = C.WinDLL('advapi32', use_last_error=True)
        self.ole = C.WinDLL('ole32')
        def bind(dll, name, args, result):
            function = getattr(dll, name)
            function.argtypes, function.restype = args, result
            setattr(self, name, function)
        bind(self.userenv, 'CreateAppContainerProfile',
             [W.LPCWSTR, W.LPCWSTR, W.LPCWSTR, C.c_void_p, W.DWORD, C.POINTER(C.c_void_p)], C.c_long)
        bind(self.userenv, 'DeleteAppContainerProfile', [W.LPCWSTR], C.c_long)
        bind(self.userenv, 'GetAppContainerFolderPath', [W.LPCWSTR, C.POINTER(C.c_void_p)], C.c_long)
        bind(self.advapi, 'FreeSid', [C.c_void_p], C.c_void_p)
        bind(self.advapi, 'ConvertSidToStringSidW', [C.c_void_p, C.POINTER(C.c_void_p)], W.BOOL)
        bind(self.advapi, 'OpenProcessToken', [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)], W.BOOL)
        bind(self.advapi, 'GetTokenInformation',
             [W.HANDLE, C.c_int, C.c_void_p, W.DWORD, C.POINTER(W.DWORD)], W.BOOL)
        bind(self.kernel, 'LocalFree', [C.c_void_p], C.c_void_p)
        bind(self.ole, 'CoTaskMemFree', [C.c_void_p], None)
        bind(self.kernel, 'InitializeProcThreadAttributeList',
             [C.c_void_p, W.DWORD, W.DWORD, C.POINTER(C.c_size_t)], W.BOOL)
        bind(self.kernel, 'UpdateProcThreadAttribute',
             [C.c_void_p, W.DWORD, C.c_size_t, C.c_void_p, C.c_size_t, C.c_void_p, C.c_void_p], W.BOOL)
        bind(self.kernel, 'DeleteProcThreadAttributeList', [C.c_void_p], None)
        bind(self.kernel, 'CreateProcessW',
             [W.LPCWSTR, W.LPWSTR, C.c_void_p, C.c_void_p, W.BOOL, W.DWORD,
              C.c_void_p, W.LPCWSTR, C.c_void_p, C.POINTER(PROCESS_INFORMATION)], W.BOOL)
        bind(self.kernel, 'ResumeThread', [W.HANDLE], W.DWORD)
        bind(self.kernel, 'WaitForSingleObject', [W.HANDLE, W.DWORD], W.DWORD)
        bind(self.kernel, 'GetExitCodeProcess', [W.HANDLE, C.POINTER(W.DWORD)], W.BOOL)
        bind(self.kernel, 'CloseHandle', [W.HANDLE], W.BOOL)
        bind(self.kernel, 'TerminateProcess', [W.HANDLE, W.UINT], W.BOOL)

    def require(self, result, operation):
        if not result:
            raise OSError(C.get_last_error(), operation)

    def hresult(self, value, operation):
        if value < 0:
            raise OSError(value & 0xffffffff, operation)

    def sid_string(self, pointer):
        text = C.c_void_p()
        self.require(self.ConvertSidToStringSidW(pointer, C.byref(text)), 'ConvertSidToStringSid')
        try:
            return C.wstring_at(text)
        finally:
            self.LocalFree(text)

    def token(self, process):
        handle = W.HANDLE()
        self.require(self.OpenProcessToken(process, 8, C.byref(handle)), 'OpenProcessToken')
        try:
            def information(kind):
                size = W.DWORD()
                self.GetTokenInformation(handle, kind, None, 0, C.byref(size))
                buffer = C.create_string_buffer(size.value)
                self.require(self.GetTokenInformation(handle, kind, buffer, size, C.byref(size)),
                             'GetTokenInformation')
                return buffer
            is_container = W.DWORD.from_buffer(information(29)).value == 1
            capability_count = W.DWORD.from_buffer(information(30)).value
            container = information(31)
            sid = self.sid_string(C.c_void_p.from_buffer(container).value)
            return is_container, capability_count, sid
        finally:
            self.CloseHandle(handle)


def set_acl(root, path, user_sid, app_sid, app_mask=0, inherit=False):
    import win32security as security
    target = checked_target(root, path)
    acl = security.ACL()
    flags = 3 if inherit else 0  # OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE
    acl.AddAccessAllowedAceEx(security.ACL_REVISION, flags, 0x1f01ff, user_sid)
    if app_mask:
        acl.AddAccessAllowedAceEx(security.ACL_REVISION, flags, app_mask, app_sid)
    descriptor = security.SECURITY_DESCRIPTOR()
    descriptor.SetSecurityDescriptorDacl(1, acl, 0)
    # pywin32 parses SECURITY_INFORMATION as signed Windows LONG.
    security.SetFileSecurity(str(target), C.c_long(0x80000004).value, descriptor)


def read_report(path):
    return {key: value == 'true' for key, value in
            (line.split('=', 1) for line in path.read_text(encoding='utf-8-sig').splitlines())}


def assess_network(run):
    """Timeout alone is inconclusive: require OS capability + positive-control evidence."""
    return (all(run.get(key) is True for key in ('parent_verified_token', 'zero_capabilities',
                'network_capability_missing', 'host_loopback_control_connected',
                'managed_loopback_control_connected'))
            and run.get('network_capability_diagnostic') in ('0:1', '0:2', '0:3')
            and run.get('loopback_result') in ('AccessDenied:10013', 'timeout-not-proof-of-permission-denial'))


def run_child(api, executable, root, sid, sid_text, mode, port):
    import win32job as jobs
    size = C.c_size_t()
    api.InitializeProcThreadAttributeList(None, 1, 0, C.byref(size))
    attributes = C.create_string_buffer(size.value)
    api.require(api.InitializeProcThreadAttributeList(attributes, 1, 0, C.byref(size)), 'InitializeAttributes')
    capabilities = SECURITY_CAPABILITIES(sid, None, 0, 0)
    job = jobs.CreateJobObject(None, 'FocusPilot.Prototype.Job.' + uuid.uuid4().hex)
    process = PROCESS_INFORMATION()
    try:
        limits = jobs.QueryInformationJobObject(job, jobs.JobObjectExtendedLimitInformation)
        limits['BasicLimitInformation']['LimitFlags'] |= jobs.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        jobs.SetInformationJobObject(job, jobs.JobObjectExtendedLimitInformation, limits)
        api.require(api.UpdateProcThreadAttribute(attributes, 0, 0x20009, C.byref(capabilities),
                                                C.sizeof(capabilities), None, None), 'SetSecurityCapabilities')
        startup = STARTUPINFOEX()
        startup.StartupInfo.cb = C.sizeof(startup)
        startup.StartupInfo.dwFlags = 1
        startup.lpAttributeList = C.cast(attributes, C.c_void_p)
        command = C.create_unicode_buffer(subprocess.list2cmdline(
            [str(executable), 'normal' if mode == 'normal' else 'linger', str(root), str(port)]))
        env = {'SystemRoot': os.environ['SystemRoot'], 'WINDIR': os.environ['SystemRoot'],
               'SystemDrive': Path(os.environ['SystemRoot']).drive,
               'PATH': str(Path(os.environ['SystemRoot']) / 'System32'),
               'USERPROFILE': str(root / 'work'), 'LOCALAPPDATA': str(root / 'work'),
               'APPDATA': str(root / 'work'),
               'TEMP': str(root / 'work'), 'TMP': str(root / 'work')}
        environment = C.create_unicode_buffer('\0'.join(f'{key}={value}' for key, value in
            sorted(env.items(), key=lambda pair: pair[0].upper())) + '\0\0')
        flags = 0x80000 | 0x400 | 0x4 | 0x08000000  # extended, unicode env, suspended, hidden
        api.require(api.CreateProcessW(str(executable), command, None, None, False, flags,
                                      environment, str(root), C.byref(startup), C.byref(process)), 'CreateProcess-AppContainer')
        # Assign/check BEFORE any application instruction executes. No fallback to normal launch.
        jobs.AssignProcessToJobObject(job, int(process.hProcess))
        is_container, count, observed_sid = api.token(process.hProcess)
        if not is_container or count != 0 or observed_sid != sid_text:
            raise RuntimeError('AppContainer identity/capabilities did not match')
        if api.ResumeThread(process.hThread) == 0xffffffff:
            api.require(False, 'ResumeThread')
        deadline = time.monotonic() + 10
        while api.WaitForSingleObject(process.hProcess, 25) == 258:
            ready = root / 'work' / 'sleep.ready'
            if mode != 'normal' and ready.exists():
                contents = ready.read_text(encoding='utf-8-sig')
                if not contents.endswith(':True'):
                    raise RuntimeError('Sleeping descendant is not in AppContainer')
                if mode == 'timeout':
                    if api.WaitForSingleObject(process.hProcess, 250) != 258:
                        raise RuntimeError('Expected timeout did not occur')
                jobs.TerminateJobObject(job, 77)
                break
            if time.monotonic() >= deadline:
                raise TimeoutError('Prototype child exceeded deadline')
        if mode == 'normal':
            exit_code = W.DWORD()
            api.require(api.GetExitCodeProcess(process.hProcess, C.byref(exit_code)), 'GetExitCode')
            if exit_code.value:
                raise RuntimeError('Prototype child exited: ' + str(exit_code.value))
        else:
            if not (root / 'work' / 'sleep.ready').exists():
                raise RuntimeError('No live descendant to exercise cleanup')
        until = time.monotonic() + 3
        while jobs.QueryInformationJobObject(job, jobs.JobObjectBasicAccountingInformation)['ActiveProcesses']:
            if time.monotonic() >= until:
                raise TimeoutError('Job still has live descendants')
            time.sleep(0.02)
        report = read_report(root / 'work' / 'report.txt')
        report.update(read_report(root / 'work' / 'leaf.txt'))
        if not report or not all(value for key, value in report.items()
                                 if key not in ('loopback_connect_denied', 'network_capability_missing')):
            raise RuntimeError('File/process AppContainer assertions failed: ' + json.dumps(report))
        report['loopback_result'] = (root / 'work' / 'network.txt').read_text(encoding='utf-8-sig')
        report['network_capability_diagnostic'] = (root / 'work' / 'network-capability.txt').read_text(encoding='utf-8-sig')
        report.update(parent_verified_token=True, zero_capabilities=True, job_empty=True)
        return report
    finally:
        # Kill the whole job even after failed validation; suspended unassigned process also gets stopped.
        try:
            jobs.TerminateJobObject(job, 78)
        finally:
            if process.hProcess:
                api.TerminateProcess(process.hProcess, 78)
                api.WaitForSingleObject(process.hProcess, 2000)
                api.CloseHandle(process.hProcess)
            if process.hThread:
                api.CloseHandle(process.hThread)
            job.Close()
            api.DeleteProcThreadAttributeList(attributes)


def probe():
    import win32api
    import win32security as security
    from .storage import TaskStore
    api = Native()
    windows = Path(os.environ['SystemRoot'])
    compiler = windows / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    if not compiler.is_file() or not (windows / 'System32/winsqlite3.dll').is_file():
        raise RuntimeError('Required existing Windows compiler/SQLite runtime unavailable')
    scratch = Path(__file__).resolve().parents[2] / '.tmp'
    scratch.mkdir(exist_ok=True)
    name, sid = profile_name(), C.c_void_p()
    report = {'profile': name, 'model_called': False, 'production_data_used': False}
    with TemporaryDirectory(prefix='appcontainer-', dir=scratch) as directory:
        root = checked_target(scratch, directory)
        profile_created = False
        folder = None
        try:
            executable = root / 'probe.exe'
            compilation = subprocess.run([str(compiler), '/nologo', '/target:exe', '/platform:x64',
                '/out:' + str(executable), str(Path(__file__).with_name('appcontainer_child.cs'))],
                capture_output=True, timeout=20, creationflags=0x08000000)
            if compilation.returncode:
                raise RuntimeError('Fixed child compilation failed: ' + compilation.stdout.decode(errors='replace')[-3000:])
            work = root / 'work'
            work.mkdir()
            (root / 'readonly.txt').write_text('fictional-readonly', encoding='utf-8')
            (root / 'ungranted.txt').write_text('fictional-ungranted', encoding='utf-8')
            database = root / 'fixture.sqlite3'
            store = TaskStore(database)
            store.create('fictional-sandbox-task')
            before = database.read_bytes()
            api.hresult(api.CreateAppContainerProfile(name, name, 'FocusPilot offline disposable prototype',
                                                      None, 0, C.byref(sid)), 'CreateAppContainerProfile')
            profile_created = True
            sid_text = api.sid_string(sid)
            buffer = C.c_void_p()
            api.hresult(api.GetAppContainerFolderPath(sid_text, C.byref(buffer)), 'GetAppContainerFolderPath')
            try:
                folder = Path(C.wstring_at(buffer))
            finally:
                api.CoTaskMemFree(buffer)
            app_sid = security.ConvertStringSidToSid(sid_text)
            token = security.OpenProcessToken(win32api.GetCurrentProcess(), 8)
            try:
                user_sid = security.GetTokenInformation(token, security.TokenUser)[0]
            finally:
                token.Close()
            set_acl(root, root, user_sid, app_sid, 0x1200a9)
            set_acl(root, executable, user_sid, app_sid, 0x1200a9)
            set_acl(root, root / 'readonly.txt', user_sid, app_sid, 0x120089)
            set_acl(root, database, user_sid, app_sid, 0x120089)
            set_acl(root, root / 'ungranted.txt', user_sid, app_sid)
            set_acl(root, work, user_sid, app_sid, 0x1f01ff, inherit=True)
            label = subprocess.run([str(windows / 'System32/icacls.exe'), str(checked_target(root, work)),
                '/setintegritylevel', '(OI)(CI)L'], capture_output=True, timeout=10, creationflags=0x08000000)
            if label.returncode:
                raise RuntimeError('Could not set low integrity label on disposable work directory')
            for mode in ('normal', 'cancel', 'timeout'):
                ready = work / 'sleep.ready'
                if ready.exists():
                    ready.unlink()
                # Listener is a local positive control, not an external connection.
                with socket.socket() as listener:
                    listener.settimeout(1)
                    listener.bind(('127.0.0.1', 0))
                    listener.listen(2)
                    with socket.create_connection(listener.getsockname(), timeout=1):
                        connection, _ = listener.accept()
                        connection.close()
                    # Same fixed managed socket code outside the container is a positive control,
                    # never a fallback for failed sandbox work. Only touches its control file.
                    control = subprocess.run([str(executable), 'network-control', str(root),
                        str(listener.getsockname()[1])], capture_output=True, timeout=3,
                        creationflags=0x08000000)
                    if control.returncode:
                        raise RuntimeError('Managed network positive control failed')
                    connection, _ = listener.accept()
                    connection.close()
                    report[mode] = run_child(api, executable, root, sid, sid_text, mode,
                                             listener.getsockname()[1])
                    report[mode]['host_loopback_control_connected'] = True
                    report[mode]['managed_loopback_control_connected'] = True
            report['database_unchanged'] = database.read_bytes() == before
            if not report['database_unchanged']:
                raise RuntimeError('Sandbox changed readonly fixture')
        finally:
            if profile_created:
                try:
                    api.hresult(api.DeleteAppContainerProfile(name), 'DeleteAppContainerProfile-' + name)
                    report['profile_deleted'] = folder is not None and not folder.exists()
                    if not report['profile_deleted']:
                        raise RuntimeError('Profile cleanup incomplete: ' + name)
                finally:
                    api.FreeSid(sid)
            # Verify absolute target remains under our scratch before TemporaryDirectory recursively cleans it.
            checked_target(scratch, root)
    report['temporary_directory_deleted'] = not root.exists()
    report['network_policy_checks_passed'] = all(assess_network(report[mode])
                                                for mode in ('normal', 'cancel', 'timeout'))
    report['external_egress_live_tested'] = False
    report['prototype_scope'] = 'offline_file_process_and_network_capability_policy'
    report['production_ready'] = False
    report['prototype_acceptance_passed'] = (report['network_policy_checks_passed']
        and report['database_unchanged'] and report['profile_deleted']
        and report['temporary_directory_deleted'])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--approve-sandbox-prototype', action='store_true',
                        help='Approve unique profile and disposable fixture ACL changes only')
    args = parser.parse_args()
    if not args.approve_sandbox_prototype:
        parser.error('Requires --approve-sandbox-prototype; no profile or ACL was changed')
    report = probe()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report['prototype_acceptance_passed']:
        parser.exit(1, 'Prototype acceptance incomplete: network isolation not verified; no production launch.\n')


if __name__ == '__main__':
    main()
