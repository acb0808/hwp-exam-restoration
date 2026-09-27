import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
from xml.etree import ElementTree as ET

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from restoration_compiler import build_hwpx
from restoration_contract import validate_page
HP='{http://www.hancom.co.kr/hwpml/2011/paragraph}'
HH='{http://www.hancom.co.kr/hwpml/2011/head}'

def fixture():
    p=json.loads((Path(__file__).resolve().parents[1]/'examples/page-001.json').read_text(encoding='utf-8'))
    p['blocks'].append({'id':'mixed-line','question_id':'q2','kind':'line','bbox_mm':[12,115,85,12],
        'font_pt':10.5,'font_family':'바탕','runs':[
        {'kind':'text','text':'원의 중심은 '},
        {'kind':'equation','latex':r'(a,b)'},
        {'kind':'text','text':'이고 반지름은 '},
        {'kind':'equation','latex':r'\frac{3}{2}'},
        {'kind':'text','text':'이다.'}]})
    return p

class EquationPlacementTests(unittest.TestCase):
    def test_mixed_line_contract_and_no_literal_latex(self):
        p=fixture();validate_page(p)
        broken=copy.deepcopy(p);broken['blocks'][-1]['runs'][0]['text']='$x$'
        with self.assertRaises(ValueError):validate_page(broken)

    def test_mixed_line_has_one_inline_paragraph_and_native_measurement(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'line.hwpx';build_hwpx([fixture()],out)
            with zipfile.ZipFile(out) as z:
                sec=ET.fromstring(z.read('Contents/section0.xml'))
                header=ET.fromstring(z.read('Contents/header.xml'))
            paragraphs=[p for p in sec.iter(HP+'p') if len(list(p.iter(HP+'equation')))==2]
            p=next(p for p in paragraphs if p.find(HP+'run/'+HP+'t') is not None)
            self.assertEqual(len(p.findall(HP+'run')),5)
            for eq in p.iter(HP+'equation'):
                self.assertEqual(eq.find(HP+'pos').get('treatAsChar'),'1')
                self.assertEqual(eq.find(HP+'sz').get('width'),'0')
                self.assertEqual(eq.find(HP+'sz').get('protect'),'0')
            style=next(n for n in header.iter(HH+'paraPr') if n.get('id')==p.get('paraPrIDRef'))
            self.assertEqual(style.find('.//'+HH+'lineSpacing').get('type'),'PERCENT')

    def test_standalone_equation_not_stretched_to_source_bbox(self):
        p=fixture();p['blocks']=[b for b in p['blocks'] if b['kind']=='equation']
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'eq.hwpx';build_hwpx([p],out)
            with zipfile.ZipFile(out) as z:sec=ET.fromstring(z.read('Contents/section0.xml'))
            eq=next(sec.iter(HP+'equation'))
            self.assertEqual(eq.find(HP+'sz').get('width'),'0')
            self.assertEqual(eq.find(HP+'pos').get('treatAsChar'),'1')
            self.assertTrue(any(eq in list(t.iter(HP+'equation')) for t in sec.iter(HP+'tbl')))

if __name__=='__main__':unittest.main()
