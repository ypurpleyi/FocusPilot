import json
import subprocess
import sys

import pytest

from focuspilot.pi_model_probe import make_model_config, select_key, verify_evidence


def test_select_only_requested_plain_key():
    assert select_key({'deepseek': {'type': 'api_key', 'key': 'fixture-only'},
                       'other': {'type': 'oauth', 'refresh': 'do-not-copy'}},
                      'deepseek') == 'fixture-only'


@pytest.mark.parametrize('credential', [
    {}, {'type': 'oauth', 'key': 'x'}, {'type': 'api_key', 'key': '!echo secret'},
    {'type': 'api_key', 'key': '$SECRET'}, {'type': 'api_key', 'key': ''},
])
def test_reject_missing_dynamic_or_oauth_credentials(credential):
    with pytest.raises(ValueError):
        select_key({'deepseek': credential}, 'deepseek')


def test_limit_selected_provider_and_output_without_mutating_original():
    source = {'providers': {
        'deepseek': {'baseUrl': 'https://api.example.test', 'apiKey': 'do-not-copy',
                     'models': [{'id': 'flash', 'maxTokens': 100000}, {'id': 'other'}]},
        'other': {'apiKey': 'unrelated-secret'},
    }}
    result = make_model_config(source, 'deepseek', 'flash')
    assert list(result['providers']) == ['deepseek']
    provider = result['providers']['deepseek']
    assert provider['apiKey'] == '$FOCUSPILOT_PROBE_KEY'
    assert provider['models'] == [{'id': 'flash', 'maxTokens': 1024}]
    assert provider['modelOverrides']['flash']['maxTokens'] == 1024
    assert source['providers']['deepseek']['apiKey'] == 'do-not-copy'
    assert 'do-not-copy' not in json.dumps(result)


def test_reject_executable_configuration():
    with pytest.raises(ValueError):
        make_model_config({'providers': {'deepseek': {
            'headers': {'Authorization': '!powershell something'}}}}, 'deepseek', 'flash')


def test_summary_cannot_replace_actual_tool_evidence():
    with pytest.raises(ValueError):
        verify_evidence([], '{"pending_count":2,"duplicate_candidate_count":2}', {'a', 'b'})


def test_tool_evidence_and_summary_must_agree():
    evidence = [
        ('mcp__focuspilot__' + name, {'tasks': [{'id': 'a'}, {'id': 'b'}], 'truncated': False})
        for name in ('list_tasks', 'find_duplicate_tasks')
    ]
    assert verify_evidence(evidence, '{"pending_count":2,"duplicate_candidate_count":2}',
                           {'a', 'b'})['duplicate_candidate_count'] == 2
    with pytest.raises(ValueError):
        verify_evidence(evidence, '{"pending_count":3,"duplicate_candidate_count":2}', {'a', 'b'})
    evidence[1][1]['truncated'] = True
    with pytest.raises(ValueError):
        verify_evidence(evidence, '{"pending_count":2,"duplicate_candidate_count":2}', {'a', 'b'})


def test_cli_without_approval_rejects_before_reading_config(tmp_path):
    result = subprocess.run([
        sys.executable, '-m', 'focuspilot.pi_model_probe',
        '--pi-entry', str(tmp_path / 'absent.js'),
        '--auth-file', str(tmp_path / 'absent-auth.json'),
        '--provider', 'fixture', '--model', 'fixture',
    ], capture_output=True, timeout=10)
    assert result.returncode == 2
    assert b'--approve-model-test' in result.stderr
    assert b'Traceback' not in result.stderr


def test_failure_report_retains_status_without_raw_provider_error():
    import os
    import time
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from focuspilot.pi_model_probe import ProbeController, ProbeFailure
    from focuspilot.rpc import RunState
    app = QApplication.instance() or QApplication([])
    controller = ProbeController(['unused-fixture-command'])
    controller.run = RunState('fixture', now=time.monotonic())
    controller.receive(controller.run.run_id, {'type': 'message_end', 'message': {
        'role': 'assistant', 'stopReason': 'error', 'errorMessage': 'DO_NOT_EXPOSE_RAW_ERROR',
    }})
    report = {'state': controller.run.state, 'event_counts': controller.event_counts,
              'assistant_stop_reasons': controller.stop_reasons}
    assert 'DO_NOT_EXPOSE_RAW_ERROR' not in json.dumps(ProbeFailure(report).report)
    assert report['assistant_stop_reasons'] == ['error']
    controller.shutdown()
    app.processEvents()
