"""Offline RPC primitives and read-only business queries; no model or network client."""

from contextlib import contextmanager
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
import uuid


class ProtocolError(ValueError):
    pass


class JsonLines:
    """Frame bytes on LF only, including fragmented UTF-8 and CRLF input."""

    def __init__(self, max_record_bytes=1_048_576):
        self.buffer = bytearray()
        self.limit = max_record_bytes

    def feed(self, chunk: bytes) -> list[dict]:
        self.buffer.extend(chunk)
        records = []
        while (end := self.buffer.find(b'\n')) >= 0:
            if end > self.limit:
                raise ProtocolError('RPC 记录超过大小限制')
            raw = bytes(self.buffer[:end]).removesuffix(b'\r')
            del self.buffer[:end + 1]
            try:
                def invalid_constant(value):
                    raise ValueError(value)
                record = json.loads(raw.decode('utf-8'), parse_constant=invalid_constant)
            except (ValueError, UnicodeError, RecursionError) as error:
                raise ProtocolError('RPC 包含无效 JSON 或 UTF-8') from error
            if not isinstance(record, dict) or not isinstance(record.get('type'), str):
                raise ProtocolError('RPC 记录必须包含 type 字符串')
            pending = [(record, 0)]
            while pending:
                value, depth = pending.pop()
                if depth > 64:
                    raise ProtocolError('RPC JSON 嵌套超过 64 层')
                if isinstance(value, dict):
                    pending.extend((item, depth + 1) for item in value.values())
                elif isinstance(value, list):
                    pending.extend((item, depth + 1) for item in value)
            records.append(record)
        if len(self.buffer) > self.limit:
            raise ProtocolError('RPC 记录超过大小限制')
        return records

    def finish(self):
        if self.buffer:
            raise ProtocolError('RPC 退出时存在未完成记录')


@dataclass(frozen=True)
class RunPolicy:
    timeout_seconds: float = 60
    stop_grace_seconds: float = 1
    max_tool_calls: int = 8

    def __post_init__(self):
        import math
        for value in (self.timeout_seconds, self.stop_grace_seconds):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError('运行时限和停止宽限必须为正有限数')
        if type(self.max_tool_calls) is not int or not 1 <= self.max_tool_calls <= 100:
            raise ValueError('工具次数上限必须为 1–100')


READ_ONLY_TOOLS = frozenset({'list_tasks', 'find_duplicate_tasks'})
# Reserved registration name for this project's future Pi MCP configuration.
# Exact aliases only: an unrelated server/tool must not pass an endswith check.
READ_ONLY_EVENT_TOOLS = READ_ONLY_TOOLS | frozenset(
    'mcp__focuspilot__' + name for name in READ_ONLY_TOOLS)


class RunState:
    """Application state, not a verbatim mirror of Pi events. One run per process."""

    TERMINAL = frozenset({'succeeded', 'failed', 'cancelled', 'timed_out'})

    def __init__(self, text: str, *, now: float, policy: RunPolicy | None = None):
        if not isinstance(text, str) or not text.strip() or len(text) > 8000:
            raise ValueError('分析文本必须为 1–8000 字符')
        self.run_id = uuid.uuid4().hex
        self.command_id = 'prompt-' + self.run_id
        self.text = text
        self.policy = policy or RunPolicy()
        self.deadline = now + self.policy.timeout_seconds
        self.state = 'submitting'
        self.error = ''
        self.result = None
        self.accepted = False
        self.settled = False
        self.final_text = ''
        self.run_error = ''
        self.tool_errors = set()
        self.tool_calls = 0

    @property
    def terminal(self):
        return self.state in self.TERMINAL

    def fail(self, message):
        if not self.terminal:
            self.error = message
            self.state = 'failed'

    def cancel(self):
        if not self.terminal:
            self.state = 'cancelled'
            self.result = None

    def check_deadline(self, now):
        if not self.terminal and now >= self.deadline:
            self.state = 'timed_out'
            self.error = '分析超过总时限，原始输入已保留'
            self.result = None

    def receive(self, record: dict):
        if self.terminal:
            return
        kind = record['type']
        if kind == 'response':
            if record.get('id') != self.command_id:
                return
            if record.get('command') != 'prompt' or record.get('success') is not True:
                self.fail('分析请求被拒绝')
                return
            data = record.get('data')
            if not isinstance(data, dict) or data.get('disposition') != 'started':
                self.fail('不支持排队或被其他处理器接管的分析请求')
                return
            self.accepted = True
            self.state = 'running'
        elif kind == 'agent_start':
            self.state = 'running'
        elif kind == 'message_end':
            message = record.get('message')
            if not isinstance(message, dict):
                self.fail('模型消息格式无效')
                return
            if message.get('role') == 'assistant':
                if message.get('stopReason') in ('error', 'aborted'):
                    self.run_error = '模型最终错误或中止'
                    self.final_text = ''
                elif message.get('stopReason') == 'stop':
                    content = message.get('content', [])
                    if not isinstance(content, list):
                        self.fail('模型消息内容格式无效')
                        return
                    self.final_text = ''.join(part.get('text', '') for part in content
                                              if isinstance(part, dict) and part.get('type') == 'text'
                                              and isinstance(part.get('text'), str))
                else:
                    self.final_text = ''
        elif kind == 'auto_retry_end':
            if record.get('success') is True:
                self.run_error = ''
            else:
                self.run_error = '模型重试失败'
        elif kind == 'tool_execution_start':
            if record.get('toolName') not in READ_ONLY_EVENT_TOOLS:
                self.fail('观察到未授权工具，分析已停止')
                return
            self.tool_calls += 1
            if self.tool_calls > self.policy.max_tool_calls:
                self.fail('工具调用超过本轮上限')
                return
        elif kind == 'tool_execution_end':
            # Observation only. Never dispatch commands from event payloads.
            if record.get('toolName') not in READ_ONLY_EVENT_TOOLS:
                self.fail('观察到未授权工具结果')
                return
            if record.get('isError') is True:
                self.tool_errors.add(record['toolName'])
            elif record.get('isError') is False:
                self.tool_errors.discard(record['toolName'])
        elif kind == 'agent_settled':
            self.settled = True
        self._complete_if_ready()

    def _complete_if_ready(self):
        if self.terminal or not (self.accepted and self.settled):
            return
        if self.run_error or self.tool_errors or not self.final_text.strip():
            self.fail(self.run_error or ('只读工具查询失败' if self.tool_errors else '运行结束但没有有效分析结果'))
        else:
            self.state = 'succeeded'
            self.result = self.final_text


class ReadOnlyTaskTools:
    """Trusted app supplies the path; model arguments never contain SQL or paths.

    URI mode=ro and query_only protect this connection, not the whole process.
    A real Pi extension and OS isolation are separate, still-unimplemented gates.
    """

    COLUMNS = 'id,title,due_at,importance,estimated_minutes'

    def __init__(self, database_path: Path | str):
        self.path = Path(database_path).resolve()

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=1)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA query_only=ON')
            yield connection
        finally:
            connection.close()

    def call(self, name: str, arguments: dict) -> dict:
        if not isinstance(arguments, dict):
            raise ValueError('工具参数必须为对象')
        if name == 'list_tasks':
            if set(arguments) - {'limit'}:
                raise ValueError('list_tasks 含未授权参数')
            limit = arguments.get('limit', 100)
            if type(limit) is not int or not 1 <= limit <= 100:
                raise ValueError('limit 必须为 1–100 的整数')
            query = f"SELECT {self.COLUMNS} FROM tasks WHERE status='pending' ORDER BY id LIMIT ?"
            params = (limit + 1,)
        elif name == 'find_duplicate_tasks':
            if set(arguments) != {'titles'}:
                raise ValueError('查重仅接受 titles 参数')
            titles = arguments['titles']
            if (not isinstance(titles, list) or not 1 <= len(titles) <= 100
                    or any(not isinstance(t, str) or not t.strip() or len(t) > 1000 for t in titles)):
                raise ValueError('titles 必须含 1–100 个非空标题，每项不超过 1000 字符')
            titles = list(dict.fromkeys(t.strip() for t in titles))
            placeholders = ','.join('?' for _ in titles)
            limit = 100
            query = (f"SELECT {self.COLUMNS} FROM tasks WHERE status='pending' "
                     f'AND title IN ({placeholders}) ORDER BY id LIMIT ?')
            params = (*titles, limit + 1)
        else:
            raise ValueError('未授权工具')
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return {'tasks': [dict(row) for row in rows[:limit]], 'truncated': len(rows) > limit}
