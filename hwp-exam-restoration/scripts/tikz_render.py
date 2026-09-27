"""Use the TikZ renderer shipped in this skill, without a sibling skill install."""
from pathlib import Path
import runpy,sys
if __name__=='__main__':
    runpy.run_path(str(Path(__file__).resolve().parents[1]/'runtime/tikz_render.py'),run_name='__main__')
