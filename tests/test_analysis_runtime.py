import os
from types import SimpleNamespace

import pytest

from focuspilot.analysis_runtime import ContainerProcess


@pytest.mark.parametrize('scenario',['wrong-token','job-failure','channel-failure'])
def test_live_stdio_launch_fails_closed_before_resume(tmp_path,monkeypatch,scenario):
    import win32job
    calls=[]
    job=SimpleNamespace(Close=lambda:calls.append('job-closed'))
    monkeypatch.setattr(win32job,'CreateJobObject',lambda *args:job)
    monkeypatch.setattr(win32job,'QueryInformationJobObject',lambda job,kind:
        {'BasicLimitInformation':{'LimitFlags':0}} if kind==win32job.JobObjectExtendedLimitInformation else {'ActiveProcesses':0})
    monkeypatch.setattr(win32job,'SetInformationJobObject',lambda *args:None)
    def assign(*args):
        if scenario=='job-failure':raise RuntimeError('fixture assignment failure')
    monkeypatch.setattr(win32job,'AssignProcessToJobObject',assign)
    monkeypatch.setattr(win32job,'TerminateJobObject',lambda *args:calls.append('job-terminated'))
    def initialize(pointer,count,flags,size):size._obj.value=64;return True
    def attribute(attrs,flags,kind,value,size,previous,returned):
        if kind==0x20002:
            assert len(value)==3 and len(set(value))==3
            calls.append('three-handles')
        return True
    def create(*args):
        assert args[4] is True and args[5]&4
        assert args[-2]._obj.StartupInfo.dwFlags&0x100
        args[-1]._obj.hProcess=123;args[-1]._obj.hThread=124
        return True
    api=SimpleNamespace(InitializeProcThreadAttributeList=initialize,UpdateProcThreadAttribute=attribute,
        require=lambda value,step:None,CreateProcessW=create,
        token=lambda handle:(False,0,'bad') if scenario=='wrong-token' else (True,0,'fixture-sid'),
        ResumeThread=lambda handle:calls.append('resumed'),
        CloseHandle=lambda handle:calls.append(('closed',handle)),DeleteProcThreadAttributeList=lambda attrs:calls.append('attrs-freed'))
    box=SimpleNamespace(api=api,sid=None,sid_text='fixture-sid',work=tmp_path,command=lambda:['fixture-node.exe'])
    def verified(job):
        calls.append('channels-opened')
        if scenario=='channel-failure':raise RuntimeError('fixture channel failure')
        return lambda:None
    with pytest.raises(RuntimeError):ContainerProcess(box,{'SystemRoot':os.environ['SystemRoot']},verified)
    assert 'resumed' not in calls
    if scenario!='channel-failure':assert 'channels-opened' not in calls
    assert 'three-handles' in calls and 'job-terminated' in calls and 'job-closed' in calls
    assert ('closed',123) in calls and ('closed',124) in calls and 'attrs-freed' in calls
