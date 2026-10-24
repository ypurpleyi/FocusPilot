"""Opt-in offline real Pi extension/MCP integration in a disposable AppContainer."""

import argparse
import json
from pathlib import Path
import shutil

from .mcp_server import LIST_INPUT, DUPLICATE_INPUT, QUERY_OUTPUT
from .pi_local_probe import check_state
from .sandbox_runtime_probe import copy_program_tree, digest, ProbeFailure, read_bounded


TOOL_NAMES = ['mcp__focuspilot__find_duplicate_tasks', 'mcp__focuspilot__list_tasks']


def validate_evidence(evidence, expected, scenario):
    if (evidence.get('failure') or evidence.get('model_called') is not False
            or evidence.get('fixed_driver') is not True or evidence.get('shutdown_called') is not True
            or evidence.get('active_tools_after_shutdown') != []
            or evidence.get('active_tools_at_ready') != TOOL_NAMES
            or evidence.get('registered_tools') != TOOL_NAMES):
        raise ValueError('Pi extension evidence missing or unexpected tools')
    if scenario == 'normal':
        if evidence.get('ready') is not True:
            raise ValueError('Pi tools were not activated')
        for key in ('pending', 'duplicates'):
            query = evidence.get(key, {})
            tasks = query.get('tasks', [])
            if (query.get('truncated') is not False or len(tasks) != len(expected)
                    or {item.get('id') for item in tasks} != expected):
                raise ValueError('Pi query evidence mismatch')
    elif (evidence.get('ready') is not False or evidence.get('call_after_stop_rejected') is not True
          or evidence.get('active_tools_after_stop') != []):
        raise ValueError('Pi did not invalidate stopped run')


def check_inprocess(api, base, root, sid, sid_text, source_pi, inventory, relay_command, expected, report):
    runtime, work = root / 'runtime', root / 'work'
    staged = runtime / 'pi'
    print('Copying approved Pi tree for in-process MCP extension...', flush=True)
    report['copy'] = copy_program_tree(source_pi, staged, root,
        progress=lambda count: print(f'Copied and verified {count} Pi files...', flush=True))
    entry = staged / 'dist/bundle/cli.js'
    if digest(entry) != inventory['pi']['cli_sha256']:
        raise ValueError('Copied Pi fingerprint mismatch')
    for asset in ('mcp_transport.mjs', 'mcp_adapter.mjs', 'pi_mcp_extension.mjs'):
        shutil.copyfile(Path(__file__).with_name(asset), staged / asset)
    (staged / 'focuspilot_mcp_contract.json').write_text(json.dumps({
        'list_tasks': {'inputSchema': LIST_INPUT, 'outputSchema': QUERY_OUTPUT},
        'find_duplicate_tasks': {'inputSchema': DUPLICATE_INPUT, 'outputSchema': QUERY_OUTPUT},
    }), encoding='utf-8')
    agent = work / 'agent'
    agent.mkdir()
    (agent / 'settings.json').write_text(json.dumps({'defaultProjectTrust': 'never',
        'enableAnalytics': False, 'packages': [], 'retry': {'enabled': False},
        'compaction': {'enabled': False}}), encoding='utf-8')
    artifact = work / 'focuspilot-adapter-evidence.json'
    for scenario in ('normal', 'cancel', 'timeout'):
        if artifact.exists():
            artifact.unlink()  # Only the explicitly named artifact in this owned root.
        def command(pipe):
            (staged / 'focuspilot_mcp_config.json').write_text(json.dumps({
                'pipe': pipe, 'offline_probe': True, 'scenario': scenario}), encoding='utf-8')
            return base + [entry, '--mode', 'rpc', '--no-session', '--no-approve',
                '--no-extensions', '-e', staged / 'pi_mcp_extension.mjs', '--no-mcp',
                '--no-builtin-tools', '--tools', ','.join(TOOL_NAMES),
                '--no-skills', '--no-themes', '--no-prompt-templates', '--no-context-files']
        label = 'inprocess-' + scenario
        try:
            # Only a state query is sent. The offline extension invokes its own
            # registered callbacks with fixed fictional args, never an LLM.
            output = relay_command(label, command, timeout=25,
                interrupt=None if scenario == 'normal' else scenario,
                ready_path=None if scenario == 'normal' else artifact,
                expected_error=None if scenario == 'normal' else 'inactive_or_expired',
                data=b'{"id":"local-state","type":"get_state"}\n')
            if output is None:
                raise ValueError('Pi did not complete the stopped session normally')
        except Exception:
            report[label + '-diagnostic'] = {
                suffix: read_bounded(work / (label + suffix))[-2500:].decode('utf-8', errors='replace')
                for suffix in ('.out', '.err') if (work / (label + suffix)).exists()}
            if artifact.exists():
                report[label + '-artifact'] = json.loads(read_bounded(artifact))
            raise
        check_state(output)
        evidence = json.loads(read_bounded(artifact))
        report[label + '-evidence'] = evidence
        validate_evidence(evidence, expected, scenario)
        if report[label]['forwarded_calls'] != (2 if scenario == 'normal' else 0):
            raise ValueError('MCP call evidence mismatch')
        report[label]['extension'] = {
            'active_tools_at_ready': evidence['active_tools_at_ready'], 'registered_tools': evidence['registered_tools'],
            'active_tools_after_shutdown': evidence['active_tools_after_shutdown'],
            'shutdown_called': evidence['shutdown_called'], 'fixed_driver': True,
            'ready': evidence['ready'], 'call_after_stop_rejected': evidence.get('call_after_stop_rejected'),
        }
        del report[label + '-evidence']
    report['pi_inprocess_mcp_verified'] = True
    report['pi_native_stdio_fixed'] = False
    report['autonomous_agent_loop_tested'] = False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path,
        default=Path(__file__).resolve().parents[2] / 'docs/PI_RUNTIME_INVENTORY.json')
    parser.add_argument('--approve-inprocess-mcp', action='store_true')
    args = parser.parse_args()
    if not args.approve_inprocess_mcp:
        parser.error('Requires --approve-inprocess-mcp; no inventory, profile or runtime accessed')
    from .sandbox_mcp_probe import probe
    try:
        report = probe(args.inventory, inprocess=True)
    except ProbeFailure as error:
        print(json.dumps(error.report, ensure_ascii=False, indent=2))
        parser.exit(1, 'In-process MCP probe failed; no privilege fallback.\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
