"""Small source-derived templates; geometry is fixed before bounded label layout.

Candidate/overlap heuristic, not a global optimizer or arbitrary TikZ parser.
No model, image read, subprocess, or extra compilation is used for placement.
"""
import math
import re

SIDES={'above':(0,1),'above-right':(1,1),'right':(1,0),'below-right':(1,-1),
       'below':(0,-1),'below-left':(-1,-1),'left':(-1,0),'above-left':(-1,1)}
KINDS=('triangle','triangle-height','right-midpoints','circle-triangle')

def make_template(kind,options):
    if kind not in KINDS:raise ValueError('unknown_exam_template: '+', '.join(KINDS))
    def number(key,default,low,high):
        try:value=float(options.get(key,default))
        except (ValueError,TypeError):raise ValueError('template_numeric_parameter_required: '+key) from None
        if not math.isfinite(value) or not low<=value<=high:raise ValueError(f'template_parameter_range: {key}={low}..{high}')
        return value
    circles=[]
    if kind=='circle-triangle':
        r=number('radius',2,.8,6)
        points={'O':(0.,0.)}
        for key,default in [('A',220),('B',320),('P',90)]:
            a=math.radians(number(key.lower(),default,-360,360));points[key]=(r*math.cos(a),r*math.sin(a))
        edge_names=[('A','B'),('B','P'),('P','A')];boundary=['A','B','P'];circles=[((0.,0.),r)]
        allowed={'radius','a','b','p'}
    else:
        w=number('base',4,1,12);h=number('height',3,.8,10)
        if kind=='right-midpoints':
            points={'A':(w,h),'B':(0.,0.),'C':(w,0.),'D':(w/2,0.),'E':(w,h/2),'O':(w/2,h/2)}
            edge_names=[('A','B'),('B','C'),('C','A'),('O','D'),('O','E')]
            boundary=['A','B','C','D','E'];allowed={'base','height'}
        else:
            t=number('apex',.5,.05,.95)
            points={'A':(w*t,h),'B':(0.,0.),'C':(w,0.)}
            edge_names=[('A','B'),('B','C'),('C','A')];boundary=['A','B','C'];allowed={'base','height','apex'}
            if kind=='triangle-height':
                points['H']=(w*t,0.);edge_names.append(('A','H'));boundary.append('H')
    for a in points:
        for b in points:
            if a<b and math.dist(points[a],points[b])<.02:raise ValueError('template_points_too_close: '+a+','+b)
    allowed|={'labels'}|{'pos'+key for key in points}
    unknown=set(options)-allowed
    if unknown:raise ValueError('unknown_template_parameter: '+','.join(sorted(unknown)))
    names=options.get('labels','/'.join(points)).split('/')
    if len(names)!=len(points) or any(not re.fullmatch(r'[A-Za-z](?:_[0-9]{1,2})?|-',name) for name in names):
        raise ValueError('template_labels_required: slash-separated Latin names, optional _digits; - hides an unprinted label; order='+','.join(points))
    labels={key:value for key,value in zip(points,names) if value!='-'}
    if len(set(labels.values()))!=len(labels):raise ValueError('template_duplicate_labels')
    preferred={key:options['pos'+key] for key in points if 'pos'+key in options}
    if any(side not in SIDES for side in preferred.values()):raise ValueError('template_label_side_required: '+','.join(SIDES))
    segments=[(points[a],points[b]) for a,b in edge_names]
    # Polygonal approximation is used only as a collision obstacle, never drawn.
    for (cx,cy),r in circles:
        ring=[(cx+r*math.cos(i*math.tau/128),cy+r*math.sin(i*math.tau/128)) for i in range(128)]
        segments.extend(zip(ring,ring[1:]+ring[:1]))
    return dict(points=points,edge_names=edge_names,segments=segments,circles=circles,
                labels=labels,boundary=boundary,preferred=preferred,obstacles=[])

def intersects(a,b):return a[0]<b[2] and b[0]<a[2] and a[1]<b[3] and b[1]<a[3]

def line_hits(a,b,box):
    """Liang-Barsky clipping, including horizontal and vertical segments."""
    lo,hi=0.,1.;dx=b[0]-a[0];dy=b[1]-a[1]
    for p,q in [(-dx,a[0]-box[0]),(dx,box[2]-a[0]),(-dy,a[1]-box[1]),(dy,box[3]-a[1])]:
        if abs(p)<1e-12:
            if q<0:return False
        elif p<0:lo=max(lo,q/p)
        else:hi=min(hi,q/p)
        if lo>hi:return False
    return True

def conflicts(box,g,others):
    # Small clearance keeps the conservative text box away from printed strokes.
    box=(box[0]-.025,box[1]-.025,box[2]+.025,box[3]+.025)
    return (sum(line_hits(a,b,box) for a,b in g['segments'])+
            sum(intersects(box,b) for b in others)+
            sum(intersects(box,b) for b in g.get('obstacles',[]))+
            sum(box[0]<=x<=box[2] and box[1]<=y<=box[3] for x,y in g['points'].values()))

def place_labels(g):
    """Greedy finite candidates plus four local-improvement sweeps; no geometry edits."""
    points=g['points'];center=tuple(sum(p[i] for p in points.values())/len(points) for i in (0,1))
    choices={}
    for key,label in g['labels'].items():
        x,y=points[key];w=.32+.12*(len(label)-1);h=.46
        outward=(x-center[0],y-center[1])
        sides=([g['preferred'][key]] if key in g['preferred'] else
               sorted(SIDES,key=lambda s:-(SIDES[s][0]*outward[0]+SIDES[s][1]*outward[1])/math.hypot(*SIDES[s])
                      if key in g['boundary'] else list(SIDES).index(s)))
        choices[key]=[]
        for rank,side in enumerate(sides):
            dx,dy=SIDES[side]
            for ring,gap in enumerate((.08,.16,.26)):
                cx=x+dx*(w/2+gap);cy=y+dy*(h/2+gap)
                # Avoid a collision-free label being mistaken for a nearby point.
                own=math.hypot(cx-x,cy-y)
                if any(math.hypot(cx-qx,cy-qy)<=own+1e-9 for other,(qx,qy) in points.items() if other!=key):continue
                choices[key].append(dict(side=side,center=(cx,cy),box=(cx-w/2,cy-h/2,cx+w/2,cy+h/2),preference=rank*.01+ring*.03))
        if not choices[key]:raise ValueError('template_labels_overlap: '+key+' has no unambiguous nearby position; adjust posNAME/base/height or use custom TikZ')
    # Place constrained points first; fixed iteration order makes output/cache stable.
    order=sorted(choices,key=lambda key:(sum(conflicts(c['box'],g,[])==0 for c in choices[key]),key))
    selected={}
    def score(candidate,others):return conflicts(candidate['box'],g,others)*1000+candidate['preference']
    for key in order:
        selected[key]=min(choices[key],key=lambda c:score(c,[v['box'] for v in selected.values()]))
    for _ in range(4):
        changed=False
        for key in order:
            others=[v['box'] for k,v in selected.items() if k!=key]
            best=min(choices[key],key=lambda c:score(c,others))
            if score(best,others)<score(selected[key],others):selected[key]=best;changed=True
        if not changed:break
    bad=[key for key,c in selected.items() if conflicts(c['box'],g,[v['box'] for k,v in selected.items() if k!=key])]
    if bad:raise ValueError('template_labels_overlap: '+','.join(bad)+'; adjust posNAME/base/height or use custom TikZ; do not omit printed labels')
    return selected

def template_tex(kind,options):
    g=make_template(kind,options);placed=place_labels(g)
    rows=['% Source-derived template; fixed geometry, candidate label placement.']
    rows.extend(f'\\coordinate ({key}) at ({x:.6f},{y:.6f});' for key,(x,y) in g['points'].items())
    rows.extend(f'\\draw ({a}) -- ({b});' for a,b in g['edge_names'])
    rows.extend(f'\\draw ({x:.6f},{y:.6f}) circle[radius={r:.6f}cm];' for (x,y),r in g['circles'])
    for key,label in g['labels'].items():
        x,y=placed[key]['center'];label=re.sub(r'_(\d+)',r'_{\1}',label)
        rows.append(f'\\node[inner sep=0pt,outer sep=0pt,font={{\\fontsize{{10}}{{12}}\\selectfont}}] at ({x:.6f},{y:.6f}) {{$\\mathrm{{{label}}}$}};')
    return '\n'.join(rows)

def expand_templates(text):
    clean=re.sub(r'(?<!\\)%[^\n]*','',text)
    if not re.search(r'\\ExamTemplate\b',clean):return text
    if len(re.findall(r'\\ExamTemplate\b',clean))!=1:raise ValueError('one_template_per_figure_required')
    if re.search(r'\\begin\{(?:tikzpicture|scope)\}\s*\[|\\(?:tikzset|pgftransform\w*)\b',clean):
        raise ValueError('template_requires_untransformed_picture: use base/height/radius parameters; custom TikZ remains supported without ExamTemplate')
    match=re.search(r'\\ExamTemplate\s*\{([a-z-]+)\}\s*\{([^{}]*)\}',clean)
    if not match:raise ValueError('template_syntax: \\ExamTemplate{kind}{key=value,...}')
    options={}
    for entry in match[2].split(','):
        if not entry.strip():continue
        pair=entry.split('=')
        if len(pair)!=2 or not all(x.strip() for x in pair):raise ValueError('template_key_value_required')
        key,value=map(str.strip,pair)
        if key in options:raise ValueError('duplicate_template_parameter: '+key)
        options[key]=value
    return clean[:match.start()]+template_tex(match[1],options)+clean[match.end():]
