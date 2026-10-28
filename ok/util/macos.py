"""Native permissions, function shortcuts and normal application termination."""
import platform
import math
import time
import threading

import psutil


class FunctionHotkey:
    """Listen for F9-F12 without querying Carbon keyboard layouts on a worker.

    A listen-only Quartz tap uses the Qt UI thread's native run loop, including
    function keys which AppKit's later global monitor may not receive.
    """
    KEY_CODES = {'f9': 0x65, 'f10': 0x6d, 'f11': 0x67, 'f12': 0x6f}

    def __init__(self, key, callback):
        self.keycode = self.KEY_CODES[key.lower()]
        self.callback = callback
        self.tap = None
        self.source = None

    def start(self):
        import Quartz as q
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError('macOS shortcuts must be registered on the UI thread')
        if self.tap is not None:
            return
        self.tap = q.CGEventTapCreate(q.kCGSessionEventTap, q.kCGHeadInsertEventTap,
            q.kCGEventTapOptionListenOnly, 1 << q.kCGEventKeyDown, self._handle, None)
        if self.tap is None:
            raise RuntimeError('macOS refused the global shortcut monitor')
        try:
            self.source = q.CFMachPortCreateRunLoopSource(None, self.tap, 0)
            q.CFRunLoopAddSource(q.CFRunLoopGetMain(), self.source, q.kCFRunLoopCommonModes)
            q.CGEventTapEnable(self.tap, True)
        except Exception:
            self.stop()
            raise

    def _handle(self, proxy, kind, event, refcon):
        import Quartz as q
        if kind in (q.kCGEventTapDisabledByTimeout, q.kCGEventTapDisabledByUserInput):
            if self.tap is not None:
                q.CGEventTapEnable(self.tap, True)
        elif kind == q.kCGEventKeyDown:
            modifiers = (q.kCGEventFlagMaskCommand | q.kCGEventFlagMaskControl |
                         q.kCGEventFlagMaskAlternate | q.kCGEventFlagMaskShift)
            if (q.CGEventGetIntegerValueField(event, q.kCGKeyboardEventKeycode) == self.keycode
                    and not q.CGEventGetIntegerValueField(event, q.kCGKeyboardEventAutorepeat)
                    and not q.CGEventGetFlags(event) & modifiers):
                self.callback()
        return event

    def stop(self):
        import Quartz as q
        if self.source is not None:
            q.CFRunLoopRemoveSource(q.CFRunLoopGetMain(), self.source, q.kCFRunLoopCommonModes)
            self.source = None
        if self.tap is not None:
            q.CFMachPortInvalidate(self.tap)
            self.tap = None

    def is_alive(self):
        import Quartz as q
        return self.tap is not None and bool(q.CGEventTapIsEnabled(self.tap))


def permission_status():
    import ApplicationServices
    import Quartz
    return {
        'screen_recording': bool(Quartz.CGPreflightScreenCaptureAccess()),
        'accessibility': bool(ApplicationServices.AXIsProcessTrusted()),
        'post_events': bool(Quartz.CGPreflightPostEventAccess()),
        'listen_events': bool(Quartz.CGPreflightListenEventAccess()),
    }


def permission_error(require_input=True):
    version = tuple(int(part) for part in platform.mac_ver()[0].split('.')[:2])
    if version < (15, 2):
        return 'Mac 原生截图需要 macOS 15.2 或更新版本。'
    status = permission_status()
    missing = []
    if not status['screen_recording']:
        missing.append('屏幕与系统音频录制')
    if require_input and not (status['accessibility'] and status['post_events']):
        missing.append('辅助功能')
    if missing:
        return '请在系统设置 → 隐私与安全性中允许当前启动程序的' + '、'.join(missing) + '权限，然后退出并重新打开工具。'


def open_permission_settings(kind):
    import subprocess
    pane = {'screen_recording': 'Privacy_ScreenCapture',
            'accessibility': 'Privacy_Accessibility',
            'listen_events': 'Privacy_ListenEvent'}[kind]
    subprocess.Popen(['/usr/bin/open', 'x-apple.systempreferences:com.apple.preference.security?' + pane])


def quit_application(bundle_id, pid, timeout=15):
    """Ask the selected process to quit normally; never launch, activate or kill it."""
    import AppKit

    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout < 0:
        raise ValueError('退出等待时长必须为有限的非负秒数。')
    if not bundle_id or not isinstance(pid, int) or pid <= 0:
        raise ValueError('无法确认所选游戏进程，已取消自动退出。')
    app = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    if app is None:
        return  # It has already exited. Do not target a replacement process.
    if app.bundleIdentifier() != bundle_id:
        raise RuntimeError('所选进程的应用标识已变化，已取消自动退出。')
    try:
        process = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    # is_running() checks process identity as well as its PID, including reuse.
    if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
        return
    if not app.terminate():
        raise RuntimeError('游戏拒绝正常退出，工具已保留；请手动处理游戏窗口。')
    deadline = time.monotonic() + timeout
    while True:
        try:
            if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
                return
        except psutil.NoSuchProcess:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError('等待游戏正常退出超时，工具已保留；未强制结束游戏。')
        time.sleep(min(.1, max(0, deadline - time.monotonic())))
