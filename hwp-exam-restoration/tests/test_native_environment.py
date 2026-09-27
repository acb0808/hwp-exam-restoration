"""MCP's safe environment omits WINDIR, which Hancom's WPF font loader needs."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from runtime_paths import automation_root
sys.path.insert(0, str(automation_root() / 'scripts'))
import native_layout as native


class NativeEnvironmentTests(unittest.TestCase):
    def test_mcp_environment_restores_windir_without_mutating_parent(self):
        parent = {'SYSTEMROOT': os.environ.get('SystemRoot', 'C:\\Windows')}
        result = native.native_worker_environment(parent)
        self.assertEqual(result['WINDIR'], parent['SYSTEMROOT'])
        self.assertNotIn('WINDIR', parent)
        self.assertEqual(result['PYTHONIOENCODING'], 'utf-8')

    def test_preserves_existing_case_insensitive_windir(self):
        parent = {'windir': 'D:\\Windows', 'SystemRoot': 'C:\\Windows'}
        result = native.native_worker_environment(parent)
        self.assertEqual(result['windir'], 'D:\\Windows')
        self.assertNotIn('WINDIR', result)

    def test_absent_windows_root_fails_before_activation(self):
        with self.assertRaisesRegex(ValueError, 'windows_directory_environment_missing'):
            native.native_worker_environment({})


if __name__ == '__main__':
    unittest.main()
