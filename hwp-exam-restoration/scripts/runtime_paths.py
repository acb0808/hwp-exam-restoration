"""Resolve only resources shipped inside this skill; never search another profile."""
from pathlib import Path
import sys

SKILL_ROOT=Path(__file__).resolve().parents[1]
RUNTIME=SKILL_ROOT/'runtime'

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
