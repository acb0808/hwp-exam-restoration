import json,os,shutil,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

class PortableSkillTests(unittest.TestCase):
    def test_relocated_skill_builds_equations_without_home_or_sibling_skills(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);skill=base/'다른 설치 위치'/'hwp-exam-restoration'
            shutil.copytree(ROOT,skill,ignore=shutil.ignore_patterns('__pycache__','.venv','.pytest_cache'))
            code=r'''
import json,sys
from pathlib import Path
from unittest.mock import patch
skill=Path(sys.argv[1]);sys.path.insert(0,str(skill/'scripts'))
from restoration_compiler import build_hwpx
q=lambda c,x:{'id':c,'region_id':c,'bbox_mm':[x,30,80,220],'font_family':'바탕','font_pt':10,
 'content':[{'id':c+'p','kind':'paragraph','runs':[{'kind':'text','text':'분수 '},{'kind':'equation','latex':r'\frac{1}{2}'}]}]}
page={'version':2,'page_number':1,'size_mm':[210,297],'blocks':[],
 'regions':[{'id':'L','bbox_mm':[10,20,90,260]},{'id':'R','bbox_mm':[110,20,90,260]}],
 'questions':[q('L',10),q('R',110)]}
with patch.object(Path,'home',side_effect=AssertionError('must_not_read_home_installation')):
 result=build_hwpx([page],Path(sys.argv[2]),template_dir=skill/'assets/templates/pdf2hwp-grid',
   title='합성 배포 검증',template_fields={'school':'검증 학교','year':'2026','exam_title':'합성 예제'})
 import latex_to_hwpeqn,hwpx_builder
 assert Path(latex_to_hwpeqn.__file__).is_relative_to(skill)
 assert Path(hwpx_builder.__file__).is_relative_to(skill)
 assert result['template']['name']=='pdf2hwp-grid'
 assert len(result['equations'])==2
print('relocation_passed')
'''
            process=subprocess.run([sys.executable,'-I','-X','utf8','-c',code,str(skill),str(base/'smoke.hwpx')],cwd=base,capture_output=True,text=True,encoding='utf-8',timeout=90)
            self.assertEqual(process.returncode,0,process.stdout+process.stderr)
            self.assertIn('relocation_passed',process.stdout)

    def test_distribution_profile_has_no_personal_source_path(self):
        profile=json.loads((ROOT/'assets/templates/pdf2hwp-grid/profile.json').read_text(encoding='utf-8'))
        self.assertNotIn('source_hwp',profile)
        self.assertTrue((ROOT/'assets/templates/pdf2hwp-grid'/profile['source_package']).is_file())

