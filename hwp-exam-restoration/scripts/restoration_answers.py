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
    state=job.load_json(Path(root)/'mcp/state.json');rows=[];references=[];labels=set();review_questions={}
    for page in pages:
        n=page['page_number'];s=state['pages'][str(n)]
        if not s.get('answers'):raise ValueError('submit_answers_for_page: '+str(n))
        answers=job.load_json(job._artifact(root,s['answers']))
        if answers['reading_sha256']!=s['reading']['sha256']:raise ValueError('answers_stale_for_page: '+str(n))
        value=job.load_json(job._artifact(root,s['reading']))
        reading_questions={q['id']:q for q in value['questions']}
        rendered_questions={q['id']:q for q in page['questions']}
        for row in answers['rows']:
            key=(row['kind'],row['label'])
            if key in labels:raise ValueError('duplicate_printed_answer_number: '+row['label'])
            labels.add(key);rows.append(row)
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
    return pages+[page]

def table_xml(flow,q,rows,width):
    """Editable HWP table: four number/answer pairs, then full-width written rows."""
    fill=flow.border(('left','right','top','bottom'),0.12)
    choices=[r for r in rows if r['kind']=='choice'];written=[r for r in rows if r['kind']=='written']
    cells=[];heights=[];rindex=0
    def paragraph(text,align='LEFT',pt=10):
        block={'id':'answer-'+flow.uid(),'runs':_runs(text,1),'font_pt':pt,'align':align,'line_spacing_pct':120}
        inner,cid=flow.runs(block['runs'],q,block)
        return flow.paragraph(inner,flow.style(block),cid)
    title=paragraph('정답표','CENTER',16)
    def add(items,height):
        nonlocal rindex
        col=0;xml=[]
        for text,w,span,align in items:
            xml.append(flow.cell(paragraph(text,align),w,height,col,rindex,cs=span,fill=fill,padding=(2,2,2,2)))
            col+=span
        cells.append('<hp:tr>'+''.join(xml)+'</hp:tr>');heights.append(height);rindex+=1
    if choices:
        add([('객관식',width,8,'CENTER')],9)
        for start in range(0,len(choices),4):
            group=choices[start:start+4];items=[]
            for index in range(4):
                row=group[index] if index<len(group) else {'label':'','answer':''}
                items.extend([(row['label'] or ' ',width/16,1,'CENTER'),(row['answer'] or ' ',width*3/16,1,'CENTER')])
            # Fractions and short values fit comfortably; longer answers wrap.
            add(items,14 if any(len(r['answer'])>24 for r in group) else 11)
    if written:
        add([('서답형',width,8,'CENTER')],9)
        for row in written:
            height=max(15,7+5*((len(row['answer'])+65)//66))
            add([(row['label'],width/4,2,'CENTER'),(row['answer'],width*3/4,6,'LEFT')],height)
    height=sum(heights)
    if height>225:raise ValueError('answer_sheet_exceeds_one_page: shorten_answer_values_without_omitting_subparts')
    ident=flow.uid();table=flow.table(''.join(cells),width,height,ident,flow.inline_position(width,height),rowcount=rindex,colcount=8)
    flow.measurements.append({'block_id':q['id']+'/answer-table','kind':'logical_box',
                              'container_id':ident,'available_width_mm':width})
    cid=flow.char(q['font_family'],10)
    return title+flow.paragraph(f'<hp:run charPrIDRef="{cid}">{table}</hp:run>',flow.style({'before_mm':5}),cid)

NOTE_COLUMNS=(('쪽',0.11,'CENTER'),('문항',0.13,'CENTER'),('구분',0.13,'CENTER'),('검수 내용',0.63,'LEFT'))
NOTE_CHARS_PER_LINE=40  # 10pt Hangul in the 63% content column of a one-column body (measured in Hangul output)

def note_row_height(text):
    return max(9,4+5*((len(text)+NOTE_CHARS_PER_LINE-1)//NOTE_CHARS_PER_LINE))

def notes_table_xml(flow,q,rows,width):
    """Editable 검수 노트 table: page, printed question, kind and the reviewer's observation."""
    fill=flow.border(('left','right','top','bottom'),0.12)
    def paragraph(text,align='LEFT',pt=10):
        block={'id':'note-'+flow.uid(),'runs':[{'kind':'text','text':text}],'font_pt':pt,'align':align,'line_spacing_pct':130}
        inner,cid=flow.runs(block['runs'],q,block)
        return flow.paragraph(inner,flow.style(block),cid)
    cells=[];heights=[]
    for index,values in enumerate([[c[0] for c in NOTE_COLUMNS]]+[[r['page'],r['question'],r['kind'],r['text']] for r in rows]):
        height=9 if index==0 else note_row_height(values[3])
        xml=[flow.cell(paragraph(value,'CENTER' if index==0 else align),width*share,height,col,index,fill=fill,padding=(2,1.5,2,1.5))
             for col,(value,(_,share,align)) in enumerate(zip(values,NOTE_COLUMNS))]
        cells.append('<hp:tr>'+''.join(xml)+'</hp:tr>');heights.append(height)
    height=sum(heights)
    if height>225:raise ValueError('review_notes_exceed_one_page')
    ident=flow.uid();table=flow.table(''.join(cells),width,height,ident,flow.inline_position(width,height),rowcount=len(cells),colcount=len(NOTE_COLUMNS))
    flow.measurements.append({'block_id':q['id']+'/notes-table','kind':'logical_box','container_id':ident,'available_width_mm':width})
    cid=flow.char(q['font_family'],10)
    return (paragraph('검수 노트','CENTER',16)
            +paragraph('자동 검수에서 수정하지 않고 기록만 한 항목입니다. 원본과 대조해 직접 확인하세요.','LEFT',9)
            +flow.paragraph(f'<hp:run charPrIDRef="{cid}">{table}</hp:run>',flow.style({'before_mm':3}),cid))
