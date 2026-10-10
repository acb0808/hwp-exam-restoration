"""Reject native-measured content that exceeds its source allocation.

This check is necessary, not sufficient: PDF/source visual comparison is still
required, particularly horizontal text extent, font substitution and baselines.
"""
from pathlib import Path
from xml.etree import ElementTree as ET
import zipfile
HP='{http://www.hancom.co.kr/hwpml/2011/paragraph}'

def content_height(cell,lines):
    """(height in HWPUNIT, continued) of a question cell's content.
    A cell pushed past the page bottom is continued on the next page and its line positions start again from 0
    there, so the parts are added up and the cell is reported as continued. A figure floats over the space its
    anchor paragraph reserves below itself, which no line records, so its bottom edge counts as content too."""
    top=max(int(n.get('vertpos','0'))+int(n.get('vertsize','0')) for n in lines)
    sub=cell.find(HP+'subList');total=part=0;previous=-1;continued=False
    for paragraph in (sub.findall(HP+'p') if sub is not None else []):
        first=None
        for n in paragraph.findall(HP+'linesegarray/'+HP+'lineseg'):
            position=int(n.get('vertpos','0'))
            if position<previous:total+=part;part=0;continued=True
            if first is None:first=position
            previous=position;part=max(part,position+int(n.get('vertsize','0')))
        for picture in paragraph.iter(HP+'pic'):
            size,place=picture.find(HP+'sz'),picture.find(HP+'pos')
            if first is None or size is None or place is None or place.get('treatAsChar')=='1':continue
            part=max(part,first+int(place.get('vertOffset','0'))+int(size.get('height','0')))
    return max(top,total+part),continued

def check_native_fit(build_receipt,native_hwpx):
    with zipfile.ZipFile(native_hwpx) as archive:
        roots=[ET.fromstring(archive.read(n)) for n in archive.namelist()
               if n.startswith('Contents/section') and n.endswith('.xml')]
    tables={}
    for root in roots:
        for table in root.iter(HP+'tbl'):tables.setdefault(table.get('id'),[]).append(table)
    issues=[];checked=0;measured=[]
    for page in build_receipt['pages']:
        for block in page['objects']:
            if block['kind'] in ('question_cell','page_grid'):
                context={'page':page['page_number'],'block_id':block['block_id']}
                matches=tables.get(block['container_id'],[])
                if len(matches)!=1:
                    issues.append({**context,'code':'native_grid_missing_or_ambiguous'});continue
                table=matches[0]
                if block['kind']=='page_grid':
                    if int(table.find(HP+'sz').get('height'))>round(block['height_mm']*7200/25.4)+2:
                        issues.append({**context,'code':'native_grid_expanded'})
                    continue
                cells=[c for row in table.findall(HP+'tr') for c in row.findall(HP+'tc') if [int(c.find(HP+'cellAddr').get(k)) for k in ('colAddr','rowAddr')]==block['cell_addr']]
                if len(cells)!=1:
                    issues.append({**context,'code':'native_question_cell_missing'});continue
                checked+=1;cell=cells[0]
                if int(cell.find(HP+'cellSpan').get('rowSpan'))!=block['row_span']:
                    issues.append({**context,'code':'native_question_merge_changed'})
                if int(cell.find(HP+'cellSz').get('height'))>round(block['cell_height_mm']*7200/25.4)+2:
                    issues.append({**context,'code':'question_cell_expanded'})
                lines=list(cell.iter(HP+'lineseg'))
                if not lines:issues.append({**context,'code':'native_line_metrics_missing'})
                else:
                    content,continued=content_height(cell,lines)
                    figures=sum(int(size.get('height','0')) for picture in cell.iter(HP+'pic') for size in picture.findall(HP+'sz'))
                    measured.append({**context,'content_mm':round(content*25.4/7200,1),'available_mm':round(block['available_height_mm'],1),
                                     'cell_mm':round(block['cell_height_mm'],2),'figures_mm':round(figures*25.4/7200,1),'continued':continued,
                                     'rows':block.get('logical_row_span',block['row_span']),'column':block.get('column')})
                    if continued or content>round(block['available_height_mm']*7200/25.4)+2:
                        issues.append({**context,'code':'question_cell_content_overflow','actual_mm':measured[-1]['content_mm'],
                                       'available_mm':measured[-1]['available_mm']})
                allowed={b['container_id'] for b in page['objects'] if b['kind']=='logical_box'}
                if not {t.get('id') for t in cell.iter(HP+'tbl')}<=allowed:
                    issues.append({**context,'code':'unexpected_nested_layout_container'})
                for eq in cell.iter(HP+'equation'):
                    size=eq.find(HP+'sz')
                    if size is None or not 0<int(size.get('width','0'))<=round(block['available_width_mm']*7200/25.4)+2:
                        issues.append({**context,'code':'question_cell_equation_width'})
                continue
            if block['kind'] not in ('text','line','equation','question','logical_box'):continue
            context={'page':page['page_number'],'block_id':block['block_id']}
            matches=tables.get(block.get('container_id'),[])
            if len(matches)!=1:
                issues.append({**context,'code':'native_container_missing_or_ambiguous'});continue
            checked+=1;table=matches[0]
            if block['kind']=='logical_box':
                size=table.find(HP+'sz')
                if size is None or int(size.get('width','0'))>round(block['available_width_mm']*7200/25.4)+2:
                    issues.append({**context,'code':'logical_box_width_exceeds_question'})
                continue
            width,height=block['bbox_mm'][2:]
            # HWPUNIT rounding tolerance only; no percentage-based relaxation.
            tolerance=2
            lines=list(table.iter(HP+'lineseg'))
            if not lines:
                issues.append({**context,'code':'native_line_metrics_missing'});continue
            measured_height=max(int(n.get('vertpos','0'))+int(n.get('vertsize','0')) for n in lines)
            if measured_height>round(height*7200/25.4)+tolerance:
                issues.append({**context,'code':'content_height_exceeds_source_box',
                               'actual_mm':measured_height*25.4/7200,'available_mm':height})
            if block['kind']=='question':
                size=table.find(HP+'sz')
                if size is None or int(size.get('height','0'))>round(height*7200/25.4)+tolerance:
                    issues.append({**context,'code':'question_expanded_beyond_allocation'})
                # A real question flow may contain semantic boxes, never a table per line.
                child_ids={t.get('id') for t in table.iter(HP+'tbl') if t is not table}
                allowed={b['container_id'] for b in page['objects'] if b['kind']=='logical_box'}
                if not child_ids<=allowed:issues.append({**context,'code':'unexpected_nested_layout_container'})
            for eq in table.iter(HP+'equation'):
                size=eq.find(HP+'sz')
                if size is None or int(size.get('width','0'))<=0:
                    issues.append({**context,'code':'native_equation_metrics_missing'});continue
                if int(size.get('width'))>round(width*7200/25.4)+tolerance:
                    issues.append({**context,'code':'equation_width_exceeds_source_box',
                                   'actual_mm':int(size.get('width'))*25.4/7200,'available_mm':width})
    return {'status':'failed' if issues else 'measured_pass_needs_visual_review',
            'checked_containers':checked,'issues':issues,'question_cells':measured,'visual_status':'not_verified',
            'limitations':['horizontal_mixed_text_extent_requires_visual_review','font_and_source_baselines_require_visual_review']}
