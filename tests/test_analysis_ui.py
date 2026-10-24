import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')

import pytest
from PySide6.QtWidgets import QApplication,QDialog

from focuspilot.analysis_ui import AnalysisDialog,ReviewTaskDialog
from focuspilot.storage import TaskStore


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


def draft():
    return {'title':'待检查任务','due_at':None,'importance':2,'estimated_minutes':30,
        'field_sources':{'title':'explicit','due_at':'missing','importance':'suggested','estimated_minutes':'suggested'},
        'source_text':'原通知内容','due_note':'下午需要补充具体时间'}


def test_review_requires_explicit_no_deadline_choice(app):
    dialog=ReviewTaskDialog(draft(),0,1)
    assert dialog.title_edit.text()=='待检查任务'
    assert not dialog.has_due.isChecked()
    dialog.accept()
    assert dialog.result()!=QDialog.DialogCode.Accepted
    dialog.no_due.setChecked(True);dialog.accept()
    assert dialog.result()==QDialog.DialogCode.Accepted
    assert dialog.fields()['due_at'] is None
    dialog.deleteLater()


class Engine:
    def close(self):pass


def test_cancel_preserves_input_and_late_result_cannot_open_confirmation(app,tmp_path,monkeypatch):
    store=TaskStore(tmp_path/'fixture.sqlite3')
    dialog=AnalysisDialog(store,engine=Engine())
    dialog.input.setPlainText('用户原始通知')
    dialog.token='old-run';dialog.cancel()
    def forbidden(*args,**kwargs):
        pytest.fail('Late cancelled result opened a confirmation')
    monkeypatch.setattr('focuspilot.analysis_ui.QMessageBox',forbidden)
    dialog.receive('old-run',{'tasks':[draft()],'duplicates':[]})
    assert dialog.input.toPlainText()=='用户原始通知'
    assert '已取消' in dialog.status.text()
    assert store.list_tasks()==[]
    dialog.deleteLater()
