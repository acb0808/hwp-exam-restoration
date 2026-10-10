import copy,json,sys,tempfile,unittest,zipfile
from pathlib import Path
from xml.etree import ElementTree as E
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from restoration_compiler import build_hwpx
HP='{http://www.hancom.co.kr/hwpml/2011/paragraph}'
class GridTests(unittest.TestCase):
    def page(self,left=3,right=2):
        qs=[]
        for col,count,x in [('L',left,10),('R',right,110)]:
            for i in range(count):qs.append({'id':f'{col}{i}','region_id':col,'bbox_mm':[x,30+i*70,90,60],
                'font_family':'바탕','font_pt':10,'content':[{'id':'body','kind':'paragraph','runs':[{'kind':'text','text':f'{col}{i} 문제'}]}]})
        return {'version':2,'page_number':1,'size_mm':[210,297],'blocks':[],
                'regions':[{'id':'L','bbox_mm':[10,20,90,270]},{'id':'R','bbox_mm':[110,20,90,270]}],'questions':qs}
    def build(self,page,d):
        p=Path(d)/'grid.hwpx'
        receipt=build_hwpx([page],p,template_dir=ROOT/'assets/templates/pdf2hwp-grid',title='시험',template_fields={'school':'예시고등학교','year':'2025','exam_title':'고1-2 중간고사'})
        with zipfile.ZipFile(p) as z:root=E.fromstring(z.read('Contents/section0.xml'))
        return receipt,root
    def test_three_left_two_right_are_merged_cells_of_one_source_table(self):
        with tempfile.TemporaryDirectory() as d:
            receipt,root=self.build(self.page(),d)
            tables=list(root.iter(HP+'tbl'));self.assertEqual(len(tables),1)
            cells=[c for c in tables[0].iter(HP+'tc') if int(c.find(HP+'cellAddr').get('rowAddr'))>=4]
            self.assertEqual(len(cells),5)
            plan=receipt['pages'][0]['objects'][0]['merges']
            self.assertEqual({col:[m['row_span'] for m in plan if m['column']==col] for col in (0,1)},{0:[2,2,2],1:[3,3]})
            self.assertEqual(len([o for o in receipt['pages'][0]['objects'] if o['kind']=='question_cell']),5)
            self.assertNotIn('#M',''.join(root.itertext()))
    def test_all_columns_merged_rows_are_normalized_for_hancom(self):
        with tempfile.TemporaryDirectory() as d:
            _,root=self.build(self.page(3,3),d)
            table=next(root.iter(HP+'tbl'));rows=table.findall(HP+'tr')
            self.assertTrue(all(len(row)>0 for row in rows),'Hancom rejects empty physical rows after full-column merge')
            self.assertEqual(len(rows),7)
            self.assertEqual(table.find('.//'+HP+'cellzone').get('startRowAddr'),'6')
    def test_more_than_six_questions_per_column_is_not_silently_reflowed(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError,'grid_column_requires_1_to_6_questions'):self.build(self.page(7,2),d)
    def test_merged_cell_keeps_top_of_first_and_bottom_of_last_cell(self):
        pages=[self.page(1,1),self.page(1,1)];pages[1]['page_number']=2
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'grid.hwpx'
            build_hwpx(pages,p,template_dir=ROOT/'assets/templates/pdf2hwp-grid',title='시험',template_fields={'school':'예시고등학교','year':'2025','exam_title':'고1-2 중간고사'})
            with zipfile.ZipFile(p) as z:root=E.fromstring(z.read('Contents/section0.xml'));head=E.fromstring(z.read('Contents/header.xml'))
            hh='{http://www.hancom.co.kr/hwpml/2011/head}'
            fills={n.get('id'):n for n in head.iter(hh+'borderFill')}
            table=list(root.iter(HP+'tbl'))[1]
            for cell in table.iter(HP+'tc'):
                fill=fills[cell.get('borderFillIDRef')]
                self.assertEqual(fill.find(hh+'topBorder').get('type'),'SOLID')
                self.assertEqual(fill.find(hh+'bottomBorder').get('type'),'SOLID')
    def test_cover_and_blank_are_not_exam_questions(self):
        for name in ('cover','blank'):
            page=self.page(1,1);page['questions'][0]['id']=name
            with tempfile.TemporaryDirectory() as d:
                with self.assertRaisesRegex(ValueError,'question_content_only'):self.build(page,d)
if __name__=='__main__':unittest.main()
