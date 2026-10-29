"""Foreground macOS input using the existing Pynput key/button contract."""
import math
import threading
import time

from ok.device.interaction_methods.pynput import PynputInteraction
from ok.task.exceptions import TaskDisabledException
from ok.util.logger import Logger

logger = Logger.get_logger(__name__)


class MacOSInteraction(PynputInteraction):
    refresh_frame_on_run = True
    # pynput's reverse layout map overwrites top-row digits with keypad keys.
    # Games bind party slots to ANSI number-row key codes, not keypad digits.
    _NUMBER_ROW_KEYS = {'1': 18, '2': 19, '3': 20, '4': 21, '5': 23,
                        '6': 22, '7': 26, '8': 28, '9': 25, '0': 29}

    def _parse_key(self, key):
        if isinstance(key, str) and key in self._NUMBER_ROW_KEYS:
            from pynput.keyboard import KeyCode
            return KeyCode.from_vk(self._NUMBER_ROW_KEYS[key])
        return super()._parse_key(key)

    def __init__(self, capture, hwnd_window=None, *, allowed=None, on_focus_lost=None,
                 keyboard_controller=None, mouse_controller=None, monitor=True):
        super().__init__(capture, hwnd_window or capture)
        self._keyboard = keyboard_controller
        self._mouse = mouse_controller
        self._allowed = allowed or (lambda: True)
        self._on_focus_lost = on_focus_lost or (lambda: None)
        self._lock = threading.RLock()
        self._keys = set()
        self._buttons = set()
        self._closed = threading.Event()
        self._monitor = None
        if monitor:
            self._monitor = threading.Thread(target=self._watch, name='MacInputRelease', daemon=True)
            self._monitor.start()

    @property
    def keyboard(self):
        if self._keyboard is None:
            if threading.current_thread() is not threading.main_thread():
                raise RuntimeError('macOS keyboard must be initialized from the application UI before starting')
            from pynput.keyboard import Controller
            self._keyboard = Controller()
        return self._keyboard

    @property
    def mouse(self):
        if self._mouse is None:
            from pynput.mouse import Controller
            self._mouse = Controller()
        return self._mouse

    def _focus_ok(self):
        return self.capture.is_foreground() and self.capture._geometry_matches()

    def _check(self):
        if self._closed.is_set() or not self._allowed():
            self.release_all()
            raise TaskDisabledException()
        if not self._focus_ok():
            self.release_all()
            self._on_focus_lost()
            raise TaskDisabledException()

    def _wait(self, seconds):
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError('Input duration must be finite and non-negative')
        end = time.monotonic() + seconds
        while True:
            self._check()
            remaining = end - time.monotonic()
            if remaining <= 0:
                return
            self._closed.wait(min(.02, remaining))

    def _watch(self):
        while not self._closed.wait(.02):
            with self._lock:
                if not (self._keys or self._buttons):
                    continue
            try:
                self._check()
            except TaskDisabledException:
                pass
            except Exception:
                logger.exception('macOS input monitor failed; releasing held input')
                self.release_all()
                self._on_focus_lost()

    def release_all(self):
        # Releases must still run after focus is lost or the task is stopped.
        with self._lock:
            for held, controller in ((self._keys, self._keyboard), (self._buttons, self._mouse)):
                for value in tuple(held):
                    try:
                        controller.release(value)
                    except Exception:
                        logger.exception('Could not release macOS input')
                    else:
                        held.discard(value)

    def send_key_down(self, key):
        key = self._parse_key(key)
        with self._lock:
            self._check()
            self._keys.add(key)
            try:
                self.keyboard.press(key)
            except BaseException:
                self.release_all()
                raise

    def send_key_up(self, key):
        key = self._parse_key(key)
        with self._lock:
            if key in self._keys:
                self.keyboard.release(key)
                self._keys.discard(key)

    def send_key(self, key, down_time=.01):
        try:
            self.send_key_down(key)
            self._wait(down_time)
        finally:
            self.send_key_up(key)

    def send_key_combination(self, keys, down_time=.01):
        pressed = []
        try:
            for key in keys:
                self.send_key_down(key)
                pressed.append(key)
            self._wait(down_time)
        finally:
            for key in reversed(pressed):
                self.send_key_up(key)

    def move(self, x, y):
        with self._lock:
            self._check()
            self.mouse.position = self.capture.get_abs_cords(x, y)

    def get_cursor_position(self):
        return self.mouse.position

    def restore_cursor_position(self, position):
        with self._lock:
            self._check()
            self.mouse.position = position

    def move_relative_pixels(self, dx, dy):
        """Camera deltas in capture pixels; Task.move_relative remains normalized XY."""
        import Quartz
        if not all(math.isfinite(value) for value in (dx, dy)):
            raise ValueError('Mouse delta must be finite')
        with self._lock:
            self._check()
            rect = self.capture.frame_points
            dx = round(dx * rect.size.width / self.capture.width)
            dy = round(dy * rect.size.height / self.capture.height)
            event = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved,
                                                  self.mouse.position, Quartz.kCGMouseButtonLeft)
            Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventDeltaX, dx)
            Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventDeltaY, dy)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

    def mouse_down(self, x=-1, y=-1, name=None, key='left'):
        with self._lock:
            self._check()
            if x != -1 and y != -1:
                self.move(x, y)
                # UE updates its pointer hit-test on a later frame. Pressing
                # in the same tick can drag the map from the old pointer.
                self._wait(.05)
            button = self.get_mouse_button(key)
            self._buttons.add(button)
            try:
                self.mouse.press(button)
            except BaseException:
                self.release_all()
                raise

    def mouse_up(self, key='left'):
        button = self.get_mouse_button(key)
        with self._lock:
            if button in self._buttons:
                self.mouse.release(button)
                self._buttons.discard(button)

    def click(self, x=-1, y=-1, move_back=False, name=None, down_time=.01, move=False, key='left'):
        previous = self.get_cursor_position() if move_back else None
        try:
            self.mouse_down(x, y, name=name, key=key)
            self._wait(down_time)
        finally:
            self.mouse_up(key)
        if previous is not None:
            self.restore_cursor_position(previous)

    def swipe(self, x1, y1, x2, y2, duration, settle_time=0):
        # Existing Task.swipe passes milliseconds; settle_time is seconds.
        seconds = duration / 1000
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError('Swipe duration must be finite and non-negative')
        steps = max(1, math.ceil(seconds * 60))
        try:
            self.mouse_down(x1, y1)
            start = time.monotonic()
            for index in range(1, steps + 1):
                self._wait(max(0, start + seconds * index / steps - time.monotonic()))
                fraction = index / steps
                self.move(x1 + (x2 - x1) * fraction, y1 + (y2 - y1) * fraction)
        finally:
            self.mouse_up()
        self._wait(settle_time)

    def scroll(self, x, y, scroll_amount):
        if not isinstance(scroll_amount, int):
            raise ValueError('Scroll count must be an integer')
        self.move(x, y)
        for _ in range(abs(scroll_amount)):
            with self._lock:
                self._check()
                self.mouse.scroll(0, 1 if scroll_amount > 0 else -1)
            self._wait(.01)

    def input_text(self, text):
        for character in text:
            self.send_key(character)

    def should_capture(self):
        if self._closed.is_set():
            return False
        foreground = self._focus_ok()
        if not foreground:
            self.release_all()
            if self._allowed():
                self._on_focus_lost()
        return foreground

    def on_destroy(self):
        self._closed.set()
        self.release_all()
        if self._monitor is not None and self._monitor is not threading.current_thread():
            self._monitor.join(timeout=.5)
