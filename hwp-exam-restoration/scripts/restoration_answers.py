"""Question-bound answers, kept separate from question and diagram caches."""
import copy,re
from pathlib import Path
from restoration_markdown import _runs

class AnswerEquationError(ValueError):
    def __init__(self,errors):
        self.errors=errors
        super().__init__('invalid_answer_equations')

def split_answers(text):
    """Keep the legacy pair API, replacing answer lines with blank source lines."""
    clean,answers,_,errors=split_answers_detailed(text)
    if errors:raise ValueError(errors[0]['message'])
    return clean,answers


def answer_error(qid,field,line,message):
    return {'question_id':qid,'field':field,'line':line,'source_line':line,
            'location':(qid+'.' if qid else '')+field,'message':message,
            'hint':'원본 reading.md의 해당 줄과 정답 필드를 수정하세요. 다른 문항이나 수학적 의미를 바꾸지 마세요.'}


def split_answers_detailed(text):
    """Collect closed-block shape errors; stop at an uncertain block boundary.

    Returned partial answers are diagnostic data only. Any errors reject the
    entire submission. Blank replacements keep every original source position.
    """
    lines=text.splitlines();clean=list(lines);answers={};locations={};errors=[]
    qid=None;i=0;block=False;unsafe_block=False;labels=set()
    fields={'번호':'label','정답':'answer','근거':'reason'}
    while i<len(lines):
        line=lines[i].strip()
        heading=re.fullmatch(r'### ([A-Za-z0-9][A-Za-z0-9_-]*)(?: \?)?',line)
        if heading and not block:qid=heading.group(1)
        elif line in ('## left','## right') and not block:qid=None
        if line=='::: answer' and not block:
            start=i;row={};source={'answer_block':start+1};i+=1;ambiguous=False
            while i<len(lines) and lines[i].strip()!=':::':
                if lines[i].lstrip().startswith((':::','#')):
                    ambiguous=True;break
                item=re.fullmatch(r'(번호|정답|근거):\s*(.+)',lines[i].strip())
                field=fields[item.group(1)] if item else 'answer_block'
                if not item or field in row:
                    errors.append(answer_error(qid,field,i+1,'answer_block_requires_번호_정답_근거_once'))
                else:row[field]=item.group(2);source[field]=i+1
                i+=1
            if i==len(lines) or ambiguous:
                errors.append(answer_error(qid,'answer_block',start+1,'unclosed_answer_block: '+str(qid)))
                clean[start:]=['']*(len(lines)-start)
                if qid is not None:locations.setdefault(qid,source)
                break
            clean[start:i+1]=['']*(i-start+1)
            if qid is None or qid in answers:
                errors.append(answer_error(qid,'answer_block',start+1,'answer_requires_unique_question_heading'))
            else:
                answers[qid]=row;locations[qid]=source
                for field in ('label','answer','reason'):
                    if field not in row:
                        errors.append(answer_error(qid,field,start+1,'complete_answer_block_required: '+qid))
                if 'answer' in row and re.search(r'확인\s*필요|미확정|모름|미생성|^\?$',row['answer']):
                    errors.append(answer_error(qid,'answer',source['answer'],'unresolved_answer: '+qid))
                for field,limit in [('label',30),('answer',1000),('reason',2000)]:
                    if field in row and len(row[field])>limit:
                        errors.append(answer_error(qid,field,source[field],'answer_entry_too_long: '+qid))
                if 'label' in row:
                    if row['label'] in labels:
                        errors.append(answer_error(qid,'label',source['label'],'distinct_answer_labels_required: '+row['label']))
                    labels.add(row['label'])
        else:
            if line.startswith(':::'):
                if line==':::' and not unsafe_block:block=False
                elif block:unsafe_block=True
                elif not block:block=True
        i+=1
    return '\n'.join(clean),answers,locations,errors

def validate_answers(value,answers):
    from restoration_compiler import _studio_equation
    from restoration_author_help import equation_guidance
    if set(answers)!={q['id'] for q in value['questions']}:
        raise ValueError('one_answer_for_each_question_required')
    labels=set();rows=[];errors=[]
    for q in value['questions']:
        a=answers[q['id']];label=a['label']
        if label in labels:raise ValueError('distinct_answer_labels_required: '+label)
        labels.add(label)
        # Picture choices (①~⑤ drawn inside one figure) have no choices block but a numbered answer.
        kind=('choice' if any(b['kind']=='choices' for b in q['content'])
              or re.match(r'^[①②③④⑤](?:\s|$)',a['answer'].strip()) else 'written')
        if kind=='choice' and not re.match(r'^[①②③④⑤](?:\s|$)',a['answer']):
            raise ValueError('choice_answer_requires_printed_choice_number: '+q['id'])
        for field in ('answer','reason'):
            try:runs=_runs(a[field],1)
            except (ValueError,KeyError,TypeError) as exc:
                errors.append({'question_id':q['id'],'field':field,'location':q['id']+'.'+field,
                    'text':a[field],'message':str(exc),
                    'hint':'이 문항의 해당 answer 필드에서 $ 짝과 빈 수식을 확인하세요. 수식 의미를 삭제하거나 다른 문항을 재작성하지 마세요.'})
                continue
            # The reason is reviewer evidence, not part of the printed answer page.
            # Keep its Markdown delimiters valid without requiring HWP conversion.
            if field=='reason':continue
            for index,run in enumerate(runs):
                if run['kind']!='equation':continue
                latex=run['latex'];location=f"{q['id']}.{field}[{index}]"
                try:_studio_equation(latex,location)
                except (ValueError,KeyError,TypeError) as exc:
                    errors.append({'question_id':q['id'],'field':field,'location':location,
                        'latex':latex,'message':str(exc),**equation_guidance(latex,exc)})
        rows.append({'question_id':q['id'],'source_page':value['page'],'kind':kind,**a})
    if errors:raise AnswerEquationError(errors)
    return rows

def answer_semantics(value):
    """Ignore only choice row wrapping; retain choice order, math and figures."""
    if isinstance(value,list):return [answer_semantics(item) for item in value]
    if not isinstance(value,dict):return value
    result={key:answer_semantics(item) for key,item in value.items()}
    if value.get('kind')=='choices':
        result.pop('columns',None)
        result['rows']=[[cell for row in result['rows'] for cell in row]]
    return result

def append_answer_page(root,manifest,pages):
    if not manifest.get('include_answers'):return pages
    import restoration_job as job
    from restoration_single import fingerprint
    state=job.load_json(Path(root)/'mcp/state.json');rows=[];references=[];review_questions={}
    for page in pages:
        n=page['page_number'];s=state['pages'][str(n)]
        if not s.get('answers'):raise ValueError('submit_answers_for_page: '+str(n))
        answers=job.load_json(job._artifact(root,s['answers']))
        if answers['reading_sha256']!=s['reading']['sha256']:raise ValueError('answers_stale_for_page: '+str(n))
        value=job.load_json(job._artifact(root,s['reading']))
        reading_questions={q['id']:q for q in value['questions']}
        rendered_questions={q['id']:q for q in page['questions']}
        for row in answers['rows']:
            # A number printed again on a later page (a workbook's next unit) is kept as printed: the table
            # starts a new group there (answer_groups). Numbers within one page are distinct (validate_answers).
            rows.append(row)
            qid=row['question_id'];question=reading_questions[qid]
            from restoration_markdown import reading_to_markdown
            review_questions[f'{n}/{qid}']={
                'source_page':n,'question_id':qid,
                'question':reading_to_markdown({**value,'questions':[question]}),
                'label':row['label'],'kind':row['kind'],
                'answer':row['answer'],'reason':row['reason'],
                'revision':fingerprint({'page':n,'reading':answer_semantics(question),
                                         'rendered':answer_semantics(rendered_questions[qid]),'answer':row})}
        references.append({'page':n,'reading':value,'answers':answers['rows'],
                           'question_revision':fingerprint(page['questions'])})
    if not rows:raise ValueError('answer_sheet_requires_answers')
    from restoration_markdown import reading_to_markdown
    reference=['# 정답 검수 자료','문제 원본은 앞서 본 해당 쪽을 재사용하세요. 아래 후보 정답을 독립 계산·대입으로 검증하세요. 근거가 있다는 이유만으로 통과하지 마세요.']
    if not manifest.get('include_figures',True):
        # Text-only job: the questions below carry no figures, so their lengths and angles are read off the source page.
        reference.append('이 작업은 글과 수식만 복원했습니다. 아래 문항 글에는 그림이 없으므로, 그림이 필요한 문항은 앞서 본 원본 쪽의 그림에서 값을 읽어 검산하세요. '
                         '글만으로 풀 수 없다는 것은 오류가 아닙니다.')
    for item in references:
        reference+=['\n## 원본 '+str(item['page'])+'쪽',
                    '검수 대상 문항 판본: '+item['question_revision'],reading_to_markdown(item['reading'])]
        for row in item['answers']:
            reference += [f"{row['question_id']} / {row['label']} / 후보: {row['answer']} / 검산 근거: {row['reason']}"]
    digest=fingerprint(references);out=Path(root)/'mcp/answer-references';out.mkdir(exist_ok=True)
    path=out/(digest+'.md');text='\n'.join(reference)
    if path.exists() and path.read_text(encoding='utf8')!=text:raise ValueError('answer_reference_changed')
    if not path.exists():path.write_text(text,encoding='utf8')
    page=copy.deepcopy(pages[0]);w,h=page['size_mm'];box=[10,15,w-20,h-30]
    page.update(page_number=len(manifest['pages'])+1,role='answer_sheet',worker_id='mechanical-answer-sheet',
        assignment_id='mechanical-answer-sheet',blocks=[],issues=[],answer_rows=rows,
        answer_review_questions=review_questions,
        answer_reference={'path':str(path.resolve()),'sha256':job.digest(path)},
        regions=[{'id':'answers','bbox_mm':box}],
        questions=[{'id':'answer-sheet','region_id':'answers','bbox_mm':box,'font_family':'함초롬바탕','font_pt':10,
                    'content':[{'id':'answer-title','kind':'paragraph','runs':[{'kind':'text','text':'정답표'}]}]}])
    # A table longer than one page continues on pages of its own. The first page stays the one reviewed answer
    # sheet (it carries every row and the reference); the others only print their part of the same table.
    count=len(answer_parts(rows,1));more=[]
    if count>1:page['answer_part']=[1,count]
    for index in range(2,count+1):
        extra=copy.deepcopy(page)
        extra.update(page_number=page['page_number']+index-1,role='answer_sheet_more',answer_part=[index,count])
        for key in ('answer_review_questions','answer_reference'):extra.pop(key,None)
        more.append(extra)
    return pages+[page]+more

ANSWER_PAGE_MM=225   # table height that fits the answer page under its title
PACK_UNITS=14        # half-width characters that fit one line of an answer cell of the four-pair grid
PACK_LABEL_UNITS=4   # and of its number cell (1-1, 12-3); a longer number such as 서답형 1 keeps its own row
KIND_HEADS=('객관식','서답형')
CONTINUED_ROLES=('answer_sheet_more',)   # pages that only print a further part of the answer table

def _units(text):
    """Rough printed width of an answer in half-width characters: Hangul counts two, LaTeX markup is not printed."""
    plain=re.sub(r'[${}^_\s]','',re.sub(r'\\[A-Za-z]+',' x',text))
    return sum(2 if ord(c)>=0x1100 else 1 for c in plain)

def answer_groups(rows):
    """Rows split where the printed numbering starts again (the next unit of a workbook): [(title or None, rows)].
    One group, without a title, when no number is printed twice. An exam whose written questions count from 1
    again after its last choice question (객관식 1…, then 서답형 1…) is one group too: the two lists tell them apart."""
    kinds=[row['kind'] for row in rows]
    choices_first='written' not in kinds or 'choice' not in kinds[kinds.index('written'):]
    groups=[[]];seen=set()
    for row in rows:
        key=(row['kind'],row['label']) if choices_first else row['label']
        if key in seen:groups.append([]);seen=set()
        seen.add(key);groups[-1].append(row)
    if len(groups)==1:return [(None,rows)]
    titled=[]
    for group in groups:
        first,last=group[0].get('source_page'),group[-1].get('source_page')
        titled.append((f'원본 {first}쪽' if first==last else f'원본 {first}~{last}쪽',group))
    return titled

def table_rows(rows,width,compact=False):
    """Rows of the answer table as ([(text, width, column span, align)], height): four number/answer pairs per
    choice row, then one full-width row per written answer. With compact, short written answers share rows in
    the same four-pair grid. table_xml prints them, answer_table_check reads them back."""
    out=[]
    def grid(group):
        for start in range(0,len(group),4):
            four=group[start:start+4];items=[]
            for index in range(4):
                row=four[index] if index<len(four) else {'label':'','answer':''}
                items.extend([(row['label'] or ' ',width/16,1,'CENTER'),(row['answer'] or ' ',width*3/16,1,'CENTER')])
            # Fractions and short values fit comfortably; longer answers wrap.
            out.append((items,14 if any(len(r['answer'])>24 for r in four) else 11))
    for title,group in answer_groups(rows):
        choices=[r for r in group if r['kind']=='choice'];written=[r for r in group if r['kind']=='written']
        if title:out.append(([(title,width,8,'CENTER')],9))
        if choices:
            out.append(([('객관식',width,8,'CENTER')],9));grid(choices)
        if written:
            out.append(([('서답형',width,8,'CENTER')],9));short=[]
            for row in written:
                if compact and _units(row['answer'])<=PACK_UNITS and _units(row['label'])<=PACK_LABEL_UNITS:
                    short.append(row);continue
                grid(short);short=[]  # source order is kept: a long answer ends the run of short ones before it
                height=max(15,7+5*((len(row['answer'])+65)//66))
                out.append(([(row['label'],width/4,2,'CENTER'),(row['answer'],width*3/4,6,'LEFT')],height))
            grid(short)
    return out

def answer_parts(rows,width):
    """The answer table as one list of rows per page. The usual table when it fits one page. Otherwise short
    written answers share rows like choices, and what still does not fit continues on further pages, each
    starting with a heading. Shortening answers cannot make a long table fit, so nothing is asked of producers."""
    table=table_rows(rows,width)
    if sum(height for _,height in table)<=ANSWER_PAGE_MM:return [table]
    parts=[[]];used=0;head=group=None
    for row in table_rows(rows,width,compact=True):
        items,height=row;heading=len(items)==1
        if parts[-1] and used+height>ANSWER_PAGE_MM:
            carried=[]
            while parts[-1] and len(parts[-1][-1][0])==1:carried.insert(0,parts[-1].pop())  # no heading left at a page end
            # A page that starts inside a list repeats its headings: the numbering group, then 객관식 or 서답형.
            starts_group=heading and items[0][0] not in KIND_HEADS
            if group and not starts_group and not any(r is group for r in carried):carried.insert(0,group)
            if head and not heading and not any(len(r[0])==1 and r[0][0][0] in KIND_HEADS for r in carried):carried.append(head)
            parts.append(carried);used=sum(h for _,h in carried)
        parts[-1].append(row);used+=height
        if heading and items[0][0] in KIND_HEADS:head=row
        elif heading:group=row;head=None
    return [part for part in parts if part]

EQUATION_MARK='\x00'

def _printed(cell):
    """A cell's text with one mark per equation, and its equations' scripts, in reading order."""
    HP='{http://www.hancom.co.kr/hwpml/2011/paragraph}';text=[];scripts=[]
    for node in cell.iter():
        if node.tag==HP+'t':text.append(''.join(node.itertext()))
        elif node.tag==HP+'equation':
            text.append(EQUATION_MARK);scripts.append(' '.join((node.findtext(HP+'script') or '').split()))
    return re.sub(r'\s+','',''.join(text)),scripts

def _answer_tables(hwpx):
    """Cells of every answer table of the document (one per answer page), in page order."""
    import zipfile
    from xml.etree import ElementTree as ET
    HP='{http://www.hancom.co.kr/hwpml/2011/paragraph}'
    with zipfile.ZipFile(hwpx) as archive:
        names=sorted((n for n in archive.namelist() if re.fullmatch(r'Contents/section\d+\.xml',n)),key=lambda n:int(re.search(r'\d+',n).group()))
        roots=[ET.fromstring(archive.read(n)) for n in names]
    found=[]
    for root in roots:
        for table in root.iter(HP+'tbl'):
            cells=[c for row in table.findall(HP+'tr') for c in row.findall(HP+'tc')]
            # Every part starts with a heading: 객관식, 서답형 or the page range of a numbering group.
            if table.get('colCnt')=='8' and cells and (_printed(cells[0])[0] in KIND_HEADS or _printed(cells[0])[0].startswith('원본')):found.append(cells)
    return found

def answer_table_check(built_hwpx,native_hwpx,rows):
    """Compare the answer table Hangul saved with the answer rows, without a model: every cell holds the
    number or answer of its row (equations by their script, as the engine wrote them), and no equation is wider
    than its cell. A verified table lets the reviewer skip reading the table image and only recompute answers.
    A table printed over several pages is read as one."""
    HP='{http://www.hancom.co.kr/hwpml/2011/paragraph}'
    parts=answer_parts(rows,1);problems=[]
    native=_answer_tables(native_hwpx);built=_answer_tables(built_hwpx)
    expected=[text for part in parts for items,_ in part for text,*_ in items]
    if not len(native)==len(built)==len(parts):return {'verified':False,'problems':['answer_table_not_found']}
    native=[cell for table in native for cell in table];built=[cell for table in built for cell in table]
    if not len(native)==len(built)==len(expected):
        return {'verified':False,'problems':[f'cell_count: answers {len(expected)}, built {len(built)}, printed {len(native)}']}
    for index,(text,was,now) in enumerate(zip(expected,built,native)):
        wanted=re.sub(r'\s+','',''.join(EQUATION_MARK if r['kind']=='equation' else r.get('text','') for r in _runs(text,1)))
        printed,scripts=_printed(now)
        if printed!=wanted:problems.append(f'cell {index+1}: text differs from the answer block ({text[:30]})')
        elif scripts!=_printed(was)[1]:problems.append(f'cell {index+1}: an equation changed in the export ({text[:30]})')
        size=now.find(HP+'cellSz');margin=now.find(HP+'cellMargin')
        room=int(size.get('width'))-sum(int(margin.get(k,'0')) for k in ('left','right')) if size is not None and margin is not None else None
        for eq in now.iter(HP+'equation'):
            box=eq.find(HP+'sz')
            if room is None or box is None or not 0<int(box.get('width','0'))<=room+2:
                problems.append(f'cell {index+1}: an equation is wider than its cell ({text[:30]})');break
    return {'verified':not problems,'cells':len(expected),'problems':problems[:5]}

def table_xml(flow,q,rows,width,part=None):
    """Editable HWP table: four number/answer pairs, then full-width written rows. part=[n,total] prints the
    n-th page of a table that continues over several answer pages (answer_parts)."""
    fill=flow.border(('left','right','top','bottom'),0.12)
    cells=[];heights=[];rindex=0
    def paragraph(text,align='LEFT',pt=10):
        block={'id':'answer-'+flow.uid(),'runs':_runs(text,1),'font_pt':pt,'align':align,'line_spacing_pct':120}
        inner,cid=flow.runs(block['runs'],q,block)
        return flow.paragraph(inner,flow.style(block),cid)
    parts=answer_parts(rows,width)
    if len(parts)!=(part[1] if part else 1):raise ValueError('answer_sheet_parts_changed')
    title=paragraph('정답표'+(' (%d/%d)'%tuple(part) if part else ''),'CENTER',16)
    def add(items,height):
        nonlocal rindex
        col=0;xml=[]
        for text,w,span,align in items:
            xml.append(flow.cell(paragraph(text,align),w,height,col,rindex,cs=span,fill=fill,padding=(2,2,2,2)))
            col+=span
        cells.append('<hp:tr>'+''.join(xml)+'</hp:tr>');heights.append(height);rindex+=1
    for items,height in parts[part[0]-1 if part else 0]:add(items,height)
    height=sum(heights)
    ident=flow.uid();table=flow.table(''.join(cells),width,height,ident,flow.inline_position(width,height),rowcount=rindex,colcount=8)
    flow.measurements.append({'block_id':q['id']+'/answer-table','kind':'logical_box',
                              'container_id':ident,'available_width_mm':width})
    cid=flow.char(q['font_family'],10)
    return title+flow.paragraph(f'<hp:run charPrIDRef="{cid}">{table}</hp:run>',flow.style({'before_mm':5}),cid)

NOTE_COLUMNS=(('쪽',0.11,'CENTER'),('문항',0.13,'CENTER'),('구분',0.13,'CENTER'),('검수 내용',0.63,'LEFT'))
NOTE_CHARS_PER_LINE=40  # 10pt Hangul in the 63% content column of a one-column body (measured in Hangul output)

def note_row_height(text,image=None):
    return max(9,4+5*((len(text)+NOTE_CHARS_PER_LINE-1)//NOTE_CHARS_PER_LINE))+(image['size_mm'][1]+4 if image else 0)

def notes_table_xml(flow,q,rows,width,part=None):
    """Editable 검수 노트 table: page, printed question, kind and the reviewer's observation."""
    fill=flow.border(('left','right','top','bottom'),0.12)
    def paragraph(text,align='LEFT',pt=10):
        block={'id':'note-'+flow.uid(),'runs':[{'kind':'text','text':text}],'font_pt':pt,'align':align,'line_spacing_pct':130}
        inner,cid=flow.runs(block['runs'],q,block)
        return flow.paragraph(inner,flow.style(block),cid)
    cells=[];heights=[]
    for index,values in enumerate([[c[0] for c in NOTE_COLUMNS]]+[[r['page'],r['question'],r['kind'],r['text']] for r in rows]):
        image=rows[index-1].get('image') if index else None
        height=9 if index==0 else note_row_height(values[3],image)
        xml=[]
        for col,(value,(_,share,align)) in enumerate(zip(values,NOTE_COLUMNS)):
            body=paragraph(value,'CENTER' if index==0 else align)
            if image and col==len(NOTE_COLUMNS)-1:
                # What to look at, under the observation: left the source, right the output (restoration_single.note_image).
                cid=flow.char(q['font_family'],10)
                body+=flow.paragraph(f'<hp:run charPrIDRef="{cid}">'+flow.inline_picture(image)+'</hp:run>',flow.style({'before_mm':1,'line_spacing_pct':100}),cid)
            xml.append(flow.cell(body,width*share,height,col,index,fill=fill,padding=(2,1.5,2,1.5)))
        cells.append('<hp:tr>'+''.join(xml)+'</hp:tr>');heights.append(height)
    height=sum(heights)
    if height>225:raise ValueError('review_notes_exceed_one_page')
    ident=flow.uid();table=flow.table(''.join(cells),width,height,ident,flow.inline_position(width,height),rowcount=len(cells),colcount=len(NOTE_COLUMNS))
    flow.measurements.append({'block_id':q['id']+'/notes-table','kind':'logical_box','container_id':ident,'available_width_mm':width})
    cid=flow.char(q['font_family'],10)
    return (paragraph('검수 노트'+(' (%d/%d)'%tuple(part) if part else ''),'CENTER',16)
            +paragraph('자동 검수에서 수정하지 않고 기록만 한 항목입니다. 원본과 대조해 직접 확인하세요.','LEFT',9)
            +flow.paragraph(f'<hp:run charPrIDRef="{cid}">{table}</hp:run>',flow.style({'before_mm':3}),cid))
