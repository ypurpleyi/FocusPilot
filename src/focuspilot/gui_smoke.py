"""Visible Windows Qt interaction check using only fictional tasks.

Opt-in model use: real analysis, count/duplicate/field/save confirmations, then
cancel a subsequent run. QTest drives this process's own widgets only.
"""

import argparse
import copy
from datetime import datetime,timedelta
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--approve-real-model',action='store_true')
    args=parser.parse_args()
    if not args.approve_real_model:parser.error('Requires --approve-real-model; no application or model started')
    os.environ.pop('QT_QPA_PLATFORM',None)
    from PySide6.QtCore import QTimer,Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication,QMessageBox,QPushButton,QDialogButtonBox
    from .analysis_ui import ReviewTaskDialog
    from .gui import FocusWindow,configure_app
    from .storage import TaskStore
    project=Path(__file__).resolve().parents[2];scratch=project/'.tmp';scratch.mkdir(exist_ok=True)
    app=QApplication([sys.argv[0]]);configure_app(app);app.setQuitOnLastWindowClosed(False)
    report={'production_data_used':False,'qt_driver':'QTest-visible-windows','passed':False}
    with TemporaryDirectory(prefix='gui-smoke-',dir=scratch) as folder:
        store=TaskStore(Path(folder)/'fixture.sqlite3');original=store.create('提交周报')
        window=FocusWindow(store);window.setWindowTitle('FocusPilot · 验证用虚构数据');window.show()
        window.show_analysis();dialog=window.analysis_dialog
        notification='明天17点提交周报，高优先级，预计30分钟。整理项目资料，无截止日期，预计45分钟。'
        dialog.input.setPlainText(notification)
        phase='analyzing';handled=set();started=time.monotonic();last_status=''
        def fail(message):
            nonlocal phase
            if phase=='failed':return
            phase='failed';report['error']=message;dialog.cancel()
            modal=app.activeModalWidget()
            if modal:modal.reject()
            window.exit_app()
        def click(button):QTest.mouseClick(button,Qt.MouseButton.LeftButton)
        def drive():
            nonlocal phase,last_status
            if phase=='failed':return
            try:
                status=dialog.status.text()
                if status!=last_status:print(status,flush=True);last_status=status
                if time.monotonic()-started>300:fail('Visible GUI acceptance timed out');return
                modal=app.activeModalWidget()
                if modal is not None and id(modal) not in handled:
                    if isinstance(modal,QMessageBox):
                        title=modal.windowTitle()
                        if title=='确认识别结果':
                            assert '识别到2项任务' in modal.text()
                            wanted='清单正确，继续检查';report['count_confirmed']=True
                        elif title=='发现重复候选':wanted='仍然新增';report['duplicate_keep_confirmed']=True
                        elif title=='保存待办':
                            assert len(store.list_tasks())==1
                            wanted='保存全部待办';report['no_writes_before_confirmation']=True
                        else:raise ValueError('Unexpected modal: '+title)
                        handled.add(id(modal));click(next(button for button in modal.buttons() if button.text()==wanted));return
                    if isinstance(modal,ReviewTaskDialog):
                        assert modal.title_edit.text().strip()
                        if modal.title_edit.text()=='提交周报':
                            assert modal.has_due.isChecked()
                        if modal.has_due.isChecked():
                            assert modal.due_edit.dateTime().date().toPython()==(datetime.now()+timedelta(days=1)).date()
                            assert modal.due_edit.dateTime().time().hour()==17
                        else:click(modal.no_due)
                        report['reviewed_fields']=report.get('reviewed_fields',0)+1
                        handled.add(id(modal));buttons=modal.findChild(QDialogButtonBox)
                        click(buttons.button(QDialogButtonBox.StandardButton.Ok));return
                if phase=='analyzing' and status.startswith('已新增'):
                    assert len(store.list_tasks())==3
                    assert TaskStore(store.path).get(original.id).status=='pending'
                    report['saved_and_reopened_count']=len(TaskStore(store.path).list_tasks())
                    window.refresh();window.grab().save(str(scratch/'focuspilot-product.png'))
                    report['model_evidence']=copy.deepcopy(dialog.engine.last_report)
                    phase='canceling';dialog.input.setPlainText('请分析：明天18点整理另一份资料。')
                    # The second run reuses the prepared runtime; cancel while live.
                    QTimer.singleShot(0,lambda:click(dialog.start_button));return
                if phase=='canceling' and dialog.worker is not None and '正在识别' in status:
                    click(dialog.cancel_button);phase='wait-cancel';return
                if phase=='wait-cancel' and dialog.worker is None:
                    assert '已取消' in dialog.status.text()
                    assert dialog.input.toPlainText()=='请分析：明天18点整理另一份资料。'
                    assert len(store.list_tasks())==3
                    report['live_cancel_preserved_input_and_database']=True
                    report['passed']=True;phase='done';window.exit_app()
            except Exception as error:fail(str(error))
        timer=QTimer();timer.setInterval(100);timer.timeout.connect(drive);timer.start()
        QTimer.singleShot(0,lambda:click(dialog.start_button))
        app.exec();timer.stop()
        report['runtime_cleaned']=dialog.engine.box is None
        window.tray.hide()
    (project/'docs/T08_GUI_VALIDATION.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
