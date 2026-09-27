import sys,unittest,tempfile,json,hashlib
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import restoration_job as job

def sha(data):return hashlib.sha256(data).hexdigest()

class JobTests(unittest.TestCase):
 def result(self,manifest,assignment):
  return {'version':1,'source_sha256':manifest['source']['sha256'],'page_number':assignment['page'],
          'size_mm':[210,297],'assignment_id':assignment['assignment_id'],'worker_id':assignment['worker_id'],'issues':[],
          'regions':[{'id':'left','bbox_mm':[10,20,90,260],'question_ids':['q1']}],
          'questions':[{'id':'q1','region_id':'left','bbox_mm':[10,20,90,100]}],
          'blocks':[{'id':'b1','question_id':'q1','kind':'text','bbox_mm':[12,22,60,5],'text':'Source line','font_pt':10,'font_family':'Batang'}]}
 def complete(self,root,main=False):
  manifest,evidence=self.fixture(root);receipts=[]
  for page in (2,1):
   kwargs={}
   if main:
    approval=root/'approval.json';job.save_json(approval,{'user_message':'Explicit approval for both pages','reason':'delegated workers unavailable','allowed_pages':[1,2]})
    kwargs={'mode':'main_exception','approval':approval}
   assignment=job.assign(root,page,f'worker-{page}',evidence,**kwargs)
   path=root/f'result-{page}.json';job.save_json(path,self.result(manifest,assignment));receipts.append(job.accept(root,path))
  return receipts
 def fixture(self,root):
  source=root/'source.pdf';source.write_bytes(b'source')
  pages=[]
  for page in (1,2):
   from PIL import Image
   path=root/f'page-{page}.png';Image.new('RGB',(210,297),'white').save(path)
   pages.append({'page':page,'width_mm':210,'height_mm':297,'image':{'path':path.name,'sha256':sha(path.read_bytes())}})
  manifest={'schema':'restoration-job/1','source':{'path':source.name,'sha256':sha(b'source')},'pages':pages,'assignments':[],'accepted':[]}
  job.save_json(root/'manifest.json',manifest)
  evidence=root/'spawn.json';evidence.write_text('{"tool":"spawn_agent","worker_id":"worker-1"}')
  return manifest,evidence
 def test_assignment_requires_nonempty_evidence(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);manifest,evidence=self.fixture(root);evidence.write_text(' ')
   with self.assertRaises(ValueError):job.assign(root,1,'worker-1',evidence)
 def test_main_exception_requires_page_scoped_approval(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);manifest,evidence=self.fixture(root)
   with self.assertRaises(ValueError):job.assign(root,1,'main',evidence,mode='main_exception')
   approval=root/'approval.json';job.save_json(approval,{'user_message':'Only page 2 may be handled here','reason':'worker unavailable','allowed_pages':[2]})
   with self.assertRaises(ValueError):job.assign(root,1,'main',evidence,mode='main_exception',approval=approval)
 def test_assemble_rejects_missing_pages(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);self.fixture(root)
   with self.assertRaises(ValueError):job.assemble(root)
 def test_complete_is_ordered_and_exact_bytes_are_snapshotted(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);receipts=self.complete(root)
   self.assertEqual([v['page_number'] for v in job.assemble(root)],[1,2])
   for receipt in receipts:
    self.assertEqual((root/receipt['result']['path']).read_bytes(),(root/f"result-{receipt['page']}.json").read_bytes())
 def test_worker_cannot_be_reused_for_another_page(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);manifest,evidence=self.fixture(root)
   job.assign(root,1,'worker',evidence)
   with self.assertRaisesRegex(ValueError,'one_page_per_independent_worker'):job.assign(root,2,'worker',evidence)
 def test_result_metadata_must_match_assignment_and_source(self):
  for field,value in [('worker_id','other'),('assignment_id','other'),('source_sha256','a'*64),('size_mm',[211,297]),('page_number',2),('issues',['unreadable'])]:
   with self.subTest(field=field),tempfile.TemporaryDirectory() as d:
    root=Path(d);manifest,evidence=self.fixture(root);assignment=job.assign(root,1,'worker',evidence)
    result=self.result(manifest,assignment);result[field]=value;path=root/'result.json';job.save_json(path,result)
    with self.assertRaises(ValueError):job.accept(root,path)
 def test_artifact_tampering_blocks_assembly(self):
  for kind in ('source','image','evidence','approval','result'):
   with self.subTest(kind=kind),tempfile.TemporaryDirectory() as d:
    root=Path(d);self.complete(root,main=True);manifest=job.load_json(root/'manifest.json')
    record={'source':manifest['source'],'image':manifest['pages'][0]['image'],
            'evidence':manifest['assignments'][0]['evidence'],'approval':manifest['assignments'][0]['approval'],
            'result':manifest['accepted'][0]['result']}[kind]
    (root/record['path']).write_bytes(b'tampered')
    with self.assertRaisesRegex(ValueError,'hash_mismatch'):job.assemble(root)
 def test_duplicate_accept_and_explicit_worker_revision(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);self.complete(root);path=root/'result-1.json';old=path.read_bytes()
   with self.assertRaises(ValueError):job.accept(root,path)
   changed=job.load_json(path);changed['blocks'][0]['text']='Worker corrected source line';job.save_json(path,changed)
   job.revise(root,path)
   manifest=job.load_json(root/'manifest.json')
   self.assertEqual(len(manifest['history']),1)
   self.assertEqual((root/manifest['history'][0]['result']['path']).read_bytes(),old)
   self.assertEqual(job.assemble(root)[0]['blocks'][0]['text'],'Worker corrected source line')
   (root/manifest['history'][0]['result']['path']).write_bytes(b'changed')
   with self.assertRaises(ValueError):job.assemble(root)
 def test_image_assets_must_be_absolute_and_unchanged(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);manifest,evidence=self.fixture(root);assignment=job.assign(root,1,'worker',evidence)
   result=self.result(manifest,assignment);image=root/'figure.png';image.write_bytes(b'figure')
   figure={'id':'figure','question_id':'q1','kind':'image','bbox_mm':[12,35,30,20],'path':'figure.png','sha256':sha(b'figure'),'role':'figure'}
   result['blocks'].append(figure);path=root/'result.json';job.save_json(path,result)
   with self.assertRaisesRegex(ValueError,'absolute_path'):job.accept(root,path)
   figure['path']=str(image);image.write_bytes(b'changed');job.save_json(path,result)
   with self.assertRaisesRegex(ValueError,'image_asset_hash_mismatch'):job.accept(root,path)
   image.write_bytes(b'figure')
   with self.assertRaisesRegex(ValueError,'tikz_provenance_required'):job.accept(root,path)
 def test_prepare_pdf_keeps_each_source_page_size_and_hash(self):
  try:import fitz
  except ImportError:self.skipTest('Optional PyMuPDF is unavailable')
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);source=root/'two.pdf'
   with fitz.open() as document:
    document.new_page(width=612,height=792);document.new_page(width=595,height=842);document.save(source)
   original=source.read_bytes();manifest=job.prepare(source,root/'job')
   self.assertEqual(len(manifest['pages']),2)
   self.assertAlmostEqual(manifest['pages'][0]['width_mm'],215.9)
   self.assertAlmostEqual(manifest['pages'][1]['height_mm'],842*25.4/72)
   self.assertEqual(manifest['source']['sha256'],sha(original))
   self.assertEqual(source.read_bytes(),original)
   for page in manifest['pages']:self.assertEqual(job.digest(root/'job'/page['image']['path']),page['image']['sha256'])
 def test_single_image_requires_explicit_dpi(self):
  try:import fitz
  except ImportError:self.skipTest('Optional PyMuPDF is unavailable')
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);image=root/'source.png'
   with fitz.open() as document:
    page=document.new_page(width=100,height=200);page.get_pixmap().save(image)
   with self.assertRaisesRegex(ValueError,'explicit_dpi'):job.prepare(image,root/'missing-dpi')
   manifest=job.prepare(image,root/'job',dpi=100)
   self.assertEqual(manifest['pages'][0]['width_mm'],25.4)
   self.assertEqual(manifest['pages'][0]['height_mm'],50.8)

if __name__=='__main__':unittest.main()
