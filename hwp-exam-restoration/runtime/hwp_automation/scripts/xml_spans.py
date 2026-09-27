"""Namespace-aware XML byte spans; edits never serialize untouched elements."""
from __future__ import annotations
from dataclasses import dataclass, field
from xml.parsers import expat
from xml.sax.saxutils import escape
import re

HP = 'http://www.hancom.co.kr/hwpml/2011/paragraph'
HH = 'http://www.hancom.co.kr/hwpml/2011/head'

def xml_escape(value):
    if any(not (c in '\t\n\r' or 0x20 <= ord(c) <= 0xD7FF or 0xE000 <= ord(c) <= 0xFFFD or 0x10000 <= ord(c) <= 0x10FFFF) for c in value):
        raise ValueError('Illegal XML character')
    return escape(value, {'"': '&quot;', "'": '&apos;'})

@dataclass(eq=False)
class XmlNode:
    local: str
    uri: str
    qname: str
    attrs: dict
    parent: 'XmlNode | None'
    children: list = field(default_factory=list)
    start: int = 0
    open_end: int = 0
    close_start: int = 0
    end: int = 0
    path: str = ''
    characters: list = field(default_factory=list)

class XmlDocument:
    def __init__(self, data: bytes):
        data.decode('utf-8')
        declaration = re.match(br'<\?xml\b[^?]*encoding\s*=\s*[\x22\x27]([^\x22\x27]+)',data)
        if declaration and declaration[1].lower() not in (b'utf-8',b'utf8',b'us-ascii'):
            raise ValueError('Only UTF-8 XML is supported for byte-span editing')
        self.data, self.nodes = data, []
        stack, namespaces = [], [{}]
        # Namespace processing separately validates all expanded names and bindings.
        validator = expat.ParserCreate(namespace_separator='|')
        def forbidden(*args): raise ValueError('DTD and entities are prohibited')
        validator.StartDoctypeDeclHandler = forbidden
        validator.Parse(data, True)
        parser = expat.ParserCreate()
        def tag_end(start):
            quote = None
            for i in range(start, len(data)):
                c = data[i]
                if quote:
                    if c == quote: quote = None
                elif c in (34,39): quote = c
                elif c == 62: return i+1
            raise ValueError('Unterminated XML tag')
        def start(name, attrs):
            ns = namespaces[-1].copy()
            for k,v in attrs.items():
                if k == 'xmlns': ns[''] = v
                elif k.startswith('xmlns:'): ns[k[6:]] = v
            prefix, local = name.split(':',1) if ':' in name else ('',name)
            parent = stack[-1] if stack else None
            siblings = parent.children if parent else [n for n in self.nodes if n.parent is None]
            idx = sum(n.local == local for n in siblings)+1
            pos = parser.CurrentByteIndex
            n = XmlNode(local, ns.get(prefix,''), name, attrs, parent, start=pos, open_end=tag_end(pos), path=(parent.path if parent else '')+f'/{local}[{idx}]')
            if parent: parent.children.append(n)
            self.nodes.append(n); stack.append(n); namespaces.append(ns)
        def end(name):
            n = stack.pop(); namespaces.pop()
            if data[n.start:n.open_end].rstrip().endswith(b'/>'):
                n.close_start = n.end = n.open_end
            else:
                n.close_start = parser.CurrentByteIndex; n.end = tag_end(n.close_start)
        def chars(value):
            for n in stack: n.characters.append(value)
        parser.StartElementHandler = start
        parser.EndElementHandler = end
        parser.CharacterDataHandler = chars
        parser.Parse(data, True)
    def find(self, local, uri=None): return [n for n in self.nodes if n.local == local and (uri is None or n.uri == uri)]
    def bytes(self,node): return self.data[node.start:node.end]
    def text(self,node): return ''.join(node.characters)
    def path(self,node): return node.path
    def replace(self,splices):
        edits = sorted(splices, key=lambda s:(s[0],s[1]))
        previous = 0
        for start,end,payload in edits:
            if not 0 <= start <= end <= len(self.data) or start < previous: raise ValueError('Overlapping or invalid XML edits')
            previous = end
        result = self.data
        for start,end,payload in reversed(edits): result = result[:start]+payload+result[end:]
        XmlDocument(result)
        return result
    def set_attrs(self,node,updates):
        opening = self.data[node.start:node.open_end]
        for key,value in updates.items():
            if not re.fullmatch(r'[A-Za-z_][\w.:-]*',key): raise ValueError('Invalid attribute name')
            replacement = key.encode()+b'="'+xml_escape(str(value)).encode()+b'"'
            pattern = rb'([A-Za-z_][\w:.-]*)\s*=\s*(?:"[^"]*"|\x27[^\x27]*\x27)'
            match = next((m for m in re.finditer(pattern,opening) if m[1]==key.encode()),None)
            if match: opening=opening[:match.start()]+replacement+opening[match.end():]
            else:
                offset = len(opening)- (2 if opening.endswith(b'/>') else 1)
                opening=opening[:offset]+b' '+replacement+opening[offset:]
        return self.replace([(node.start,node.open_end,opening)])
