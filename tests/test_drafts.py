import json

import pytest

from focuspilot.drafts import parse_drafts, DraftBatch
from focuspilot.storage import TaskStore


TEXT = '明天17点提交周报，预计30分钟。'


def proposal():
    return {'title': '提交周报', 'due_at': '2026-10-25T17:00:00', 'importance': 2,
        'estimated_minutes': 30, 'source_text': TEXT, 'due_note': '',
        'field_sources': {'title': 'explicit', 'due_at': 'explicit',
                          'importance': 'suggested', 'estimated_minutes': 'explicit'}}


def test_drafts_preserve_explicit_missing_and_suggested_fields():
    item = proposal()
    parsed = parse_drafts(json.dumps({'tasks': [item]}), TEXT)
    assert parsed[0]['field_sources']['importance'] == 'suggested'
    item['due_at'] = None
    item['field_sources']['due_at'] = 'missing'
    assert parse_drafts(json.dumps({'tasks': [item]}), TEXT)[0]['due_at'] is None


@pytest.mark.parametrize('mutation', ['invented-quote', 'suggested-deadline', 'extra-field', 'bad-date', 'boolean', 'six'])
def test_invalid_proposals_never_become_saveable(mutation):
    item = proposal()
    if mutation == 'invented-quote':
        item['source_text'] = '另一段没有授权的文本'
    elif mutation == 'suggested-deadline':
        item['field_sources']['due_at'] = 'suggested'
    elif mutation == 'extra-field':
        item['sql'] = 'INSERT INTO tasks'
    elif mutation == 'bad-date':
        item['due_at'] = '2026-02-30T17:00:00'
    elif mutation == 'boolean':
        item['importance'] = True
    with pytest.raises(ValueError):
        parse_drafts(json.dumps({'tasks': [item] * (6 if mutation == 'six' else 1)}), TEXT)


def test_user_confirmation_is_required_and_cancellation_cannot_delete_saved(tmp_path):
    store = TaskStore(tmp_path / 'fixture.sqlite3')
    tasks = parse_drafts(json.dumps({'tasks': [proposal()]}), TEXT)
    batch = DraftBatch(tasks)
    with pytest.raises(ValueError):
        batch.save(store)
    batch.confirm_count()
    with pytest.raises(ValueError):
        batch.save(store)
    batch.review(0, {'title': '用户修改标题', 'due_at': None, 'importance': 3, 'estimated_minutes': 45}, no_due_confirmed=True)
    saved = batch.save(store)
    assert len(saved) == 1 and saved[0].title == '用户修改标题'
    batch.cancel()
    assert len(store.list_tasks()) == 1


def test_cancel_before_save_invalidates_all_drafts(tmp_path):
    store = TaskStore(tmp_path / 'fixture.sqlite3')
    batch = DraftBatch([proposal()])
    batch.confirm_count()
    batch.review(0, {'title': 'x', 'due_at': None, 'importance': 2, 'estimated_minutes': 30}, no_due_confirmed=True)
    batch.cancel()
    with pytest.raises(ValueError):
        batch.save(store)
    assert store.list_tasks() == []


def test_batch_insert_rolls_back_when_one_insert_fails(tmp_path):
    store = TaskStore(tmp_path / 'fixture.sqlite3')
    with store._connect() as connection:
        connection.execute("CREATE TRIGGER reject_fixture BEFORE INSERT ON tasks WHEN NEW.title='reject' BEGIN SELECT RAISE(ABORT,'fictional'); END")
    import sqlite3
    with pytest.raises(sqlite3.Error):
        store.create_many([{'title':'ok','due_at':None,'importance':2,'estimated_minutes':30},
                           {'title':'reject','due_at':None,'importance':2,'estimated_minutes':30}])
    assert store.list_tasks() == []
