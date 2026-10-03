"""Ensure helper use remains self-contained, cache-sensitive and optional."""
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import restoration_single as single

PICTURE=r'\begin{tikzpicture}\coordinate (A) at (0,0);\ExamLabel[above]{A}{$A$}\end{tikzpicture}'

class TikzAnnotationToolsTests(unittest.TestCase):
    def test_helper_picture_is_self_contained_and_idempotent(self):
        doc=single.diagram_document(PICTURE)
        for command in ('ExamLabel','ExamRightAngle','ExamLengthArc','ExamTicks'):
            self.assertIn('\\newcommand{\\'+command+'}',doc)
        self.assertIn(r'\usetikzlibrary{decorations.markings,shapes.arrows}',doc)  # shapes.arrows: \ExamImplies (v2.7.8)
        self.assertNotIn(r'\input',doc)
        self.assertEqual(single.diagram_document(doc),doc)

    def test_ordinary_picture_keeps_old_wrapper_and_does_not_read_helpers(self):
        picture=r'\begin{tikzpicture}\draw (0,0)--(1,1);\end{tikzpicture}'
        expected=('\\documentclass[tikz,border=2pt]{standalone}\n'
                  '\\usepackage{kotex}\n\\usepackage{amsmath}\n'
                  '\\usetikzlibrary{calc,arrows.meta,angles,quotes}\n'
                  '\\begin{document}\n'+picture+'\n\\end{document}\n')
        self.assertEqual(single.diagram_document(picture),expected)

    def test_helper_version_changes_compiled_source_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'marks.tex';path.write_text('% version one\n',encoding='utf-8')
            with patch.object(single,'TIKZ_HELPERS',path):
                before=single.diagram_document(PICTURE)
                path.write_text('% version two\n',encoding='utf-8')
                after=single.diagram_document(PICTURE)
        self.assertNotEqual(hashlib.sha256(before.encode()).digest(),hashlib.sha256(after.encode()).digest())

    def test_legacy_complete_document_remains_untouched(self):
        full=r'\documentclass{standalone}\usepackage{tikz}\begin{document}\begin{tikzpicture}\draw (0,0)--(1,1);\end{tikzpicture}\end{document}'
        self.assertEqual(single.diagram_document(full),full)

    def test_helpers_cannot_bypass_scan_rejection(self):
        with self.assertRaisesRegex(ValueError,'embedded_image'):
            single.diagram_document(PICTURE.replace(r'\end{tikzpicture}',r'\includegraphics{scan.png}\end{tikzpicture}'))

    def test_comment_only_helper_name_does_not_load_library(self):
        picture='\\begin{tikzpicture}\n% \\ExamLabel\n\\draw (0,0)--(1,1);\n\\end{tikzpicture}'
        self.assertNotIn(r'\newcommand{\ExamLabel}',single.diagram_document(picture))

if __name__=='__main__':unittest.main()
