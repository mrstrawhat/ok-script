from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout
from qfluentwidgets import BodyLabel, PushButton

from ok.util.macos import permission_status, open_permission_settings


class MacPermissionsCard(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.status = BodyLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        for name, kind in [('录屏设置', 'screen_recording'), ('辅助功能设置', 'accessibility'),
                           ('输入监控设置', 'listen_events')]:
            button = PushButton(name)
            button.clicked.connect(lambda checked=False, kind=kind: open_permission_settings(kind))
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(2000)
        self.refresh()

    def refresh(self):
        status = permission_status()
        enabled = lambda value: '已允许' if value else '未允许'
        self.status.setText(
            f"录屏：{enabled(status['screen_recording'])}　"
            f"辅助功能：{enabled(status['accessibility'] and status['post_events'])}　"
            f"全局快捷键：{enabled(status['listen_events'])}\n"
            '前台全屏运行；失焦时释放按住的键鼠。授权变更后请重开工具。')
