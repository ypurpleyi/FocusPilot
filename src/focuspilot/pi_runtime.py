"""Background subprocess RPC transport with trusted host launch configuration.

Commands come from trusted application code, never from a prompt or tool argument.
Defaults serve the offline peer; opt-in integration probes supply isolated launch options.
"""

import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from .rpc import JsonLines, ProtocolError, RunPolicy, RunState


def offline_command(scenario='normal', database_path=None):
    command = [sys.executable, '-u', '-m', 'focuspilot.offline_peer', '--scenario', scenario]
    if database_path is not None:
        command.extend(['--fixture-db', str(Path(database_path).resolve())])
    return command


class _RpcWorker(QThread):
    record = Signal(str, dict)
    ended = Signal(str, str, str)

    def __init__(self, command, run, *, environment=None, cwd=None):
        super().__init__()
        self.command = tuple(command)
        self.environment = None if environment is None else dict(environment)
        self.cwd = cwd
        self.identity = run.run_id
        self.prompt = {'id': run.command_id, 'type': 'prompt', 'message': run.text}
        self.deadline = run.deadline
        self.grace = run.policy.stop_grace_seconds
        self.stop_requested = threading.Event()

    def request_stop(self):
        self.stop_requested.set()

    def run(self):
        incoming = queue.Queue(maxsize=128)
        outgoing = queue.Queue(maxsize=8)
        readers_done = threading.Event()
        diagnostics = bytearray()
        threads = []
        process = None
        error = ''
        parser = JsonLines()

        def enqueue(item):
            while not readers_done.is_set():
                try:
                    incoming.put(item, timeout=0.05)
                    return
                except queue.Full:
                    pass

        def stdout_reader():
            try:
                while data := process.stdout.read(4096):
                    enqueue(('data', data))
            except (OSError, ValueError):
                pass
            finally:
                enqueue(('eof', None))

        def stderr_reader():
            try:
                while data := process.stderr.read(4096):
                    # Keep a bounded tail, never treat diagnostics as RPC records.
                    diagnostics.extend(data)
                    if len(diagnostics) > 4096:
                        del diagnostics[:-4096]
            except (OSError, ValueError):
                pass

        def stdin_writer():
            try:
                while True:
                    record = outgoing.get()
                    if record is None:
                        break
                    process.stdin.write(json.dumps(record, ensure_ascii=False).encode('utf-8') + b'\n')
                    process.stdin.flush()
            except (OSError, ValueError):
                enqueue(('write_error', None))
            finally:
                try:
                    process.stdin.close()
                except (OSError, ValueError):
                    pass

        try:
            # No API keys, home configuration, or arbitrary environment variables
            # are needed by the offline peer. This is hygiene, not an OS sandbox.
            environment = {k: v for k, v in os.environ.items()
                           if k.upper() in {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP'}}
            environment.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8',
                               PYTHONPATH=str(Path(__file__).resolve().parents[1]))
            if self.environment is not None:
                environment = self.environment
            flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            process = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, bufsize=0, shell=False,
                                       env=environment, cwd=self.cwd, creationflags=flags)
            for target in (stdout_reader, stderr_reader, stdin_writer):
                thread = threading.Thread(target=target, daemon=True)
                threads.append(thread)
                thread.start()
            outgoing.put(self.prompt)
            stop_deadline = None
            stdout_done = False
            received_bytes = 0
            received_records = 0
            while True:
                now = time.monotonic()
                if now >= self.deadline and not self.stop_requested.is_set():
                    error = 'timed_out'
                    self.request_stop()
                if self.stop_requested.is_set() and stop_deadline is None:
                    stop_deadline = now + self.grace
                    outgoing.put({'id': 'clear-' + self.identity, 'type': 'clear_queue'})
                    outgoing.put({'id': 'abort-' + self.identity, 'type': 'abort'})
                    outgoing.put(None)  # EOF requests graceful shutdown after commands.
                if stop_deadline is not None and now >= stop_deadline and process.poll() is None:
                    process.terminate()  # Only the child started by this worker.
                    try:
                        process.wait(timeout=0.5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    break
                try:
                    kind, payload = incoming.get(timeout=0.02)
                    if kind == 'data':
                        received_bytes += len(payload)
                        if received_bytes > 4_194_304:
                            raise ProtocolError('本轮 RPC 输出超过总量限制')
                        for record in parser.feed(payload):
                            received_records += 1
                            if received_records > 2000:
                                raise ProtocolError('本轮 RPC 记录超过上限')
                            self.record.emit(self.identity, record)
                    elif kind == 'eof':
                        stdout_done = True
                        parser.finish()
                    elif kind == 'write_error' and not self.stop_requested.is_set():
                        raise OSError('RPC 输入管道已关闭')
                except queue.Empty:
                    pass
                if process.poll() is not None and stdout_done and incoming.empty():
                    break
            if not error and not self.stop_requested.is_set():
                error = 'Pi 子进程在结果确认前退出'
        except (OSError, ValueError, ProtocolError) as exception:
            error = f'RPC 通信失败：{type(exception).__name__}'
        finally:
            readers_done.set()
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=0.5)
                try:
                    outgoing.put_nowait(None)
                except queue.Full:
                    pass
                for thread in threads:
                    thread.join(timeout=0.2)
                for stream in (process.stdin, process.stdout, process.stderr):
                    try:
                        stream.close()
                    except (OSError, ValueError):
                        pass
            self.ended.emit(self.identity, error, diagnostics.decode('utf-8', errors='replace'))


class RpcController(QObject):
    """GUI-thread controller; results are read-only analysis, never task drafts/saves."""

    changed = Signal()
    completed = Signal(str, object)

    def __init__(self, command, *, policy=None, parent=None, environment=None, cwd=None):
        super().__init__(parent)
        self.command = tuple(command)
        # Trusted host configuration only; never populated from model arguments.
        self.environment = None if environment is None else dict(environment)
        self.cwd = cwd
        self.policy = policy or RunPolicy()
        self.run = None
        self.diagnostics = ''
        self._worker = None
        self._published = False
        self._timer = QTimer(self)
        self._timer.setInterval(25)
        self._timer.timeout.connect(self._check_deadline)

    @property
    def busy(self):
        return self._worker is not None

    def start(self, text):
        if self.busy:
            return False
        self.run = RunState(text, now=time.monotonic(), policy=self.policy)
        self.diagnostics = ''
        self._published = False
        worker = _RpcWorker(self.command, self.run, environment=self.environment, cwd=self.cwd)
        self._worker = worker
        worker.record.connect(self.receive)
        worker.ended.connect(self._ended)
        worker.finished.connect(self._release)
        self._timer.start()
        self.changed.emit()
        worker.start()
        return True

    @Slot(str, dict)
    def receive(self, identity, record):
        if not self.run or identity != self.run.run_id or self.run.terminal:
            return
        try:
            self.run.receive(record)
        except (KeyError, TypeError, ValueError):
            self.run.fail('RPC 事件结构无效')
        self._sync()

    def cancel(self):
        if self.run:
            self.run.cancel()
        if self._worker:
            self._worker.request_stop()
        self.changed.emit()

    @Slot()
    def _check_deadline(self):
        if self.run:
            self.run.check_deadline(time.monotonic())
            self._sync()

    def _sync(self):
        if self.run and self.run.terminal:
            if self._worker:
                self._worker.request_stop()
            if self.run.state == 'succeeded' and not self._published:
                self._published = True
                self.completed.emit(self.run.run_id, self.run.result)
        self.changed.emit()

    @Slot(str, str, str)
    def _ended(self, identity, error, diagnostics):
        if self.run and identity == self.run.run_id:
            self.diagnostics = diagnostics
            if error == 'timed_out':
                self.run.check_deadline(self.run.deadline)
            elif not self.run.terminal:
                self.run.fail(error or '运行已停止但无有效结果')
            self._sync()

    @Slot()
    def _release(self):
        worker = self._worker
        if worker is not None and not worker.isRunning():
            self._worker = None
            worker.deleteLater()
            self._timer.stop()
            self.changed.emit()

    def shutdown(self):
        """Bounded wait only for window/app shutdown, never during analysis."""
        self.cancel()
        worker = self._worker
        if worker is not None:
            if not worker.wait(int((self.policy.stop_grace_seconds + 3) * 1000)):
                raise RuntimeError('RPC 后台尚未停止，请等待清理')
            self._release()
