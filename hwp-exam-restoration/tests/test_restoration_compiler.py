import sys, tempfile, unittest, zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from restoration_compiler import build_hwpx
HP='{http://www.hancom.co.kr/hwpml/2011/paragraph}'
HH='{http://www.hancom.co.kr/hwpml/2011/head}'

class CompilerTests(unittest.TestCase):
    def page(self, n=1):
        return {'page_number':n,'size_mm':[210,297],'blocks':[
            {'id':'line','kind':'text','question_id':'q1','bbox_mm':[20,25,70,7],'text':'Source line','font_pt':11,'font_family':'함초롬바탕'},
            {'id':'box','kind':'box','question_id':'q1','bbox_mm':[20,40,80,25],'title':'보기','title_bbox_mm':[25,37,15,6],'stroke_mm':0.2,'font_pt':10,'font_family':'함초롬바탕'}]}
    def test_fixed_anchors_pagebreaks_and_title_gap(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'test.hwpx'; receipt=build_hwpx([self.page(),self.page(2)],out)
            self.assertEqual(receipt['status'],'built_pending_native_validation')
            with zipfile.ZipFile(out) as z:
                sec=ET.fromstring(z.read('Contents/section0.xml'));head=ET.fromstring(z.read('Contents/header.xml'))
            positions=list(sec.iter(HP+'pos'))
            self.assertTrue(positions)
            self.assertTrue(all(p.get('horzRelTo')=='PAGE' and p.get('vertRelTo')=='PAGE' and p.get('treatAsChar')=='0' and p.get('flowWithText')=='0' for p in positions))
            self.assertEqual(sum(p.get('pageBreak')=='1' for p in sec if p.tag==HP+'p'),1)
            self.assertTrue(any('Source line' in ''.join(t.itertext()) for t in sec.iter(HP+'t')))
            self.assertEqual(len(receipt['pages'][0]['objects']),7) # text, five outline segments, title
            self.assertTrue(all(s.get('lineWrap')=='KEEP' for s in sec.iter(HP+'subList')))
            self.assertIn('#000000',ET.tostring(head,encoding='unicode'))
    def test_bad_math_fails_without_output(self):
        page=self.page();page['blocks']=[{'id':'e','kind':'equation','question_id':'q1','bbox_mm':[20,20,40,10],'latex':r'\def\x{a}','font_pt':11}]
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'no.hwpx'
            with self.assertRaises(Exception):build_hwpx([page],out)
            self.assertFalse(out.exists())
    def test_editable_equation(self):
        page=self.page();page['blocks']=[{'id':'e','kind':'equation','question_id':'q1','bbox_mm':[20,20,40,10],'latex':r'\frac{x}{2}','font_pt':11}]
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'eq.hwpx';build_hwpx([page],out)
            with zipfile.ZipFile(out) as z:sec=ET.fromstring(z.read('Contents/section0.xml'))
            eq=list(sec.iter(HP+'equation'));self.assertEqual(len(eq),1)
            self.assertIn('over',eq[0].find(HP+'script').text)
    def test_rejects_diagonal_and_mixed_page_sizes(self):
        page=self.page();page['blocks']=[{'id':'r','kind':'rule','question_id':None,'bbox_mm':[20,20,40,10],'stroke_mm':0.2}]
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):build_hwpx([page],Path(d)/'r.hwpx')
            other=self.page(2);other['size_mm']=[200,297]
            with self.assertRaises(ValueError):build_hwpx([self.page(),other],Path(d)/'m.hwpx')

if __name__=='__main__':unittest.main()
