"""Registration must preserve unrelated credentials and restore exact prior bytes."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/'scripts/install_mcp_server.py'

class MCPInstallationTests(unittest.TestCase):
    def test_unrelated_entry_preserved_exact_backup_and_guarded_rollback(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); config=root/'mcp.json'; backup=root/'backup'
            before=b'{ "mcpServers": {"other": {"command":"preserve", "env":{"TOKEN":"synthetic-secret"}}}, "setting":true }\n'
            config.write_bytes(before)
            base=[sys.executable,'-B','-X','utf8',str(SCRIPT),'--config',str(config),'--backup',str(backup),'--python',sys.executable]
            def run(*args): return subprocess.run(base+list(args),capture_output=True,text=True,encoding='utf-8')
            result=run();self.assertEqual(result.returncode,0,result.stderr)
            self.assertNotIn('synthetic-secret',result.stdout)
            self.assertEqual((backup/'mcp_config.before.json').read_bytes(),before)
            installed=config.read_bytes(); value=json.loads(installed)
            self.assertEqual(value['mcpServers']['other'],json.loads(before)['mcpServers']['other'])
            config.write_bytes(installed+b' ')
            self.assertNotEqual(run('--rollback').returncode,0)
            config.write_bytes(installed)
            self.assertEqual(run('--rollback','--dry-run').returncode,0)
            self.assertEqual(config.read_bytes(),installed)
            self.assertEqual(run('--rollback').returncode,0)
            self.assertEqual(config.read_bytes(),before)
