"""Conservative, deterministic local dates. Ambiguous periods stay unset."""
from datetime import datetime,timedelta
import re


def number(text):
    if text.isdigit():return int(text)
    digits={'零':0,'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9}
    if text=='十':return 10
    if '十' in text:
        left,right=text.split('十')
        return (digits.get(left,1) if left else 1)*10+(digits.get(right,0) if right else 0)
    return digits.get(text,-1)


def explicit_deadline(text,reference):
    """Return local ISO only when one date and one explicit clock are clear."""
    if len([part for part in re.split(r'[。；;\n]+',text) if part.strip()])>1:return None
    dates=[]
    for match in re.finditer(r'(?:(\d{4})[年/-])?(\d{1,2})[月/-](\d{1,2})日?',text):
        try:dates.append(datetime(int(match[1]) if match[1] else reference.year,int(match[2]),int(match[3])).date())
        except ValueError:return None
    days={'今天':0,'明天':1,'后天':2}
    for match in re.finditer(r'今天|明天|后天',text):dates.append((reference+timedelta(days=days[match[0]])).date())
    weekdays={'一':0,'二':1,'三':2,'四':3,'五':4,'六':5,'日':6,'天':6}
    for match in re.finditer(r'(本周|下周|周|星期)([一二三四五六日天])',text):
        target=weekdays[match[2]]
        if match[1] in ('本周','下周'):
            offset=target-reference.weekday()+(7 if match[1]=='下周' else 0)
        else:offset=(target-reference.weekday())%7
        dates.append((reference+timedelta(days=offset)).date())
    clocks=[]
    pattern=r'(凌晨|早上|上午|中午|下午|晚上)?\s*(\d{1,2}|[零一二两三四五六七八九十]{1,3})(?:[:：](\d{2})|[点时](?:(半)|(\d{1,2}|[零一二两三四五六七八九十]{1,3})分?)?)'
    for match in re.finditer(pattern,text):
        hour=number(match[2]);minute=int(match[3]) if match[3] else 30 if match[4] else number(match[5]) if match[5] else 0
        if match[1] in ('下午','晚上') and 1<=hour<12:hour+=12
        if match[1]=='中午' and 1<=hour<6:hour+=12
        if match[1] in ('凌晨','早上','上午') and hour==12:hour=0
        if not 0<=hour<=23 or not 0<=minute<=59:return None
        clocks.append((hour,minute))
    if len(dates)!=1 or len(clocks)!=1:return None
    return datetime.combine(dates[0],datetime.min.time()).replace(hour=clocks[0][0],minute=clocks[0][1]).isoformat(timespec='seconds')


def normalize_deadlines(tasks,original,reference):
    for task in tasks:
        source=task['source_text']
        if len([part for part in re.split(r'[。；;\n]+',source) if part.strip()])>1:
            candidates=[part.strip() for part in re.split(r'[。；;\n]+',original) if task['title'] in part]
            if len(candidates)==1:source=candidates[0];task['source_text']=source
        deadline=explicit_deadline(source,reference)
        task['due_at']=deadline
        task['field_sources']['due_at']='explicit' if deadline else 'missing'
        task['due_note']='按本次输入的本地参考日期换算，请检查。' if deadline else (task['due_note'] or '请补充明确截止时间，或确认不设置。')
    return tasks
