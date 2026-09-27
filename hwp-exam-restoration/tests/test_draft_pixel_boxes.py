"""Explicit source observations can use pixels without changing strict mm output."""
import copy,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import restoration_job as job
from restoration_draft import compile_draft
import test_compact_draft

class DraftPixelTests(unittest.TestCase):
    def test_page_pixel_observations_compile_and_preserve_content(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            root,manifest,assignment,page,draft=test_compact_draft.CompactDraftTests().fixture(Path(tmp))
            baseline=root/'baseline.json'
            self.assertEqual(compile_draft(root,1,draft,baseline)['status'],'compiled')
            record=manifest['pages'][0]
            with Image.open(root/record['image']['path']) as image:w,h=image.size
            scale=[record['width_mm']/w,record['height_mm']/h]
            for item in draft['regions']+draft['questions']:
                item['bbox_px']=[v/scale[i%2] for i,v in enumerate(item.pop('bbox_mm'))]
                item['coordinate_space']='page'
            before=copy.deepcopy(draft);output=root/'pixel.json'
            result=compile_draft(root,1,draft,output)
            self.assertEqual(result['status'],'compiled',result)
            self.assertEqual(before,draft)
            actual=job.load_json(output)
            for original,expanded in zip(page['regions']+page['questions'],actual['regions']+actual['questions']):
                for a,b in zip(original['bbox_mm'],expanded['bbox_mm']):self.assertAlmostEqual(a,b,places=8)
                self.assertNotIn('bbox_px',expanded)
                self.assertNotIn('coordinate_space',expanded)
            self.assertEqual(actual['questions'][0]['content'],job.load_json(baseline)['questions'][0]['content'])
            self.assertFalse(result['accepted'])

    def test_pixel_box_requires_explicit_space_and_rejects_ambiguous_boxes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,_,_,_,draft=test_compact_draft.CompactDraftTests().fixture(Path(tmp))
            for mode in ('missing_space','two_boxes','outside','invented_space'):
                value=copy.deepcopy(draft);item=value['questions'][0]
                if mode!='two_boxes':item.pop('bbox_mm')
                item['bbox_px']=[0,0,10,10]
                if mode!='missing_space':item['coordinate_space']='page'
                if mode=='outside':item['bbox_px']=[-1,0,10,10]
                if mode=='invented_space':item['coordinate_space']='unregistered.png'
                result=compile_draft(root,1,value,root/(mode+'.json'))
                self.assertEqual(result['status'],'failed',mode)
                self.assertFalse((root/(mode+'.json')).exists())

if __name__=='__main__':unittest.main()
