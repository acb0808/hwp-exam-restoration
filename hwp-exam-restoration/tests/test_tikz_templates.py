import copy
import importlib
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))

class TikzTemplateTests(unittest.TestCase):
    def setUp(self):self.mod=importlib.import_module('restoration_tikz_templates')

    def test_fixed_geometry_and_deterministic_labels(self):
        geometry=self.mod.make_template('right-midpoints',{})
        before=copy.deepcopy(geometry)
        a=self.mod.place_labels(geometry)
        self.assertEqual(a,self.mod.place_labels(geometry))
        self.assertEqual(geometry,before)
        self.assertTrue(all(self.mod.conflicts(v['box'],geometry,[q['box'] for k,q in a.items() if k!=name])==0 for name,v in a.items()))

    def test_midpoint_and_height_relations(self):
        g=self.mod.make_template('right-midpoints',{'base':'6','height':'4'})
        p=g['points'];self.assertEqual(p['D'],(3,0));self.assertEqual(p['E'],(6,2));self.assertEqual(p['O'],(3,2))
        p=self.mod.make_template('triangle-height',{'base':'6','height':'4','apex':'0.3'})['points']
        self.assertEqual(p['A'][0],p['H'][0]);self.assertEqual(p['H'][1],0)

    def test_circle_points_stay_on_circle(self):
        g=self.mod.make_template('circle-triangle',{'radius':'2.4','a':'210','b':'330','p':'75'})
        for key in ('A','B','P'):
            x,y=g['points'][key];self.assertAlmostEqual(x*x+y*y,2.4**2)

    def test_long_names_and_close_points_avoid_overlaps(self):
        g=self.mod.make_template('triangle-height',{'base':'2','height':'1','apex':'0.12','labels':'A_12/B_12/C_12/H_12'})
        placed=self.mod.place_labels(g)
        for key,v in placed.items():self.assertEqual(self.mod.conflicts(v['box'],g,[q['box'] for k,q in placed.items() if k!=key]),0)

    def test_fixed_source_side_is_respected(self):
        g=self.mod.make_template('triangle',{'posA':'above','posB':'left'})
        positions=self.mod.place_labels(g)
        self.assertEqual(positions['A']['side'],'above');self.assertEqual(positions['B']['side'],'left')

    def test_labels_never_move_closer_to_a_different_point(self):
        import math
        for kind,options in [('triangle-height',{'base':'3','height':'5','apex':'.08'}),
                             ('circle-triangle',{'radius':'1','a':'25','b':'260','p':'35'})]:
            g=self.mod.make_template(kind,options)
            try:placed=self.mod.place_labels(g)
            except ValueError as exc:
                self.assertIn('template_labels_overlap',str(exc));continue
            for key,c in placed.items():
                own=math.dist(c['center'],g['points'][key])
                self.assertTrue(all(own<math.dist(c['center'],point) for other,point in g['points'].items() if other!=key),(kind,key))

    def test_unresolved_layout_is_reported(self):
        g={'points':{'A':(0,0)},'segments':[], 'labels':{'A':'A'},'boundary':[], 'preferred':{},'obstacles':[(-100,-100,100,100)]}
        with self.assertRaisesRegex(ValueError,'template_labels_overlap'):self.mod.place_labels(g)

    def test_invalid_parameters_never_execute_or_add_features(self):
        for kind,options in [('missing',{}),('triangle',{'base':'nan'}),('triangle',{'height':'0'}),('triangle',{'unexpected':'1'}),('triangle',{'labels':'A/B/\\input{evil}'}),('circle-triangle',{'a':'90','b':'90'})]:
            with self.subTest(kind=kind,options=options),self.assertRaises(ValueError):self.mod.make_template(kind,options)

    def test_expansion_uses_existing_render_and_omits_unrequested_marks(self):
        from restoration_single import diagram_document
        doc=diagram_document(r'\begin{tikzpicture}\ExamTemplate{triangle-height}{base=4,height=3,labels=A/B/C/D}\end{tikzpicture}')
        self.assertNotIn(r'\ExamTemplate',doc);self.assertIn(r'\coordinate (H)',doc)
        body=doc.split(r'\begin{document}',1)[1]
        self.assertNotIn(r'\ExamRightAngle',body);self.assertNotIn(r'\ExamTicks',body)
        self.assertNotIn(r'\input',doc)

    def test_extra_template_and_malformed_options_fail(self):
        from restoration_single import diagram_document
        for body in [r'\ExamTemplate{triangle}{base=4,base=5}',r'\ExamTemplate{triangle}{base=4}\ExamTemplate{triangle}{}',r'\ExamTemplate{triangle}{bad}',r'\ExamTemplate{triangle}{base=4']:
            with self.subTest(body=body),self.assertRaises(ValueError):diagram_document('\\begin{tikzpicture}'+body+'\\end{tikzpicture}')

if __name__=='__main__':unittest.main()
