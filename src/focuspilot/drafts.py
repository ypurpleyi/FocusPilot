"""Untrusted model proposals, human review, then atomic application commit.

No model-facing write tool exists. Deadline ambiguity remains for human review.
"""

import copy
from datetime import datetime
import json
import re
import uuid


def reviewed_fields(value):
    if not isinstance(value, dict) or set(value) != {'title', 'due_at', 'importance', 'estimated_minutes'}:
        raise ValueError('待办字段不完整')
    title, due = value['title'], value['due_at']
    if not isinstance(title, str) or not title.strip() or len(title) > 1000:
        raise ValueError('任务标题须为1–1000字')
    if due is not None:
        if not isinstance(due, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}', due):
            raise ValueError('截止时间格式无效')
        datetime.fromisoformat(due)
    for field, maximum in (('importance', 3), ('estimated_minutes', 1440)):
        if type(value[field]) is not int or not 1 <= value[field] <= maximum:
            raise ValueError('重要程度或预计耗时无效')
    return {**value, 'title': title.strip()}


def parse_drafts(text, original):
    if not isinstance(text, str) or len(text.encode('utf-8')) > 65_536:
        raise ValueError('分析结果过大')
    text = text.strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text)
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('分析结果有重复字段')
            result[key] = value
        return result
    try:
        data = json.loads(text, object_pairs_hook=pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError('非法数值')))
    except (ValueError, RecursionError):
        raise ValueError('分析结果格式无效，请重试') from None
    if isinstance(data, dict) and data.get('error') == 'too_many_tasks':
        raise ValueError('一次最多分析5项任务，请分批输入')
    if not isinstance(data, dict) or set(data) != {'tasks'} or not isinstance(data['tasks'], list) or not 1 <= len(data['tasks']) <= 5:
        raise ValueError('需要1–5项有效待办')
    expected = {'title', 'due_at', 'importance', 'estimated_minutes', 'field_sources', 'source_text', 'due_note'}
    fields = {'title', 'due_at', 'importance', 'estimated_minutes'}
    for item in data['tasks']:
        if not isinstance(item, dict) or set(item) != expected:
            raise ValueError('草稿包含未知或缺失字段')
        sources = item['field_sources']
        if (not isinstance(sources, dict) or set(sources) != fields
                or any(value not in ('explicit', 'suggested', 'missing') for value in sources.values())):
            raise ValueError('草稿必须标明字段来源')
        quote = item['source_text']
        if not isinstance(quote, str) or not quote.strip() or quote not in original:
            raise ValueError('草稿引用不属于本次输入')
        if not isinstance(item['due_note'], str) or len(item['due_note']) > 1000:
            raise ValueError('截止时间提示无效')
        if item['due_at'] is not None and sources['due_at'] != 'explicit':
            raise ValueError('截止时间不明确，需要用户补充')
        if item['due_at'] is None and sources['due_at'] != 'missing':
            raise ValueError('缺失截止时间需明确标注')
        reviewed_fields({key: item[key] for key in fields})
    return copy.deepcopy(data['tasks'])


class DraftBatch:
    def __init__(self, tasks):
        self.batch_id = uuid.uuid4().hex
        self.tasks = copy.deepcopy(tasks)
        self.confirmed = False
        self.reviewed = {}
        self.discarded = set()
        self.state = 'draft'

    def confirm_count(self):
        if self.state != 'draft':
            raise ValueError('此草稿批次已失效')
        self.confirmed = True

    def discard(self, index):
        if self.state != 'draft' or not self.confirmed or not 0 <= index < len(self.tasks):
            raise ValueError('草稿不可修改')
        self.discarded.add(index)
        self.reviewed.pop(index, None)

    def review(self, index, fields, *, no_due_confirmed=False):
        if (self.state != 'draft' or not self.confirmed or index in self.discarded
                or not 0 <= index < len(self.tasks)):
            raise ValueError('请先确认本次任务清单')
        fields = reviewed_fields(fields)
        if fields['due_at'] is None and not no_due_confirmed:
            raise ValueError('请补充截止时间，或明确确认不设置截止时间')
        self.reviewed[index] = fields

    def cancel(self):
        if self.state != 'saved':
            self.state = 'cancelled'
            self.reviewed.clear()

    def save(self, store):
        required = set(range(len(self.tasks))) - self.discarded
        if self.state != 'draft' or not self.confirmed or set(self.reviewed) != required or not required:
            raise ValueError('请先确认所有保留任务的字段')
        saved = store.create_many([self.reviewed[index] for index in sorted(required)])
        self.state = 'saved'
        return saved
