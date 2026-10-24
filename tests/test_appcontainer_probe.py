from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from focuspilot.appcontainer_probe import assess_network, checked_target, profile_name, run_child


def test_security_changes_cannot_target_outside_disposable_root(tmp_path):
    root = tmp_path / 'owned'
    root.mkdir()
    assert checked_target(root, root / 'fixture') == root / 'fixture'
    with pytest.raises(ValueError):
        checked_target(root, root / '..' / 'outside')
    with pytest.raises(ValueError):
        checked_target(root, tmp_path)


def test_profiles_are_unique_and_have_dedicated_namespace():
    a, b = profile_name(), profile_name()
    assert a != b
    assert a.startswith('FocusPilot.Prototype.')
    assert len(a) <= 64


def test_cli_requires_explicit_prototype_approval():
    result = subprocess.run([sys.executable, '-m', 'focuspilot.appcontainer_probe'],
                            capture_output=True, timeout=10)
    assert result.returncode == 2
    assert b'--approve-sandbox-prototype' in result.stderr
    assert b'Traceback' not in result.stderr


def test_network_timeout_alone_never_proves_permission_policy():
    run = {'loopback_result': 'timeout-not-proof-of-permission-denial'}
    assert assess_network(run) is False
    for key in ('parent_verified_token', 'zero_capabilities', 'network_capability_missing',
                'host_loopback_control_connected', 'managed_loopback_control_connected'):
        run[key] = True
    assert assess_network(run) is False
    run['network_capability_diagnostic'] = '0:2'
    assert assess_network(run) is True
    for bad in ('ConnectionRefused:10061', 'connected', 'unknown'):
        assert assess_network({**run, 'loopback_result': bad}) is False
    assert assess_network({**run, 'network_capability_diagnostic': '0:0'}) is False
    assert assess_network({**run, 'network_capability_diagnostic': '0:4'}) is False
    assert assess_network({**run, 'managed_loopback_control_connected': False}) is False


@pytest.mark.parametrize('scenario', ['not-container', 'unexpected-capability', 'job-assignment-failed'])
def test_invalid_launch_never_resumes_or_falls_back_and_releases_handles(tmp_path, monkeypatch, scenario):
    calls = []
    job = SimpleNamespace(Close=lambda: calls.append('close-job'))
    def assign(*args):
        if scenario == 'job-assignment-failed':
            raise RuntimeError('assignment failed')
    jobs = SimpleNamespace(
        CreateJobObject=lambda *args: job,
        QueryInformationJobObject=lambda *args: {'BasicLimitInformation': {'LimitFlags': 0}},
        SetInformationJobObject=lambda *args: None,
        AssignProcessToJobObject=assign,
        TerminateJobObject=lambda *args: calls.append('kill-job'),
        JobObjectExtendedLimitInformation=9, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE=512,
    )
    monkeypatch.setitem(sys.modules, 'win32job', jobs)
    def initialize(pointer, count, flags, size):
        size._obj.value = 64
        return True
    def create(*args):
        process = args[-1]._obj
        process.hProcess, process.hThread = 123, 124
        return True
    api = SimpleNamespace(
        InitializeProcThreadAttributeList=initialize,
        UpdateProcThreadAttribute=lambda *args: True,
        CreateProcessW=create,
        token=lambda *args: (scenario != 'not-container', 1 if scenario == 'unexpected-capability' else 0, 'fixture-sid'),
        require=lambda result, operation: None if result else pytest.fail(operation),
        ResumeThread=lambda *args: calls.append('resume'),
        TerminateProcess=lambda handle, code: calls.append(('kill-process', handle)),
        WaitForSingleObject=lambda *args: 0,
        CloseHandle=lambda handle: calls.append(('close', handle)),
        DeleteProcThreadAttributeList=lambda *args: calls.append('free-attributes'),
    )
    with pytest.raises(RuntimeError):
        run_child(api, tmp_path / 'fixture.exe', tmp_path, None, 'fixture-sid', 'normal', 1)
    assert 'resume' not in calls
    assert ('kill-process', 123) in calls
    assert ('close', 123) in calls and ('close', 124) in calls
    assert 'close-job' in calls and 'free-attributes' in calls
