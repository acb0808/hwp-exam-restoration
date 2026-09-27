"""Reuse a native HWP-derived template and place accepted question regions in it."""
from pathlib import Path
import copy,json,hashlib

def load_template(root):
    root=Path(root).resolve(strict=True)
    profile=json.loads((root/'profile.json').read_text(encoding='utf-8'))
    for relative,expected in profile['files'].items():
        p=(root/relative).resolve(strict=True)
        if not p.is_relative_to(root) or hashlib.sha256(p.read_bytes()).hexdigest()!=expected:
            raise ValueError('template_asset_hash_mismatch')
    return root,profile

def project_pages(pages,profile):
    """Deterministic template projection; never infer or rewrite question content."""
    output=[];paper=profile['paper_mm'];left,right=profile['side_margins_mm'];gap=profile['column_gap_mm']
    colwidth=(paper[0]-left-right-gap)/2
    for output_index,source in enumerate(pages):
        if source.get('version')!=2:raise ValueError('template_requires_question_flow_v2')
        page=copy.deepcopy(source);page['size_mm']=paper;page['blocks']=[]
        regions=sorted(source['regions'],key=lambda r:r['bbox_mm'][0])
        if len(regions) not in (1,2):raise ValueError('reference_template_requires_one_or_two_columns')
        top=profile['first_body_top_mm'] if output_index==0 else profile['body_top_mm']
        bottom=paper[1]-profile['body_bottom_margin_mm']
        source_top=min(q['bbox_mm'][1] for q in source['questions'])
        source_bottom=max(q['bbox_mm'][1]+q['bbox_mm'][3] for q in source['questions'])
        sy=(bottom-top)/(source_bottom-source_top)
        for index,region in enumerate(regions):
            rx,ry,rw,rh=region['bbox_mm']
            sx=((paper[0]-left-right) if len(regions)==1 else colwidth)/rw
            tx=left if len(regions)==1 else left+index*(colwidth+gap)
            qs=[q for q in page['questions'] if q['region_id']==region['id']]
            first=min(q['bbox_mm'][1] for q in qs)
            for q in qs:
                x,y,w,h=q['bbox_mm'];mapped_y=top+(y-source_top)*sy
                end_y=top+(y+h-source_top)*sy
                # Header/instruction space before the first problem is not restored.
                if y==first:mapped_y=top
                q['bbox_mm']=[tx+(x-rx)*sx,mapped_y,w*sx,end_y-mapped_y]
                q['font_family']=profile['body_font'];q['font_pt']=profile['body_font_pt']
                def style_blocks(blocks,inside_box=False):
                    for b in blocks:
                        b['font_pt']=profile['box_font_pt'] if inside_box else profile['body_font_pt']
                        b['font_family']=profile['box_font'] if inside_box else profile['body_font']
                        b['line_spacing_pct']=profile['line_spacing_pct']
                        if b['kind']=='paragraph':b.setdefault('align',profile.get('body_align','JUSTIFY'))
                        for key in ('left_mm','right_mm','before_mm','after_mm'):
                            if key in b:b[key]*=sx
                        if 'tab_stops_mm' in b:b['tab_stops_mm']=[v*sx for v in b['tab_stops_mm']]
                        if 'figure' in b:
                            f=b['figure'];f['size_mm']=[v*sx for v in f['size_mm']];f['offset_mm']=[v*sx for v in f['offset_mm']]
                        if b['kind']=='box':
                            b['stroke_mm']=profile['box_stroke_mm']
                            b['padding_mm']=profile['box_padding_mm']
                            style_blocks(b['content'],True)
                style_blocks(q['content'])
        output.append(page)
    return output
