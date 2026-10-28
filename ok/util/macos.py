"""Read native permissions without prompting or changing system settings."""
import platform


def permission_status():
    import Quartz
    return {
        'screen_recording': bool(Quartz.CGPreflightScreenCaptureAccess()),
        'accessibility': bool(Quartz.AXIsProcessTrusted()),
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
