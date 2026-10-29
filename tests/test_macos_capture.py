"""Mock-only capture regressions: no app discovery, screenshots or input events."""
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

if sys.platform != 'darwin':
    pytest.skip('macOS capture backend', allow_module_level=True)

from ok.device.capture_methods import macos as mac
from ok.task.exceptions import CaptureException


def error(code, domain='com.apple.ScreenCaptureKit.SCStreamErrorDomain'):
    return SimpleNamespace(code=lambda: code, domain=lambda: domain)


def test_completed_returns_the_callback_image():
    image = object()
    assert mac._completed(lambda callback: callback(image, None)) is image


def test_completed_classifies_only_the_transient_screen_capture_error():
    with pytest.raises(mac._FrameNotReady):
        mac._completed(lambda callback: callback(None, error(-3811)))


@pytest.mark.parametrize('native_error', [error(-3801), error(-3811, 'other.domain')])
def test_completed_keeps_other_errors_non_retryable(native_error):
    with pytest.raises(CaptureException) as raised:
        mac._completed(lambda callback: callback(None, native_error))
    assert not isinstance(raised.value, mac._FrameNotReady)


def test_completed_timeout_does_not_call_native_run_loop(monkeypatch):
    ticks = iter([0, 100])
    monkeypatch.setattr(mac.time, 'monotonic', lambda: next(ticks, 100))
    with pytest.raises(CaptureException, match='timed out'):
        mac._completed(lambda callback: None, timeout=.1)


@pytest.fixture
def capture():
    instance = mac.MacOSCaptureMethod.__new__(mac.MacOSCaptureMethod)
    instance.exit_event = Mock()
    instance.exit_event.is_set.return_value = False
    instance.exit_event.wait.return_value = False
    instance.connected = Mock(return_value=True)
    instance._geometry_matches = Mock(return_value=True)
    instance.refresh_window = Mock()
    instance._geometry = Mock(return_value=('current-window', 100, 80))
    instance._last_frame_geometry = ('previous-frame',)
    instance._size = (100, 80)
    instance.filter = SimpleNamespace(
        contentRect=lambda: SimpleNamespace(size=SimpleNamespace(width=50, height=40)),
        pointPixelScale=lambda: 2.0)
    return instance


def test_transient_failure_refreshes_once_and_recovers(capture):
    frame = np.ones((80, 100, 3), dtype=np.uint8)
    capture._capture_frame_once = Mock(side_effect=[mac._FrameNotReady('not ready'), frame])
    assert capture._capture_frame() is frame
    assert capture._capture_frame_once.call_count == 2
    capture.refresh_window.assert_called_once_with()
    capture.exit_event.wait.assert_called_once_with(.1)


def test_persistent_transient_failure_has_a_fixed_attempt_limit(capture):
    capture._capture_frame_once = Mock(side_effect=mac._FrameNotReady('still not ready'))
    with pytest.raises(CaptureException):
        capture._capture_frame()
    assert capture._capture_frame_once.call_count == 2
    assert capture._last_frame_geometry is None


def test_other_capture_errors_are_not_retried(capture):
    capture._capture_frame_once = Mock(side_effect=CaptureException('permission refused'))
    with pytest.raises(CaptureException, match='permission refused'):
        capture._capture_frame()
    capture._capture_frame_once.assert_called_once_with()
    capture.refresh_window.assert_not_called()
    capture.exit_event.wait.assert_not_called()
    assert capture._last_frame_geometry is None


def test_already_cancelled_capture_sends_no_native_request(capture):
    capture.exit_event.is_set.return_value = True
    capture._capture_frame_once = Mock()
    with pytest.raises(CaptureException):
        capture._capture_frame()
    capture._capture_frame_once.assert_not_called()
    assert capture._last_frame_geometry is None


def test_cancellation_during_recovery_prevents_the_second_request(capture):
    capture._capture_frame_once = Mock(side_effect=mac._FrameNotReady('not ready'))
    capture.exit_event.wait.return_value = True
    with pytest.raises(CaptureException):
        capture._capture_frame()
    capture._capture_frame_once.assert_called_once_with()
    assert capture._last_frame_geometry is None


@pytest.fixture
def native_frame(monkeypatch, capture):
    """Replace every native entry point; retain real PNG decode and frame checks."""
    state = SimpleNamespace(frame=np.full((80, 100, 3), 1, dtype=np.uint8),
                            image_width=100, image_height=80)
    config = Mock()
    state.config = config
    stream_config = SimpleNamespace(alloc=lambda: SimpleNamespace(init=lambda: config))
    monkeypatch.setattr(mac, 'SC', SimpleNamespace(SCStreamConfiguration=stream_config,
        SCScreenshotManager=Mock()))
    monkeypatch.setattr(mac, 'Quartz', SimpleNamespace(
        CGImageGetWidth=lambda image: state.image_width,
        CGImageGetHeight=lambda image: state.image_height))

    def encode(*args):
        success, data = cv2.imencode('.png', state.frame)
        assert success
        return data.tobytes()

    bitmap = SimpleNamespace(representationUsingType_properties_=encode)
    representation = SimpleNamespace(alloc=lambda: SimpleNamespace(initWithCGImage_=lambda image: bitmap))
    monkeypatch.setattr(mac, 'AppKit', SimpleNamespace(NSBitmapImageRep=representation,
                                                     NSBitmapImageFileTypePNG=0))
    monkeypatch.setattr(mac, '_completed', Mock(return_value=object()))
    return state


def test_valid_dark_frame_is_accepted_without_brightness_threshold(capture, native_frame):
    frame = capture._capture_frame()
    assert np.array_equal(frame, native_frame.frame)
    assert frame.max() == 1
    assert capture._last_frame_geometry == capture._geometry()
    assert capture._size == (100, 80)
    native_frame.config.setWidth_.assert_called_once_with(100)
    native_frame.config.setHeight_.assert_called_once_with(80)


def test_all_zero_frame_is_not_published_as_a_valid_frame(capture, native_frame):
    native_frame.frame[:] = 0
    with pytest.raises(CaptureException):
        capture._capture_frame()
    assert capture._last_frame_geometry is None
    # Previous successful geometry must not permit input after a failed frame.
    with pytest.raises(CaptureException):
        capture.get_abs_cords(1, 1)


@pytest.mark.parametrize('width,height', [(99, 80), (100, 79), (0, 0)])
def test_native_image_dimensions_must_match_the_requested_window(capture, native_frame, width, height):
    native_frame.image_width, native_frame.image_height = width, height
    with pytest.raises(CaptureException):
        capture._capture_frame()
    assert capture._last_frame_geometry is None
    assert capture._size == (100, 80)


def test_decoded_frame_dimensions_must_match_the_native_image(capture, native_frame):
    native_frame.frame = np.ones((79, 100, 3), dtype=np.uint8)
    with pytest.raises(CaptureException):
        capture._capture_frame()
    assert capture._last_frame_geometry is None
    assert capture._size == (100, 80)


def test_decode_failure_invalidates_the_previous_input_geometry(capture, native_frame, monkeypatch):
    monkeypatch.setattr(mac.cv2, 'imdecode', Mock(return_value=None))
    with pytest.raises(CaptureException, match='decode'):
        capture._capture_frame()
    assert capture._last_frame_geometry is None


def test_geometry_changing_during_capture_rejects_the_frame(capture, native_frame):
    capture._geometry_matches.side_effect = [True, False, True, False]
    with pytest.raises(CaptureException):
        capture._capture_frame()
    assert capture._last_frame_geometry is None
    capture.refresh_window.assert_called_once_with()


def test_window_movement_retries_with_fresh_geometry(capture, native_frame):
    capture._geometry_matches.side_effect = [True, False, True, True]
    frame = capture._capture_frame()
    assert np.array_equal(frame, native_frame.frame)
    capture.refresh_window.assert_called_once_with()
    assert capture._last_frame_geometry == capture._geometry()


def test_client_frame_excludes_title_bar_and_maps_input_to_content(capture, native_frame):
    capture._content_top = 4  # points; the fixture is 2x Retina
    capture.frame_points = SimpleNamespace(origin=SimpleNamespace(x=10, y=20),
                                          size=SimpleNamespace(width=50, height=40))
    frame = capture._capture_frame()
    assert frame.shape == (72, 100, 3)
    assert capture.get_abs_cords(0, 0) == (10, 24)
    assert capture.get_abs_cords(50, 36) == (35, 42)


@pytest.mark.parametrize('fullscreen,title,expected', [(False, True, 32), (True, True, 0), (False, False, 0)])
def test_native_client_area_matches_window_and_ignores_sharing_overlay(capture, monkeypatch, fullscreen, title, expected):
    def rect(x, y, w, h):
        return {'AXPosition': SimpleNamespace(x=x, y=y), 'AXSize': SimpleNamespace(width=w, height=h)}
    window = rect(100, 200, 756, 523)
    window['AXFullScreen'] = fullscreen
    if title:
        window['AXTitleUIElement'] = rect(180, 208, 37, 16)
    app = {'AXWindows': [rect(105, 205, 68, 22), window]}
    ax = SimpleNamespace(AXUIElementCreateApplication=lambda pid: app,
        AXUIElementCopyAttributeValue=lambda e, n, _: (0, e[n]) if n in e else (-25205, None),
        AXValueGetValue=lambda v, t, _: (True, v), kAXValueCGPointType=1, kAXValueCGSizeType=2)
    monkeypatch.setitem(sys.modules, 'ApplicationServices', ax)
    capture.frame_points = SimpleNamespace(origin=SimpleNamespace(x=100, y=200),
                                          size=SimpleNamespace(width=756, height=523))
    capture.window = SimpleNamespace(owningApplication=lambda: SimpleNamespace(processID=lambda: 10))
    assert capture._title_bar_height() == expected
