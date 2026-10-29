"""Exercise real input orchestration with recording controllers, never OS input."""
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from ok.device.interaction_methods.macos import MacOSInteraction
from ok.task.exceptions import TaskDisabledException


class Controller:
    def __init__(self):
        self.events = []
        self.position = (0, 0)
        self.pressed = threading.Event()

    def press(self, key):
        self.events.append(('down', key))
        self.pressed.set()

    def release(self, key):
        self.events.append(('up', key))

    def scroll(self, x, y):
        self.events.append(('scroll', x, y))


@pytest.fixture
def native():
    capture = SimpleNamespace(is_foreground=lambda: True, _geometry_matches=lambda: True,
        get_abs_cords=lambda x, y: (x / 2, y / 2), width=200, height=100,
        frame_points=SimpleNamespace(size=SimpleNamespace(width=100, height=50)))
    keyboard, mouse = Controller(), Controller()
    interaction = MacOSInteraction(capture, keyboard_controller=keyboard, mouse_controller=mouse, monitor=False)
    interaction._parse_key = lambda key: key
    interaction.get_mouse_button = lambda key: key
    yield interaction
    interaction.on_destroy()


def test_hold_releases_when_executor_cancels(native):
    native._allowed = lambda: not native.keyboard.pressed.is_set()
    with pytest.raises(TaskDisabledException):
        native.send_key('w', .5)
    assert native.keyboard.events == [('down', 'w'), ('up', 'w')]


def test_key_up_still_releases_after_focus_is_lost(native):
    native.send_key_down('w')
    native.capture.is_foreground = lambda: False
    native.send_key_up('w')
    assert native.keyboard.events[-1] == ('up', 'w')


def test_chord_releases_in_reverse_order(native):
    native.send_key_combination(['shift', 'w'], 0)
    assert native.keyboard.events == [('down', 'shift'), ('down', 'w'), ('up', 'w'), ('up', 'shift')]


def test_chord_failure_releases_already_held_keys(native):
    def press(key):
        if key == 'w':
            raise RuntimeError('controller failed')
    native.keyboard.press = press
    with pytest.raises(RuntimeError):
        native.send_key_combination(['shift', 'w'])
    assert not native._keys
    assert ('up', 'shift') in native.keyboard.events


def test_swipe_uses_milliseconds_and_finishes_at_endpoint(native):
    start = time.monotonic()
    native.swipe(0, 0, 100, 60, 60)
    assert .05 <= time.monotonic() - start < 1
    assert native.mouse.position == (50, 30)
    assert native.mouse.events == [('down', 'left'), ('up', 'left')]


def test_swipe_failure_releases_button(native):
    calls = 0
    def move(x, y):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError('geometry changed')
        return x, y
    native.capture.get_abs_cords = move
    with pytest.raises(RuntimeError):
        native.swipe(0, 0, 100, 60, 0)
    assert native.mouse.events[-1] == ('up', 'left')


def test_scroll_cannot_continue_after_focus_loss(native):
    native.mouse.scroll = lambda x, y: setattr(native.capture, 'is_foreground', lambda: False)
    native._on_focus_lost = Mock()
    with pytest.raises(TaskDisabledException):
        native.scroll(0, 0, 3)
    native._on_focus_lost.assert_called_once()


def test_mouse_up_after_stop_does_not_require_foreground(native):
    native.mouse_down()
    native._allowed = lambda: False
    native.mouse_up()
    assert native.mouse.events == [('down', 'left'), ('up', 'left')]


def test_destroy_releases_owned_inputs_and_blocks_new_events(native):
    native.send_key_down('w')
    native.mouse_down()
    native.on_destroy()
    with pytest.raises(TaskDisabledException):
        native.send_key_down('e')
    assert native.keyboard.events == [('down', 'w'), ('up', 'w')]
    assert native.mouse.events[-1] == ('up', 'left')


def test_watchdog_releases_hold_while_task_is_asleep(native):
    native.send_key_down('w')
    native.capture.is_foreground = lambda: False
    native._on_focus_lost = Mock()
    worker = threading.Thread(target=native._watch)
    worker.start()
    deadline = time.monotonic() + 1
    while native._keys and time.monotonic() < deadline:
        time.sleep(.01)
    native.on_destroy()
    worker.join(timeout=1)
    assert not native._keys
    native._on_focus_lost.assert_called_once()


def test_relative_camera_posts_deltas_scaled_from_retina_pixels(native):
    quartz = Mock(kCGEventMouseMoved=5, kCGMouseButtonLeft=0,
                  kCGMouseEventDeltaX=4, kCGMouseEventDeltaY=5, kCGHIDEventTap=0)
    with patch.dict(sys.modules, {'Quartz': quartz}):
        native.move_relative_pixels(20, -8)
    event = quartz.CGEventCreateMouseEvent.return_value
    assert quartz.CGEventSetIntegerValueField.call_args_list == [
        ((event, 4, 10),), ((event, 5, -4),)]
    quartz.CGEventPost.assert_called_once_with(0, event)


def test_no_input_when_foreground_or_geometry_check_fails(native):
    native.capture._geometry_matches = lambda: False
    with pytest.raises(TaskDisabledException):
        native.mouse_down(10, 10)
    assert native.mouse.events == []


def test_cancel_while_pointer_settles_never_presses_a_button(native):
    def cancelled(duration):
        raise TaskDisabledException()
    native._wait = cancelled
    with pytest.raises(TaskDisabledException):
        native.click(100, 50)
    assert native.mouse.events == []
    assert not native._buttons
