"""Strict semantic flow blocks: no line coordinates or nested line containers."""
from restoration_contract import _fail,_list,_number,_string,_text,_hash
COMMON={'before_mm','after_mm','left_mm','right_mm','font_pt','font_family','line_spacing_pct','align'}
class ContentFieldsError(ValueError):
    """Carry exact field differences without changing contract acceptance."""
    def __init__(self,block,required,allowed):
        self.block=block
        self.missing_fields=sorted(required-set(block))
        self.unknown_fields=sorted(set(block)-allowed,key=str)
        super().__init__('content: unknown/missing fields; '
            f'missing_fields={self.missing_fields!r}; unknown_fields={self.unknown_fields!r}; '
            'line bbox coordinates forbidden')

def validate_content(q,ids):
    _number(q['font_pt'],'font_pt',True);_string(q['font_family'],'font_family')
    def runs(values,allow_empty=False):
        _list(values,'runs',not allow_empty)
        for r in values:
            if not isinstance(r,dict):_fail('run','expected object')
            kind=r.get('kind')
            expected={'text':{'kind','text'},'boxed_text':{'kind','text'},'equation':{'kind','latex'},'break':{'kind'}}.get(kind)
            if expected is None or set(r)!=expected:_fail('run','expected text/equation/break with exact fields')
            if kind=='text':_text(r['text'],'text',True)
            elif kind=='boxed_text':
                _text(r['text'],'boxed_text')
                if len(r['text'])>20 or any(c in r['text'] for c in '[]\\'):_fail('boxed_text','1..20 plain-text characters required')
            elif kind=='equation':
                _string(r['latex'],'latex')
                if any(c in r['latex'] for c in '$\r\n'):_fail('latex','delimiter-free math required')
    def walk(content,width,in_box=False):
        _list(content,'content',True)
        for b in content:
            if not isinstance(b,dict):_fail('content','expected object')
            kind=b.get('kind');req={'paragraph':{'runs'},'choices':{'columns','rows'},'box':{'title','content'}}.get(kind)
            if req is None or (in_box and kind!='paragraph'):_fail('content','unsupported semantic block')
            required={'kind','id'}|req;allowed=required|COMMON|({'figure'} if kind=='paragraph' else {'stroke_mm','padding_mm'} if kind=='box' else {'tab_stops_mm'})
            if not required<=set(b) or not set(b)<=allowed:raise ContentFieldsError(b,required,allowed)
            _string(b['id'],'content.id')
            if b['id'] in ids:_fail('content.id','duplicate ID')
            ids.add(b['id'])
            for k in COMMON-{'align','font_family'}:
                if k in b:_number(b[k],k,k in {'font_pt','line_spacing_pct'})
            if 'font_family' in b:_string(b['font_family'],'font_family')
            if b.get('align','LEFT') not in {'LEFT','CENTER','RIGHT','JUSTIFY'}:_fail('align','unsupported')
            available=width-b.get('left_mm',0)-b.get('right_mm',0)
            if available<=0:_fail('content','indents consume full width')
            if kind=='paragraph':
                # Only a real, structurally validated figure may carry an empty
                # anchor paragraph. Its provenance is still checked at acceptance.
                runs(b['runs'],allow_empty='figure' in b)
                if 'figure' in b:
                    f=b['figure']
                    required={'path','sha256','size_mm','offset_mm'}
                    if not isinstance(f,dict) or not required<=set(f) or not set(f)<=required|{'tikz'}:_fail('figure','invalid fields')
                    _string(f['path'],'path');_hash(f['sha256'],'sha256')
                    for k in ('size_mm','offset_mm'):
                        if not isinstance(f[k],list) or len(f[k])!=2:_fail(k,'expected pair')
                        for v in f[k]:_number(v,k,k=='size_mm')
                    if f['offset_mm'][0]+f['size_mm'][0]>width+0.001:_fail('figure','outside question width')
            elif kind=='choices':
                if type(b['columns']) is not int or not 1<=b['columns']<=5:_fail('columns','expected 1..5')
                if 'tab_stops_mm' in b:
                    stops=b['tab_stops_mm'];_list(stops,'tab_stops_mm')
                    if len(stops)!=b['columns']-1:_fail('tab_stops_mm','one stop per subsequent column')
                    for stop in stops:_number(stop,'tab_stops_mm',True)
                    if stops!=sorted(set(stops)) or any(s>=available for s in stops):_fail('tab_stops_mm','ascending stops inside content width required')
                _list(b['rows'],'rows',True)
                for row in b['rows']:
                    _list(row,'row',True)
                    if len(row)>b['columns']:_fail('row','too many choices')
                    for r in row:runs(r)
            else:
                _text(b['title'],'title',True);_number(b.get('stroke_mm',0.12),'stroke_mm',True)
                pad=b.get('padding_mm',[3,2,3,2])
                if not isinstance(pad,list) or len(pad)!=4:_fail('padding','expected four values')
                for v in pad:_number(v,'padding')
                if available-pad[0]-pad[2]<=0:_fail('padding','box has no content width')
                walk(b['content'],available-pad[0]-pad[2],True)
    walk(q['content'],q['bbox_mm'][2])

def figures(page):
    """Uniform figure provenance traversal for both contract generations."""
    for _,figure in figure_items(page):yield figure

def figure_items(page):
    """Preserve question ownership while walking figures inside semantic boxes."""
    def walk(blocks):
        for block in blocks:
            if block.get('figure'):yield block['figure']
            if block.get('kind')=='box':yield from walk(block.get('content',[]))
    for b in page.get('blocks',[]):
        if b.get('kind')=='image':yield b['question_id'],b
    for q in page.get('questions',[]):
        for figure in walk(q.get('content',[])):yield q['id'],figure
