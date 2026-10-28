"""Test the production quit path without enumerating or touching real apps."""
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

import psutil
import pytest

from ok.device.DeviceManager import DeviceManager
from ok.task.TaskExecutor import TaskExecutor
from ok.util.macos import quit_application


@pytest.fixture
def target():
    app = Mock()
    app.bundleIdentifier.return_value = 'test.game'
    app.terminate.return_value = True
    factory = Mock(return_value=app)
    process = Mock()
    process.is_running.side_effect = [True, False]
    process.status.return_value = psutil.STATUS_RUNNING
    kit = SimpleNamespace(NSRunningApplication=SimpleNamespace(
        runningApplicationWithProcessIdentifier_=factory))
    with patch.dict(sys.modules, {'AppKit': kit}), patch('ok.util.macos.psutil.Process', return_value=process):
        yield SimpleNamespace(app=app, factory=factory, process=process)


def test_requests_only_the_selected_application_to_quit(target):
    quit_application('test.game', 73)
    target.factory.assert_called_once_with(73)
    target.app.terminate.assert_called_once_with()
    target.app.activateWithOptions_.assert_not_called()
    target.app.forceTerminate.assert_not_called()


def test_already_exited_does_not_quit_a_replacement_application(target):
    target.factory.return_value = None
    quit_application('test.game', 73)
    target.app.terminate.assert_not_called()


def test_pid_reused_by_other_bundle_is_rejected(target):
    target.app.bundleIdentifier.return_value = 'other.app'
    with pytest.raises(RuntimeError, match='标识已变化'):
        quit_application('test.game', 73)
    target.app.terminate.assert_not_called()


@pytest.mark.parametrize('pid', [None, 0, -1, '73'])
def test_missing_process_identity_is_rejected(target, pid):
    with pytest.raises(ValueError):
        quit_application('test.game', pid)
    target.factory.assert_not_called()


def test_rejected_quit_never_forces_termination(target):
    target.app.terminate.return_value = False
    with pytest.raises(RuntimeError, match='拒绝'):
        quit_application('test.game', 73)
    target.app.forceTerminate.assert_not_called()


def test_timeout_never_forces_termination(target):
    target.process.is_running.side_effect = None
    target.process.is_running.return_value = True
    with pytest.raises(RuntimeError, match='超时'):
        quit_application('test.game', 73, timeout=0)
    target.app.forceTerminate.assert_not_called()


def test_process_disappearing_during_wait_is_success(target):
    target.process.is_running.side_effect = [True, psutil.NoSuchProcess(73)]
    quit_application('test.game', 73)


@pytest.mark.parametrize('timeout', [-1, float('nan'), float('inf'), '15'])
def test_invalid_timeout_does_not_request_quit(target, timeout):
    with pytest.raises(ValueError):
        quit_application('test.game', 73, timeout=timeout)
    target.app.terminate.assert_not_called()


def test_device_manager_releases_input_before_native_quit():
    events = []
    manager = SimpleNamespace(get_preferred_device=lambda: {
        'device': 'macos', 'address': 'test.game', 'pid': 73}, macos_capture_config={},
        interaction=SimpleNamespace(release_all=lambda: events.append('release')))
    with patch('ok.util.macos.quit_application', side_effect=lambda *a, **kw: events.append('quit')) as request:
        DeviceManager.stop_hwnd(manager)
    assert events == ['release', 'quit']
    request.assert_called_once_with('test.game', 73, timeout=15)


def test_completed_task_waits_for_game_exit_before_quitting_tool():
    events = []
    executor = SimpleNamespace(release_input=lambda: events.append('release'),
        device_manager=SimpleNamespace(get_preferred_device=lambda: {'device': 'macos'},
                                       stop_hwnd=lambda: events.append('game exited')))
    with patch('ok.task.TaskExecutor.alert_info'), \
         patch('ok.task.TaskExecutor.communicate.quit.emit', side_effect=lambda: events.append('tool quit')):
        TaskExecutor.exit_after_completed_task(executor)
    assert events == ['release', 'game exited', 'tool quit']


def test_failed_game_exit_keeps_tool_open():
    executor = SimpleNamespace(release_input=Mock(),
        device_manager=SimpleNamespace(get_preferred_device=lambda: {'device': 'macos'},
                                       stop_hwnd=Mock(side_effect=RuntimeError('quit rejected'))))
    with patch('ok.task.TaskExecutor.alert_info') as alert, \
         patch('ok.task.TaskExecutor.communicate.quit.emit') as quit_tool:
        with pytest.raises(RuntimeError, match='quit rejected'):
            TaskExecutor.exit_after_completed_task(executor)
    quit_tool.assert_not_called()
    alert.assert_not_called()


def test_windows_exit_sequence_is_preserved():
    events = []
    executor = SimpleNamespace(release_input=lambda: events.append('release'),
        device_manager=SimpleNamespace(get_preferred_device=lambda: {'device': 'windows'},
                                       stop_hwnd=lambda: events.append('stop')))
    with patch('ok.task.TaskExecutor.alert_info'), \
         patch('ok.task.TaskExecutor.time.sleep', side_effect=lambda seconds: events.append(seconds)), \
         patch('ok.task.TaskExecutor.communicate.quit.emit', side_effect=lambda: events.append('quit')):
        TaskExecutor.exit_after_completed_task(executor)
    assert events == ['release', 5, 'stop', 5, 'quit']
