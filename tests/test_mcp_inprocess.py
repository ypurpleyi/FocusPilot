import json
from pathlib import Path
import subprocess

import pytest

from focuspilot.pi_local_probe import isolated_environment


def test_node_inprocess_transport_and_adapter(tmp_path):
    root = Path(__file__).resolve().parents[1]
    inventory = json.loads((root / 'docs/PI_RUNTIME_INVENTORY.json').read_text(encoding='utf-8'))
    node = Path(inventory['node']['source'])
    if not node.exists():
        pytest.skip('Approved installed Node runtime unavailable')
    result = subprocess.run([str(node), '--test', str(root / 'tests/js/mcp_inprocess.test.mjs')],
        cwd=root, env=isolated_environment(tmp_path), capture_output=True, timeout=20)
    assert result.returncode == 0, result.stdout.decode('utf-8', errors='replace') + result.stderr.decode('utf-8', errors='replace')


def test_optin_required_before_any_inventory_or_runtime_read(tmp_path):
    import sys
    result = subprocess.run([sys.executable, '-m', 'focuspilot.pi_inprocess_probe',
        '--inventory', str(tmp_path / 'missing.json')], capture_output=True, timeout=10)
    assert result.returncode == 2 and b'--approve-inprocess-mcp' in result.stderr
    assert b'Traceback' not in result.stderr


@pytest.mark.parametrize('mutation', ['valid', 'extra-tool', 'not-stopped', 'wrong-id', 'truncated', 'model-used'])
def test_evidence_requires_real_bindings_data_and_shutdown(mutation):
    pytest.importorskip('mcp')
    from focuspilot.pi_inprocess_probe import validate_evidence, TOOL_NAMES
    evidence = {'fixed_driver': True, 'model_called': False, 'shutdown_called': True,
        'active_tools_at_ready': TOOL_NAMES, 'active_tools_after_shutdown': [],
        'registered_tools': TOOL_NAMES, 'ready': True,
        'pending': {'tasks': [{'id': 'a'}, {'id': 'b'}], 'truncated': False},
        'duplicates': {'tasks': [{'id': 'a'}, {'id': 'b'}], 'truncated': False}}
    if mutation == 'extra-tool':
        evidence['registered_tools'] = TOOL_NAMES + ['bash']
    elif mutation == 'not-stopped':
        evidence['active_tools_after_shutdown'] = TOOL_NAMES
    elif mutation == 'wrong-id':
        evidence['pending']['tasks'][0]['id'] = 'wrong'
    elif mutation == 'truncated':
        evidence['duplicates']['truncated'] = True
    elif mutation == 'model-used':
        evidence['model_called'] = True
    if mutation == 'valid':
        validate_evidence(evidence, {'a', 'b'}, 'normal')
    else:
        with pytest.raises(ValueError):
            validate_evidence(evidence, {'a', 'b'}, 'normal')
