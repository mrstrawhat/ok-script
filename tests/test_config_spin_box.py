import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from qfluentwidgets import Theme, setTheme

from ok import og
from ok.ui.qt.tasks.LabelAndDoubleSpinBox import LabelAndDoubleSpinBox
from ok.ui.qt.tasks.LabelAndSpinBox import LabelAndSpinBox
from ok.util.config import Config


class TestConfigSpinBox(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.old_app = og.app
        og.app = SimpleNamespace(tr=lambda text: text)
        self.addCleanup(setattr, og, 'app', self.old_app)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.config = Config('target', {'Boss Number': 1, 'Repeat': 10000, 'Ratio': 13.8},
                             folder=self.folder.name)

    def row(self, cls, key):
        row = cls({}, self.config, key)
        row.resize(900, 80)
        row.show()
        self.app.processEvents()
        self.addCleanup(row.close)
        return row

    def test_keyboard_and_arrow_changes_persist_after_reopen(self):
        for cls, key, text, expected in (
                (LabelAndSpinBox, 'Boss Number', '3', 3),
                (LabelAndDoubleSpinBox, 'Ratio', '15.5', 15.5)):
            with self.subTest(key=key):
                row = self.row(cls, key)
                spin = row.spin_box
                editor = spin.lineEdit()
                editor.selectAll()
                QTest.keyClicks(editor, text)
                QTest.keyClick(editor, Qt.Key_Return)
                self.app.processEvents()
                self.assertEqual(spin.value(), expected)
                with open(self.config.config_file, encoding='utf-8') as saved:
                    self.assertEqual(json.load(saved)[key], expected)
                spin.upButton.click()
                self.app.processEvents()
                reopened = Config('target', self.config.default, folder=self.folder.name)
                self.assertEqual(reopened[key], spin.value())
                self.assertEqual(reopened[key], expected + spin.singleStep())

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS Fluent editor layout regression')
    def test_editor_remains_visible_and_separate_from_arrows(self):
        self.addCleanup(setTheme, Theme.LIGHT)
        for cls, key in ((LabelAndSpinBox, 'Boss Number'), (LabelAndDoubleSpinBox, 'Ratio')):
            row = self.row(cls, key)
            for theme in (Theme.LIGHT, Theme.DARK):
                setTheme(theme)
                row.resize(1000, 90)
                self.app.processEvents()
                spin = row.spin_box
                editor = spin.lineEdit()
                self.assertGreaterEqual(editor.width(), 60)
                self.assertLess(editor.geometry().right(), spin.upButton.x())

    def test_positive_repeat_count_keeps_existing_minimum(self):
        row = self.row(LabelAndSpinBox, 'Repeat')
        row.spin_box.setValue(0)
        self.assertEqual(row.spin_box.value(), 1)
        self.assertEqual(self.config['Repeat'], 1)


if __name__ == '__main__':
    unittest.main()
