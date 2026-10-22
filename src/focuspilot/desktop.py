"""Qt native hotkey bridge and per-database single-instance coordination."""

import ctypes
import hashlib
from pathlib import Path

from PySide6.QtCore import QAbstractNativeEventFilter, QLockFile, QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from .windows import HOTKEY_ID, WM_HOTKEY, HotkeyRegistration


class NativeHotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, app, callback, registration=None):
        super().__init__()
        self.app = app
        self.callback = callback
        self.registration = registration if registration is not None else HotkeyRegistration()
        app.installNativeEventFilter(self)

    def start(self):
        return self.registration.start()

    def close(self):
        self.registration.close()
        self.app.removeNativeEventFilter(self)

    def nativeEventFilter(self, event_type, message):
        if bytes(event_type) not in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            return False, 0
        if not self.registration.registered:
            return False, 0
        from ctypes import wintypes

        event = wintypes.MSG.from_address(int(message))
        if event.message == WM_HOTKEY and event.wParam == HOTKEY_ID:
            QTimer.singleShot(0, self.callback)
            return True, 0
        return False, 0


class InstanceGuard(QObject):
    activated = Signal()

    def __init__(self, database_path):
        super().__init__()
        path = Path(database_path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        identity = str(path).casefold()
        self.name = "FocusPilot-" + hashlib.sha256(identity.encode()).hexdigest()[:24]
        self.lock = QLockFile(str(path) + ".lock")
        self.lock.setStaleLockTime(0)
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.newConnection.connect(self._accept)
        self.owned = False

    def acquire(self):
        if self.lock.tryLock(0):
            if not self.server.listen(self.name):
                self.lock.unlock()
                raise OSError(f"无法创建 FocusPilot 本地入口：{self.server.errorString()}")
            self.owned = True
            return True
        socket = QLocalSocket()
        socket.connectToServer(self.name)
        if not socket.waitForConnected(500):
            raise OSError("FocusPilot 已在启动或数据目录被锁定，请稍后重试。")
        socket.write(b"show\n")
        socket.flush()
        socket.waitForBytesWritten(500)
        if not socket.bytesAvailable() and not socket.waitForReadyRead(1500):
            raise OSError("FocusPilot 窗口暂未响应，请稍后重试。")
        if bytes(socket.readAll()).strip() != b"ok":
            raise OSError("FocusPilot 本地入口返回了无效响应。")
        socket.disconnectFromServer()
        return False

    def _accept(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.readyRead.connect(lambda s=socket: self._read(s))
            socket.disconnected.connect(socket.deleteLater)
            self._read(socket)

    def _read(self, socket):
        if socket.canReadLine():
            command = bytes(socket.readLine(32)).strip()
            if command == b"show":
                self.activated.emit()
                socket.write(b"ok\n")
                socket.flush()
            socket.disconnectFromServer()

    def close(self):
        if self.owned:
            self.server.close()
            self.lock.unlock()
            self.owned = False
