"""Opt-in, one-run real Pi tool-calling check with fictional tasks only.

Reading the specified credential/config files and making paid model requests
require explicit approval. Default invocation performs neither operation.
"""

import argparse
import copy
import json
from pathlib import Path
import re
import shutil
import sys
from tempfile import TemporaryDirectory

from PySide6.QtCore import QCoreApplication, QTimer, Slot

from .pi_local_probe import TOOLS, isolated_environment
from .pi_runtime import RpcController
from .rpc import RunPolicy
from .storage import TaskStore


PROMPT = (
    '这是虚构数据的只读工具集成测试。请实际调用list_tasks查询全部未完成任务，'
    '再调用find_duplicate_tasks，titles仅为["提交周报"]。'
    '根据工具结果，最后仅输出JSON对象，字段为pending_count和duplicate_candidate_count。'
    '不要猜测数量。工具失败就明确报告失败，不把失败当成零。不要创建、修改或删除任务。'
)


def select_key(auth, provider):
    credential = auth.get(provider, {})
    key = credential.get('key')
    if (credential.get('type') != 'api_key' or not isinstance(key, str)
            or not key.strip() or key.lstrip().startswith(('!', '$'))):
        raise ValueError('此入口仅支持所选提供方已保存的普通API密钥；不执行取密命令或OAuth刷新')
    return key


def make_model_config(source, provider, model):
    selected = copy.deepcopy(source.get('providers', {}).get(provider, {}))
    selected.pop('apiKey', None)
    selected.pop('oauth', None)
    # Do not execute config-based secret commands or copy unrelated models.
    def reject_dynamic(value):
        if isinstance(value, str) and value.startswith(('!', '$')):
            raise ValueError('测试配置不允许动态命令或环境引用')
        if isinstance(value, dict):
            for item in value.values():
                reject_dynamic(item)
        elif isinstance(value, list):
            for item in value:
                reject_dynamic(item)
    if 'models' in selected:
        selected['models'] = [item for item in selected['models'] if item.get('id') == model]
        for item in selected['models']:
            item['maxTokens'] = 1024
    overrides = selected.get('modelOverrides', {}).get(model, {})
    selected['modelOverrides'] = {model: {**overrides, 'maxTokens': 1024}}
    reject_dynamic(selected)
    selected['apiKey'] = '$FOCUSPILOT_PROBE_KEY'
    return {'providers': {provider: selected}}


def verify_evidence(evidence, final_text, expected_ids):
    observed = {}
    for name, result in evidence:
        if name not in TOOLS or result.get('truncated') is not False:
            raise ValueError('工具证据不完整或结果被截断')
        tasks = result.get('tasks', [])
        if len(tasks) != len(expected_ids) or {task.get('id') for task in tasks} != expected_ids:
            raise ValueError('工具查询结果不符合虚构样例')
        observed[name] = result
    if set(observed) != set(TOOLS):
        raise ValueError('缺少两个只读工具的实际成功执行证据')
    text = final_text.strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text)
    summary = json.loads(text)
    if (not isinstance(summary, dict) or set(summary) != {'pending_count', 'duplicate_candidate_count'}
            or any(type(value) is not int or value != 2 for value in summary.values())):
        raise ValueError('模型最终摘要与工具结果不一致')
    return summary


class ProbeController(RpcController):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.evidence = []
        self.started = {}
        self.event_counts = {}
        self.stop_reasons = []

    @Slot(str, dict)
    def receive(self, identity, record):
        if self.run and identity == self.run.run_id and not self.run.terminal:
            event_type = record.get('type', 'unknown')
            self.event_counts[event_type] = self.event_counts.get(event_type, 0) + 1
            if event_type == 'message_end' and record.get('message', {}).get('role') == 'assistant':
                reason = record['message'].get('stopReason')
                self.stop_reasons.append(reason if reason in ('stop', 'toolUse', 'error', 'aborted', 'length')
                                         else 'unknown')
            name = record.get('toolName')
            call_id = record.get('toolCallId')
            if record.get('type') == 'tool_execution_start' and name in TOOLS:
                args = record.get('args', {})
                if (name.endswith('__find_duplicate_tasks')
                        and args != {'titles': ['提交周报']}):
                    self.run.fail('模型查重参数与验收范围不符')
                self.started[call_id] = name
            elif (record.get('type') == 'tool_execution_end' and record.get('isError') is False
                  and name in TOOLS and self.started.get(call_id) == name):
                try:
                    content = record['result']['content']
                    text = ''.join(part['text'] for part in content if part.get('type') == 'text')
                    result = json.loads(text)
                    if not isinstance(result, dict):
                        raise ValueError('Expected object')
                    self.evidence.append((name, result))
                except (KeyError, TypeError, ValueError):
                    self.run.fail('工具结果无法按契约解析')
        super().receive(identity, record)


def read_json(path):
    if path.stat().st_size > 1_048_576:
        raise ValueError('配置文件过大，未读取')
    return json.loads(path.read_text(encoding='utf-8-sig'))


def probe(node, entry, auth_file, models_file, provider, model):
    key = select_key(read_json(auth_file), provider)
    config = make_model_config(read_json(models_file) if models_file else {}, provider, model)
    app = QCoreApplication.instance() or QCoreApplication([])
    with TemporaryDirectory(prefix='focuspilot-pi-model-') as directory:
        root = Path(directory)
        env = isolated_environment(root)
        # Key remains in process memory/environment; never in argv or test output.
        env['FOCUSPILOT_PROBE_KEY'] = key
        agent = root / 'agent'
        agent.mkdir()
        (agent / 'models.json').write_text(json.dumps(config), encoding='utf-8')
        (agent / 'settings.json').write_text(json.dumps({
            'defaultProjectTrust': 'never', 'enableAnalytics': False,
            'retry': {'enabled': False, 'provider': {'maxRetries': 0}},
            'compaction': {'enabled': False},
        }), encoding='utf-8')
        fixture = root / 'fixture.sqlite3'
        store = TaskStore(fixture)
        a = store.create('提交周报', '2026-10-25T17:00:00')
        b = store.create('提交周报', '2026-10-26T17:00:00')
        completed = store.create('提交周报')
        store.complete(completed.id)
        before = fixture.read_bytes()
        (agent / 'mcp.json').write_text(json.dumps({
            'autoEnableCodemode': False,
            'mcpServers': {'focuspilot': {
                'command': sys.executable, 'args': ['-u', '-m', 'focuspilot.mcp_server',
                    '--database', str(fixture), '--max-calls', '4'],
                'cwd': str(root), 'timeout': 10, 'exposure': 'direct',
                'env': {'PYTHONPATH': env['PYTHONPATH'], 'PYTHONUTF8': '1',
                        'FOCUSPILOT_PROBE_KEY': ''},
            }},
        }), encoding='utf-8')
        command = [str(node), str(entry), '--mode', 'rpc', '--no-session', '--no-approve',
                   '--no-extensions', '-e', 'builtin:mcp', '--no-skills', '--no-themes',
                   '--no-context-files', '--no-prompt-templates', '--tools', ','.join(TOOLS),
                   '--provider', provider, '--model', model, '--thinking', 'off',
                   '--system-prompt', '你是只读任务查询助手。使用已开放的业务工具，不执行其他操作。']
        controller = ProbeController(command, environment=env, cwd=str(root),
                                     policy=RunPolicy(timeout_seconds=60, max_tool_calls=4))
        timer = QTimer()
        timer.setInterval(25)
        timer.timeout.connect(lambda: app.quit() if not controller.busy else None)
        try:
            controller.start(PROMPT)
            timer.start()
            app.exec()
            if controller.run.state != 'succeeded':
                raise ProbeFailure({
                    'provider': provider, 'model': model, 'state': controller.run.state,
                    'prompt_accepted': controller.run.accepted,
                    'event_counts': controller.event_counts,
                    'assistant_stop_reasons': controller.stop_reasons,
                    'successful_tool_calls': [name for name, _ in controller.evidence],
                    'database_unchanged': fixture.read_bytes() == before,
                })
            summary = verify_evidence(controller.evidence, controller.run.result, {a.id, b.id})
            if fixture.read_bytes() != before:
                raise ValueError('虚构数据库发生变更')
            return {'provider': provider, 'model': model, 'summary': summary,
                    'successful_tool_calls': [name for name, _ in controller.evidence],
                    'database_unchanged': True}
        finally:
            timer.stop()
            controller.shutdown()
            # Diagnostics are intentionally not printed or written to disk.
            controller.environment.clear()


class ProbeFailure(ValueError):
    def __init__(self, report):
        super().__init__('真实模型验证未成功；不自动重试')
        self.report = report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node', default=shutil.which('node'))
    parser.add_argument('--pi-entry', required=True, type=Path)
    parser.add_argument('--auth-file', required=True, type=Path)
    parser.add_argument('--models-file', type=Path)
    parser.add_argument('--provider', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--approve-model-test', action='store_true',
                        help='Approve specified private config reads and one potentially paid analysis')
    args = parser.parse_args()
    if not args.approve_model_test:
        parser.error('未授权读取指定凭据/配置及模型请求；没有启动Pi或读取这些文件')
    if not args.node or not Path(args.node).is_file() or not args.pi_entry.is_file():
        parser.error('Node和Pi入口必须存在')
    try:
        report = probe(args.node, args.pi_entry, args.auth_file, args.models_file,
                       args.provider, args.model)
    except ProbeFailure as error:
        print(json.dumps(error.report, ensure_ascii=False, indent=2))
        parser.exit(1, '验证未通过；未自动重试。以上仅输出白名单状态，不包含密钥或原始API错误。\n')
    except (OSError, ValueError):
        # Do not echo exceptions containing paths, credentials or raw API responses.
        parser.exit(1, '验证未通过；未自动重试。请检查配置、模型兼容性或工具执行状态。\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
