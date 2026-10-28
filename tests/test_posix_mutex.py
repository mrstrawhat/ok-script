"""Check real project-lock contention on POSIX without Windows API mocks."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ok.util import process


@unittest.skipIf(sys.platform == 'win32', 'POSIX file locks')
class TestPosixMutex(unittest.TestCase):
    def setUp(self):
        self.previous = os.getcwd()
        self.directory = tempfile.TemporaryDirectory()
        self.framework = str(Path(__file__).resolve().parents[1])
        os.chdir(self.directory.name)

    def tearDown(self):
        process._release_mutex()
        os.chdir(self.previous)
        self.directory.cleanup()

    def child_acquires(self, directory):
        code = ('from ok.util.process import check_mutex; '
                'print("LOCK_RESULT=" + str(check_mutex(wait_time=0)))')
        env = dict(os.environ, PYTHONPATH=self.framework)
        result = subprocess.run([sys.executable, '-c', code], cwd=directory,
                                env=env, capture_output=True, text=True, timeout=10, check=True)
        return 'LOCK_RESULT=True' in result.stdout

    def test_same_project_is_excluded_then_can_reopen(self):
        self.assertTrue(process.check_mutex(wait_time=0))
        self.assertFalse(self.child_acquires(self.directory.name))
        process._release_mutex()
        self.assertTrue(self.child_acquires(self.directory.name))

    def test_other_project_remains_independent(self):
        self.assertTrue(process.check_mutex(wait_time=0))
        with tempfile.TemporaryDirectory() as other:
            self.assertTrue(self.child_acquires(other))

    def test_owner_record_is_removed_on_release(self):
        self.assertTrue(process.check_mutex(wait_time=0, gui=True))
        record = Path(process._mutex_owner_file)
        self.assertEqual(json.loads(record.read_text())['pid'], os.getpid())
        process._release_mutex()
        self.assertFalse(record.exists())


if __name__ == '__main__':
    unittest.main()
