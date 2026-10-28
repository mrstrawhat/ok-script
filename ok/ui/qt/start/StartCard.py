import sys
if sys.platform == 'win32':
    from ctypes import windll, wintypes

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QShortcut, QKeySequence
from _ctypes import byref
from qfluentwidgets import FluentIcon, PrimaryPushButton, SettingCard, PushButton

from ok import Handler
from ok import Logger
from ok import og
from ok.ui.qt.Communicate import communicate
from ok.ui.qt.widget.StatusBar import StatusBar

logger = Logger.get_logger(__name__)


class StartCard(SettingCard):
    show_choose_hwnd = Signal()
    hotkey_changed = Signal()
    hotkey_pressed = Signal()

    def __init__(self, exit_event):
        super().__init__(og.config.get('gui_icon'), og.app.title, og.app.version)
        self.basic_options = og.executor.basic_options

        self.iconLabel.setFixedSize(30, 30)
        self.hBoxLayout.setAlignment(Qt.AlignVCenter)
        self.status_bar = StatusBar("test")
        self.status_bar.clicked.connect(self.status_clicked)
        self.hBoxLayout.addWidget(self.status_bar, 0, Qt.AlignLeft)
        self.hBoxLayout.addSpacing(6)

        self.capture_button = PushButton(FluentIcon.ZOOM, self.tr("Capture"), self)
        self.hBoxLayout.addWidget(self.capture_button, 0, Qt.AlignRight)
        self.hBoxLayout.addSpacing(6)

        self.refresh_button = PushButton(FluentIcon.SYNC, self.tr("Refresh"), self)
        self.hBoxLayout.addWidget(self.refresh_button, 0, Qt.AlignRight)
        self.hBoxLayout.addSpacing(6)

        self.start_button = PrimaryPushButton(FluentIcon.PLAY, self.tr("Start"), self)
        self.hBoxLayout.addWidget(self.start_button, 0, Qt.AlignRight)
        self.hBoxLayout.addSpacing(20)

        self.hotkey_changed.connect(self.update_status)
        self.update_status()
        self.start_button.clicked.connect(self.clicked)
        communicate.executor_paused.connect(self.update_status)
        communicate.window.connect(self.update_status)
        communicate.task.connect(self.update_task)
        communicate.executor_exclusive.connect(self.update_exclusive)
        self.update_exclusive(getattr(og.executor, 'exclusive_owner', None))

        self.handler = Handler(exit_event, "StartCard")
        self.current_hotkey = "UNINIT"
        self.qt_shortcut = None
        self.hotkey_listener = None
        self.hotkey_pressed.connect(self.clicked)
        from PySide6.QtWidgets import QApplication
        QApplication.instance().aboutToQuit.connect(self.stop_native_hotkey)
        if sys.platform == 'win32':
            self.handler.post(self.check_hotkey, 0.1)
        else:
            self.hotkey_timer = QTimer(self)
            self.hotkey_timer.timeout.connect(self.check_hotkey)
            self.hotkey_timer.start(100)
            self.start_button.setToolTip(self.tr('Shortcut works while this application is active.'))
        logger.debug('basic_options.start/stop: {}'.format(self.basic_options.get('Start/Stop')))

    def status_clicked(self):
        if not og.executor.paused:
            if og.executor.current_task:
                communicate.tab.emit("onetime")
            elif og.executor.active_trigger_task_count():
                communicate.tab.emit("trigger")
            else:
                communicate.tab.emit("start")
            self.status_bar.show()

    @staticmethod
    def clicked():
        if not og.executor.paused:
            og.executor.pause()
        else:
            og.app.start_controller.start()

    def update_task(self, task):
        self.update_status()

    def update_exclusive(self, owner):
        # Starting is refused while another owner holds the executor.
        self.start_button.setEnabled(owner is None)

    def update_status(self):
        hotkey = self.basic_options.get('Start/Stop')
        suffix = f'({hotkey})' if hotkey and hotkey != 'None' else ''

        if og.executor.paused:
            device = og.device_manager.get_preferred_device()
            if device and not device['connected'] and device.get('full_path'):
                self.start_button.setText(self.tr("Start Game") + suffix)
            else:
                self.start_button.setText(self.tr("Start") + suffix)
            self.start_button.setIcon(FluentIcon.PLAY)
            self.status_bar.hide()
        else:
            self.start_button.setText(self.tr("Pause") + suffix)
            self.start_button.setIcon(FluentIcon.PAUSE)
            if not og.executor.connected():
                self.status_bar.setTitle(self.tr("Game Window Disconnected"))
                self.status_bar.setState(True)
            elif active_trigger_task_count := og.executor.active_trigger_task_count():
                if not og.executor.can_capture():
                    self.status_bar.setTitle(self.tr('Paused: PC Game Window Must Be in Front!'))
                    self.status_bar.setState(True)
                else:
                    self.status_bar.setTitle(
                        self.tr("Running") + ": " + str(active_trigger_task_count) + ' ' + self.tr("Trigger Tasks"))
                    self.status_bar.setState(False)
            elif task := og.executor.current_task:
                if not og.executor.can_capture():
                    self.status_bar.setTitle(self.tr('Paused: PC Game Window Must Be in Front!'))
                    self.status_bar.setState(True)
                elif task.enabled:
                    self.status_bar.setTitle(self.tr("Running") + ": " + task.name)
                    self.status_bar.setState(False)
                else:
                    self.status_bar.setTitle(self.tr("Waiting for task to be enabled"))
                    self.status_bar.setState(False)
            else:
                self.status_bar.setTitle(self.tr("Waiting for task to be enabled"))
                self.status_bar.setState(False)
            self.status_bar.show()

    def check_hotkey(self):
        new_hotkey = self.basic_options.get('Start/Stop')
        if new_hotkey != self.current_hotkey:
            self.rebind_hotkey(new_hotkey)
            self.current_hotkey = new_hotkey
            self.hotkey_changed.emit()
        elif sys.platform == 'darwin' and self.hotkey_listener is not None and not self.hotkey_listener.is_alive():
            self.stop_native_hotkey()
            if self.qt_shortcut is not None:
                self.qt_shortcut.setEnabled(True)
            self.start_button.setToolTip(self.tr('Shortcut works while this application is active.'))
            logger.warning('Global shortcut listener stopped; using application shortcut')

        if sys.platform == 'win32':
            msg = wintypes.MSG()
            if windll.user32.PeekMessageW(byref(msg), None, 0, 0, 1):
                if msg.message == 0x0312:  # WM_HOTKEY
                    logger.debug(f'hotkey pressed {msg}')
                    if msg.wParam == 999:
                        self.clicked()

            self.handler.post(self.check_hotkey, 0.1)

    def rebind_hotkey(self, hotkey):
        if sys.platform != 'win32':
            self.stop_native_hotkey()
            self.start_button.setToolTip(self.tr('Shortcut works while this application is active.'))
            if self.qt_shortcut is not None:
                self.qt_shortcut.setEnabled(False)
                self.qt_shortcut.deleteLater()
                self.qt_shortcut = None
            if hotkey and hotkey != 'None':
                self.qt_shortcut = QShortcut(QKeySequence(hotkey), self)
                self.qt_shortcut.setContext(Qt.ApplicationShortcut)
                self.qt_shortcut.activated.connect(self.clicked)
            if sys.platform == 'darwin' and hotkey and hotkey.lower() in ('f9', 'f10', 'f11', 'f12'):
                from ok.util.macos import permission_status
                if permission_status()['listen_events']:
                    try:
                        from ok.util.macos import FunctionHotkey
                        self.hotkey_listener = FunctionHotkey(hotkey, self.hotkey_pressed.emit)
                        self.hotkey_listener.start()
                        # The native tap also sees this app's keys.
                        self.qt_shortcut.setEnabled(False)
                        self.start_button.setToolTip(self.tr('Global shortcut; losing game focus also releases held input.'))
                    except Exception as error:
                        logger.error('Could not register macOS global shortcut', error)
                        self.stop_native_hotkey()
            return
        windll.user32.UnregisterHotKey(None, 999)
        vk_map = {'F9': 0x78, 'F10': 0x79, 'F11': 0x7A, 'F12': 0x7B}

        if hotkey and hotkey != 'None' and hotkey in vk_map:
            if not windll.user32.RegisterHotKey(None, 999, 0, vk_map[hotkey]):
                logger.error(f"Failed to register hotkey {hotkey}")
        else:
            logger.debug(f"Hotkey disabled or invalid: {hotkey}")

    def stop_native_hotkey(self):
        if self.hotkey_listener is not None:
            listener = self.hotkey_listener
            self.hotkey_listener = None
            try:
                listener.stop()
            except Exception as error:
                logger.error('Global shortcut listener cleanup failed', error)
