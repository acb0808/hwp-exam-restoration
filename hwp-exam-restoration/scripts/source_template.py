"""Keep the actual reference document shell, replacing only declared content."""
from pathlib import Path
from xml.etree import ElementTree as E
import copy,hashlib,zipfile
HP='{http://www.hancom.co.kr/hwpml/2011/paragraph}'

def preserve_source_shell(candidate,template_root,profile,fields):
    source=Path(template_root)/profile['source_package']
    if hashlib.sha256(source.read_bytes()).hexdigest()!=profile['files'][profile['source_package']]:
        raise ValueError('source_template_hash_mismatch')
    fields=fields or {}
    if set(fields)!=set(profile['fields']):raise ValueError('template_fields_required:'+','.join(profile['fields']))
    bad=[k for k,v in fields.items() if not isinstance(v,str) or not v.strip() or '\n' in v]
    if bad:raise ValueError('invalid_template_field: '+','.join(bad)+' must be non-empty one-line text (use a neutral value such as - when unknown)')
    with zipfile.ZipFile(source) as z:original={n:z.read(n) for n in z.namelist()}
    with zipfile.ZipFile(candidate) as z:generated={n:z.read(n) for n in z.namelist()}
    source_root=E.fromstring(original['Contents/section0.xml'])
    output_root=E.fromstring(generated['Contents/section0.xml'])
    leading=copy.deepcopy(list(source_root)[:profile['preserve_leading_paragraphs']])
    if any(list(p.iter(HP+'pic')) for p in leading):raise ValueError('source_shell_images_require_asset_mapping')
    changes=[]
    for key,spec in profile['fields'].items():
        nodes=[t for p in leading for t in p.iter(HP+'t') if t.text==spec['source_text']]
        if len(nodes)!=1:raise ValueError('template_field_ambiguous:'+key)
        nodes[0].text=fields[key];changes.append({'field':key,'before':spec['source_text'],'after':fields[key]})
    # The source column rule is rendered by the existing full-height line path;
    # every table cell, border, fill, size, font reference and footer is retained.
    for p in leading:
        for line in p.iter(HP+'colLine'):line.set('type','NONE')
    for parent in output_root.iter():
        for child in list(parent):
            if child.tag==HP+'secPr' or (child.tag==HP+'ctrl' and child.find(HP+'colPr') is not None):parent.remove(child)
    for p in reversed(leading):output_root.insert(0,p)
    original['Contents/section0.xml']=E.tostring(output_root,encoding='utf-8',xml_declaration=True)
    for name in ('Contents/header.xml','Contents/content.hpf'):
        original[name]=generated[name]
    # Old question images and preview are replaced with current question assets.
    for name in list(original):
        if name.startswith(('BinData/','Preview/')):del original[name]
    original.update({n:v for n,v in generated.items() if n.startswith('BinData/')})
    with zipfile.ZipFile(candidate,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for name,data in original.items():z.writestr(name,data,compress_type=zipfile.ZIP_STORED if name=='mimetype' else zipfile.ZIP_DEFLATED)
    return {'mode':'source_document_shell','source_package_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
            'preserved_leading_paragraphs':len(leading),'field_changes':changes,
            'preserved_tables':[t.get('id') for p in leading for t in p.iter(HP+'tbl')]}
