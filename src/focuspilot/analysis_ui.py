"""Human-in-the-loop task capture. The agent never receives a save capability."""
import threading
import uuid

from PySide6.QtCore import QThread, Signal, Qt, QDateTime
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QTextEdit,QPushButton,
                              QMessageBox,QCheckBox)

from .drafts import DraftBatch
from .gui import AddTaskDialog
from .rpc import ReadOnlyTaskTools


class AnalysisWorker(QThread):
    result=Signal(str,dict)
    failed=Signal(str,str)
    progress=Signal(str,str)
    def __init__(self,engine,text,database,token):
        super().__init__();self.engine=engine;self.text=text;self.database=database;self.token=token
        self.stop=threading.Event()
    def run(self):
        try:
            result=self.engine.analyze(self.text,self.database,self.stop,
                lambda message:self.progress.emit(self.token,message))
            if not self.stop.is_set():self.result.emit(self.token,result)
        except Exception as error:
            if not self.stop.is_set():
                message=str(error) if isinstance(error,ValueError) else '分析暂时不可用，请重试或手动添加待办'
                self.failed.emit(self.token,message)


class ReviewTaskDialog(AddTaskDialog):
    def __init__(self,draft,index,total,parent=None):
        super().__init__(parent)
        self.setWindowTitle(f'检查待办 · {index+1}/{total}')
        self.title_edit.setText(draft['title'])
        self.has_due.setChecked(draft['due_at'] is not None)
        if draft['due_at']:
            self.due_edit.setDateTime(QDateTime.fromString(draft['due_at'],Qt.DateFormat.ISODate))
        self.importance.setCurrentIndex(self.importance.findData(draft['importance']))
        self.estimate.setValue(draft['estimated_minutes'])
        names={'title':'任务内容','due_at':'截止时间','importance':'重要程度','estimated_minutes':'预计耗时'}
        origins={'explicit':'来自原文','suggested':'建议值，请检查','missing':'需要补充或明确不设置'}
        note=QLabel('\n'.join(f'{names[key]}：{origins[value]}' for key,value in draft['field_sources'].items()))
        note.setWordWrap(True);note.setTextFormat(Qt.TextFormat.PlainText)
        self.layout().insertWidget(0,note)
        quote=QLabel('原文：'+draft['source_text']+('\n提示：'+draft['due_note'] if draft['due_note'] else ''))
        quote.setWordWrap(True);quote.setTextFormat(Qt.TextFormat.PlainText)
        self.layout().insertWidget(1,quote)
        self.no_due=QCheckBox('我确认此项不设置截止时间')
        self.no_due.setVisible(not self.has_due.isChecked())
        self.has_due.toggled.connect(lambda enabled:self.no_due.setVisible(not enabled))
        self.layout().insertWidget(self.layout().count()-2,self.no_due)
    def accept(self):
        if not self.has_due.isChecked() and not self.no_due.isChecked():
            self.error.setText('请设置截止时间，或确认不设置截止时间');return
        super().accept()
    def fields(self):
        return {'title':self.title_edit.text(),'due_at':self.due_edit.dateTime().toString('yyyy-MM-ddTHH:mm:ss')
                if self.has_due.isChecked() else None,'importance':int(self.importance.currentData()),
                'estimated_minutes':self.estimate.value()}


class AnalysisDialog(QDialog):
    saved=Signal(int)
    def __init__(self,store,parent=None,*,engine=None):
        super().__init__(parent)
        if engine is None:
            from .analysis_engine import AnalysisEngine
            engine=AnalysisEngine()
        self.store=store;self.engine=engine;self.worker=None;self.token=None;self.batch=None;self.acknowledged=set()
        self.setWindowTitle('分析待办');self.resize(540,430)
        layout=QVBoxLayout(self)
        description=QLabel('粘贴任务通知或输入文本，一次最多5项。识别后先确认清单，再检查每项字段。')
        description.setWordWrap(True);layout.addWidget(description)
        self.input=QTextEdit();self.input.setPlaceholderText('例如：明天下午交项目方案，预计一小时；周五15点参加评审。')
        layout.addWidget(self.input)
        privacy=QLabel('点击分析将使用已配置的DeepSeek Flash，发送本段文本及必要的只读查重结果。检查并确认保存前，不会新增待办。')
        privacy.setWordWrap(True);layout.addWidget(privacy)
        self.status=QLabel('准备就绪');self.status.setWordWrap(True);self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        row=QHBoxLayout();self.start_button=QPushButton('分析待办');self.cancel_button=QPushButton('取消本次分析')
        self.retry_button=QPushButton('重试保存草稿');self.retry_button.hide()
        self.start_button.clicked.connect(self.start);self.cancel_button.clicked.connect(self.cancel)
        self.retry_button.clicked.connect(self.save_batch)
        row.addWidget(self.start_button);row.addWidget(self.cancel_button);row.addWidget(self.retry_button);layout.addLayout(row)
        self.cancel_button.setEnabled(False)

    def start(self):
        if self.worker is not None:return
        text=self.input.toPlainText()
        if not text.strip() or len(text)>8000:self.status.setText('请输入1–8000字的任务通知');return
        self.token=uuid.uuid4().hex;self.batch=None;self.acknowledged.clear();self.retry_button.hide()
        self.input.setReadOnly(True);self.start_button.setEnabled(False);self.cancel_button.setEnabled(True)
        self.status.setText('正在准备分析…')
        worker=AnalysisWorker(self.engine,text,self.store.path,self.token);self.worker=worker
        worker.result.connect(self.receive);worker.failed.connect(self.failure);worker.progress.connect(self.progress)
        worker.finished.connect(self.worker_finished);worker.start()

    def progress(self,token,message):
        if token!=self.token:return
        if message.startswith('首次准备Pi运行环境'):message='正在准备分析功能，首次使用可能需要几分钟；本地提醒仍正常运行。'
        self.status.setText(message)

    def worker_finished(self):
        worker=self.worker;self.worker=None
        if worker:worker.deleteLater()
        self.input.setReadOnly(False);self.start_button.setEnabled(True)
        if self.batch is None:self.cancel_button.setEnabled(False)

    def failure(self,token,message):
        if token==self.token:self.status.setText(message);self.batch=None

    def cancel(self):
        self.token=None
        if self.worker:self.worker.stop.set()
        if self.batch:self.batch.cancel();self.batch=None
        self.input.setReadOnly(False);self.status.setText('本次分析已取消，原始输入已保留。')
        self.cancel_button.setEnabled(False);self.retry_button.hide()

    def choose_duplicate(self,title,candidates):
        box=QMessageBox(self);box.setWindowTitle('发现重复候选');box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText('新增草稿：'+title+'\n\n已有待办：\n'+'\n'.join(
            item['title']+' · '+(item['due_at'] or '无截止时间') for item in candidates)+
            '\n\n丢弃仅影响这项新增草稿，已有待办不会改变。')
        discard=box.addButton('丢弃本次新增',QMessageBox.ButtonRole.RejectRole)
        keep=box.addButton('仍然新增',QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(discard);box.exec()
        return box.clickedButton()==keep

    def receive(self,token,result):
        if token!=self.token:return
        self.batch=DraftBatch(result['tasks'])
        titles='\n'.join(f'{index+1}. {task["title"]}' for index,task in enumerate(self.batch.tasks))
        box=QMessageBox(self);box.setWindowTitle('确认识别结果');box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(f'识别到{len(self.batch.tasks)}项任务，请确认数量和内容是否正确：\n\n'+titles)
        yes=box.addButton('清单正确，继续检查',QMessageBox.ButtonRole.AcceptRole)
        box.addButton('返回修改原文',QMessageBox.ButtonRole.RejectRole);box.exec()
        if token!=self.token or self.batch is None:return
        if box.clickedButton()!=yes:self.cancel();return
        self.batch.confirm_count()
        for index,draft in enumerate(self.batch.tasks):
            candidates=[item for item in result['duplicates'] if item['title'].strip()==draft['title'].strip()]
            if candidates:
                if not self.choose_duplicate(draft['title'],candidates):self.batch.discard(index);continue
                self.acknowledged.add((draft['title'],tuple(sorted(item['id'] for item in candidates))))
            dialog=ReviewTaskDialog(draft,index,len(self.batch.tasks),self)
            accepted=dialog.exec()==QDialog.DialogCode.Accepted
            if token!=self.token or self.batch is None:dialog.deleteLater();return
            if not accepted:dialog.deleteLater();self.cancel();return
            self.batch.review(index,dialog.fields(),no_due_confirmed=dialog.no_due.isChecked())
            dialog.deleteLater()
        self.save_batch()

    def save_batch(self):
        if not self.batch or self.batch.state!='draft':return
        try:
            if not self.batch.reviewed:self.status.setText('本次没有保留的新增待办。');self.cancel_button.setEnabled(False);return
            titles=[item['title'] for item in self.batch.reviewed.values()]
            current=ReadOnlyTaskTools(self.store.path).call('find_duplicate_tasks',{'titles':titles})
            if current['truncated']:raise ValueError('重复候选过多，请分批确认')
            for index,item in list(self.batch.reviewed.items()):
                candidates=[task for task in current['tasks'] if task['title'].strip()==item['title'].strip()]
                acknowledgement=(item['title'],tuple(sorted(task['id'] for task in candidates)))
                if candidates and acknowledgement not in self.acknowledged:
                    if not self.choose_duplicate(item['title'],candidates):self.batch.discard(index)
                    else:self.acknowledged.add(acknowledgement)
            count=len(self.batch.reviewed)
            if not count:self.cancel();return
            box=QMessageBox(self);box.setWindowTitle('保存待办');box.setText(f'已检查{count}项待办，是否现在保存？')
            save=box.addButton('保存全部待办',QMessageBox.ButtonRole.AcceptRole)
            box.addButton('取消本次新增',QMessageBox.ButtonRole.RejectRole);box.exec()
            if self.batch is None or self.batch.state!='draft':return
            if box.clickedButton()!=save:self.cancel();return
            saved=self.batch.save(self.store)
        except Exception:
            self.status.setText('保存或查重失败，草稿已保留。可重试保存，或取消本次新增。');self.retry_button.show();return
        self.token=None;self.batch=None;self.retry_button.hide();self.cancel_button.setEnabled(False)
        self.status.setText(f'已新增{len(saved)}项待办。');self.input.clear();self.saved.emit(len(saved))

    def shutdown(self):
        self.cancel()
        if self.worker and self.worker.isRunning():return False
        self.engine.close();return True

    def closeEvent(self,event):
        self.cancel();event.accept()
