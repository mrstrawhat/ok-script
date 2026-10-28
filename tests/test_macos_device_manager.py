import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys
import numpy as np

from ok.device.DeviceManager import DeviceManager
from ok.device.interaction_methods.pynput import PynputInteraction
from ok.task.exceptions import CaptureException
from ok.util.config import Config
from ok.util.handler import ExitEvent
from ok.task.TaskExecutor import TaskExecutor
if sys.platform == 'darwin':
    from ok.device.capture_methods.macos import MacOSCaptureMethod


class CaptureFixture:
    @staticmethod
    def prepare_session(gui=False):
        pass

    @staticmethod
    def discover_window(bundle):
        return SimpleNamespace(windowID=lambda: 101, title=lambda: 'Game',
                               owningApplication=lambda: SimpleNamespace(processID=lambda: 7))

    def __init__(self, bundle, exit_event):
        self.bundle_id = bundle
        self.width, self.height = 3024, 1898
        self.closed = False

    def refresh_window(self):
        pass

    def connected(self):
        return not self.closed

    def close(self):
        self.closed = True


@unittest.skipUnless(sys.platform == 'darwin', 'native macOS routing')
class TestMacOSDeviceManager(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.old_folder = Config.config_folder
        Config.config_folder = self.directory.name
        self.patch = patch('ok.device.capture_methods.macos.MacOSCaptureMethod', CaptureFixture)
        self.patch.start()
        self.stop = ExitEvent()
        self.manager = DeviceManager({'macos': {'bundle_id': 'test.game'}}, self.stop)

    def tearDown(self):
        self.stop.set()
        self.manager.handler.thread.join(timeout=2)
        self.manager.close()
        self.patch.stop()
        Config.config_folder = self.old_folder
        self.directory.cleanup()

    def connect(self):
        self.manager.update_macos_device()
        self.manager.config['preferred'] = 'macos:test.game'
        self.manager.do_start(notify=False)

    def test_native_device_uses_existing_input_backend(self):
        self.connect()
        self.assertIsInstance(self.manager.capture_method, CaptureFixture)
        self.assertIsInstance(self.manager.interaction, PynputInteraction)
        self.assertIs(self.manager.interaction.hwnd_window, self.manager.capture_method)
        self.assertTrue(self.manager.device_connected())
        self.assertEqual(self.manager.get_preferred_device()['resolution'], '3024x1898')
        self.assertEqual(self.manager.available_capture_methods(), ['macos'])
        self.assertEqual(self.manager.available_interaction_methods(), ['Pynput'])

    def test_disconnect_drops_stale_input_and_capture(self):
        self.connect()
        previous = self.manager.capture_method
        with patch.object(CaptureFixture, 'discover_window', side_effect=CaptureException('closed')):
            self.manager.update_macos_device()
        self.manager.do_start(notify=False)
        self.assertTrue(previous.closed)
        self.assertIsNone(self.manager.capture_method)
        self.assertIsNone(self.manager.interaction)
        self.assertFalse(self.manager.device_connected())

    def test_reconnect_after_window_disappears(self):
        self.test_disconnect_drops_stale_input_and_capture()
        self.connect()
        self.assertTrue(self.manager.device_connected())

    def test_no_preferred_device_is_disconnected(self):
        self.assertFalse(self.manager.device_connected())

    def test_unsupported_input_does_not_fall_into_windows_backend(self):
        self.connect()
        with self.assertRaises(ValueError):
            self.manager.set_interaction('PostMessage')


class TestNativeResolutionChecks(unittest.TestCase):
    def check_size(self, width, height):
        capture = SimpleNamespace(width=width, height=height,
            get_frame=lambda: np.zeros((height, width, 3), dtype=np.uint8))
        executor = SimpleNamespace(method=capture,
            device_manager=SimpleNamespace(update_resolution_for_hwnd=lambda: None))
        return TaskExecutor.check_frame_and_resolution(executor, None, (1280, 720))

    def test_no_fixed_aspect_ratio_still_enforces_minimum_size(self):
        self.assertFalse(self.check_size(640, 480)[0])

    def test_fullscreen_mac_aspect_ratio_is_accepted(self):
        self.assertEqual(self.check_size(3024, 1898), (True, '3024x1898'))


@unittest.skipUnless(sys.platform == 'darwin', 'native macOS window state')
class TestMacWindowState(unittest.TestCase):
    def setUp(self):
        self.capture = MacOSCaptureMethod.__new__(MacOSCaptureMethod)
        self.capture.window = CaptureFixture.discover_window('test.game')

    def test_closed_window_is_not_connected_even_if_app_stays_running(self):
        with patch('ok.device.capture_methods.macos.Quartz.CGWindowListCopyWindowInfo', return_value=[]):
            self.assertFalse(self.capture.connected())

    def test_replaced_window_does_not_reuse_old_capture(self):
        with patch('ok.device.capture_methods.macos.Quartz.CGWindowListCopyWindowInfo',
                   return_value=[{'kCGWindowNumber': 102, 'kCGWindowOwnerPID': 7}]):
            self.assertFalse(self.capture.connected())

    def test_matching_window_is_connected(self):
        with patch('ok.device.capture_methods.macos.Quartz.CGWindowListCopyWindowInfo',
                   return_value=[{'kCGWindowNumber': 101, 'kCGWindowOwnerPID': 7}]):
            self.assertTrue(self.capture.connected())


if __name__ == '__main__':
    unittest.main()
