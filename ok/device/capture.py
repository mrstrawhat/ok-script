# Compatibility shim for the old ok.device.capture module.
from ok.device.capture_methods import *


def __getattr__(name):
    if name == "render_full":
        from ok.device.capture_methods import bitblt as _bitblt
        return _bitblt.render_full
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
