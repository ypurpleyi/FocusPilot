"""Small Windows adapters. No startup writes occur until the user opts in."""

import ctypes
import os
from pathlib import Path
import subprocess
import sys

HOTKEY_ID = 0x4650
WM_HOTKEY = 0x0312
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


class WindowsHotkeyAPI:
    def __init__(self):
        from ctypes import wintypes

        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        self.user32.RegisterHotKey.restype = wintypes.BOOL
        self.user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.UnregisterHotKey.restype = wintypes.BOOL

    def register(self):
        # CTRL | ALT | NOREPEAT; associated with this Qt thread's message queue.
        if not self.user32.RegisterHotKey(None, HOTKEY_ID, 0x4003, ord("N")):
            raise ctypes.WinError(ctypes.get_last_error())

    def unregister(self):
        if not self.user32.UnregisterHotKey(None, HOTKEY_ID):
            raise ctypes.WinError(ctypes.get_last_error())


class HotkeyRegistration:
    def __init__(self, api=None):
        self.api = api if api is not None else (WindowsHotkeyAPI() if os.name == "nt" else None)
        self.registered = False
        self.error = ""

    def start(self):
        if self.registered:
            return True
        if self.api is None:
            self.error = "全局快捷键仅支持 Windows"
            return False
        try:
            self.api.register()
        except OSError as error:
            self.error = f"Ctrl+Alt+N 无法注册，可能已被占用（{error}）；可继续点击添加任务。"
            return False
        self.registered = True
        self.error = ""
        return True

    def close(self):
        if self.registered:
            try:
                self.api.unregister()
            except OSError as error:
                self.error = str(error)
                return
            self.registered = False


def startup_command(executable=None):
    if executable is None:
        candidate = Path(sys.executable).with_name("pythonw.exe")
        executable = str(candidate if candidate.exists() else Path(sys.executable).resolve())
    command = subprocess.list2cmdline([str(executable), "-m", "focuspilot", "--background"])
    if len(command) > 260:
        raise ValueError("登录自启命令超过 Windows Run 限制，请将项目放在更短的目录。")
    return command


class StartupManager:
    def __init__(self, registry=None, command=None):
        if registry is None and os.name == "nt":
            import winreg
            registry = winreg
        self.registry = registry
        self.command = command if command is not None else startup_command()

    @property
    def supported(self):
        return self.registry is not None

    def is_enabled(self):
        if not self.supported:
            return False
        registry = self.registry
        try:
            with registry.OpenKey(registry.HKEY_CURRENT_USER, RUN_KEY, 0, registry.KEY_READ) as key:
                value, _ = registry.QueryValueEx(key, "FocusPilot")
                return bool(value)
        except FileNotFoundError:
            return False

    def set_enabled(self, enabled):
        if not self.supported:
            raise OSError("登录自启仅支持 Windows")
        registry = self.registry
        if enabled:
            with registry.CreateKeyEx(registry.HKEY_CURRENT_USER, RUN_KEY, 0, registry.KEY_SET_VALUE) as key:
                registry.SetValueEx(key, "FocusPilot", 0, registry.REG_SZ, self.command)
        else:
            try:
                with registry.OpenKey(registry.HKEY_CURRENT_USER, RUN_KEY, 0, registry.KEY_SET_VALUE) as key:
                    registry.DeleteValue(key, "FocusPilot")
            except FileNotFoundError:
                pass
