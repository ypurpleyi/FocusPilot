"""Windows integration contracts; fake adapters never change real login settings."""

import pytest

from focuspilot.windows import HotkeyRegistration, StartupManager, startup_command


class FakeHotkeys:
    def __init__(self, available=True):
        self.available = available
        self.registered = 0
        self.released = 0

    def register(self):
        self.registered += 1
        if not self.available:
            raise OSError(1409, "Already registered")

    def unregister(self):
        self.released += 1


def test_hotkey_conflict_is_nonfatal_and_does_not_unregister_another_owner():
    api = FakeHotkeys(available=False)
    registration = HotkeyRegistration(api)
    assert not registration.start()
    assert registration.error
    registration.close()
    assert api.released == 0


def test_hotkey_registration_and_cleanup_are_idempotent():
    api = FakeHotkeys()
    registration = HotkeyRegistration(api)
    assert registration.start()
    assert registration.start()
    registration.close()
    registration.close()
    assert (api.registered, api.released) == (1, 1)


def test_startup_command_quotes_paths_and_does_not_depend_on_working_directory():
    command = startup_command(r"C:\Program Files\FocusPilot\pythonw.exe")
    assert command == '"C:\\Program Files\\FocusPilot\\pythonw.exe" -m focuspilot --background'
    with pytest.raises(ValueError):
        startup_command("C:/" + "x" * 270 + "/pythonw.exe")


class FakeRegistry:
    HKEY_CURRENT_USER = 1
    KEY_READ, KEY_SET_VALUE, REG_SZ = 1, 2, 1

    def __init__(self):
        self.values = {"OtherApp": "untouched"}
        self.deny = False

    def OpenKey(self, *args):
        return self

    def CreateKeyEx(self, *args):
        if self.deny:
            raise PermissionError("Denied")
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def QueryValueEx(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ

    def SetValueEx(self, key, name, reserved, kind, value):
        if self.deny:
            raise PermissionError("Denied")
        self.values[name] = value

    def DeleteValue(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


def test_startup_is_opt_in_and_can_be_disabled_without_touching_other_apps():
    registry = FakeRegistry()
    manager = StartupManager(registry=registry, command='"C:\\FocusPilot\\pythonw.exe" -m focuspilot --background')
    assert not manager.is_enabled()
    assert "FocusPilot" not in registry.values
    manager.set_enabled(True)
    assert manager.is_enabled()
    manager.set_enabled(False)
    manager.set_enabled(False)
    assert not manager.is_enabled()
    assert registry.values == {"OtherApp": "untouched"}


def test_startup_write_error_is_reported_and_does_not_persist_enabled_state():
    registry = FakeRegistry()
    registry.deny = True
    manager = StartupManager(registry=registry, command="pythonw -m focuspilot --background")
    with pytest.raises(PermissionError):
        manager.set_enabled(True)
    assert not manager.is_enabled()
