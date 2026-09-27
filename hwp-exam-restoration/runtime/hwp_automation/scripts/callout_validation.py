"""Keep plain report callouts distinct from editable exam boxes."""
import re

def validate_report_callout(text, title=None, style='summary'):
    label=re.sub(r'[\s<>〈〉《》\[\]【】()（）]','',title or '')
    if label in {'보기','조건','주어진조건','함수와구간'} or style in {'exam','보기','조건'}:
        raise ValueError('exam_box_requires_studio: use a Studio box block with label and text/equation blocks; see references/exam-boxes.md')
    for value in (text,title or ''):
        if re.search(r'(?<!\\)\$[^$\n]+\$|\\\(|\\\[',value):
            raise ValueError('callout_math_requires_equation_blocks: report callouts are plain text; use Studio text with math delimiters or equation blocks')
