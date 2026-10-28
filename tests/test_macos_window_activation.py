"""Exercise window activation with mocks only; never activate an actual app."""
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

import ok.ui.qt.MainWindow as window_module


def make_window(minimized=False, has_handle=True):
    handle = Mock() if has_handle else None
    return SimpleNamespace(isMinimized=Mock(return_value=minimized), showNormal=Mock(),
        show=Mock(), raise_=Mock(), activateWindow=Mock(),
        windowHandle=Mock(return_value=handle), winId=Mock(return_value=73))


@pytest.mark.parametrize('minimized', [False, True])
def test_mac_window_activates_only_the_current_tool_process(minimized):
    widget = make_window(minimized)
    application = Mock()
    application.activateWithOptions_.return_value = True
    registry = Mock()
    registry.currentApplication.return_value = application
    kit = SimpleNamespace(NSRunningApplication=registry,
        NSApplicationActivateIgnoringOtherApps=2)

    with patch.object(window_module.sys, 'platform', 'darwin'), \
         patch.dict(sys.modules, {'AppKit': kit}):
        window_module.MainWindow.bring_to_front(widget)

    if minimized:
        widget.showNormal.assert_called_once_with()
        widget.show.assert_not_called()
    else:
        widget.show.assert_called_once_with()
        widget.showNormal.assert_not_called()
    widget.raise_.assert_called_once_with()
    widget.activateWindow.assert_called_once_with()
    widget.windowHandle().requestActivate.assert_called_once_with()
    registry.currentApplication.assert_called_once_with()
    registry.runningApplicationsWithBundleIdentifier_.assert_not_called()
    registry.runningApplicationWithProcessIdentifier_.assert_not_called()
    application.activateWithOptions_.assert_called_once_with(2)


@pytest.mark.parametrize('failure', ['exception', 'declined'])
def test_mac_native_activation_failure_keeps_the_qt_path(failure):
    widget = make_window(has_handle=False)
    application = Mock()
    if failure == 'exception':
        application.activateWithOptions_.side_effect = RuntimeError('activation unavailable')
    else:
        application.activateWithOptions_.return_value = False
    kit = SimpleNamespace(NSRunningApplication=SimpleNamespace(
        currentApplication=lambda: application), NSApplicationActivateIgnoringOtherApps=2)

    with patch.object(window_module.sys, 'platform', 'darwin'), \
         patch.dict(sys.modules, {'AppKit': kit}), \
         patch.object(window_module.logger, 'debug') as log:
        window_module.MainWindow.bring_to_front(widget)

    widget.show.assert_called_once_with()
    widget.raise_.assert_called_once_with()
    widget.activateWindow.assert_called_once_with()
    log.assert_called_once()
    assert 'activation' in log.call_args.args[0]


def test_windows_native_activation_is_preserved():
    widget = make_window()
    win32gui = Mock()
    with patch.object(window_module.sys, 'platform', 'win32'), \
         patch.object(window_module.os, 'name', 'nt'), \
         patch.dict(sys.modules, {'win32gui': win32gui,
                                  'win32con': SimpleNamespace(SW_RESTORE=9)}):
        window_module.MainWindow.bring_to_front(widget)

    win32gui.ShowWindow.assert_called_once_with(73, 9)
    win32gui.BringWindowToTop.assert_called_once_with(73)
    win32gui.SetForegroundWindow.assert_called_once_with(73)
