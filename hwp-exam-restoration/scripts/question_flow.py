"""Question-level containers with ordinary paragraphs, tabs and logical boxes."""
from __future__ import annotations
import copy, hashlib, re
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape
HP='http://www.hancom.co.kr/hwpml/2011/paragraph'
HH='http://www.hancom.co.kr/hwpml/2011/head'
HC='http://www.hancom.co.kr/hwpml/2011/core'
def mm(v):return round(v*7200/25.4)

class QuestionFlow:
    def __init__(self,header,builder,char,equation,position,base_para,transparent,profile=None):
        self.header=header;self.b=builder;self.char=char;self.equation=equation
        self.position=position;self.base_para=base_para;self.transparent=transparent
        self.serial=10000000
        self.paras=header.find('.//{'+HH+'}paraProperties')
        self.tabs=header.find('.//{'+HH+'}tabProperties')
        self.fills=header.find('.//{'+HH+'}borderFills')
        self.measurements=[]
        self.profile=profile or {}
        self.box_chars={}
    def uid(self):
        self.serial+=1;return str(self.serial)
    def style(self,options,tabs=(),tiny=False):
        p=copy.deepcopy(self.base_para);ident=str(max(int(n.get('id')) for n in self.paras)+1);p.set('id',ident)
        p.set('condense','0');p.set('snapToGrid','0')
        if tabs:
            tabid=str(max(int(t.get('id')) for t in self.tabs)+1)
            tab=ET.SubElement(self.tabs,'{'+HH+'}tabPr',id=tabid,autoTabLeft='0',autoTabRight='0')
            for stop in tabs:ET.SubElement(tab,'{'+HH+'}tabItem',pos=str(mm(stop)),type='LEFT',leader='NONE')
            self.tabs.set('itemCnt',str(len(self.tabs)));p.set('tabPrIDRef',tabid)
        for n in p.iter():
            name=n.tag.split('}')[-1]
            if name=='align':n.set('horizontal',options.get('align','LEFT'))
            elif name=='lineSpacing':n.attrib.update(type='FIXED' if tiny else 'PERCENT',value='100' if tiny else str(options.get('line_spacing_pct',150)),unit='HWPUNIT')
            elif name in ('left','right','prev','next','intent'):
                field={'left':'left_mm','right':'right_mm','prev':'before_mm','next':'after_mm','intent':'indent_mm'}[name]
                n.set('value',str(mm(options.get(field,0))))
            elif name=='breakSetting':n.attrib.update(lineWrap='BREAK',keepLines='0',keepWithNext='0')
        self.paras.append(p);return ident
    def paragraph(self,inner,style,cid):
        return f'<hp:p id="{self.uid()}" paraPrIDRef="{style}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">{inner or f"<hp:run charPrIDRef=\"{cid}\"><hp:t/></hp:run>"}</hp:p>'
    def runs(self,runs,question,block):
        pt=block.get('font_pt',question['font_pt']);cid=self.char(block.get('font_family',question['font_family']),pt);out=[]
        for i,r in enumerate(runs):
            if r['kind']=='equation':out.append(self.equation(r['latex'],question['id']+'-'+block['id']+'-'+self.uid(),self.profile.get('equation_font_pt',pt),cid))
            elif r['kind']=='break':out.append(f'<hp:run charPrIDRef="{cid}"><hp:t><hp:lineBreak/></hp:t></hp:run>')
            elif r['kind']=='boxed_text':out.append(f'<hp:run charPrIDRef="{self.box_char(cid)}"><hp:t>{escape(r["text"])}</hp:t></hp:run>')
            else:out.append(f'<hp:run charPrIDRef="{cid}"><hp:t>{escape(r["text"])}</hp:t></hp:run>')
        return ''.join(out),cid
    def box_char(self,cid):
        if cid not in self.box_chars:
            chars=self.header.find('.//{'+HH+'}charProperties')
            base=next(c for c in chars if c.get('id')==str(cid))
            node=copy.deepcopy(base)
            ident=str(max(int(c.get('id')) for c in chars)+1)
            node.set('id',ident)
            node.set('borderFillIDRef',self.border(['left','right','top','bottom'],0.12))
            chars.append(node)
            self.box_chars[cid]=ident
        return self.box_chars[cid]
    def cell(self,content,w,h,col=0,row=0,cs=1,rs=1,fill=None,padding=(0,0,0,0)):
        fill=self.transparent if fill is None else fill;l,t,r,b=map(mm,padding)
        return (f'<hp:tc name="" header="0" hasMargin="1" protect="0" editable="1" dirty="0" borderFillIDRef="{fill}">'
            '<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="TOP" linkListIDRef="0" linkListNextIDRef="0" textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">'+content+'</hp:subList>'
            f'<hp:cellAddr colAddr="{col}" rowAddr="{row}"/><hp:cellSpan colSpan="{cs}" rowSpan="{rs}"/><hp:cellSz width="{mm(w)}" height="{mm(h)}"/>'
            f'<hp:cellMargin left="{l}" right="{r}" top="{t}" bottom="{b}"/></hp:tc>')
    def table(self,rows,w,h,ident,position,rowcount=1,colcount=1):
        return (f'<hp:tbl id="{ident}" zOrder="{ident}" numberingType="TABLE" textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" pageBreak="NONE" repeatHeader="0" rowCnt="{rowcount}" colCnt="{colcount}" cellSpacing="0" borderFillIDRef="{self.transparent}" noAdjust="0">'
            +position+'<hp:inMargin left="0" right="0" top="0" bottom="0"/>'+rows+'</hp:tbl>')
    def inline_position(self,w,h):
        return (f'<hp:sz width="{mm(w)}" height="{mm(h)}" widthRelTo="ABSOLUTE" heightRelTo="ABSOLUTE" protect="0"/>'
            '<hp:pos treatAsChar="1" affectLSpacing="1" flowWithText="1" allowOverlap="0" holdAnchorAndSO="1" vertRelTo="PARA" horzRelTo="COLUMN" vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/><hp:outMargin left="0" right="0" top="0" bottom="0"/>')
    def border(self,sides,stroke):
        n=copy.deepcopy(self.fills[0]);ident=str(max(int(e.get('id')) for e in self.fills)+1);n.set('id',ident)
        for c in list(n):
            name=c.tag.split('}')[-1]
            if name=='fillBrush':n.remove(c)
            elif name.endswith('Border') or name=='diagonal':c.attrib.update(type='SOLID' if name.removesuffix('Border') in sides else 'NONE',width=f'{stroke} mm',color='#000000')
        self.fills.append(n);return ident
    def picture(self,figure):
        p=Path(figure['path']);assert hashlib.sha256(p.read_bytes()).hexdigest()==figure['sha256'],'figure_hash_changed'
        w,h=figure['size_mm'];x,y=figure['offset_mm']
        self.b.add_picture(p,width_hwpunit=mm(w),height_hwpunit=mm(h));raw=self.b.elements.pop()
        raw=raw[raw.index('<hp:pic'):raw.index('</hp:pic>')+len('</hp:pic>')]
        position=(f'<hp:sz width="{mm(w)}" height="{mm(h)}" widthRelTo="ABSOLUTE" heightRelTo="ABSOLUTE" protect="1"/>'
            f'<hp:pos treatAsChar="0" affectLSpacing="0" flowWithText="1" allowOverlap="1" holdAnchorAndSO="1" vertRelTo="PARA" horzRelTo="COLUMN" vertAlign="TOP" horzAlign="LEFT" vertOffset="{mm(y)}" horzOffset="{mm(x)}"/><hp:outMargin left="0" right="0" top="0" bottom="0"/>')
        return re.sub(r'<hp:sz\b[^>]*/>.*?<hp:outMargin\b[^>]*/>',lambda _:position,raw,flags=re.S).replace('textWrap="TOP_AND_BOTTOM"','textWrap="IN_FRONT_OF_TEXT"')
    def contents(self,blocks,q,width):
        out=[]
        for block in blocks:
            kind=block['kind']
            if kind=='paragraph':
                inner,cid=self.runs(block['runs'],q,block)
                figure=block.get('figure')
                if not figure:
                    out.append(self.paragraph(inner,self.style(block),cid))
                else:
                    has_text=any(r['kind'] in ('equation','boxed_text') or (r['kind']=='text' and r['text'].strip()) for r in block['runs'])
                    if has_text:out.append(self.paragraph(inner,self.style(dict(block,after_mm=0)),cid))
                    # A floating image needs a dedicated anchor and space for its
                    # full extent. Never position it over the preceding text.
                    options=dict(block,before_mm=0 if has_text else block.get('before_mm',0),
                        after_mm=max(block.get('after_mm',0),figure['offset_mm'][1]+figure['size_mm'][1]+1))
                    picture=f'<hp:run charPrIDRef="{cid}">'+self.picture(figure)+'</hp:run>'
                    out.append(self.paragraph(picture,self.style(options),cid))
            elif kind=='choices':
                span=(width-block.get('left_mm',0)-block.get('right_mm',0))/block['columns']
                stops=[block.get('left_mm',0)+v for v in block.get('tab_stops_mm',[span*i for i in range(1,block['columns'])])]
                for index,row in enumerate(block['rows']):
                    chunks=[];cid=self.char(q['font_family'],block.get('font_pt',q['font_pt']))
                    for j,runs in enumerate(row):
                        if j:chunks.append(f'<hp:run charPrIDRef="{cid}"><hp:t><hp:tab width="0" leader="0" type="1"/></hp:t></hp:run>')
                        chunks.append(self.runs(runs,q,block)[0])
                    opts=dict(block,before_mm=block.get('before_mm',0) if index==0 else 0,after_mm=block.get('after_mm',0) if index==len(block['rows'])-1 else 0)
                    out.append(self.paragraph(''.join(chunks),self.style(opts,stops),cid))
            elif kind=='box':
                w=width-block.get('left_mm',0)-block.get('right_mm',0);ident=self.uid();pad=block.get('padding_mm',[3,2,3,2]);stroke=block.get('stroke_mm',0.12)
                body=self.contents(block['content'],q,w-pad[0]-pad[2]);title=block['title'];cid=self.char(q['font_family'],block.get('font_pt',q['font_pt']))
                if title and self.profile.get('box_title_placement')=='inside':
                    cid=self.char(self.profile['box_font'],self.profile['box_font_pt'])
                    titlep=self.paragraph(f'<hp:run charPrIDRef="{cid}"><hp:t>{escape(title)}</hp:t></hp:run>',self.style({'align':'CENTER','line_spacing_pct':160,'after_mm':1}),cid)
                    rows='<hp:tr>'+self.cell(titlep+body,w,1,fill=self.border(['left','right','top','bottom'],stroke),padding=pad)+'</hp:tr>'
                    xml=self.table(rows,w,1,ident,self.inline_position(w,1))
                elif title:
                    middle=min(w/2,max(20,len(title)*3.7));side=(w-middle)/2
                    empty=self.paragraph('',self.style({},tiny=True),self.char(q['font_family'],1))
                    titlep=self.paragraph(f'<hp:run charPrIDRef="{cid}"><hp:t>{escape(title)}</hp:t></hp:run>',self.style({'align':'CENTER','line_spacing_pct':100}),cid)
                    rows='<hp:tr>'+self.cell(empty,side,2.1)+self.cell(titlep,middle,4.2,col=1,rs=2)+self.cell(empty,side,2.1,col=2)+'</hp:tr>'
                    rows+='<hp:tr>'+self.cell(empty,side,2.1,row=1,fill=self.border(['left','top'],stroke))+self.cell(empty,side,2.1,col=2,row=1,fill=self.border(['right','top'],stroke))+'</hp:tr>'
                    rows+='<hp:tr>'+self.cell(body,w,1,row=2,cs=3,fill=self.border(['left','right','bottom'],stroke),padding=pad)+'</hp:tr>'
                    xml=self.table(rows,w,5.2,ident,self.inline_position(w,5.2),3,3)
                else:
                    rows='<hp:tr>'+self.cell(body,w,1,fill=self.border(['left','right','top','bottom'],stroke),padding=pad)+'</hp:tr>'
                    xml=self.table(rows,w,1,ident,self.inline_position(w,1))
                self.measurements.append({'block_id':q['id']+'/'+block['id'],'kind':'logical_box','container_id':ident,'available_width_mm':w})
                out.append(self.paragraph(f'<hp:run charPrIDRef="{cid}">{xml}<hp:t/></hp:run>',self.style(dict(block,line_spacing_pct=100,after_mm=block.get('after_mm',2))),cid))
            else:raise ValueError('unsupported_question_content:'+kind)
        return ''.join(out)
    def question(self,q):
        x,y,w,h=q['bbox_mm'];ident=self.uid();start=len(self.measurements)
        content=self.contents(q['content'],q,w)
        rows='<hp:tr>'+self.cell(content,w,h)+'</hp:tr>'
        xml=self.table(rows,w,h,ident,self.position(q['bbox_mm']))
        return xml,[{'block_id':q['id'],'kind':'question','bbox_mm':q['bbox_mm'],'container_id':ident,'placement':'question_flow'}]+self.measurements[start:]
