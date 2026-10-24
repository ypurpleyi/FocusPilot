"""Fail-closed MCP request gate and a single-run Windows local pipe relay.

The trusted host chooses the DB. The peer cannot choose paths, commands or SQL.
This optional offline prototype does not replace the desktop RPC backend.
"""

import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import uuid

from jsonschema import Draft202012Validator
from .mcp_server import LIST_INPUT, DUPLICATE_INPUT


class BridgeRejected(ValueError):
    pass


class Frames:
    def __init__(self, limit=65_536):
        self.buffer = bytearray()
        self.limit = limit

    def feed(self, data):
        self.buffer.extend(data)
        records = []
        while (end := self.buffer.find(b'\n')) >= 0:
            if end > self.limit:
                raise BridgeRejected('frame_too_large')
            raw = bytes(self.buffer[:end])
            del self.buffer[:end + 1]
            def pairs(items):
                result = {}
                for key, value in items:
                    if key in result:
                        raise ValueError('duplicate key')
                    result[key] = value
                return result
            def invalid(value):
                raise ValueError('nonfinite JSON')
            try:
                record = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=invalid)
            except (ValueError, UnicodeError, RecursionError):
                raise BridgeRejected('invalid_json') from None
            if not isinstance(record, dict):
                raise BridgeRejected('object_required')
            pending = [(record, 0)]
            while pending:
                value, depth = pending.pop()
                if depth > 64:
                    raise BridgeRejected('json_too_deep')
                if isinstance(value, dict):
                    pending.extend((item, depth + 1) for item in value.values())
                elif isinstance(value, list):
                    pending.extend((item, depth + 1) for item in value)
            records.append(record)
        if len(self.buffer) > self.limit:
            raise BridgeRejected('frame_too_large')
        return records

    def finish(self):
        if self.buffer:
            raise BridgeRejected('truncated_frame')


def encode(record):
    return (json.dumps(record, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')


class BridgePolicy:
    def __init__(self, *, now, max_calls=8):
        self.deadline = now + 60
        self.max_calls = max_calls
        self.attempts = 0
        self.active = True
        self.phase = 'new'
        self.ids = set()
        self.pending = set()
        self.requests = 0
        self.validators = {'list_tasks': Draft202012Validator(LIST_INPUT),
                           'find_duplicate_tasks': Draft202012Validator(DUPLICATE_INPUT)}

    def cancel(self):
        self.active = False

    def check(self, now):
        if not self.active or now >= self.deadline:
            self.active = False
            raise BridgeRejected('inactive_or_expired')

    def accept(self, record, *, now):
        try:
            self.check(now)
            self.requests += 1
            if self.requests > 32 or record.get('jsonrpc') != '2.0':
                raise BridgeRejected('invalid_envelope')
            method = record.get('method')
            # Count all tool attempts before rejecting names/arguments.
            if method == 'tools/call':
                self.attempts += 1
                if self.attempts > self.max_calls:
                    raise BridgeRejected('budget_exceeded')
            if set(record) - {'jsonrpc', 'id', 'method', 'params'}:
                raise BridgeRejected('extra_envelope_fields')
            params = record.get('params', {})
            if not isinstance(params, dict):
                raise BridgeRejected('invalid_params')
            if method == 'notifications/initialized':
                if 'id' in record or params or self.phase != 'initializing':
                    raise BridgeRejected('invalid_initialization')
                self.phase = 'ready'
                return
            ident = record.get('id')
            if type(ident) not in (int, str) or (isinstance(ident, str) and len(ident) > 128):
                raise BridgeRejected('invalid_id')
            if ident in self.ids:
                raise BridgeRejected('reused_id')
            if method == 'initialize':
                if (self.phase != 'new' or set(params) != {'protocolVersion', 'capabilities', 'clientInfo'}
                        or params.get('protocolVersion') not in {'2025-11-25', '2026-07-28'}
                        or not isinstance(params.get('capabilities'), dict)
                        or not isinstance(params.get('clientInfo'), dict)):
                    raise BridgeRejected('invalid_initialization')
                # Do not advertise client capabilities beyond the reviewed flow.
                if params['capabilities']:
                    raise BridgeRejected('client_capabilities_not_allowed')
                self.phase = 'initializing'
            elif self.phase != 'ready':
                raise BridgeRejected('not_initialized')
            elif method in {'tools/list', 'ping'}:
                if params:
                    raise BridgeRejected('invalid_params')
            elif method == 'tools/call':
                if (set(params) - {'name', 'arguments'} or not isinstance(params.get('name'), str)
                        or params['name'] not in self.validators):
                    raise BridgeRejected('unauthorized_tool')
                if not self.validators[params['name']].is_valid(params.get('arguments', {})):
                    raise BridgeRejected('invalid_tool_arguments')
            else:
                raise BridgeRejected('unauthorized_method')
            self.ids.add(ident)
            self.pending.add(ident)
        except BridgeRejected:
            self.active = False
            raise

    def response(self, record, *, now):
        self.check(now)
        ident = record.get('id')
        if (record.get('jsonrpc') != '2.0' or type(ident) not in (int, str)
                or ident not in self.pending or 'method' in record
                or ('result' in record) == ('error' in record)):
            self.active = False
            raise BridgeRejected('unexpected_server_response')
        self.pending.remove(ident)
        if 'error' in record or (isinstance(record.get('result'), dict)
                                and record['result'].get('isError') is True):
            self.active = False
            raise BridgeRejected('tool_or_protocol_failed')


class PipeRelay:
    """Single connection, one host MCP child, no TCP listener or fallback.

    Windows NOWAIT pipe IO is polled with a hard deadline. Trusted child IO
    uses bounded queues and owned subprocess handles; errors revoke this run.
    """

    def __init__(self, database, sid_text, user_sid_text):
        import pywintypes
        import win32pipe
        import win32security
        self.name = r'\\.\pipe\LOCAL\FocusPilot.' + uuid.uuid4().hex
        self.sid = sid_text
        self.policy = BridgePolicy(now=time.monotonic())
        self.stop = threading.Event()
        self.job = None
        self.database = Path(database)
        self.error = None
        self.report = {'identity_verified': False, 'forwarded_calls': 0, 'child_reaped': True,
                       'pipe_tag': self.name[-32:]}
        # Low integrity peer may write only this explicitly granted pipe.
        descriptor = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
            f'D:P(A;;GA;;;{user_sid_text})(A;;GRGW;;;{sid_text})S:(ML;;NW;;;LW)', 1)
        attributes = pywintypes.SECURITY_ATTRIBUTES()
        attributes.SECURITY_DESCRIPTOR = descriptor
        # LOCAL is translated by Windows for the container client. The host
        # must create the object in that container's explicit named namespace.
        function = C.WinDLL('kernelbase', use_last_error=True).GetAppContainerNamedObjectPath
        function.argtypes = [W.HANDLE, C.c_void_p, W.ULONG, W.LPWSTR, C.POINTER(W.ULONG)]
        function.restype = W.BOOL
        sid_binary = win32security.ConvertStringSidToSid(sid_text)
        sid_buffer = C.create_string_buffer(bytes(sid_binary))
        length = W.ULONG()
        function(None, sid_buffer, 0, None, C.byref(length))
        namespace = C.create_unicode_buffer(length.value)
        if not function(None, sid_buffer, length.value, namespace, C.byref(length)):
            raise OSError(C.get_last_error(), 'GetAppContainerNamedObjectPath')
        get_session = C.WinDLL('kernel32', use_last_error=True).ProcessIdToSessionId
        get_session.argtypes, get_session.restype = [W.DWORD, C.POINTER(W.DWORD)], W.BOOL
        session = W.DWORD()
        if not get_session(os.getpid(), C.byref(session)):
            raise OSError(C.get_last_error(), 'ProcessIdToSessionId')
        relative = 'AppContainerNamedObjects\\' + sid_text
        if namespace.value != relative:
            raise BridgeRejected('unexpected_container_namespace')
        server_name = (rf'\\.\pipe\Sessions\{session.value}' + '\\' + relative + '\\'
                       + self.name.rsplit('\\', 1)[-1])
        self.host_name = server_name
        try:
            self.pipe = win32pipe.CreateNamedPipe(server_name, 3 | 0x80000,
                1 | 8, 1, 262_144, 65_536, 2000, attributes)
        except pywintypes.error as error:
            raise RuntimeError(f'Pipe namespace creation failed ({error.winerror}): {namespace.value!r}') from None
        self.thread = None
        self.closed = False

    def verify_peer(self):
        import win32api
        import win32job
        from .appcontainer_probe import Native
        api = Native()
        function = api.kernel.GetNamedPipeClientProcessId
        function.argtypes, function.restype = [W.HANDLE, C.POINTER(W.ULONG)], W.BOOL
        pid = W.ULONG()
        api.require(function(int(self.pipe), C.byref(pid)), 'GetNamedPipeClientProcessId')
        handle = win32api.OpenProcess(0x1000, False, pid.value)
        try:
            try:
                actual = api.token(int(handle))
            except OSError:
                raise BridgeRejected('peer_identity_mismatch') from None
            if actual != (True, 0, self.sid) or self.job is None or not win32job.IsProcessInJob(handle, self.job):
                raise BridgeRejected('peer_identity_mismatch')
        finally:
            handle.Close()
        self.report['identity_verified'] = True

    def attach(self, job):
        self.job = job
        self.thread = threading.Thread(target=self._run, name='FocusPilot-MCP-relay', daemon=True)
        self.thread.start()
        return self.close

    def close(self):
        # Revoke before asking the IO loop to stop. Late responses fail check().
        self.policy.cancel()
        self.stop.set()
        if self.thread:
            self.thread.join(8)
            if self.thread.is_alive():
                raise RuntimeError('Relay did not stop')
        if not self.closed:
            self.pipe.Close()
            self.closed = True
            self.report['pipe_closed'] = True

    def _run(self):
        import pywintypes
        import win32file
        import win32pipe
        child = None
        child_job = None
        workers = []
        stdout_queue, input_queue = queue.Queue(32), queue.Queue(32)
        def read_child():
            try:
                while line := child.stdout.readline(262_146):
                    if len(line) > 262_145:
                        raise BridgeRejected('server_output_too_large')
                    stdout_queue.put_nowait(line)
                stdout_queue.put_nowait(None)
            except Exception:
                self.stop.set()
                self.error = 'server_output_failed'
        def write_child():
            try:
                while not self.stop.is_set():
                    try:
                        data = input_queue.get(timeout=.05)
                    except queue.Empty:
                        continue
                    child.stdin.write(data)
                    child.stdin.flush()
            except Exception:
                self.error = 'server_input_failed'
                self.stop.set()
        try:
            while not self.stop.is_set():
                self.policy.check(time.monotonic())
                try:
                    win32pipe.ConnectNamedPipe(self.pipe, None)
                    break
                except pywintypes.error as error:
                    if error.winerror == 535:  # already connected
                        break
                    if error.winerror != 536:  # listening
                        raise
                time.sleep(.01)
            if self.stop.is_set():
                return
            self.verify_peer()
            from .pi_local_probe import isolated_environment
            env = isolated_environment(self.database.parent)
            child = subprocess.Popen([sys.executable, '-u', '-m', 'focuspilot.mcp_server',
                '--database', str(self.database), '--max-calls', '8'],
                cwd=self.database.parent, env=env, shell=False, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=0x08000000)
            self.report['child_reaped'] = False
            import win32job
            child_job = win32job.CreateJobObject(None, 'FocusPilot.MCP.Host.' + uuid.uuid4().hex)
            limits = win32job.QueryInformationJobObject(child_job, win32job.JobObjectExtendedLimitInformation)
            limits['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            win32job.SetInformationJobObject(child_job, win32job.JobObjectExtendedLimitInformation, limits)
            # This is trusted fixed server code, not untrusted agent execution.
            win32job.AssignProcessToJobObject(child_job, int(child._handle))
            workers = [threading.Thread(target=read_child, daemon=True), threading.Thread(target=write_child, daemon=True)]
            for worker in workers:
                worker.start()
            incoming, outgoing = Frames(), Frames(262_144)
            pending_write = bytearray()
            while not self.stop.is_set():
                self.policy.check(time.monotonic())
                try:
                    _, available, _ = win32pipe.PeekNamedPipe(self.pipe, 0)
                    data = win32file.ReadFile(self.pipe, min(available, 4096))[1] if available else b''
                except pywintypes.error as error:
                    if error.winerror in (109, 232, 233):
                        incoming.finish()
                        if self.policy.pending:
                            raise BridgeRejected('peer_disconnected_with_pending_requests')
                        break
                    raise
                if data:
                    for record in incoming.feed(data):
                        self.policy.accept(record, now=time.monotonic())
                        input_queue.put_nowait(encode(record))
                        if record.get('method') == 'tools/call':
                            self.report['forwarded_calls'] += 1
                while not stdout_queue.empty():
                    raw = stdout_queue.get_nowait()
                    if raw is None:
                        raise BridgeRejected('server_exited')
                    for record in outgoing.feed(raw):
                        self.policy.response(record, now=time.monotonic())
                        pending_write.extend(encode(record))
                    if len(pending_write) > 524_288:
                        raise BridgeRejected('output_backpressure')
                if pending_write:
                    _, written = win32file.WriteFile(self.pipe, bytes(pending_write[:4096]))
                    del pending_write[:written]
                time.sleep(.005)
        except Exception as error:
            # Only stable local codes, never raw server/peer diagnostics or data.
            self.error = str(error) if isinstance(error, BridgeRejected) else type(error).__name__
        finally:
            self.policy.cancel()
            self.stop.set()
            if child:
                if child.poll() is None:
                    child.terminate()
                try:
                    child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=2)
                self.report['child_reaped'] = child.poll() is not None
                # Kill descendants before joining readers: a descendant holding
                # stdout open must not strand the host's reader/stream close.
                if child_job:
                    win32job.TerminateJobObject(child_job, 78)
                    until = time.monotonic() + 2
                    while win32job.QueryInformationJobObject(child_job,
                            win32job.JobObjectBasicAccountingInformation)['ActiveProcesses']:
                        if time.monotonic() >= until:
                            self.error = 'host_job_cleanup_failed'
                            break
                        time.sleep(.01)
                    else:
                        self.report['host_job_empty'] = True
                    child_job.Close()
                    child_job = None
                for worker in workers:
                    worker.join(2)
                self.report['worker_threads_stopped'] = all(not worker.is_alive() for worker in workers)
                for stream in (child.stdin, child.stdout):
                    stream.close()
            if child_job:
                child_job.Close()
            try:
                win32pipe.DisconnectNamedPipe(self.pipe)
            except pywintypes.error:
                pass
