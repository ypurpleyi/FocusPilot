import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from datetime import datetime, timedelta
import time

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from focuspilot.pi_runtime import RpcController, offline_command
from focuspilot.rpc import RunPolicy
from focuspilot.storage import TaskStore


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


def wait_until(predicate, timeout=4000):
    end = time.monotonic() + timeout / 1000
    while not predicate() and time.monotonic() < end:
        QTest.qWait(10)
    assert predicate(), 'condition did not become true within bounded wait'


@pytest.fixture
def make_controller(app, tmp_path):
    controllers = []
    path = tmp_path / 'fixture.sqlite3'
    store = TaskStore(path)
    store.create('Offline fixture')
    def make(scenario='normal', policy=None, database=path):
        controller = RpcController(offline_command(scenario, database), policy=policy)
        controllers.append(controller)
        return controller
    yield make
    for controller in controllers:
        controller.shutdown()
    app.processEvents()


@pytest.mark.parametrize('scenario', ['normal', 'fragmented', 'fast', 'retry'])
def test_subprocess_success_with_pipe_events_and_stderr(make_controller, scenario):
    controller = make_controller(scenario)
    results = []
    controller.completed.connect(lambda *args: results.append(args))
    assert controller.start('离线分析：不发送网络')
    wait_until(lambda: not controller.busy)
    assert controller.run.state == 'succeeded'
    assert '1' in controller.run.result
    assert len(results) == 1
    assert results[0][0] == controller.run.run_id
    assert controller.diagnostics.splitlines() == ['offline fixture diagnostic']


def test_trusted_launch_environment_and_cwd_reach_child(app, tmp_path):
    import sys
    program = (
        'import json,os,sys; p=json.loads(sys.stdin.readline()); '
        'assert os.getcwd()==os.environ["PROBE_CWD"]; '
        'assert "PROBE_VALUE" in os.environ; '
        'print(json.dumps({"type":"response","id":p["id"],"command":"prompt",'
        '"success":True,"data":{"disposition":"started"}}),flush=True); '
        'print(json.dumps({"type":"message_end","message":{"role":"assistant",'
        '"stopReason":"stop","content":[{"type":"text","text":"launch verified"}]}}),flush=True); '
        'print(json.dumps({"type":"agent_settled"}),flush=True); sys.stdin.read()'
    )
    env = {key: value for key, value in os.environ.items()
           if key.upper() in {'SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP'}}
    env.update(PROBE_CWD=str(tmp_path), PROBE_VALUE='fixture')
    controller = RpcController([sys.executable, '-u', '-c', program], environment=env,
                               cwd=str(tmp_path))
    # A caller modifying its original dict cannot change the configured launch.
    env.pop('PROBE_VALUE')
    try:
        controller.start('fixture')
        wait_until(lambda: not controller.busy)
        assert controller.run.state == 'succeeded'
        assert controller.run.result == 'launch verified'
    finally:
        controller.shutdown()


@pytest.mark.parametrize('scenario', [
    'error', 'handled', 'queued', 'broken', 'truncated', 'exit',
    'noresult', 'tool-error', 'unauthorized', 'budget',
])
def test_failures_never_publish_results_and_preserve_text(make_controller, scenario):
    controller = make_controller(scenario)
    results = []
    controller.completed.connect(lambda *args: results.append(args))
    controller.start('original')
    wait_until(lambda: not controller.busy)
    assert controller.run.state == 'failed'
    assert controller.run.text == 'original'
    assert controller.run.result is None and results == []


def test_duplicate_start_cancel_and_late_callbacks(make_controller):
    controller = make_controller('wait')
    results = []
    controller.completed.connect(lambda *args: results.append(args))
    controller.start('original A')
    old_id = controller.run.run_id
    wait_until(lambda: controller.run.accepted)
    assert not controller.start('duplicate')
    controller.cancel()
    assert controller.run.state == 'cancelled'
    assert controller.run.text == 'original A'
    assert controller.busy  # UI cancellation is immediate; cleanup is separate.
    wait_until(lambda: not controller.busy)
    controller.command = offline_command('normal')
    assert controller.start('B')
    controller.receive(old_id, {'type': 'agent_settled'})
    controller.receive(old_id, {'type': 'message_end', 'message': {
        'role': 'assistant', 'content': [{'type': 'text', 'text': 'stale A'}]}})
    wait_until(lambda: not controller.busy)
    assert controller.run.text == 'B' and 'stale A' not in controller.run.result
    assert len(results) == 1


def test_total_timeout_and_uncooperative_child_are_bounded(make_controller):
    controller = make_controller('ignore-stop', RunPolicy(timeout_seconds=0.5, stop_grace_seconds=0.1))
    controller.start('original')
    wait_until(lambda: not controller.busy)
    assert controller.run.state == 'timed_out'
    assert controller.run.result is None


def test_cancel_works_even_when_child_never_reads_large_input(make_controller):
    controller = make_controller('no-read', RunPolicy(timeout_seconds=0.5, stop_grace_seconds=0.1))
    controller.start('中' * 8000)
    wait_until(lambda: not controller.busy)
    assert controller.run.state == 'timed_out'


def test_startup_failure_is_recoverable(app, tmp_path):
    controller = RpcController([str(tmp_path / 'nonexistent.exe')])
    try:
        controller.start('original')
        wait_until(lambda: not controller.busy)
        assert controller.run.state == 'failed'
        assert controller.run.text == 'original'
    finally:
        controller.shutdown()


def test_readonly_failure_is_not_an_empty_query(make_controller, tmp_path):
    controller = make_controller(database=tmp_path / 'missing.sqlite3')
    controller.start('original')
    wait_until(lambda: not controller.busy)
    assert controller.run.state == 'failed'
    assert not (tmp_path / 'missing.sqlite3').exists()


def test_reminder_workflow_runs_while_peer_waits(make_controller, app, tmp_path, monkeypatch):
    from focuspilot.gui import FocusWindow
    from focuspilot.reminders import ReminderSettings
    store = TaskStore(tmp_path / 'reminder.sqlite3')
    now = datetime(2026, 10, 23, 10)
    task = store.create('Reminder fixture', (now + timedelta(seconds=1)).isoformat())
    panel = FocusWindow(store)
    panel.timer.stop()
    panel.reminder_settings = ReminderSettings(mode='panel', overdue_override=False)
    monkeypatch.setattr(panel, 'show_panel', lambda: None)
    controller = make_controller('wait')
    try:
        controller.start('waiting peer')
        wait_until(lambda: controller.run.accepted)
        QTimer.singleShot(0, lambda: panel.tick(now + timedelta(seconds=2)))
        wait_until(lambda: store.get(task.id).reminder_stage == 'overdue')
        assert controller.run.state == 'running'
    finally:
        controller.cancel()
        controller.shutdown()
        panel.prepare_shutdown()
        panel.deleteLater()
