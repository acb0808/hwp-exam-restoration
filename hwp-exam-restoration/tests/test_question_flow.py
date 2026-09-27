import sys,tempfile,unittest,zipfile,json,copy
from pathlib import Path
from xml.etree import ElementTree as E
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from restoration_compiler import build_hwpx
from restoration_contract import validate_page
HP='{http://www.hancom.co.kr/hwpml/2011/paragraph}'
class QuestionFlowTests(unittest.TestCase):
    def test_inline_box_is_native_editable_text_with_rectangular_border(self):
        page=self.page()
        page['questions'][0]['content'][0]['runs'].insert(1, {'kind':'boxed_text','text':'(가)'})
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'inline-box.hwpx';build_hwpx([page],p)
            with zipfile.ZipFile(p) as z:
                head=E.fromstring(z.read('Contents/header.xml'))
                root=E.fromstring(z.read('Contents/section0.xml'))
            runs=[n for n in root.iter(HP+'run') if n.find(HP+'t') is not None and n.find(HP+'t').text=='(가)']
            self.assertEqual(len(runs),1)
            HH='{http://www.hancom.co.kr/hwpml/2011/head}'
            char=next(c for c in head.iter(HH+'charPr') if c.get('id')==runs[0].get('charPrIDRef'))
            fill=next(f for f in head.iter(HH+'borderFill') if f.get('id')==char.get('borderFillIDRef'))
            self.assertEqual({side.tag.split('}')[-1] for side in fill if side.tag.split('}')[-1].endswith('Border') and side.get('type')=='SOLID'},
                             {'leftBorder','rightBorder','topBorder','bottomBorder'})

    def test_v2_contract_rejects_line_boxes_and_page_owned_question_fragments(self):
        p=Path(__file__).resolve().parents[1]/'examples/question-flow-page.json'
        page=json.loads(p.read_text(encoding='utf-8'));validate_page(page)
        changed=copy.deepcopy(page);changed['questions'][0]['content'][0]['bbox_mm']=[15,30,60,8]
        with self.assertRaisesRegex(ValueError,'coordinates forbidden'):validate_page(changed)
        changed=copy.deepcopy(page);changed['blocks'][0]['question_id']='q1'
        with self.assertRaisesRegex(ValueError,'never page blocks'):validate_page(changed)
    def page(self):
        return {'version':2,'page_number':1,'size_mm':[210,297],'blocks':[], 'questions':[
            {'id':'q1','bbox_mm':[15,30,85,110],'font_pt':10.5,'font_family':'바탕','content':[
                {'id':'body','kind':'paragraph','runs':[{'kind':'text','text':'문장 '},{'kind':'equation','latex':r'\frac{x}{2}'},{'kind':'break'},{'kind':'text','text':'두 번째 줄'}]},
                {'id':'options','kind':'choices','columns':2,'rows':[[[{'kind':'text','text':'① '},{'kind':'equation','latex':'1'}],[{'kind':'text','text':'② '},{'kind':'equation','latex':r'\frac{3}{2}'}]]]}]}]}
    def test_one_container_with_flowing_paragraphs_and_tabs(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'flow.hwpx';receipt=build_hwpx([self.page()],p)
            with zipfile.ZipFile(p) as z:root=E.fromstring(z.read('Contents/section0.xml'))
            tables=list(root.iter(HP+'tbl'));self.assertEqual(len(tables),1)
            self.assertEqual(len(list(tables[0].iter(HP+'p'))),2)
            self.assertEqual(len(list(root.iter(HP+'equation'))),3)
            self.assertEqual(len(list(root.iter(HP+'tab'))),1)
            self.assertEqual(tables[0].find('.//'+HP+'subList').get('lineWrap'),'BREAK')
            self.assertEqual(receipt['pages'][0]['objects'][0]['kind'],'question')
    def test_only_semantic_box_adds_a_nested_table(self):
        page=self.page();page['questions'][0]['content'].append({'id':'given','kind':'box','title':'〈보 기〉','content':[{'id':'condition','kind':'paragraph','runs':[{'kind':'text','text':'ㄱ. 조건'}]}]})
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'box.hwpx';build_hwpx([page],p)
            with zipfile.ZipFile(p) as z:root=E.fromstring(z.read('Contents/section0.xml'))
            self.assertEqual(len(list(root.iter(HP+'tbl'))),2)
            outer=next(root.iter(HP+'tbl'));self.assertEqual(len(list(outer.iter(HP+'tbl'))),2)
    def test_custom_tabs_keep_long_choices_in_one_paragraph(self):
        page=self.page();page['questions'][0]['content'][1]['tab_stops_mm']=[50]
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'tabs.hwpx';build_hwpx([page],p)
            with zipfile.ZipFile(p) as z:head=E.fromstring(z.read('Contents/header.xml'));root=E.fromstring(z.read('Contents/section0.xml'))
            self.assertTrue(any(e.get('pos')==str(round(50*7200/25.4)) for e in head.iter('{http://www.hancom.co.kr/hwpml/2011/head}tabItem')))
            self.assertEqual(len(list(root.iter(HP+'tbl'))),1)
if __name__=='__main__':unittest.main()
