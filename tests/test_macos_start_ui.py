"""Isolated Qt controls; native tests are opt-in and target test processes only."""
import sys
import os
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import ok.ui.qt.start.StartCard as card_module
from ok.ui.qt.start.MacPermissionsCard import MacPermissionsCard
from ok.core.events import communicate
from ok.core.start_controller import StartController
from ok.util.config import Config
from ok.util.handler import ExitEvent


@pytest.fixture(scope='module')
def qt_app():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    from ok.ui.qt.events import install_qt_event_dispatcher
    from ok.core.events import set_event_dispatcher
    install_qt_event_dispatcher()
    yield app
    set_event_dispatcher(None)


@pytest.fixture
def card(qt_app, monkeypatch, tmp_path):
    options = Config('Basic Options', {'Start/Stop': 'None'}, folder=str(tmp_path))
    executor = SimpleNamespace(paused=True, basic_options=options, exclusive_owner=None,
        connected=lambda: True, can_capture=lambda: True, active_trigger_task_count=lambda: 0,
        current_task=None)
    def pause():
        executor.paused = True
        communicate.executor_paused.emit(True)
    def start():
        executor.paused = False
        communicate.executor_paused.emit(False)
    executor.pause = Mock(side_effect=pause)
    controller = SimpleNamespace(start=Mock(side_effect=start))
    context = SimpleNamespace(config={'gui_icon': ''}, executor=executor,
        app=SimpleNamespace(title='OK-WW 非游戏测试', version='test', start_controller=controller),
        device_manager=SimpleNamespace(get_preferred_device=lambda: {'connected': True}))
    monkeypatch.setattr(card_module, 'og', context)
    stop = ExitEvent()
    widget = card_module.StartCard(stop)
    widget.hotkey_timer.stop()
    widget.resize(800, 150)
    widget.show()
    widget.activateWindow()
    qt_app.processEvents()
    yield SimpleNamespace(widget=widget, options=options, executor=executor, controller=controller)
    widget.stop_native_hotkey()
    stop.set()
    widget.handler.thread.join(timeout=2)
    for event, callback in [(communicate.executor_paused, widget.update_status),
                            (communicate.window, widget.update_status),
                            (communicate.task, widget.update_task),
                            (communicate.executor_exclusive, widget.update_exclusive)]:
        event.disconnect(callback)
    widget.close()
    widget.deleteLater()
    qt_app.processEvents()


def test_start_pause_and_exclusive_controls(card):
    QTest.mouseClick(card.widget.start_button, Qt.LeftButton)
    card.controller.start.assert_called_once_with()
    assert not card.executor.paused
    QTest.mouseClick(card.widget.start_button, Qt.LeftButton)
    card.executor.pause.assert_called_once_with()
    assert card.executor.paused
    card.widget.update_exclusive('test-owner')
    assert not card.widget.start_button.isEnabled()
    card.widget.update_exclusive(None)
    assert card.widget.start_button.isEnabled()


@pytest.mark.parametrize('key', ['F9', 'F10', 'F11', 'F12'])
def test_local_shortcut_when_monitoring_permission_is_missing(card, qt_app, key):
    card.options['Start/Stop'] = key
    with patch('ok.util.macos.permission_status', return_value={'listen_events': False}):
        card.widget.check_hotkey()
    assert card.widget.qt_shortcut.isEnabled()
    assert card.widget.qt_shortcut.key().toString() == key
    QTest.qWait(30)
    QTest.keyClick(card.widget, getattr(Qt, 'Key_' + key))
    qt_app.processEvents()
    card.controller.start.assert_called_once_with()


@pytest.mark.parametrize('key', ['F9', 'F10', 'F11', 'F12'])
def test_global_registration_callback_and_local_fallback(card, qt_app, key):
    listener = Mock()
    factory = Mock(return_value=listener)
    with patch('ok.util.macos.permission_status', return_value={'listen_events': True}), \
         patch('ok.util.macos.FunctionHotkey', factory):
        card.widget.rebind_hotkey(key)
    listener.start.assert_called_once_with()
    assert not card.widget.qt_shortcut.isEnabled()
    assert factory.call_args.args[0] == key
    factory.call_args.args[1]()
    qt_app.processEvents()
    card.controller.start.assert_called_once_with()
    with patch('ok.util.macos.permission_status', return_value={'listen_events': False}):
        card.widget.rebind_hotkey(key)
    listener.stop.assert_called_once_with()
    assert card.widget.qt_shortcut.isEnabled()
    assert 'Global shortcut' not in card.widget.start_button.toolTip()


def test_listener_start_failure_keeps_local_shortcut(card):
    listener = Mock()
    listener.start.side_effect = RuntimeError('event tap refused')
    with patch('ok.util.macos.permission_status', return_value={'listen_events': True}), \
         patch('ok.util.macos.FunctionHotkey', return_value=listener):
        card.widget.rebind_hotkey('F9')
    assert card.widget.qt_shortcut.isEnabled()
    assert card.widget.hotkey_listener is None


def test_listener_dying_after_start_falls_back_to_local(card):
    listener = Mock()
    listener.is_alive.return_value = False
    card.options['Start/Stop'] = 'F9'
    with patch('ok.util.macos.permission_status', return_value={'listen_events': True}), \
         patch('ok.util.macos.FunctionHotkey', return_value=listener):
        card.widget.check_hotkey()
    assert not card.widget.qt_shortcut.isEnabled()
    card.widget.check_hotkey()
    assert card.widget.qt_shortcut.isEnabled()
    assert card.widget.hotkey_listener is None


def test_function_monitor_ignores_repeats_modifiers_and_other_keys():
    from ok.util.macos import FunctionHotkey
    q = SimpleNamespace(kCGSessionEventTap=1, kCGHeadInsertEventTap=0, kCGEventTapOptionListenOnly=1,
        kCGEventKeyDown=10, kCGEventTapDisabledByTimeout=-1, kCGEventTapDisabledByUserInput=-2,
        kCFRunLoopCommonModes='common', kCGEventFlagMaskCommand=2, kCGEventFlagMaskControl=4,
        kCGEventFlagMaskAlternate=8, kCGEventFlagMaskShift=16, kCGKeyboardEventKeycode='code',
        kCGKeyboardEventAutorepeat='repeat', CGEventTapCreate=Mock(return_value='tap'),
        CFMachPortCreateRunLoopSource=Mock(return_value='source'), CFRunLoopGetMain=lambda: 'main',
        CFRunLoopAddSource=Mock(), CFRunLoopRemoveSource=Mock(), CFMachPortInvalidate=Mock(),
        CGEventTapEnable=Mock(), CGEventTapIsEnabled=Mock(return_value=True),
        CGEventGetIntegerValueField=lambda event, field: event[field], CGEventGetFlags=lambda event:event['flags'])
    callback = Mock()
    listener = FunctionHotkey('F9', callback)
    with patch.dict(sys.modules, {'Quartz': q}):
        listener.start()
        listener.start()
        assert listener.is_alive()
        for code, repeat, flags in [(101, False, 0), (101, True, 0),
                                     (109, False, 0), (101, False, 2)]:
            event = {'code':code, 'repeat':repeat, 'flags':flags}
            assert listener._handle(None, 10, event, None) is event
        callback.assert_called_once_with()
        listener._handle(None, -1, None, None)
        assert q.CGEventTapEnable.call_count == 2
        listener.stop()
        listener.stop()
        assert not listener.is_alive()
    q.CFMachPortInvalidate.assert_called_once_with('tap')
    q.CGEventTapCreate.assert_called_once()


def test_hotkey_setting_survives_reload(card, tmp_path):
    card.options['Start/Stop'] = 'F12'
    reloaded = Config('Basic Options', {'Start/Stop': 'F9', 'New option': True}, folder=str(tmp_path))
    assert reloaded['Start/Stop'] == 'F12'
    assert reloaded['New option'] is True


def test_permission_card_refresh_and_settings_buttons(qt_app):
    state = {'screen_recording': False, 'accessibility': False,
             'post_events': False, 'listen_events': False}
    with patch('ok.ui.qt.start.MacPermissionsCard.permission_status', side_effect=lambda: state), \
         patch('ok.ui.qt.start.MacPermissionsCard.open_permission_settings') as settings:
        widget = MacPermissionsCard()
        widget.timer.stop()
        assert widget.status.text().count('未允许') == 3
        for key in state:
            state[key] = True
        widget.refresh()
        assert widget.status.text().count('已允许') == 3
        from qfluentwidgets import PushButton
        for button in widget.findChildren(PushButton):
            QTest.mouseClick(button, Qt.LeftButton)
        assert [call.args[0] for call in settings.call_args_list] == [
            'screen_recording', 'accessibility', 'listen_events']
        widget.deleteLater()
        qt_app.processEvents()


def test_missing_permissions_prevent_device_access_and_emit_error():
    controller = StartController.__new__(StartController)
    controller.config = {'macos': {'bundle_id': 'test.game'}}
    context = SimpleNamespace(executor=SimpleNamespace(exclusive_owner=None), device_manager=Mock())
    with patch('ok.core.start_controller.og', context), \
         patch('ok.util.macos.permission_error', return_value='missing screen permission'), \
         patch('ok.core.start_controller.communicate.starting_emulator.emit') as event:
        assert controller._do_start() is False
    event.assert_called_once_with(True, 'missing screen permission', 0)
    context.device_manager.do_refresh.assert_not_called()


@pytest.mark.skipif(os.environ.get('OKWW_NATIVE_TESTS') != '1', reason='opt-in native keyboard test')
def test_native_local_shortcuts_only_control_the_test_window(card, qt_app):
    """Post four keys to this test PID; never put keys in another app's queue."""
    import AppKit
    import Quartz
    from ok.util.macos import FunctionHotkey
    from ok.util.macos import permission_status

    game_bundle = 'com.kurogame.mingchao'
    games = lambda: AppKit.NSRunningApplication.runningApplicationsWithBundleIdentifier_(game_bundle)
    if games():
        pytest.skip('Game is running; no native keys will be sent')
    if not permission_status()['listen_events']:
        pytest.skip('Native input monitoring permission is unavailable')

    def wait_for(predicate, timeout=2):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(20)
        assert predicate()

    try:
        for index, key in enumerate(['F9', 'F10', 'F11', 'F12']):
            assert not games(), 'Game appeared; stop before sending keys'
            with patch('ok.util.macos.permission_status', return_value={'listen_events': False}):
                card.widget.rebind_hotkey(key)
            assert card.widget.qt_shortcut.isEnabled()
            code = FunctionHotkey.KEY_CODES[key.lower()]
            for down in (True, False):
                event = Quartz.CGEventCreateKeyboardEvent(None, code, down)
                Quartz.CGEventPostToPid(os.getpid(), event)
            expected_paused = index % 2 == 1
            wait_for(lambda: card.executor.paused == expected_paused)
        assert card.controller.start.call_count == 2
        assert card.executor.pause.call_count == 2
        assert not games()
    finally:
        card.widget.stop_native_hotkey()


@pytest.mark.skipif(os.environ.get('OKWW_NATIVE_TESTS') != '1', reason='opt-in native keyboard test')
def test_native_global_monitor_from_a_separate_test_process(card, qt_app, tmp_path):
    """Observe four session events while our disposable Qt receiver is foreground."""
    import AppKit
    import Quartz
    import subprocess
    from pathlib import Path
    from ok.util.macos import FunctionHotkey, permission_status

    games = lambda: AppKit.NSRunningApplication.runningApplicationsWithBundleIdentifier_('com.kurogame.mingchao')
    if games() or not all(permission_status()[p] for p in ('accessibility', 'listen_events', 'post_events')):
        pytest.skip('Game running or native input permission unavailable')
    ready = tmp_path / 'receiver.ready'
    received = tmp_path / 'receiver.keys'
    program = '''
import os,sys
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication,QWidget
class Receiver(QWidget):
    def keyPressEvent(self,event):
        with open(sys.argv[2], 'a') as stream: stream.write(str(event.key())+'\\n')
app=QApplication([])
window=Receiver(); window.setWindowTitle('OK-WW disposable key receiver'); window.show()
Path(sys.argv[1]).write_text(str(os.getpid()))
QTimer.singleShot(15000, app.quit)
app.exec()
'''
    previous = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    child = subprocess.Popen([sys.executable, '-c', program, str(ready), str(received)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    def wait_for(predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(20)
        assert predicate()
    try:
        wait_for(ready.exists)
        assert int(ready.read_text()) == child.pid
        target = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(child.pid)
        target.activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps)
        foreground = lambda: AppKit.NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier() == child.pid
        wait_for(foreground)
        for index, key in enumerate(['F9', 'F10', 'F11', 'F12']):
            assert not games()
            assert foreground(), 'Receiver lost focus; send no further keys'
            card.widget.rebind_hotkey(key)
            assert card.widget.hotkey_listener.is_alive()
            QTest.qWait(200)
            assert foreground(), 'Receiver lost focus; send no further keys'
            for down in (True, False):
                event = Quartz.CGEventCreateKeyboardEvent(None, FunctionHotkey.KEY_CODES[key.lower()], down)
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
            wait_for(lambda: card.executor.paused == (index % 2 == 1))
        assert card.controller.start.call_count == 2
        assert card.executor.pause.call_count == 2
        # macOS may consume F11/F12 for system actions after our listen-only tap.
        # The callback must still observe all four; do not alter system shortcuts.
        wait_for(lambda: received.exists())
    finally:
        card.widget.stop_native_hotkey()
        # Only this disposable child may be terminated, never another app.
        child.terminate()
        child.wait(timeout=5)
        if previous and previous.bundleIdentifier() != 'com.kurogame.mingchao':
            previous.activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps)
