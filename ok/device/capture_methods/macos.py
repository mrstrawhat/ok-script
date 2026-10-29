"""Native macOS window capture with the existing ok-script capture contract."""
import threading
import time

import AppKit
import cv2
import numpy as np
import Quartz  # Register CGImage bridging before ScreenCaptureKit callbacks.
import ScreenCaptureKit as SC

from ok.device.capture_methods.base import BaseCaptureMethod
from ok.task.exceptions import CaptureException, CaptureBusyException


class _FrameNotReady(CaptureException):
    """A transient native failure or incomplete frame, safe to retry once."""


def _completed(start, timeout=5):
    result = []
    start(lambda value, error: result.append((value, error)))
    deadline = time.monotonic() + timeout
    while not result and time.monotonic() < deadline:
        AppKit.NSRunLoop.currentRunLoop().runUntilDate_(
            AppKit.NSDate.dateWithTimeIntervalSinceNow_(0.01))
    if not result:
        raise CaptureException('macOS capture timed out')
    value, error = result[0]
    if error is not None:
        if (error.domain() == 'com.apple.ScreenCaptureKit.SCStreamErrorDomain'
                and error.code() == -3811):
            raise _FrameNotReady(str(error))
        raise CaptureException(str(error))
    return value


class MacOSCaptureMethod(BaseCaptureMethod):
    name = 'macOS ScreenCaptureKit'

    def __init__(self, bundle_id, exit_event=None, client_area=False):
        super().__init__()
        self.exit_event = exit_event or threading.Event()
        self.bundle_id = bundle_id
        self.client_area = client_area
        self._content_top = 0
        self._capture_lock = threading.RLock()
        self._last_frame_geometry = None
        self.prepare_session()
        self.refresh_window()

    @staticmethod
    def prepare_session(gui=False):
        existing = AppKit.NSApp()
        app = existing or AppKit.NSApplication.sharedApplication()
        if existing is None and not gui:
            app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyProhibited)
        AppKit.NSScreen.screens()

    @staticmethod
    def discover_window(bundle_id):
        from ok.util.macos import permission_error
        if error := permission_error(require_input=False):
            raise CaptureException(error)
        content = _completed(lambda callback:
            SC.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
                True, False, callback))
        windows = [w for w in content.windows()
                   if w.owningApplication().bundleIdentifier() == bundle_id
                   and w.windowLayer() == 0 and w.frame().size.width > 100
                   and w.frame().size.height > 100]
        if not windows:
            raise CaptureException(f'No running game window: {bundle_id}')
        return max(windows, key=lambda w: w.frame().size.width * w.frame().size.height)

    def refresh_window(self):
        with self._capture_lock:
            self._refresh_window()

    def _refresh_window(self):
        self.window = self.discover_window(self.bundle_id)
        self.filter = SC.SCContentFilter.alloc().initWithDesktopIndependentWindow_(self.window)
        self.frame_points = self.window.frame()
        rect, scale = self.filter.contentRect(), self.filter.pointPixelScale()
        self._content_top = self._title_bar_height() if self.client_area else 0
        self._size = (round(rect.size.width * scale),
                      round(rect.size.height * scale) - round(self._content_top * scale))

    def _title_bar_height(self):
        """Find the native title bar from its centered AX title, in points.

        Fullscreen/borderless windows have no native title to exclude. Match
        the outer bounds because AXWindows can also contain sharing overlays.
        """
        import ApplicationServices as AX

        def value(element, name):
            error, result = AX.AXUIElementCopyAttributeValue(element, name, None)
            return result if error == 0 else None

        def geometry(element):
            position, size = value(element, 'AXPosition'), value(element, 'AXSize')
            if position is None or size is None:
                return None
            ok_pos, pos = AX.AXValueGetValue(position, AX.kAXValueCGPointType, None)
            ok_size, dim = AX.AXValueGetValue(size, AX.kAXValueCGSizeType, None)
            return (pos.x, pos.y, dim.width, dim.height) if ok_pos and ok_size else None

        app = AX.AXUIElementCreateApplication(self.window.owningApplication().processID())
        outer = self.frame_points
        expected = (outer.origin.x, outer.origin.y, outer.size.width, outer.size.height)
        for window in value(app, 'AXWindows') or []:
            bounds = geometry(window)
            if bounds is None or any(abs(a - b) > .5 for a, b in zip(bounds, expected)):
                continue
            if value(window, 'AXFullScreen'):
                return 0
            title = value(window, 'AXTitleUIElement')
            if title is None:
                return 0
            title_bounds = geometry(title)
            if title_bounds:
                top = title_bounds[1] - outer.origin.y
                height = 2 * top + title_bounds[3]
                if 0 <= top and 0 < height < min(100, outer.size.height / 4):
                    return height
            break
        raise CaptureException('Cannot determine the game client area; check accessibility permission')

    def _geometry(self):
        rect = self.frame_points
        return (self.window.windowID(), self.window.owningApplication().processID(),
                rect.origin.x, rect.origin.y, rect.size.width, rect.size.height, self._size,
                getattr(self, '_content_top', 0))

    def connected(self):
        windows = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionAll, Quartz.kCGNullWindowID) or []
        return any(int(w.get('kCGWindowNumber', 0)) == self.window.windowID()
                   and int(w.get('kCGWindowOwnerPID', 0)) == self.window.owningApplication().processID()
                   for w in windows)

    def _geometry_matches(self):
        windows = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionAll, Quartz.kCGNullWindowID) or []
        for window in windows:
            if int(window.get('kCGWindowNumber', 0)) == self.window.windowID():
                bounds = window['kCGWindowBounds']
                rect = self.frame_points
                expected = (rect.origin.x, rect.origin.y, rect.size.width, rect.size.height)
                return all(abs(float(bounds[key]) - value) < .5
                           for key, value in zip(('X', 'Y', 'Width', 'Height'), expected))
        return False

    def is_foreground(self):
        app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        return (app is not None and app.bundleIdentifier() == self.bundle_id
                and self.connected())

    def activate(self):
        apps = AppKit.NSRunningApplication.runningApplicationsWithBundleIdentifier_(self.bundle_id)
        if not apps:
            raise CaptureException('Game is not running')
        apps[0].activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if self.is_foreground():
                time.sleep(0.5)
                self.refresh_window()
                return
            time.sleep(0.05)
        raise CaptureException('Game did not become foreground')

    def bring_to_front(self):
        self.activate()

    def do_get_frame(self):
        return self.do_get_frame_wait(0)

    def do_get_frame_wait(self, timeout):
        if not self._capture_lock.acquire(timeout=max(0, timeout)):
            raise CaptureBusyException()
        try:
            return self._capture_frame()
        finally:
            self._capture_lock.release()

    def _capture_frame(self):
        # A failed screenshot must invalidate coordinates from the old frame.
        self._last_frame_geometry = None
        for attempt in range(2):
            if self.exit_event.is_set():
                raise CaptureException('macOS capture cancelled')
            try:
                return self._capture_frame_once()
            except _FrameNotReady:
                if attempt == 1:
                    raise
                if self.exit_event.wait(.1):
                    raise CaptureException('macOS capture cancelled')
                self.refresh_window()

    def _capture_frame_once(self):
        if not self.connected() or not self._geometry_matches():
            self.refresh_window()
        config = SC.SCStreamConfiguration.alloc().init()
        rect = self.filter.contentRect()
        scale = self.filter.pointPixelScale()
        expected_size = (round(rect.size.width * scale), round(rect.size.height * scale))
        if min(expected_size) <= 0:
            raise _FrameNotReady('macOS window has no drawable content yet')
        config.setWidth_(expected_size[0])
        config.setHeight_(expected_size[1])
        config.setShowsCursor_(False)
        config.setIgnoreShadowsSingleWindow_(True)
        config.setIncludeChildWindows_(False)
        image = _completed(lambda callback:
            SC.SCScreenshotManager.captureImageWithFilter_configuration_completionHandler_(
                self.filter, config, callback))
        if (image is None or
                (Quartz.CGImageGetWidth(image), Quartz.CGImageGetHeight(image)) != expected_size):
            raise _FrameNotReady('macOS screenshot dimensions do not match the selected window')
        bitmap = AppKit.NSBitmapImageRep.alloc().initWithCGImage_(image)
        data = bitmap.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {})
        frame = cv2.imdecode(np.frombuffer(bytes(data), dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise CaptureException('Could not decode macOS screenshot')
        if (frame.shape[1], frame.shape[0]) != expected_size:
            raise _FrameNotReady('Decoded macOS screenshot dimensions do not match the selected window')
        if not np.any(frame):
            raise _FrameNotReady('macOS screenshot is entirely black; waiting for drawable content')
        if not self._geometry_matches():
            raise _FrameNotReady('Game window moved during capture; retry with a new frame')
        content_top = round(getattr(self, '_content_top', 0) * scale)
        if content_top:
            frame = frame[content_top:].copy()
        self._size = (frame.shape[1], frame.shape[0])
        self._last_frame_geometry = self._geometry()
        return frame

    def get_abs_cords(self, x, y):
        if (not self.connected() or not self._geometry_matches()
                or self._last_frame_geometry != self._geometry()):
            raise CaptureException('Game window changed after capture; obtain a fresh frame before input')
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise CaptureException(f'Input outside game content: {x}, {y}')
        rect = self.frame_points
        top = getattr(self, '_content_top', 0)
        return (rect.origin.x + x * rect.size.width / self.width,
                rect.origin.y + top + y * (rect.size.height - top) / self.height)
