from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from focuspilot.sandbox_runtime_probe import copy_program_tree, read_bounded, run_command, runtime_environment, validate_versions


def test_copy_filters_configuration_and_stays_in_owned_root(tmp_path):
    source = tmp_path / 'installed'
    source.mkdir()
    for name in ('cli.js', 'auth.json', 'settings.json', 'models.json', '.env', '.env.local', '.npmrc'):
        (source / name).write_text('fictional', encoding='utf-8')
    (source / '.pi').mkdir()
    (source / '.pi' / 'secret').write_text('must-not-copy')
    (source / 'modules').mkdir()
    (source / 'modules' / 'runtime.js').write_text('runtime')
    owned = tmp_path / 'owned'
    owned.mkdir()
    report = copy_program_tree(source, owned / 'runtime', owned)
    assert report['files'] == 2 and report['hashes_verified'] is True
    assert sorted(p.name for p in (owned / 'runtime').rglob('*') if p.is_file()) == ['cli.js', 'runtime.js']
    with pytest.raises(ValueError):
        copy_program_tree(source, tmp_path / 'outside', owned)


def test_copy_rejects_reparse_entries_before_copying_them(tmp_path, monkeypatch):
    import focuspilot.sandbox_runtime_probe as module
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'escape.js').write_text('fixture')
    owned = tmp_path / 'owned'
    owned.mkdir()
    monkeypatch.setattr(module, 'is_reparse', lambda path: path.name == 'escape.js')
    with pytest.raises(ValueError):
        copy_program_tree(source, owned / 'copy', owned)
    assert not (owned / 'copy' / 'escape.js').exists()


def test_copy_never_reuses_nonempty_destination(tmp_path):
    source = tmp_path / 'installed'
    source.mkdir()
    (source / 'runtime.js').write_text('fixture')
    owned = tmp_path / 'owned'
    owned.mkdir()
    destination = owned / 'copy'
    destination.mkdir()
    (destination / 'existing').write_text('preserve')
    with pytest.raises(ValueError):
        copy_program_tree(source, destination, owned)
    assert (destination / 'existing').read_text() == 'preserve'


def test_copy_can_be_cancelled_while_enumerating_before_worker_dispatch(tmp_path):
    source=tmp_path/'source';source.mkdir();(source/'program.js').write_text('fixture')
    owned=tmp_path/'owned';owned.mkdir()
    def cancelled():raise RuntimeError('cancelled fixture')
    with pytest.raises(RuntimeError,match='cancelled fixture'):
        copy_program_tree(source,owned/'runtime',owned,cancel_check=cancelled)
    assert list((owned/'runtime').iterdir())==[]


def test_runtime_environment_does_not_inherit_provider_or_node_options(tmp_path, monkeypatch):
    monkeypatch.setenv('NODE_OPTIONS', '--require private-loader')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'fixture-key')
    env = runtime_environment(tmp_path)
    assert 'NODE_OPTIONS' not in env and 'DEEPSEEK_API_KEY' not in env
    assert 'PYTHONPATH' not in env
    assert env['PI_OFFLINE'] == '1'
    assert env['PI_CODING_AGENT_DIR'] == str(tmp_path / 'work' / 'agent')
    assert env['USERPROFILE'] == str(tmp_path / 'work')


def test_versions_require_exact_fingerprinted_install():
    validate_versions('v24.15.0\n', '1.4.0\r\n', {'node': {'version': 'v24.15.0'}, 'pi': {'version': '1.4.0'}})
    with pytest.raises(ValueError):
        validate_versions('v24.15.0', 'other', {'node': {'version': 'v24.15.0'}, 'pi': {'version': '1.4.0'}})


def test_fast_exit_output_cannot_bypass_size_limit(tmp_path):
    path = tmp_path / 'output'
    path.write_bytes(b'x' * 11)
    with pytest.raises(ValueError):
        read_bounded(path, limit=10)
    path.write_bytes(b'x' * 10)
    assert read_bounded(path, limit=10) == b'x' * 10


def test_no_approval_does_not_read_inventory_or_sources(tmp_path):
    result = subprocess.run([sys.executable, '-m', 'focuspilot.sandbox_runtime_probe',
        '--inventory', str(tmp_path / 'not-found.json')], capture_output=True, timeout=10)
    assert result.returncode == 2
    assert b'--approve-runtime-copy' in result.stderr
    assert b'Traceback' not in result.stderr


@pytest.mark.parametrize('scenario', ['wrong-token', 'job-failure'])
def test_stdio_handle_whitelist_and_fail_closed_before_resume(tmp_path, monkeypatch, scenario):
    calls = []
    (tmp_path / 'work').mkdir()
    job = SimpleNamespace(Close=lambda: calls.append('close-job'))
    def assign(*args):
        if scenario == 'job-failure':
            raise RuntimeError('job failure')
    monkeypatch.setitem(sys.modules, 'win32job', SimpleNamespace(
        CreateJobObject=lambda *args: job,
        QueryInformationJobObject=lambda *args: {'BasicLimitInformation': {'LimitFlags': 0}},
        SetInformationJobObject=lambda *args: None,
        AssignProcessToJobObject=assign,
        TerminateJobObject=lambda *args: calls.append('kill-job'),
        JobObjectExtendedLimitInformation=9, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE=512,
    ))
    def initialize(pointer, count, flags, size):
        size._obj.value = 64
        return True
    def attribute(pointer, flags, kind, value, size, previous, returned):
        if kind == 0x20002:
            assert len(value) == 3
            assert len(set(value)) == 3
            calls.append('three-explicit-handles')
        return True
    def create(*args):
        assert args[4] is True
        startup = args[-2]._obj.StartupInfo
        assert startup.dwFlags & 0x100
        process = args[-1]._obj
        process.hProcess, process.hThread = 123, 124
        return True
    api = SimpleNamespace(
        InitializeProcThreadAttributeList=initialize,
        UpdateProcThreadAttribute=attribute, CreateProcessW=create,
        token=lambda *args: (False, 0, 'wrong'),
        require=lambda result, operation: None if result else pytest.fail(operation),
        ResumeThread=lambda *args: calls.append('resume'),
        TerminateProcess=lambda handle, code: calls.append(('kill', handle)),
        WaitForSingleObject=lambda *args: 0,
        CloseHandle=lambda handle: calls.append(('close', handle)),
        DeleteProcThreadAttributeList=lambda *args: calls.append('free-attributes'),
    )
    with pytest.raises(RuntimeError):
        run_command(api, ['fixture-node.exe'], tmp_path, None, 'fixture-sid', 'fixture',
                    on_verified=lambda job: calls.append('open-bridge'))
    assert 'three-explicit-handles' in calls
    assert 'resume' not in calls
    assert 'open-bridge' not in calls
    assert ('kill', 123) in calls and 'close-job' in calls and 'free-attributes' in calls
    # Host standard-stream handles are closed even when sandbox identity is invalid.
    for path in (tmp_path / 'work').iterdir():
        path.unlink()
