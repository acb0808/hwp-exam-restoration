import sys,tempfile,unittest,zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from restoration_fit import check_native_fit

class NativeFitTests(unittest.TestCase):
    def test_native_formula_overflow_blocks_delivery(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'native.hwpx'
            xml='''<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph"><hp:p><hp:run><hp:tbl id="10"><hp:sz width="1000" height="1000"/><hp:tr><hp:tc><hp:subList><hp:p><hp:run><hp:equation><hp:sz width="2000" height="1500"/></hp:equation></hp:run><hp:linesegarray><hp:lineseg vertpos="0" vertsize="1500"/></hp:linesegarray></hp:p></hp:subList></hp:tc></hp:tr></hp:tbl></hp:run></hp:p></hs:sec>'''
            with zipfile.ZipFile(p,'w') as z:z.writestr('Contents/section0.xml',xml)
            receipt={'pages':[{'page_number':1,'objects':[{'block_id':'eq','kind':'equation','container_id':'10','bbox_mm':[0,0,3,3]}]}]}
            r=check_native_fit(receipt,p)
            self.assertEqual(r['status'],'failed')
            self.assertEqual({i['code'] for i in r['issues']},{'content_height_exceeds_source_box','equation_width_exceeds_source_box'})
    def test_missing_container_is_not_a_pass(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'native.hwpx'
            with zipfile.ZipFile(p,'w') as z:z.writestr('Contents/section0.xml','<sec/>')
            r=check_native_fit({'pages':[{'page_number':1,'objects':[{'block_id':'x','kind':'line','container_id':'10','bbox_mm':[0,0,10,10]}]}]},p)
            self.assertEqual(r['status'],'failed')

if __name__=='__main__':unittest.main()
