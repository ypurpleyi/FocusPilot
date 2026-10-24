"""Local fake-Pi desktop exercise; no real Pi, model, task creation, or networking."""

from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from PySide6.QtWidgets import (QApplication, QComboBox, QHBoxLayout, QLabel,
                              QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from .gui import configure_app
from .pi_runtime import RpcController, offline_command
from .storage import TaskStore


class OfflineWindow(QWidget):
    def __init__(self, fixture_database):
        super().__init__()
        self.database = Path(fixture_database)
        self.setWindowTitle('FocusPilot · T07 离线验收')
        self.resize(600, 520)
        layout = QVBoxLayout(self)
        notice = QLabel('离线练习：不解析输入文本；只查询预置的两项虚构待办。'
                        '不调用模型、不访问正式任务、不创建待办。')
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText('输入任意练习文本；假 Pi 只演示运行控制，不理解或拆分文本。')
        layout.addWidget(self.input)
        row = QHBoxLayout()
        self.scenario = QComboBox()
        for title, name in [('正常只读查询', 'normal'), ('等待，练习取消', 'wait'),
                            ('模拟失败', 'error'), ('忽略停止，练习超时', 'ignore-stop')]:
            self.scenario.addItem(title, name)
        row.addWidget(self.scenario)
        self.analyze = QPushButton('运行离线分析')
        self.cancel_button = QPushButton('取消')
        row.addWidget(self.analyze)
        row.addWidget(self.cancel_button)
        layout.addLayout(row)
        self.status = QLabel('待输入')
        layout.addWidget(self.status)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        layout.addWidget(self.output)
        self.controller = RpcController(offline_command(database_path=self.database), parent=self)
        self.controller.changed.connect(self.refresh)
        self.controller.completed.connect(self.show_result)
        self.analyze.clicked.connect(self.start_analysis)
        self.cancel_button.clicked.connect(self.cancel_analysis)
        self.refresh()

    def start_analysis(self):
        if not self.input.toPlainText().strip():
            self.status.setText('请先输入练习文本')
            return
        self.controller.command = offline_command(self.scenario.currentData(), self.database)
        try:
            if self.controller.start(self.input.toPlainText()):
                self.output.clear()
        except ValueError as error:
            self.status.setText(str(error))

    def cancel_analysis(self):
        self.controller.cancel()
        self.output.clear()
        self.input.setFocus()

    def refresh(self):
        run = self.controller.run
        labels = {'submitting': '正在连接与提交', 'running': '正在运行离线情景',
                  'succeeded': '离线运行完成；没有创建任何任务',
                  'failed': '分析失败，输入已保留', 'cancelled': '本次分析已取消，输入已保留',
                  'timed_out': '分析超时，输入已保留'}
        text = labels.get(run.state, '待输入') if run else '待输入'
        if run and run.error:
            text += '：' + run.error
        if run and run.terminal and self.controller.busy:
            text += '（后台清理中）'
        self.status.setText(text)
        self.analyze.setEnabled(not self.controller.busy)
        self.scenario.setEnabled(not self.controller.busy)
        self.cancel_button.setEnabled(bool(run and not run.terminal))

    def show_result(self, identity, result):
        if self.controller.run and identity == self.controller.run.run_id:
            self.output.setPlainText(result)

    def closeEvent(self, event):
        self.controller.shutdown()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    configure_app(app)
    with TemporaryDirectory(prefix='focuspilot-offline-') as directory:
        path = Path(directory) / 'fixture.sqlite3'
        store = TaskStore(path)
        store.create('提交周报')
        store.create('预约牙科检查')
        window = OfflineWindow(path)
        window.show()
        return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
