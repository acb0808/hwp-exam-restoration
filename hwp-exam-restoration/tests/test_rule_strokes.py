import json,sys,tempfile,unittest,zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from restoration_compiler import build_hwpx
HP='{http://www.hancom.co.kr/hwpml/2011/paragraph}'
HC='{http://www.hancom.co.kr/hwpml/2011/core}'

class RuleStrokeTests(unittest.TestCase):
    def test_box_and_divider_use_equal_native_strokes_not_filled_tables(self):
        p=json.loads((Path(__file__).resolve().parents[1]/'examples/inline-page.json').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'thin.hwpx';r=build_hwpx([p],out)
            with zipfile.ZipFile(out) as z:root=ET.fromstring(z.read('Contents/section0.xml'))
            lines=list(root.iter(HP+'line'))
            self.assertEqual(len(lines),6) # divider plus five title-gap box segments
            self.assertEqual({n.find(HP+'lineShape').get('width') for n in lines},{'34'})
            self.assertTrue(all(n.find(HC+'startPt') is not None and n.find(HC+'endPt') is not None for n in lines))
            strokes=[o for o in r['pages'][0]['objects'] if o.get('component')=='native_rule']
            self.assertEqual(len(strokes),6)
            # No empty, black-filled table may serve as a rule.
            self.assertTrue(all(''.join(t.itertext()).strip() for t in root.iter(HP+'tbl')))

if __name__=='__main__':unittest.main()
