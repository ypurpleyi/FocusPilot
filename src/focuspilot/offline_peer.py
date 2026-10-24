"""Deterministic fake RPC peer. No model, network, credentials, or task writes.

This simulates tool execution inside a peer; it is NOT a Pi extension or an RPC
host-tool invocation protocol. Real Pi bridging remains a separate integration.
"""

import argparse
import json
import sqlite3
import sys
import threading

from .rpc import ReadOnlyTaskTools


SCENARIOS = ('normal', 'fragmented', 'fast', 'retry', 'error', 'handled', 'queued',
             'broken', 'truncated', 'exit', 'noresult', 'tool-error', 'unauthorized',
             'budget', 'wait', 'ignore-stop', 'no-read')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', choices=SCENARIOS, default='normal')
    parser.add_argument('--fixture-db')
    args = parser.parse_args()

    def emit(record):
        data = json.dumps(record, ensure_ascii=False).encode('utf-8') + b'\n'
        if args.scenario == 'fragmented':
            for index in range(0, len(data), 3):
                sys.stdout.buffer.write(data[index:index + 3])
                sys.stdout.buffer.flush()
        else:
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()

    def response(command, data=None):
        emit({'type': 'response', 'command': command['type'], 'id': command.get('id'),
              'success': True, 'data': data or {}})

    def result(text, reason='stop'):
        emit({'type': 'message_end', 'message': {'role': 'assistant', 'stopReason': reason,
              'content': [{'type': 'text', 'text': text}]}})

    sys.stderr.write('offline fixture diagnostic\n')
    sys.stderr.flush()
    if args.scenario == 'no-read':
        threading.Event().wait()
    for raw in sys.stdin.buffer:
        command = json.loads(raw)
        if command['type'] in ('clear_queue', 'abort'):
            response(command)
            if command['type'] == 'abort':
                result('取消后的迟到消息')
                emit({'type': 'agent_settled'})
            continue
        if command['type'] != 'prompt':
            continue
        scenario = args.scenario
        if scenario == 'fast':
            result('离线演示 1：快速结果先于接受响应')
            emit({'type': 'agent_settled'})
            response(command, {'disposition': 'started'})
            continue
        response(command, {'disposition': scenario if scenario in ('handled', 'queued') else 'started'})
        if scenario in ('handled', 'queued', 'wait'):
            continue
        if scenario == 'ignore-stop':
            threading.Event().wait()  # Parent must stop its own uncooperative child.
        if scenario == 'exit':
            return
        if scenario == 'broken':
            sys.stdout.buffer.write(b'{broken}\n')
            sys.stdout.buffer.flush()
            continue
        if scenario == 'truncated':
            sys.stdout.buffer.write(b'{"type":')
            sys.stdout.buffer.flush()
            return
        if scenario == 'error':
            result('模拟模型失败', 'error')
            emit({'type': 'agent_settled'})
            continue
        if scenario == 'noresult':
            emit({'type': 'agent_settled'})
            continue
        if scenario == 'unauthorized':
            emit({'type': 'tool_execution_start', 'toolName': 'bash', 'toolCallId': 'bad'})
            continue
        if scenario == 'budget':
            for index in range(9):
                emit({'type': 'tool_execution_start', 'toolName': 'list_tasks', 'toolCallId': str(index)})
            continue
        if scenario == 'retry':
            result('模拟临时错误', 'error')
            emit({'type': 'agent_end'})
            emit({'type': 'auto_retry_start'})
            emit({'type': 'auto_retry_end', 'success': True})
        if args.fixture_db or scenario == 'tool-error':
            emit({'type': 'tool_execution_start', 'toolName': 'list_tasks', 'toolCallId': 'read-1'})
            try:
                if scenario == 'tool-error':
                    raise ValueError('offline simulated query failure')
                found = ReadOnlyTaskTools(args.fixture_db).call('list_tasks', {'limit': 100})
            except (ValueError, OSError, sqlite3.Error):
                emit({'type': 'tool_execution_end', 'toolName': 'list_tasks', 'toolCallId': 'read-1',
                      'isError': True, 'result': {'content': [{'type': 'text', 'text': '查询失败'}]}})
                result('查询失败，不能判断是否存在待办')
                emit({'type': 'agent_settled'})
                continue
            emit({'type': 'tool_execution_end', 'toolName': 'list_tasks', 'toolCallId': 'read-1',
                  'isError': False, 'result': {'content': [{'type': 'text', 'text': json.dumps(found)}]}})
            text = f"离线只读查询返回 {len(found['tasks'])} 项，截断={found['truncated']}。这不是模型分析。"
        else:
            text = '离线演示 1：没有配置任务查询。'
        result(text)
        emit({'type': 'agent_end'})
        emit({'type': 'agent_settled'})


if __name__ == '__main__':
    main()
