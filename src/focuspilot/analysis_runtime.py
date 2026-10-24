"""Application-owned AppContainer lifecycle and live RPC standard streams.

No ordinary-user fallback. The model process has zero network capabilities;
its only data channels are explicitly inherited RPC handles and SID-gated pipes.
"""

import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import time
import uuid

from .appcontainer_probe import Native, PROCESS_INFORMATION, SECURITY_CAPABILITIES, STARTUPINFOEX, checked_target, profile_name, set_acl
from .sandbox_runtime_probe import copy_program_tree, digest, is_reparse, runtime_environment


class AnalysisCancelled(Exception):
    pass


class RuntimeBox:
    """One lazily prepared, disposable box reused by this application session."""
    def __init__(self, stop, status):
        import win32api
        import win32security as security
        self.api=Native();self.sid=C.c_void_p();self.profile=None;self.profile_folder=None
        self.temp=None;self.root=None
        project=Path(__file__).resolve().parents[2]
        inventory=json.loads((project/'docs/PI_RUNTIME_INVENTORY.json').read_text(encoding='utf-8'))
        source_node=Path(inventory['node']['source']);source_pi=Path(inventory['pi']['source'])
        if (not source_node.is_file() or not source_pi.is_dir() or is_reparse(source_node)
                or digest(source_node)!=inventory['node']['sha256']
                or digest(source_pi/'dist/bundle/cli.js')!=inventory['pi']['cli_sha256']):
            raise ValueError('本机Node/Pi安装与已验证版本不一致，请检查安装配置')
        scratch=project/'.tmp';scratch.mkdir(exist_ok=True)
        self.temp=TemporaryDirectory(prefix='focuspilot-app-',dir=scratch)
        self.root=checked_target(scratch,self.temp.name)
        try:
            name=profile_name()
            self.api.hresult(self.api.CreateAppContainerProfile(name,name,'FocusPilot application runtime',
                None,0,C.byref(self.sid)),'CreateAppContainerProfile')
            self.profile=name
            self.sid_text=self.api.sid_string(self.sid)
            pointer=C.c_void_p()
            self.api.hresult(self.api.GetAppContainerFolderPath(self.sid_text,C.byref(pointer)),'GetAppContainerFolderPath')
            try:self.profile_folder=Path(C.wstring_at(pointer))
            finally:self.api.CoTaskMemFree(pointer)
            app_sid=security.ConvertStringSidToSid(self.sid_text)
            token=security.OpenProcessToken(win32api.GetCurrentProcess(),8)
            try:self.user_sid=security.GetTokenInformation(token,security.TokenUser)[0]
            finally:token.Close()
            self.user_text=security.ConvertSidToStringSid(self.user_sid)
            set_acl(self.root,self.root,self.user_sid,app_sid,0x1200a9)
            self.runtime=self.root/'runtime';self.work=self.root/'work'
            self.runtime.mkdir();self.work.mkdir()
            set_acl(self.root,self.runtime,self.user_sid,app_sid,0x1200a9,inherit=True)
            set_acl(self.root,self.work,self.user_sid,app_sid,0x1f01ff,inherit=True)
            label=subprocess.run([str(Path(os.environ['SystemRoot'])/'System32/icacls.exe'),
                str(self.work),'/setintegritylevel','(OI)(CI)L'],capture_output=True,timeout=10,creationflags=0x08000000)
            if label.returncode:raise RuntimeError('无法建立受限工作区')
            status('首次准备Pi运行环境，请稍候；本地提醒仍正常运行')
            self.node=self.runtime/'node.exe';shutil.copyfile(source_node,self.node)
            if digest(self.node)!=inventory['node']['sha256']:raise RuntimeError('运行文件校验失败')
            def progress(count):
                if stop.is_set():raise AnalysisCancelled()
                status(f'首次准备Pi运行环境：已校验{count}个文件')
            def cancel_check():
                if stop.is_set():raise AnalysisCancelled()
            copy_program_tree(source_pi,self.runtime/'pi',self.root,progress=progress,cancel_check=cancel_check)
            if stop.is_set():raise AnalysisCancelled()
            self.staged=self.runtime/'pi';self.entry=self.staged/'dist/bundle/cli.js'
            for asset in ('mcp_transport.mjs','mcp_adapter.mjs','model_fetch.mjs','pi_task_extension.mjs'):
                shutil.copyfile(Path(__file__).with_name(asset),self.staged/asset)
            from .mcp_server import LIST_INPUT,DUPLICATE_INPUT,QUERY_OUTPUT
            (self.staged/'focuspilot_mcp_contract.json').write_text(json.dumps({
                'list_tasks':{'inputSchema':LIST_INPUT,'outputSchema':QUERY_OUTPUT},
                'find_duplicate_tasks':{'inputSchema':DUPLICATE_INPUT,'outputSchema':QUERY_OUTPUT}}),encoding='utf-8')
        except BaseException:
            self.close();raise

    def command(self):
        return [self.node,'--preserve-symlinks','--preserve-symlinks-main',self.entry,
            '--mode','rpc','--no-session','--no-approve','--no-extensions','-e',self.staged/'pi_task_extension.mjs',
            '--no-mcp','--no-builtin-tools','--tools',
            'mcp__focuspilot__find_duplicate_tasks,mcp__focuspilot__list_tasks',
            '--no-skills','--no-themes','--no-prompt-templates','--no-context-files',
            '--provider','deepseek','--model','deepseek-flash','--thinking','off']

    def close(self):
        try:
            if self.profile:
                self.api.hresult(self.api.DeleteAppContainerProfile(self.profile),'DeleteAppContainerProfile')
                self.profile=None
        finally:
            if self.sid.value:self.api.FreeSid(self.sid);self.sid=C.c_void_p()
            if self.temp:self.temp.cleanup();self.temp=None


class ContainerProcess:
    def __init__(self,box,environment,on_verified,*,command=None):
        import msvcrt
        import win32job
        import win32pipe
        self.api=box.api;self.process=PROCESS_INFORMATION();self.job=None
        self.stdin=self.stdout=self.stderr=None;child_handles=[];self.stop_channels=None
        size=C.c_size_t();self.api.InitializeProcThreadAttributeList(None,2,0,C.byref(size))
        attrs=C.create_string_buffer(size.value)
        self.api.require(self.api.InitializeProcThreadAttributeList(attrs,2,0,C.byref(size)),'InitializeAttributes')
        try:
            self.job=win32job.CreateJobObject(None,'FocusPilot.App.Job.'+uuid.uuid4().hex)
            limits=win32job.QueryInformationJobObject(self.job,win32job.JobObjectExtendedLimitInformation)
            limits['BasicLimitInformation']['LimitFlags']|=win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            win32job.SetInformationJobObject(self.job,win32job.JobObjectExtendedLimitInformation,limits)
            input_read,input_write=win32pipe.CreatePipe(None,65536)
            output_read,output_write=win32pipe.CreatePipe(None,65536)
            error_read,error_write=win32pipe.CreatePipe(None,65536)
            child_handles=[input_read,output_write,error_write]
            self.stdin=os.fdopen(msvcrt.open_osfhandle(input_write.Detach(),os.O_BINARY|os.O_WRONLY),'wb',buffering=0)
            self.stdout=os.fdopen(msvcrt.open_osfhandle(output_read.Detach(),os.O_BINARY|os.O_RDONLY),'rb',buffering=0)
            self.stderr=os.fdopen(msvcrt.open_osfhandle(error_read.Detach(),os.O_BINARY|os.O_RDONLY),'rb',buffering=0)
            handles=(W.HANDLE*3)(*[int(handle) for handle in child_handles])
            for handle in child_handles:os.set_handle_inheritable(int(handle),True)
            self.api.require(self.api.UpdateProcThreadAttribute(attrs,0,0x20002,handles,C.sizeof(handles),None,None),'SetHandleList')
            caps=SECURITY_CAPABILITIES(box.sid,None,0,0)
            self.api.require(self.api.UpdateProcThreadAttribute(attrs,0,0x20009,C.byref(caps),C.sizeof(caps),None,None),'SetCapabilities')
            startup=STARTUPINFOEX();startup.StartupInfo.cb=C.sizeof(startup);startup.StartupInfo.dwFlags=0x101
            startup.StartupInfo.hStdInput,startup.StartupInfo.hStdOutput,startup.StartupInfo.hStdError=handles
            startup.lpAttributeList=C.cast(attrs,C.c_void_p)
            command=box.command() if command is None else command
            line=C.create_unicode_buffer(subprocess.list2cmdline([str(part) for part in command]))
            env=C.create_unicode_buffer('\0'.join(f'{key}={value}' for key,value in sorted(environment.items(),key=lambda item:item[0].upper()))+'\0\0')
            self.api.require(self.api.CreateProcessW(str(command[0]),line,None,None,True,0x80000|0x400|0x4|0x08000000,
                env,str(box.work),C.byref(startup),C.byref(self.process)),'CreateProcess-AppContainer')
            win32job.AssignProcessToJobObject(self.job,int(self.process.hProcess))
            if self.api.token(self.process.hProcess)!=(True,0,box.sid_text):raise RuntimeError('运行身份校验失败')
            self.stop_channels=on_verified(self.job)
            if self.api.ResumeThread(self.process.hThread)==0xffffffff:self.api.require(False,'ResumeThread')
        except BaseException:
            self.close();raise
        finally:
            for handle in child_handles:handle.Close()
            self.api.DeleteProcThreadAttributeList(attrs)

    def poll(self):
        if self.api.WaitForSingleObject(self.process.hProcess,0)==258:return None
        code=W.DWORD();self.api.require(self.api.GetExitCodeProcess(self.process.hProcess,C.byref(code)),'ExitCode')
        return code.value

    def terminate(self):
        if self.process.hProcess:self.api.TerminateProcess(self.process.hProcess,78)

    def close(self):
        import win32job
        try:
            if self.job:
                win32job.TerminateJobObject(self.job,78)
                until=time.monotonic()+2
                while win32job.QueryInformationJobObject(self.job,win32job.JobObjectBasicAccountingInformation)['ActiveProcesses']:
                    if time.monotonic()>until:raise RuntimeError('运行进程清理超时')
                    time.sleep(.01)
        finally:
            try:
                if self.stop_channels:self.stop_channels();self.stop_channels=None
            finally:
                for stream in (self.stdin,self.stdout,self.stderr):
                    if stream:stream.close()
                if self.process.hProcess:self.api.CloseHandle(self.process.hProcess);self.process.hProcess=None
                if self.process.hThread:self.api.CloseHandle(self.process.hThread);self.process.hThread=None
                if self.job:self.job.Close();self.job=None


def verify_database_isolation(box,database,stop):
    """Try opening (never reading) the host-selected DB in a fixed sandbox child."""
    script=("const fs=require('node:fs');try{const fd=fs.openSync(process.argv[1],'r');"
            "fs.closeSync(fd);console.log('allowed')}catch(e){"
            "if(!['EACCES','EPERM'].includes(e.code))process.exit(3);console.log('denied')}")
    command=[box.node,'--preserve-symlinks','--preserve-symlinks-main','-e',script,str(Path(database).resolve())]
    process=ContainerProcess(box,runtime_environment(box.root),lambda job:lambda:None,command=command)
    try:
        deadline=time.monotonic()+5
        while process.poll() is None:
            if stop.is_set():raise AnalysisCancelled()
            if time.monotonic()>=deadline:raise ValueError('数据库权限检查超时')
            time.sleep(.01)
        if process.poll()!=0 or process.stdout.read(64).strip()!=b'denied':
            raise ValueError('数据库隔离检查未通过，分析已停止；仍可手动添加待办')
    finally:
        process.close()
