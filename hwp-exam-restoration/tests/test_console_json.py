"""CLI JSON survives legacy host decoders without changing Unicode values."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


class ConsoleJsonTests(unittest.TestCase):
    def test_actual_stdout_roundtrips_across_console_encodings(self):
        scripts = Path(__file__).resolve().parents[1] / 'scripts'
        value = {
            'status': 'failed',
            'errors': [{'location': 'questions[0].content[1]',
                        'hint': '본문의 한국어를 보존하고 빈 수식을 수정하세요.'}],
            'path': r'D:\시험 자료\교사\결과.json',
            'latex': r'\frac{가}{나}+\sqrt{x}\in A',
            'symbols': '≤ ≥ ∠ α ① 🧮',
            'text': '실제 줄바꿈\n역슬래시 \\ 와 "따옴표"',
        }
        # Keep the child source ASCII so only the helper's output encoding is tested.
        program = ('import sys\n'
                   f'sys.path.insert(0, {ascii(str(scripts))})\n'
                   'from restoration_console import emit_json\n'
                   f'emit_json({ascii(value)})\n')
        for encoding in ('ascii', 'cp949', 'utf-8'):
            with self.subTest(encoding=encoding):
                environment = os.environ.copy()
                environment['PYTHONIOENCODING'] = encoding
                result = subprocess.run([sys.executable, '-B', '-c', program],
                                        capture_output=True, env=environment, check=False)
                self.assertEqual(result.returncode, 0, result.stderr.decode('ascii', errors='replace'))
                self.assertTrue(result.stdout.isascii(), result.stdout)
                self.assertTrue(result.stdout.endswith(b'\n'))
                for host_encoding in ('ascii', 'cp949', 'utf-8'):
                    self.assertEqual(json.loads(result.stdout.decode(host_encoding)), value)


if __name__ == '__main__': unittest.main()
