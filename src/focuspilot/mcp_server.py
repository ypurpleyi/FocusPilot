"""Optional, local stdio MCP adapter. No Pi/model client or default database path.

Only trusted application/CLI configuration selects the database. MCP annotations
are descriptive; actual access limits are enforced by the business gateway and
read-only connection, not by annotations or the transport protocol.
"""

import argparse
import json
from pathlib import Path
import sqlite3

import anyio
from jsonschema import Draft202012Validator
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool, ToolAnnotations

from .rpc import ReadOnlyTaskTools


LIST_INPUT = {
    'type': 'object', 'properties': {'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100}},
    'additionalProperties': False,
}
DUPLICATE_INPUT = {
    'type': 'object', 'required': ['titles'], 'additionalProperties': False,
    'properties': {'titles': {'type': 'array', 'minItems': 1, 'maxItems': 100,
                              'items': {'type': 'string', 'minLength': 1,
                                        'maxLength': 1000, 'pattern': r'\S'}}},
}
TASK_OUTPUT = {
    'type': 'object', 'additionalProperties': False,
    'required': ['id', 'title', 'due_at', 'importance', 'estimated_minutes'],
    'properties': {
        'id': {'type': 'string', 'minLength': 1},
        'title': {'type': 'string', 'minLength': 1},
        'due_at': {'type': ['string', 'null']},
        'importance': {'type': 'integer', 'minimum': 1, 'maximum': 3},
        'estimated_minutes': {'type': 'integer', 'minimum': 1, 'maximum': 1440},
    },
}
QUERY_OUTPUT = {
    'type': 'object', 'additionalProperties': False, 'required': ['tasks', 'truncated'],
    'properties': {'tasks': {'type': 'array', 'maxItems': 100, 'items': TASK_OUTPUT},
                   'truncated': {'type': 'boolean'}},
}


def create_server(database_path: Path | str, *, max_calls: int = 8) -> Server:
    """One server instance/process per analysis session; all attempts count."""
    if type(max_calls) is not int or not 1 <= max_calls <= 100:
        raise ValueError('max_calls 必须为 1–100 的整数')
    gateway = ReadOnlyTaskTools(database_path)
    annotations = ToolAnnotations(read_only_hint=True, destructive_hint=False,
                                  idempotent_hint=True, open_world_hint=False)
    tools = [
        Tool(name='list_tasks', description='List pending tasks only; truncated=true means partial results.',
             input_schema=LIST_INPUT, output_schema=QUERY_OUTPUT, annotations=annotations),
        Tool(name='find_duplicate_tasks',
             description='Find same-title pending candidates, not semantic duplicates. User decides whether to keep.',
             input_schema=DUPLICATE_INPUT, output_schema=QUERY_OUTPUT, annotations=annotations),
    ]
    validators = {tool.name: Draft202012Validator(tool.input_schema) for tool in tools}
    output_validator = Draft202012Validator(QUERY_OUTPUT)
    attempted_calls = 0

    def failure(code, message):
        return CallToolResult(is_error=True, content=[TextContent(type='text', text=f'{code}: {message}')])

    async def list_tools(context, params):
        return ListToolsResult(tools=tools)

    async def call_tool(context, params):
        nonlocal attempted_calls
        # No await between budget check/increment: concurrent calls cannot bypass it.
        attempted_calls += 1
        if attempted_calls > max_calls:
            return failure('budget_exceeded', '本次会话的工具调用次数已达上限')
        if params.name not in validators:
            return failure('unknown_tool', '未授权工具')
        arguments = params.arguments if params.arguments is not None else {}
        if not validators[params.name].is_valid(arguments):
            return failure('invalid_arguments', '请求参数不符合工具契约')
        try:
            result = await anyio.to_thread.run_sync(gateway.call, params.name, arguments)
        except ValueError:
            return failure('invalid_arguments', '请求参数不符合业务约束')
        except (sqlite3.Error, OSError):
            # Never turn failure into empty tasks, or disclose a database path/SQL.
            return failure('query_failed', '无法完成只读查询，请重试或使用本地待办面板')
        if not output_validator.is_valid(result):
            return failure('invalid_result', '数据库结果不符合返回契约')
        text = json.dumps(result, ensure_ascii=False, separators=(',', ':'))
        if len(text.encode('utf-8')) > 65_536:
            return failure('result_too_large', '查询结果过大，请缩小查询范围')
        return CallToolResult(content=[TextContent(type='text', text=text)], structured_content=result)

    # No resources, prompts, subscriptions, sampling or arbitrary execution handlers.
    return Server('focuspilot-readonly', version='1.0.0', on_list_tools=list_tools,
                  on_call_tool=call_tool,
                  instructions='Read-only pending task queries. No writes. Same-title results are candidates only.')


async def serve(database_path, max_calls):
    server = create_server(database_path, max_calls=max_calls)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True, type=Path,
                        help='Explicitly authorized fixture/database path; no APPDATA fallback')
    parser.add_argument('--max-calls', type=int, default=8)
    args = parser.parse_args()
    if not 1 <= args.max_calls <= 100:
        parser.error('--max-calls must be between 1 and 100')
    anyio.run(serve, args.database, args.max_calls)


if __name__ == '__main__':
    main()
