"""Fill the fixed pdf2HWP question grid."""
import copy,zipfile
from xml.etree import ElementTree as E
HP='{http://www.hancom.co.kr/hwpml/2011/paragraph}'
HC='{http://www.hancom.co.kr/hwpml/2011/core}'
HH='{http://www.hancom.co.kr/hwpml/2011/head}'
def mm(units):return units*25.4/7200

def build_grid_page(page,index,flow,root,profile,fields):
    if set(fields or {})!=set(profile['fields']):raise ValueError('template_fields_required')
    with zipfile.ZipFile(root/profile['source_package']) as z:source=E.fromstring(z.read('Contents/section0.xml'))
    p=copy.deepcopy(source[0 if index==0 else 1]);p.set('pageBreak',str(int(index>0)))
    if any(q['id'].lower() in ('cover','blank','header','footer','copyright') for q in page['questions']):
        raise ValueError('question_content_only')
    table=next(p.iter(HP+'tbl'));table_id=flow.uid();table.set('id',table_id)
    table.set('zOrder',str(index));table.set('repeatHeader','0')
    # The original header is inside this table; do not make a second header.
    if index==0:
        for key,spec in profile['fields'].items():
            value=fields[key]
            if not isinstance(value,str) or not value.strip() or '\n' in value:raise ValueError('invalid_template_field')
            nodes=[t for t in p.iter(HP+'t') if spec['source_text'] in (t.text or '')]
            if len(nodes)!=1:raise ValueError('template_field_ambiguous:'+key)
            nodes[0].text=nodes[0].text.replace(spec['source_text'],value)
    row_offset=4 if index==0 else 0
    rows=table.findall(HP+'tr');prototypes={}
    for row in rows[row_offset:]:
        for c in list(row):
            addr=c.find(HP+'cellAddr');prototypes[(int(addr.get('rowAddr'))-row_offset,int(addr.get('colAddr')))]=copy.deepcopy(c)
            row.remove(c)
    regions=sorted(page['regions'],key=lambda r:r['bbox_mm'][0]);records=[];merges=[]
    for column,region in enumerate(regions):
        col=(0 if column==0 else 2) if index==0 else column
        questions=sorted((q for q in page['questions'] if q['region_id']==region['id']),key=lambda q:q['bbox_mm'][1])
        n=len(questions)
        if not 1<=n<=6:raise ValueError('grid_column_requires_1_to_6_questions')
        boundaries=[(i*6)//n for i in range(n+1)]
        for question,start,end in zip(questions,boundaries,boundaries[1:]):
            cell=copy.deepcopy(prototypes[start,col]);last=prototypes[end-1,col]
            cell.set('name',question['id']);cell.set('editable','1')
            first_fill=next(f for f in flow.fills if f.get('id')==cell.get('borderFillIDRef'))
            last_fill=next(f for f in flow.fills if f.get('id')==last.get('borderFillIDRef'))
            merged_fill=copy.deepcopy(first_fill)
            merged_fill.find(HH+'bottomBorder').attrib.update(last_fill.find(HH+'bottomBorder').attrib)
            fill_id=str(max(int(f.get('id')) for f in flow.fills)+1)
            merged_fill.set('id',fill_id);flow.fills.append(merged_fill)
            cell.set('borderFillIDRef',fill_id)
            cell.find(HP+'cellSpan').set('rowSpan',str(end-start))
            height=sum(int(prototypes[r,col].find(HP+'cellSz').get('height')) for r in range(start,end))
            cell.find(HP+'cellSz').set('height',str(height))
            if len(regions)==1:
                # A single source column uses one full-width question column.
                physical_cols=sorted(c for row,c in prototypes if row==start)
                cell.find(HP+'cellSpan').set('colSpan',str(sum(
                    int(prototypes[start,c].find(HP+'cellSpan').get('colSpan')) for c in physical_cols)))
                width=sum(int(prototypes[start,c].find(HP+'cellSz').get('width')) for c in physical_cols)
                cell.find(HP+'cellSz').set('width',str(width))
            else:width=int(cell.find(HP+'cellSz').get('width'))
            margin=cell.find(HP+'cellMargin') if cell.get('hasMargin')=='1' else table.find(HP+'inMargin')
            pad={k:int(margin.get(k,'0')) for k in ('left','right','top','bottom')}
            usable=mm(width-pad['left']-pad['right'])
            sub=cell.find(HP+'subList');sub.clear();sub.attrib.update(textDirection='HORIZONTAL',lineWrap='BREAK',vertAlign='TOP',linkListIDRef='0',linkListNextIDRef='0',textWidth='0',textHeight='0',hasTextRef='0',hasNumRef='0',id='')
            before=len(flow.measurements)
            if page.get('role')=='answer_sheet':
                from restoration_answers import table_xml
                content=table_xml(flow,question,page['answer_rows'],usable)
            else:content=flow.contents(question['content'],question,usable)
            wrapped=E.fromstring('<root xmlns:hp="'+HP[1:-1]+'" xmlns:hc="'+HC[1:-1]+'">'+content+'</root>')
            sub.extend(list(wrapped));rows[row_offset+start].append(cell)
            binding={'block_id':question['id'],'kind':'question_cell','container_id':table_id,
                'cell_addr':[col,row_offset+start],'row_span':end-start,'column':column,
                'available_width_mm':usable,'available_height_mm':mm(height-pad['top']-pad['bottom']),
                'cell_height_mm':mm(height),'placement':'merged_template_cell'}
            records.append(binding);records.extend(flow.measurements[before:])
            merges.append({'question_id':question['id'],'column':column,'start_row':start,'row_span':end-start})
    for row in rows[row_offset:]:row[:]=sorted(row,key=lambda c:int(c.find(HP+'cellAddr').get('colAddr')))
    # Hancom rejects physical rows containing no starting cell. Compact those
    # rows after logical six-row merging, retaining exact cell heights and zones.
    active=[i for i,row in enumerate(rows) if len(row)]
    mapping={old:new for new,old in enumerate(active+[len(rows)])}
    for old,row in enumerate(rows):
        if not len(row):table.remove(row);continue
        for cell in row:
            addr=cell.find(HP+'cellAddr');span=cell.find(HP+'cellSpan')
            start=int(addr.get('rowAddr'));end=start+int(span.get('rowSpan'))
            addr.set('rowAddr',str(mapping[start]));span.set('rowSpan',str(mapping[end]-mapping[start]))
    for zone in table.iter(HP+'cellzone'):
        for key in ('startRowAddr','endRowAddr'):
            old=int(zone.get(key));zone.set(key,str(max(i for i,value in enumerate(active) if value<=old)))
    table.set('rowCnt',str(len(active)))
    for record in records:
        if record['kind']=='question_cell':
            start=record['cell_addr'][1];end=start+record['row_span']
            record['logical_row_span']=record['row_span'];record['logical_start_row']=start-row_offset
            record['cell_addr'][1]=mapping[start];record['row_span']=mapping[end]-mapping[start]
    # Cell paragraph line caches are regenerated by Hancom; header caches stay intact.
    for parent in p.iter():
        for child in list(parent):
            if child.tag==HP+'linesegarray' and parent is p:parent.remove(child)
    records.insert(0,{'block_id':f'page-{page["page_number"]}-grid','kind':'page_grid','container_id':table_id,
        'base_rows':6,'logical_columns':2,'height_mm':mm(int(table.find(HP+'sz').get('height'))),'merges':merges})
    return E.tostring(p,encoding='unicode'),records
