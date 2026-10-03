"""Resolve only resources shipped inside this skill; never search another profile."""
from pathlib import Path
import sys

SKILL_ROOT=Path(__file__).resolve().parents[1]
RUNTIME=SKILL_ROOT/'runtime'
BINARY_SUFFIXES={'.png','.jpg','.jpeg','.gif','.bmp','.hwp','.hwpx','.pdf','.zip','.whl','.ttf','.otf','.bin'}

def content_digest(path):
    """SHA-256 used for every bundled-file check. Text is hashed with LF line ends, so a git checkout
    that converts line endings (Windows core.autocrlf, or LF on other systems) is not reported as tampering."""
    import hashlib
    path=Path(path);data=path.read_bytes()
    if path.suffix.lower() not in BINARY_SUFFIXES and b'\x00' not in data:data=data.replace(b'\r\n',b'\n')
    return hashlib.sha256(data).hexdigest()

def automation_root():
    root=RUNTIME/'hwp_automation'
    if not (root/'scripts/hwpx_builder.py').is_file():raise ValueError('bundled_hwp_runtime_missing_reinstall_skill')
    return root

def native_root():
    root=RUNTIME/'native/src'
    if not (root/'academy/documents/hwp_adapter.py').is_file():raise ValueError('bundled_native_adapter_missing_reinstall_skill')
    return root

def equation_compiler():
    if not (RUNTIME/'restoration_equations.py').is_file() or not (RUNTIME/'latex_to_hwpeqn/__init__.py').is_file():
        raise ValueError('bundled_equation_engine_missing_reinstall_skill')
    if str(RUNTIME) not in sys.path:sys.path.insert(0,str(RUNTIME))
    from restoration_equations import compile_equation
    return compile_equation
