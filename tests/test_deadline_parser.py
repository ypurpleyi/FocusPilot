from datetime import datetime
import pytest
from focuspilot.deadline_parser import explicit_deadline,normalize_deadlines


@pytest.mark.parametrize('text,expected',[
    ('明天17点提交周报','2026-10-25T17:00:00'),
    ('明天下午三点半交方案','2026-10-25T15:30:00'),
    ('周五15:30评审','2026-10-30T15:30:00'),
    ('本周五15点评审','2026-10-23T15:00:00'),
    ('下周一上午9点评审','2026-10-26T09:00:00'),
    ('2026年10月25日17点交付','2026-10-25T17:00:00'),
    ('明天下午交方案',None),('尽快完成',None),('今天25点交付',None),
    ('明天17点和后天15点分别交付',None),('明天17点交付。另一个任务没有期限。',None),
])
def test_only_unambiguous_local_deadlines_are_filled(text,expected):
    assert explicit_deadline(text,datetime(2026,10,24,18))==expected


def test_model_claim_cannot_turn_afternoon_into_arbitrary_exact_deadline():
    item={'title':'交方案','source_text':'明天下午交方案','due_at':'2026-10-25T17:00:00',
          'field_sources':{'due_at':'explicit'},'due_note':''}
    normalize_deadlines([item],item['source_text'],datetime(2026,10,24,18))
    assert item['due_at'] is None and item['field_sources']['due_at']=='missing'


def test_whole_notification_quote_does_not_copy_deadline_to_unrelated_task():
    text='明天17点提交周报。整理资料，无截止日期。'
    tasks=[{'title':title,'source_text':text,'due_at':None,'field_sources':{'due_at':'missing'},'due_note':''}
           for title in ('提交周报','整理资料')]
    normalize_deadlines(tasks,text,datetime(2026,10,24,18))
    assert tasks[0]['due_at']=='2026-10-25T17:00:00' and tasks[1]['due_at'] is None
