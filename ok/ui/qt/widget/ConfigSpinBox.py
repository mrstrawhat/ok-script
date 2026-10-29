"""Config spin boxes with a usable editor on macOS Qt styles."""
import sys

from PySide6.QtCore import QEvent
from qfluentwidgets import DoubleSpinBox, SpinBox


class _MacEditorGeometry:
    def event(self, event):
        handled = super().event(event)
        if (sys.platform == 'darwin'
                and event.type() in (QEvent.Show, QEvent.Resize, QEvent.LayoutRequest,
                                     QEvent.StyleChange, QEvent.FocusIn, QEvent.FocusOut)
                and hasattr(self, 'upButton')):
            editor = self.lineEdit()
            # Qt can collapse SC_SpinBoxEditField to one pixel when the Fluent
            # spin box hides native arrows. Keep its own arrow buttons and
            # restore just the collapsed editor, without changing valid styles.
            if editor.width() <= 1 and self.upButton.x() > 20:
                editor.setGeometry(10, 1, self.upButton.x() - 15, self.height() - 2)
        return handled


class ConfigSpinBox(_MacEditorGeometry, SpinBox):
    pass


class ConfigDoubleSpinBox(_MacEditorGeometry, DoubleSpinBox):
    pass
