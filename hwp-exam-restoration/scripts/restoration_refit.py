"""Fit an overflowing question without a model call: give it more of the page's six rows, and only when no
row split fits, shrink that question's figures a little.

The numbers are Hangul's own: restoration_fit measures every question cell of the failed export. A page column
has six equal template rows and each question takes whole rows, so the plans of a column are few (at most ten)
and every one is tried."""
from itertools import combinations

ROWS=6
MIN_FIGURE_SCALE=.8   # a figure is never shrunk below this share of the size its producer approved
SPARE_MM=1.5          # room kept under the last line
CONTINUED_SPARE_MM=4  # a cell continued on the next page is measured in parts, less exactly

def plans(count):
    """Every way to give `count` questions whole rows: each at least one, six together."""
    return [[b-a for a,b in zip((0,)+cut,cut+(ROWS,))] for cut in combinations(range(1,ROWS),count-1)]

def column_plan(cells,scales):
    """Best rows per question for one column, as (rows, {question: figure scale}); None when nothing fits.
    cells: measured question cells of the column in order. scales: figure scales already applied."""
    row=sum(c['cell_mm'] for c in cells)/sum(c['rows'] for c in cells)
    best=None
    for rows in plans(len(cells)):
        shrink={};worst=1.0;spare=[]
        for c,r in zip(cells,rows):
            padding=c['cell_mm']-c['available_mm'];room=r*row-padding
            need=c['content_mm']+(CONTINUED_SPARE_MM if c.get('continued') else SPARE_MM)
            if need<=room:spare.append(room-need);continue
            if c['figures_mm']<=0:break
            factor=1-(need-room)/c['figures_mm'];total=factor*scales.get(c['block_id'],1)
            if total<MIN_FIGURE_SCALE:break
            shrink[c['block_id']]=round(total-.005,2);worst=min(worst,factor);spare.append(0)
        else:
            # Least shrinking first, then the fewest rows moved, then the most room left in the tightest cell.
            key=(round(1-worst,3),sum(abs(r-c['rows']) for c,r in zip(cells,rows)),-min(spare))
            if best is None or key<best[0]:best=(key,rows,shrink)
    return (best[1],best[2]) if best else None

def refit(fit,row_plans,figure_scales,labels=None):
    """New row plans and figure scales for the pages whose questions overflowed, with one log line per change.
    Returns None when a page cannot be fitted this way or nothing would change."""
    cells=fit.get('question_cells') or []
    over={(i['page'],i['block_id']) for i in fit.get('issues',[]) if i.get('code')=='question_cell_content_overflow'}
    if not over or not cells:return None
    labels=labels or {};new_plans={};new_scales={};log=[]
    for page in sorted({p for p,_ in over}):
        mine=[c for c in cells if c['page']==page]
        plan={c['block_id']:c['rows'] for c in mine};scales=dict(figure_scales.get(str(page),{}))
        for column in sorted({c['column'] for c in mine},key=str):
            group=[c for c in mine if c['column']==column]
            if not any((page,c['block_id']) in over for c in group):continue
            found=column_plan(group,scales)
            if found is None:return None
            rows,shrink=found
            for c,r in zip(group,rows):
                name=labels.get((page,c['block_id']),c['block_id'])
                if r!=c['rows']:log.append(f"{page}쪽 {name}: 칸 {c['rows']}행→{r}행")
                plan[c['block_id']]=r
            for qid,value in shrink.items():
                log.append(f"{page}쪽 {labels.get((page,qid),qid)}: 도형 {round((1-value)*100)}% 축소");scales[qid]=value
        if plan==row_plans.get(str(page)) and scales==figure_scales.get(str(page),{}):return None  # same plan again: it did not help
        new_plans[str(page)]=plan
        if scales:new_scales[str(page)]=scales
    return {'row_plans':new_plans,'figure_scales':new_scales,'log':log}
