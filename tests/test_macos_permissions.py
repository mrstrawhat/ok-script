import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ok.util.macos import permission_error, permission_status


def test_permission_status_reads_accessibility_from_application_services():
    services = SimpleNamespace(AXIsProcessTrusted=Mock(return_value=True))
    quartz = SimpleNamespace(CGPreflightScreenCaptureAccess=lambda: True,
                             CGPreflightPostEventAccess=lambda: False,
                             CGPreflightListenEventAccess=lambda: True)
    with patch.dict(sys.modules, {'ApplicationServices': services, 'Quartz': quartz}):
        status = permission_status()
    assert status == {'screen_recording': True, 'accessibility': True,
                      'post_events': False, 'listen_events': True}
    services.AXIsProcessTrusted.assert_called_once_with()


def test_capture_only_does_not_require_input_permissions():
    with patch('ok.util.macos.platform.mac_ver', return_value=('15.2', (), 'arm64')), \
         patch('ok.util.macos.permission_status', return_value={
             'screen_recording': True, 'accessibility': False, 'post_events': False, 'listen_events': False}):
        assert permission_error(require_input=False) is None
        assert '辅助功能' in permission_error(require_input=True)
