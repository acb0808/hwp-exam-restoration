"""Figures pasted from the source page (job option figures=crop, or figure_fallback=crop at the fix limit).

A crop is recorded like a TikZ render (schema tikz-render/1, renderer `crop`) so the rest of the pipeline, the
provenance checks and the native insertion use it unchanged: source spec, single-page PDF, PNG and log, each
with its hash. No model judges a crop; the producer only checks that its box holds the whole figure."""
from pathlib import Path
import hashlib,json

SPEC_SUFFIX='.crop.json'
WHITE_FROM=200  # paper shade and scanner noise lighter than this print as white
PAD_PX=4


def _record(path):
    path=Path(path).resolve()
    return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def write_spec(path,source_image,bbox_px,width_mm):
    """The crop's input, written where a TeX source would be: one file binds the source page and the box."""
    x,y,w,h=(int(round(v)) for v in bbox_px)
    if w<8 or h<8:raise ValueError('source_bbox_px_too_small_for_crop')
    spec={'schema':'restoration-crop/1','source_image':str(Path(source_image).resolve()),
          'source_sha256':hashlib.sha256(Path(source_image).read_bytes()).hexdigest(),
          'bbox_px':[x,y,w,h],'width_mm':float(width_mm)}
    Path(path).write_text(json.dumps(spec,ensure_ascii=False,indent=2),encoding='utf-8')
    return Path(path)


CROP_DPI=300
SNAP_DARK=70     # print is darker than this; pencil arcs mostly are not, so they do not move an edge
SNAP_REACH=0.15  # an edge moves at most this share of the box


def snap_box(full,bbox_px):
    """The producer's box [x, y, w, h] (page-image pixels) with each edge that cuts dark ink moved to the nearest
    row or column free of it, outward or inward (outward on ties). Boxes set by eye cut labels above a figure
    or half a line of choice text below it; growing only outward followed pencil arcs into the choices."""
    gray=full.convert('L');W,H=gray.size
    l,t=max(0,int(bbox_px[0])),max(0,int(bbox_px[1]))
    r,b=min(W,int(bbox_px[0]+bbox_px[2])),min(H,int(bbox_px[1]+bbox_px[3]))
    if r-l<8 or b-t<8:return list(bbox_px)
    reach_x,reach_y=max(4,round((r-l)*SNAP_REACH)),max(4,round((b-t)*SNAP_REACH))
    def clear(region):
        return gray.crop(region).getextrema()[0]>=SNAP_DARK
    def nearest(pos,ok,reach,outward):
        if ok(pos):return pos
        for d in range(1,reach+1):
            for moved in (pos+outward*d,pos-outward*d):
                if ok(moved):return moved
        return pos
    for _ in range(3):  # a moved edge changes the span the other edges test
        t=nearest(t,lambda y:0<=y<H and clear((l,y,r,y+1)),reach_y,-1)
        b=nearest(b,lambda y:0<y<=H and clear((l,y-1,r,y)),reach_y,1)
        l=nearest(l,lambda x:0<=x<W and clear((x,t,x+1,b)),reach_x,-1)
        r=nearest(r,lambda x:0<x<=W and clear((x-1,t,x,b)),reach_x,1)
    return [l,t,r-l,b-t]


def crop_source(root,manifest,page,bbox_px):
    """(image, box) to cut: the page drawn again from the source PDF at CROP_DPI (kept beside the page image, never
    compacted), with the box scaled from page-image pixels. A source that is an image is cut as it is."""
    entry=manifest['pages'][page-1];image=Path(root)/entry['image']['path']
    source=Path(root)/manifest['source']['path'];dpi=entry.get('dpi') or 200
    if source.suffix.lower()!='.pdf' or dpi>=CROP_DPI:return image,list(bbox_px)
    sharp=Path(root)/'pages'/f'page-{page:04d}-crop{CROP_DPI}.png'
    if not sharp.is_file():
        import fitz
        with fitz.open(source) as doc:
            doc[page-1].get_pixmap(matrix=fitz.Matrix(CROP_DPI/72,CROP_DPI/72),alpha=False).save(sharp)
    k=CROP_DPI/dpi
    return sharp,[v*k for v in bbox_px]


def is_spec(path):
    return str(path).endswith(SPEC_SUFFIX)


def render(spec_path,output,dpi=300):
    """Cut the box from the source page, whiten the paper, and save the receipt files in output."""
    from PIL import Image,ImageOps
    import fitz
    spec_path=Path(spec_path).resolve(strict=True);spec=json.loads(spec_path.read_text(encoding='utf-8'))
    if spec.get('schema')!='restoration-crop/1':raise ValueError('crop_spec_required')
    source=Path(spec['source_image']).resolve(strict=True)
    if hashlib.sha256(source.read_bytes()).hexdigest()!=spec['source_sha256']:raise ValueError('crop_source_changed')
    out=Path(output);out.mkdir(parents=True,exist_ok=False)
    x,y,w,h=spec['bbox_px']
    with Image.open(source) as full:
        box=(max(0,x-PAD_PX),max(0,y-PAD_PX),min(full.width,x+w+PAD_PX),min(full.height,y+h+PAD_PX))
        if box[2]-box[0]<8 or box[3]-box[1]<8:raise ValueError('source_bbox_px_outside_page')
        gray=ImageOps.grayscale(full.crop(box))
    gray=gray.point(lambda v:255 if v>=WHITE_FROM else v)
    png=out/'diagram.png';gray.save(png,optimize=True)
    width_pt=spec['width_mm']*72/25.4;height_pt=width_pt*gray.height/gray.width
    pdf=out/'diagram.pdf'
    with fitz.open() as doc:
        page=doc.new_page(width=width_pt,height=height_pt);page.insert_image(page.rect,filename=str(png));doc.save(pdf)
    tex=out/'diagram.crop.json';tex.write_bytes(spec_path.read_bytes())
    log=out/'diagram.log';log.write_text(f'crop {source} box={list(box)} white_from={WHITE_FROM}\n',encoding='utf-8')
    result={'schema':'tikz-render/1','status':'rendered_pending_review','renderer':'crop',
            'source':_record(tex),'pdf':_record(pdf),'png':_record(png),'log':_record(log),
            'engine':'crop','command':['crop','diagram.crop.json'],'exit_code':0,'dpi':dpi,'visual_status':'not_verified'}
    (out/'render.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


def auto_review(template,reason):
    """A passed review for a figure no model compares (a crop, or figure_check=off), with the reason kept."""
    return {**template,'status':'passed','issues':[],'checks':{k:'passed' for k in template['checks']},'auto_approved':reason}
