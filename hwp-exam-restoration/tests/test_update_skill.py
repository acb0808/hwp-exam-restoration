"""The self-updater on fake releases: no network, no Antigravity folders."""
import hashlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import update_skill as up


def bundle(version, files):
    """(zip bytes, SHA256SUMS text) of a release whose skill folder holds `files` {name: text}."""
    manifest = {'version': version, 'files': {n: hashlib.sha256(t.encode()).hexdigest() for n, t in files.items()}}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as z:
        z.writestr('README.md', 'repo readme')
        for n, t in files.items(): z.writestr('hwp-exam-restoration/' + n, t)
        z.writestr('hwp-exam-restoration/BUNDLE-MANIFEST.json', json.dumps(manifest))
    data = buffer.getvalue(); name = f'hwp-exam-restoration-{version}.zip'
    return ('v' + version, 'notes ' + version, name, data, f'{hashlib.sha256(data).hexdigest()}  {name}\r\n')


class UpdateTests(unittest.TestCase):
    OLD = {'SKILL.md': 'old skill', 'scripts/a.py': 'a = 1', 'scripts/dropped.py': 'gone'}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); root = Path(self.temp.name)
        self.skill, self.backups = root / 'skill', root / 'backups'
        tag, notes, name, data, sums = bundle('2.8.2', self.OLD)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for n in z.namelist():
                if n.startswith('hwp-exam-restoration/'):
                    (self.skill / n[21:]).parent.mkdir(parents=True, exist_ok=True); (self.skill / n[21:]).write_bytes(z.read(n))
        (self.skill / '.mcp-venv').mkdir(); (self.skill / '.mcp-venv' / 'python.txt').write_text('venv')
        (self.skill / 'notes-of-the-user.txt').write_text('mine')

    def tearDown(self): self.temp.cleanup()

    def run_update(self, release, *argv):
        return up.main(list(argv), skill=self.skill, backups=self.backups, agents_dir=self.backups / 'no-agents', release=release)

    def test_a_newer_release_replaces_the_skill_and_keeps_the_rest(self):
        report = self.run_update(bundle('2.8.3', {'SKILL.md': 'new skill', 'scripts/a.py': 'a = 2', 'scripts/new.py': 'n'}))
        self.assertEqual(report['status'], 'updated', report)
        self.assertEqual((report['from'], report['to']), ('2.8.2', '2.8.3'))
        self.assertEqual((self.skill / 'SKILL.md').read_text(), 'new skill')
        self.assertTrue((self.skill / 'scripts/new.py').is_file())
        self.assertFalse((self.skill / 'scripts/dropped.py').exists())           # listed by the old manifest only
        self.assertEqual((self.skill / '.mcp-venv' / 'python.txt').read_text(), 'venv')
        self.assertEqual((self.skill / 'notes-of-the-user.txt').read_text(), 'mine')   # not the skill's file
        self.assertIn('--rollback', report['rollback'])
        self.assertTrue(Path(report['backup']).is_relative_to(self.backups))

    def test_the_same_version_with_other_bytes_is_updated_and_identical_is_left(self):
        same = bundle('2.8.2', self.OLD)
        self.assertEqual(self.run_update(same)['status'], 'up_to_date')
        self.assertFalse(self.backups.exists())
        changed = bundle('2.8.2', dict(self.OLD, **{'scripts/a.py': 'a = 3'}))
        self.assertEqual(self.run_update(changed)['status'], 'updated')
        self.assertEqual((self.skill / 'scripts/a.py').read_text(), 'a = 3')

    def test_check_changes_nothing_and_an_older_release_is_not_installed(self):
        report = self.run_update(bundle('2.8.3', {'SKILL.md': 'new'}), '--check')
        self.assertEqual(report['status'], 'update_available')
        self.assertEqual((self.skill / 'SKILL.md').read_text(), 'old skill')
        self.assertEqual(self.run_update(bundle('2.8.1', {'SKILL.md': 'older'}))['status'], 'newer_installed')
        self.assertEqual((self.skill / 'SKILL.md').read_text(), 'old skill')

    def test_a_wrong_hash_stops_before_anything_changes(self):
        tag, notes, name, data, sums = bundle('2.8.3', {'SKILL.md': 'new'})
        with self.assertRaises(RuntimeError):
            self.run_update((tag, notes, name, data, '0' * 64 + '  ' + name + '\r\n'))
        self.assertEqual((self.skill / 'SKILL.md').read_text(), 'old skill')

    def test_rollback_puts_the_old_copy_back(self):
        report = self.run_update(bundle('2.8.3', {'SKILL.md': 'new skill', 'scripts/new.py': 'n'}))
        back = self.run_update(None, '--rollback', report['backup'])
        self.assertEqual(back['status'], 'rolled_back')
        self.assertEqual((self.skill / 'SKILL.md').read_text(), 'old skill')
        self.assertTrue((self.skill / 'scripts/dropped.py').is_file())
        self.assertFalse((self.skill / 'scripts/new.py').exists())
        self.assertEqual((self.skill / '.mcp-venv' / 'python.txt').read_text(), 'venv')

    def test_a_failed_copy_restores_the_backup(self):
        real = up.shutil.copyfile
        def failing(src, dst):
            if Path(dst).name == 'new.py': raise PermissionError('locked')
            return real(src, dst)
        with mock.patch.object(up.shutil, 'copyfile', failing):
            report = self.run_update(bundle('2.8.3', {'SKILL.md': 'new skill', 'scripts/new.py': 'n'}))
        self.assertEqual(report['status'], 'failed')
        self.assertEqual((self.skill / 'SKILL.md').read_text(), 'old skill')
        self.assertTrue((self.skill / 'scripts/dropped.py').is_file())
        self.assertEqual(json.loads((self.skill / 'BUNDLE-MANIFEST.json').read_text())['version'], '2.8.2')

    def test_skill_md_routes_an_update_request_to_the_updater(self):
        text = (Path(__file__).resolve().parents[1] / 'SKILL.md').read_text(encoding='utf-8')
        self.assertIn('update_skill.py', text)
        self.assertIn('update', text.split('---')[1].lower())                      # the description names it


if __name__ == '__main__':
    unittest.main()
