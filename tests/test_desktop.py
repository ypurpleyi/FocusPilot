"""Native bridge and local IPC regressions, independent of Windows shell UI."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import ctypes
from ctypes import wintypes
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QByteArray
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from focuspilot.desktop import InstanceGuard, NativeHotkeyFilter
from focuspilot.windows import HOTKEY_ID, WM_HOTKEY


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_native_filter_only_handles_its_registered_hotkey(app):
    calls = []
    registration = SimpleNamespace(registered=True, close=lambda: None)
    bridge = NativeHotkeyFilter(app, lambda: calls.append("capture"), registration)
    message = wintypes.MSG()
    message.message = WM_HOTKEY
    message.wParam = HOTKEY_ID + 1
    pointer = ctypes.addressof(message)
    try:
        assert bridge.nativeEventFilter(QByteArray(b"windows_dispatcher_MSG"), pointer) == (False, 0)
        message.wParam = HOTKEY_ID
        assert bridge.nativeEventFilter(QByteArray(b"windows_dispatcher_MSG"), pointer) == (True, 0)
        app.processEvents()
        assert calls == ["capture"]
        registration.registered = False
        assert bridge.nativeEventFilter(QByteArray(b"windows_generic_MSG"), pointer) == (False, 0)
    finally:
        bridge.close()


def test_second_instance_requests_existing_panel_and_does_not_own_lock(app, tmp_path):
    path = tmp_path / "instance.sqlite3"
    first = InstanceGuard(path)
    calls = []
    first.activated.connect(lambda: calls.append("show"))
    script = """
import sys
from PySide6.QtCore import QCoreApplication
from focuspilot.desktop import InstanceGuard
app = QCoreApplication([])
guard = InstanceGuard(sys.argv[1])
assert not guard.acquire()
guard.close()
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    child = None
    try:
        assert first.acquire()
        child = subprocess.Popen([sys.executable, "-c", script, str(path)], env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for _ in range(100):
            if child.poll() is not None:
                break
            QTest.qWait(50)
        _, errors = child.communicate(timeout=3)
        assert child.returncode == 0, errors
        assert calls == ["show"]
        assert first.owned
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.communicate()
        first.close()
    replacement = InstanceGuard(path)
    try:
        assert replacement.acquire()
    finally:
        replacement.close()


def test_single_instance_lock_recovers_after_process_crash(app, tmp_path):
    path = tmp_path / "crashed-instance.sqlite3"
    script = """
import os, sys
from PySide6.QtCore import QCoreApplication
from focuspilot.desktop import InstanceGuard
app = QCoreApplication([])
guard = InstanceGuard(sys.argv[1])
assert guard.acquire()
os._exit(23)
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run([sys.executable, "-c", script, str(path)], env=env,
                            capture_output=True, timeout=15)
    assert result.returncode == 23, result.stderr
    replacement = InstanceGuard(path)
    try:
        assert replacement.acquire()
    finally:
        replacement.close()
