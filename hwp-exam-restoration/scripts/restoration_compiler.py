"""Compile explicit source coordinates into editable HWPX; native QA remains required."""
from __future__ import annotations
import copy
import hashlib
import io
import math
import os
from pathlib import Path
import shutil
import site
import sys
import tempfile
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

HP='http://www.hancom.co.kr/hwpml/2011/paragraph'
HH='http://www.hancom.co.kr/hwpml/2011/head'
HC='http://www.hancom.co.kr/hwpml/2011/core'

def _num(v,lo=0,hi=2000):
    if type(v) not in (int,float) or not math.isfinite(v) or not lo<=v<=hi: raise ValueError('invalid_restoration_measurement')
    return v

def _box(v):
    if not isinstance(v,list) or len(v)!=4: raise ValueError('bbox_mm_requires_xywh')
    x,y,w,h=map(_num,v)
    if w<=0 or h<=0: raise ValueError('positive_bbox_dimensions_required')
    return x,y,w,h

def _mm(v):return round(v*7200/25.4)
def _esc(v):return escape(str(v),{'"':'&quot;'})
def _digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def _studio_equation(latex,eqid):
    from runtime_paths import equation_compiler
    compile_equation=equation_compiler()
    import re
    # The converter spaces commas itself (exam typesetting), so a typed space right after a comma
    # is redundant rather than a lossy approximation: (3,\ 1) and (3, 1) print the same.
    latex=re.sub(r',\s*\\[ ,;:]\s*',', ',latex)  # \quad stays an error: it asks for a wide gap
    # HWP equations have one fraction size, so \dfrac and \tfrac print exactly like \frac.
    latex=re.sub(r'\\[dt]frac(?![A-Za-z])',r'\\frac',latex)
    if re.search(r'\\(?:i+nt|oint)(?![A-Za-z])',latex):latex=re.sub(r'\\[ ,;:]\s*(?=d[A-Za-z])','',latex)  # same for the dx of an integral
    result=compile_equation({'type':'equation','latex':latex},eqid)
    # Studio calls its parsed AST -> native-script output "fallback". This is
    # distinct from copying failed LaTeX into script; require the parsed receipt.
    payload=result.get('payload') or {}
    if (payload.get('ok') is not True or payload.get('status')!='parsed'
            or not result.get('selected_script') or result.get('approximations')):
        from restoration_equations import EquationError
        raise EquationError('equation_requires_successful_lossless_parse',
                            'equation contains approximations or lacks a successful parse',
                            diagnostics=payload.get('diagnostics'))
    return result

def build_hwpx(pages:list[dict],output:Path,automation_dir:Path|None=None,template_dir:Path|None=None,title:str|None=None,template_fields:dict|None=None)->dict:
    from runtime_paths import automation_root
    automation=Path(automation_dir) if automation_dir else automation_root()
    if not (automation/'scripts/hwpx_builder.py').is_file(): raise ValueError('hwp_automation_dependency_missing')
    sys.path.insert(0,str(automation/'scripts'))
    from hwpx_builder import HwpxBuilder
    if not isinstance(pages,list) or not pages:raise ValueError('pages_required')
    from figure_provenance import validate_figures
    figure_receipts=[r for p in pages for r in validate_figures(p)]
    profile=None;template_root=None
    if template_dir is not None:
        from exam_template import load_template,project_pages
        template_root,profile=load_template(template_dir)
        if not isinstance(title,str) or not title.strip() or any(c in title for c in '\r\n'):
            raise ValueError('template_requires_plain_first_page_title')
        pages=project_pages(pages,profile)
    first_size=pages[0]['size_mm']
    if len(first_size)!=2:raise ValueError('page_size_requires_width_height')
    width,height=[_num(x,1) for x in first_size]
    if any(abs(p['size_mm'][0]-first_size[0])>0.5 or abs(p['size_mm'][1]-first_size[1])>0.5 for p in pages):raise ValueError('mixed_page_sizes_not_supported')
    source_numbers=[p['page_number'] for p in pages]
    ordered=[p['page_number'] for p in pages if p.get('role')!='review_notes']  # the notes page sits before the answer sheet
    if (any(type(n) is not int or n<1 for n in source_numbers) or len(set(source_numbers))!=len(source_numbers)
            or ordered!=sorted(ordered)):
        raise ValueError('source_pages_must_be_positive_unique_and_ordered')
    output=Path(output).resolve()
    receipt={'status':'built_pending_native_validation','path':str(output),'pages':[],'equations':[],
             'figures':figure_receipts,
             'verification':{'native':'not_performed','visual_identity':'not_performed'},
             'limitations':['native_text_overflow_requires_verification','font_substitution_requires_verification','uniform_page_size_only','axis_aligned_rules_only']}
    b=HwpxBuilder(converter=object())
    if profile:
        b.template_base=template_root/'base';b.title=title
        receipt['template']={'name':profile['name'],'source_sha256':profile['source_sha256'],'profile_sha256':_digest(template_root/'profile.json'),'title':title,'page_chrome':'first_page_title_only'}
    with tempfile.TemporaryDirectory() as tmp:
        template=Path(tmp)/'template';shutil.copytree(b.template_base,template);b.template_base=template
        header_path=template/'Contents/header.xml';header_data=header_path.read_bytes()
        for _,pair in ET.iterparse(io.BytesIO(header_data),events=['start-ns']):ET.register_namespace(*pair)
        header=ET.fromstring(header_data)
        chars=header.find('.//{'+HH+'}charProperties');paras=header.find('.//{'+HH+'}paraProperties');fills=header.find('.//{'+HH+'}borderFills')
        # One dedicated style prevents the body anchor from creating extra pages.
        base_para=next(n for n in paras if n.get('id')==profile['body_para_id']) if profile else paras[0]
        para=copy.deepcopy(base_para);para_id=max(int(n.get('id')) for n in paras)+1;para.set('id',str(para_id))
        for n in para.iter():
            if n.tag=='{'+HH+'}align':n.set('horizontal','LEFT')
            if n.tag=='{'+HH+'}lineSpacing':n.attrib.update(type='FIXED',value='100',unit='HWPUNIT')
            if n.tag=='{'+HC+'}intent' or n.tag in ['{'+HC+'}'+k for k in ('left','right','prev','next')]:n.set('value','0')
            if n.tag=='{'+HH+'}breakSetting':n.attrib.update(keepWithNext='0',keepLines='0',pageBreakBefore='0')
        paras.append(para)
        # Anchor paragraphs may be tiny; visible content must use font/equation
        # metrics. Reusing FIXED 100 HWPUNIT (1pt) also crushed inline baselines.
        content_para=copy.deepcopy(para);content_para_id=para_id+1
        content_para.set('id',str(content_para_id))
        content_para.find('.//{'+HH+'}lineSpacing').attrib.update(type='PERCENT',value='100',unit='HWPUNIT')
        paras.append(content_para)
        styles={}
        def char(font,pt):
            if not isinstance(font,str) or not font or '\n' in font:raise ValueError('font_family_required')
            _num(pt,1,100)
            key=(font,pt)
            if key in styles:return styles[key]
            base_char=next(n for n in chars if n.get('id')==profile['body_char_id']) if profile else chars[0]
            node=copy.deepcopy(base_char);cid=max(int(n.get('id')) for n in chars)+1;node.attrib.update(id=str(cid),height=str(round(pt*100)),textColor='#000000')
            refs=node.find('{'+HH+'}fontRef')
            for face in header.findall('.//{'+HH+'}fontface'):
                existing=next((f for f in face if f.get('face')==font),None)
                if existing is None:
                    existing=copy.deepcopy(face[0]);existing.set('id',str(max(int(f.get('id')) for f in face)+1));existing.set('face',font);face.append(existing)
                face.set('fontCnt',str(len(face)))
                refs.set(face.get('lang').lower(),existing.get('id'))
            chars.append(node);styles[key]=cid;return cid
        anchor_char=char('함초롬바탕',1)
        def fill(black):
            ident=max(int(n.get('id')) for n in fills)+1
            node=copy.deepcopy(fills[0]);node.set('id',str(ident))
            for child in list(node):
                if child.tag.endswith('fillBrush'):node.remove(child)
                elif child.tag.endswith('Border') or child.tag.endswith('diagonal'):child.attrib.update(type='NONE',color='#000000')
            if black:
                brush=ET.SubElement(node,'{'+HC+'}fillBrush');ET.SubElement(brush,'{'+HC+'}winBrush',faceColor='#000000',hatchColor='#000000',alpha='0')
            fills.append(node);return ident
        transparent=fill(False);black_fill=fill(True)
        serial=10000
        def pos(box):
            x,y,w,h=box
            relative='PAPER' if profile else 'PAGE'
            return f'<hp:sz width="{_mm(w)}" height="{_mm(h)}" widthRelTo="ABSOLUTE" heightRelTo="ABSOLUTE" protect="1"/><hp:pos treatAsChar="0" affectLSpacing="0" flowWithText="0" allowOverlap="1" holdAnchorAndSO="1" vertRelTo="{relative}" horzRelTo="{relative}" vertAlign="TOP" horzAlign="LEFT" vertOffset="{_mm(y)}" horzOffset="{_mm(x)}"/><hp:outMargin left="0" right="0" top="0" bottom="0"/>'
        def table(box,text='',cid=anchor_char,black=False,runs_xml=None):
            nonlocal serial
            serial+=2;bid=black_fill if black else transparent;w,h=map(_mm,box[2:])
            inner=runs_xml if runs_xml is not None else f'<hp:run charPrIDRef="{cid}"><hp:t>{_esc(text)}</hp:t></hp:run>'
            return f'<hp:tbl id="{serial}" zOrder="{serial}" numberingType="TABLE" textWrap="IN_FRONT_OF_TEXT" textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" pageBreak="NONE" repeatHeader="0" rowCnt="1" colCnt="1" cellSpacing="0" borderFillIDRef="{bid}" noAdjust="1">'+pos(box)+f'<hp:inMargin left="0" right="0" top="0" bottom="0"/><hp:tr><hp:tc name="" header="0" hasMargin="1" protect="0" editable="1" dirty="0" borderFillIDRef="{bid}"><hp:subList id="" textDirection="HORIZONTAL" lineWrap="KEEP" vertAlign="TOP" linkListIDRef="0" linkListNextIDRef="0" textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0"><hp:p id="{serial+1}" paraPrIDRef="{para_id if black else content_para_id}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">{inner}</hp:p></hp:subList><hp:cellAddr colAddr="0" rowAddr="0"/><hp:cellSpan colSpan="1" rowSpan="1"/><hp:cellSz width="{w}" height="{h}"/><hp:cellMargin left="0" right="0" top="0" bottom="0"/></hp:tc></hp:tr></hp:tbl>'
        def equation_run(latex,eqid,pt,cid):
            converted=_studio_equation(latex,eqid);receipt['equations'].append(converted)
            _num(pt,1,100);b.add_equation(raw_hwpeqn=converted['selected_script'],font_size_pt=pt)
            fragment=b.elements.pop();start=fragment.index('<hp:equation');end=fragment.index('</hp:equation>')+len('</hp:equation>')
            # Preserve builder's inline positioning and native-measured size.
            # A source rectangle positions the container, never stretches glyphs.
            return f'<hp:run charPrIDRef="{cid}">'+fragment[start:end]+'</hp:run>'
        def rule_shape(box,stroke):
            # Never use a table as a stroke: Hancom enforces a minimum cell
            # width, turning a 0.12mm vertical rule into an approximately 1mm bar.
            nonlocal serial
            serial+=1
            x,y,w,h=box;uw,uh=_mm(w),_mm(h);thickness=_mm(stroke)
            vertical=w<h
            sx,sy=(uw//2,0) if vertical else (0,uh//2)
            ex,ey=(uw//2,uh) if vertical else (uw,uh//2)
            return (f'<hp:line id="{serial}" zOrder="{serial}" numberingType="PICTURE" '
                'textWrap="IN_FRONT_OF_TEXT" textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" '
                f'href="" groupLevel="0" instid="{serial}" isReverseHV="0">'
                f'<hp:offset x="0" y="0"/><hp:orgSz width="{uw}" height="{uh}"/>'
                f'<hp:curSz width="{uw}" height="{uh}"/><hp:flip horizontal="0" vertical="0"/>'
                f'<hp:rotationInfo angle="0" centerX="{uw//2}" centerY="{uh//2}" rotateimage="1"/>'
                '<hp:renderingInfo><hc:transMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
                '<hc:scaMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
                '<hc:rotMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/></hp:renderingInfo>'
                f'<hp:lineShape color="#000000" width="{thickness}" style="SOLID" endCap="FLAT" '
                'headStyle="NORMAL" tailStyle="NORMAL" headfill="0" tailfill="0" '
                'headSz="SMALL_SMALL" tailSz="SMALL_SMALL" outlineStyle="NORMAL" alpha="0"/>'
                f'<hc:startPt x="{sx}" y="{sy}"/><hc:endPt x="{ex}" y="{ey}"/>'
                +pos(box)+'<hp:shapeComment>원본 두께의 구분선</hp:shapeComment></hp:line>')
        from question_flow import QuestionFlow
        flow=QuestionFlow(header,b,char,equation_run,pos,content_para,transparent,profile=profile)
        for page_index,page in enumerate(pages):
            objects=[];xml=[]
            if profile and profile.get('question_layout')=='merged_grid':
                from pdf2hwp_grid import build_grid_page
                fragment,objects=build_grid_page(page,page_index,flow,template_root,profile,template_fields)
                b.elements.append(fragment)
                receipt['pages'].append({'page_number':page['page_number'],'objects':objects})
                receipt['template']['page_chrome']='source_merged_grid'
                receipt['template']['fields']=template_fields
                receipt['template']['layout']='six_row_merged_question_cells'
                continue
            if profile and page_index==0 and profile.get('page_chrome')!='source_document_shell':
                title_box=[profile['side_margins_mm'][0],profile['title_top_mm'],width-sum(profile['side_margins_mm']),profile['title_height_mm']]
                ident=flow.uid();cid=char(profile['title_font'],profile['title_font_pt'])
                p=flow.paragraph(f'<hp:run charPrIDRef="{cid}"><hp:t>{_esc(title)}</hp:t></hp:run>',flow.style({'align':'CENTER','line_spacing_pct':100}),cid)
                rows='<hp:tr>'+flow.cell(p,title_box[2],title_box[3])+'</hp:tr>'
                xml.append(flow.table(rows,title_box[2],title_box[3],ident,pos(title_box)))
                objects.append({'block_id':'template-title','kind':'text','bbox_mm':title_box,'container_id':ident})
            if profile:
                top=profile.get('first_rule_top_mm',profile['first_body_top_mm']) if page_index==0 else profile.get('rule_top_mm',profile['body_top_mm'])
                stroke=0.12
                rule_box=[width/2-stroke/2,top,stroke,height-profile['body_bottom_margin_mm']-top]
                xml.append(rule_shape(rule_box,stroke))
                objects.append({'block_id':'template-column-rule','kind':'rule','bbox_mm':rule_box,'component':'native_rule','shape_id':str(serial),'stroke_mm':stroke})
            for block in page['blocks']:
                kind=block['kind'];bbox=_box(block['bbox_mm']);x,y,w,h=bbox
                if x+w>width+1e-6 or y+h>height+1e-6:raise ValueError('block_outside_page')
                base={'block_id':block['id'],'kind':kind,'bbox_mm':list(bbox)}
                if kind=='text':
                    if '\n' in block['text'] or '\r' in block['text']:raise ValueError('one_source_line_per_text_block')
                    xml.append(table(bbox,block['text'],char(block['font_family'],block['font_pt'])));objects.append({**base,'container_id':str(serial)})
                elif kind in {'line','equation'}:
                    cid=char(block.get('font_family','함초롬바탕'),block['font_pt'])
                    runs=block['runs'] if kind=='line' else [{'kind':'equation','latex':block['latex']}]
                    if not runs:raise ValueError('line_runs_required')
                    rendered=[]
                    for index,run in enumerate(runs):
                        if run['kind']=='text':
                            from restoration_contract import _text
                            _text(run['text'],'run.text')
                            rendered.append(f'<hp:run charPrIDRef="{cid}"><hp:t>{_esc(run["text"])}</hp:t></hp:run>')
                        elif run['kind']=='equation':
                            rendered.append(equation_run(run['latex'],str(block['id'])+f'-{index}',block['font_pt'],cid))
                        else:raise ValueError('unsupported_inline_run')
                    xml.append(table(bbox,cid=cid,runs_xml=''.join(rendered)));objects.append({**base,'container_id':str(serial),'placement':'inline_native_metrics'})
                elif kind=='image':
                    path=Path(block['path']).resolve(strict=True)
                    if block['role']!='figure' or _digest(path)!=block['sha256']:raise ValueError('figure_provenance_mismatch')
                    if not path.read_bytes().startswith(b'\x89PNG\r\n\x1a\n'):raise ValueError('compiler_requires_png_figure')
                    b.add_picture(path,width_hwpunit=_mm(w),height_hwpunit=_mm(h));fragment=b.elements.pop();start=fragment.index('<hp:pic');end=fragment.index('</hp:pic>')+len('</hp:pic>');pic=fragment[start:end]
                    import re
                    pic=re.sub(r'<hp:sz\b[^>]*/>.*?<hp:outMargin\b[^>]*/>',lambda _:pos(bbox),pic,flags=re.S)
                    pic=pic.replace('textWrap="TOP_AND_BOTTOM"','textWrap="IN_FRONT_OF_TEXT"');xml.append(pic);objects.append(base)
                elif kind in {'box','rule'}:
                    stroke=_num(block['stroke_mm'],0.01,5)
                    if kind=='rule':
                        if min(w,h)>stroke+0.001:raise ValueError('only_axis_aligned_rules_supported')
                        if abs(min(w,h)-stroke)>0.001:raise ValueError('rule_bbox_must_match_stroke')
                        segments=[bbox]
                    else:
                        if 2*stroke>=min(w,h):raise ValueError('box_stroke_exceeds_dimensions')
                        title=block['title'];titlebox=_box(block['title_bbox_mm']) if title else None
                        segments=[(x,y,stroke,h),(x+w-stroke,y,stroke,h),(x,y+h-stroke,w,stroke)]
                        if titlebox and titlebox[1]<=y+stroke and titlebox[1]+titlebox[3]>=y:
                            tx,ty,tw,th=titlebox
                            if tx<x or tx+tw>x+w:raise ValueError('title_gap_outside_box')
                            if tx>x:segments.append((x,y,tx-x,stroke))
                            if tx+tw<x+w:segments.append((tx+tw,y,x+w-tx-tw,stroke))
                        else:segments.append((x,y,w,stroke))
                    for seg in segments:
                        xml.append(rule_shape(seg,stroke))
                        objects.append({**base,'bbox_mm':list(seg),'component':'native_rule','shape_id':str(serial),'stroke_mm':stroke})
                    if kind=='box' and block['title']:
                        if '\n' in block['title']:raise ValueError('box_title_requires_single_line')
                        tx,ty,tw,th=titlebox
                        if tx+tw>width or ty+th>height:raise ValueError('title_outside_page')
                        xml.append(table(titlebox,block['title'],char(block['font_family'],block['font_pt'])));objects.append({**base,'bbox_mm':list(titlebox),'component':'editable_title'})
                else:raise ValueError('unsupported_block_kind:'+str(kind))
            if page.get('version')==2:
                for question in page['questions']:
                    fragment,records=flow.question(question)
                    xml.append(fragment);objects.extend(records)
            serial+=1
            b.elements.append(f'<hp:p id="{serial}" paraPrIDRef="{para_id}" styleIDRef="0" pageBreak="{int(page_index>0)}" columnBreak="0" merged="0"><hp:run charPrIDRef="{anchor_char}">'+''.join(xml)+'<hp:t/></hp:run></hp:p>')
            receipt['pages'].append({'page_number':page['page_number'],'objects':objects})
        chars.set('itemCnt',str(len(chars)));paras.set('itemCnt',str(len(paras)));fills.set('itemCnt',str(len(fills)))
        header_path.write_bytes(ET.tostring(header,encoding='utf-8',xml_declaration=True))
        original_secpr=b._get_secpr
        def secpr():
            import re
            if profile and profile.get('question_layout')=='merged_grid':return ''
            if profile:
                raw=(template_root/'section-properties.xml').read_text(encoding='utf-8')
                # Native colLine follows the tiny anchor paragraph height. Draw
                # the template rule explicitly across the question area instead.
                return re.sub(r'<hp:colLine\b[^>]*/>','<hp:colLine type="NONE" width="0.12 mm" color="#000000"/>',raw)
            raw=original_secpr();raw=re.sub(r'<hp:pagePr\b.*?</hp:pagePr>',f'<hp:pagePr landscape="WIDELY" width="{_mm(width)}" height="{_mm(height)}" gutterType="LEFT_ONLY"><hp:margin header="0" footer="0" gutter="0" left="0" right="0" top="0" bottom="0"/></hp:pagePr>',raw,flags=re.S)
            return raw
        b._get_secpr=secpr
        candidate=Path(tmp)/'compiled.hwpx';b.save(candidate)
        if profile and profile.get('page_chrome')=='source_document_shell':
            from source_template import preserve_source_shell
            receipt['template']['preservation']=preserve_source_shell(candidate,template_root,profile,template_fields)
            receipt['template']['page_chrome']='source_document_shell'
        from inspect_hwpx import inspect_hwpx
        report=inspect_hwpx(candidate)
        if not report['ok']:raise ValueError('compiled_package_invalid:'+str(report['issues']))
        output.parent.mkdir(parents=True,exist_ok=True)
        fd,staged=tempfile.mkstemp(dir=output.parent,suffix='.hwpx');os.close(fd)
        try:shutil.copyfile(candidate,staged);os.replace(staged,output)
        finally:
            if os.path.exists(staged):os.unlink(staged)
    receipt['sha256']=_digest(output);return receipt
