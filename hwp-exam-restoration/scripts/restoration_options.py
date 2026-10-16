"""User options of a restoration job: skill defaults, then HWP_* environment values, then the user's saved
preferences, then what this request states. Only stated keys override; nothing stated keeps today's behaviour.

The engine merges the layers so the main agent never opens the preferences file: it passes only the keys the
user named, and hwp_prepare returns the options in effect."""
from pathlib import Path
import json,os,re,tempfile

DEFAULTS={
    'figures':'restore',          # restore (TikZ) / crop (source picture pasted) / off (text and equations only)
    'figure_fallback':'note',     # restore: a figure at its fix limit is a note (note) or the source crop (crop)
    'figure_check':'on',          # the producer's own figure comparison (hwp_review_figures)
    'figure_fix_limit':2,         # TeX edits of one figure after its first render
    'answers':'on',               # answer blocks and the final answer table
    'review':'full',              # final review: full / answers (missing questions and answer table) / off
    'repair_rounds':1,            # repair rounds after a failed final review
    'review_notes_page':'on',     # the 검수 노트 page before the answer table
    'reader_model':'inherit',     # producer model tier: inherit / standard / light
    'reviewer_model':'inherit',   # final reviewer model tier
    'figure_line_width':0.3,      # pt
    'figure_label_scale':0.85,
    'image_mode':'color',         # color / gray16 (page images handed to models)
    'source_tiles':'on',          # producers' six zoomed tiles of the page
    'review_tiles':'off',
    'dpi':None,                   # page image resolution at preparation (engine default)
    'metadata':{},                # school / year / exam_title / title used when hwp_build leaves them empty
    'output_dir':None,            # folder for results instead of the source folder
    'confirm_settings':'off',     # on: show the settings and wait for the user's go; off: announce them and start
}

ON_OFF=('on','off')
CHOICES={'figures':('restore','crop','off'),'figure_fallback':('note','crop'),'figure_check':ON_OFF,'answers':ON_OFF,
         'review':('full','answers','off'),'review_notes_page':ON_OFF,'reader_model':('inherit','standard','light'),
         'reviewer_model':('inherit','standard','light'),
         'image_mode':('color','gray16'),'source_tiles':ON_OFF,'review_tiles':ON_OFF,'confirm_settings':ON_OFF}
RANGES={'figure_fix_limit':(0,2,int),'repair_rounds':(0,3,int),'figure_line_width':(0.2,0.6,float),
        'figure_label_scale':(0.6,1.2,float),'dpi':(72,600,int)}
METADATA_KEYS=('school','year','exam_title','title')

# Named bundles. Keys the user states next to a preset win over it.
PRESETS={
    'fast':{'figures':'crop','review':'answers','repair_rounds':0,'review_notes_page':'off','image_mode':'gray16'},
    'balanced':{'figure_fix_limit':1,'review':'full','repair_rounds':1},
    'quality':{'figures':'restore','figure_fallback':'note','figure_check':'on','figure_fix_limit':2,'review':'full',
               'repair_rounds':1,'source_tiles':'on','review_tiles':'on'},
}

LIGHT_READER_WARNING=('reader_model=light: Flash-Lite 계열 제작자는 2026-10-15 측정(8쪽)에서 시간 2.4배, 할당량 더 많이, '
                      '정답 7개 오류로 모든 면에서 나빴습니다. 글씨가 깨끗하고 그림이 적은 쪽에만 권합니다.')

# What each tier means on the hosts the skill runs on; the main passes the value of its own host.
MODEL_TIERS={'inherit':{'antigravity':'inherit','claude_code':'inherit','opencode':'inherit'},
             'standard':{'antigravity':'flash','claude_code':'sonnet','opencode':'default'},
             'light':{'antigravity':'flash_lite','claude_code':'haiku','opencode':'lightest_with_vision'}}


def preferences_path():
    """~/.hwp-restoration/preferences.json, outside the skill folder (reinstalls overwrite it) and outside the
    host's agent folders. HWP_PREFERENCES points elsewhere (tests, several profiles)."""
    named=os.environ.get('HWP_PREFERENCES')
    return Path(named) if named else Path.home()/'.hwp-restoration'/'preferences.json'


def check(key,value):
    """The value as stored, or raise ValueError with a short reason."""
    if key not in DEFAULTS:raise ValueError('unknown_option')
    if key in CHOICES:
        value=str(value).strip().lower() if isinstance(value,(str,bool)) else value
        if value is True:value='on'
        if value is False:value='off'
        if value=='true':value='on'
        if value=='false':value='off'
        if value not in CHOICES[key]:raise ValueError('choose one of '+'/'.join(CHOICES[key]))
        return value
    if key in RANGES:
        low,high,kind=RANGES[key]
        if value is None and key=='dpi':return None
        if isinstance(value,bool) or not isinstance(value,(int,float,str)):raise ValueError(f'number {low}-{high}')
        try:number=kind(float(value)) if kind is int else float(value)
        except ValueError:raise ValueError(f'number {low}-{high}') from None
        if kind is int and float(value)!=number:raise ValueError(f'whole number {low}-{high}')
        if not low<=number<=high:raise ValueError(f'number {low}-{high}')
        return number
    if key=='metadata':
        if not isinstance(value,dict):raise ValueError('object with school/year/exam_title/title')
        return {k:str(v) for k,v in value.items() if k in METADATA_KEYS and isinstance(v,(str,int)) and str(v).strip()}
    if key=='output_dir':
        if value is None:return None
        if not isinstance(value,str) or not Path(value).is_absolute():raise ValueError('absolute folder path')
        return str(Path(value))
    raise ValueError('unknown_option')


def clean(layer,label):
    """Valid keys of one layer, its preset expanded first. Returns (values, warnings); bad entries are skipped."""
    values={};warnings=[]
    if not layer:return values,warnings
    if not isinstance(layer,dict):return values,[f'{label}: options must be an object']
    preset=layer.get('preset')
    if preset is not None:
        if preset in PRESETS:values.update(PRESETS[preset])
        else:warnings.append(f'{label}: preset {preset!r} ignored (choose one of {"/".join(PRESETS)})')
    for key,value in layer.items():
        if key=='preset':continue
        try:values[key]=check(key,value)
        except ValueError as exc:warnings.append(f'{label}: {key}={value!r} ignored ({exc})')
    return values,warnings


def environment_layer(environ=None):
    """Older HWP_* switches, kept below the preferences file."""
    env=os.environ if environ is None else environ;values={}
    if env.get('HWP_MAX_REREVIEWS') is not None:
        try:values['repair_rounds']=max(0,min(3,int(env['HWP_MAX_REREVIEWS'])))
        except ValueError:pass
    if env.get('HWP_PAGE_IMAGE_MODE')=='gray16':values['image_mode']='gray16'
    if env.get('HWP_SOURCE_TILES') is not None:
        values['source_tiles']='off' if env['HWP_SOURCE_TILES'].strip().lower() in ('0','false','off','no') else 'on'
    if env.get('HWP_REVIEW_TILES') is not None:values['review_tiles']='on' if env['HWP_REVIEW_TILES']=='1' else 'off'
    return values


def load_preferences():
    """(values, warnings) from the preferences file; a missing file is no preference."""
    path=preferences_path()
    try:data=json.loads(path.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:return {},[]
    except (OSError,ValueError) as exc:return {},[f'preferences file {path} ignored ({type(exc).__name__})']
    return clean(data,'preferences')


def save_preferences(stated):
    """Merge the stated keys into the preferences file (atomic write). Returns its path."""
    path=preferences_path()
    try:current=json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError,ValueError):current={}
    if not isinstance(current,dict):current={}
    if 'preset' in stated:
        current={k:v for k,v in current.items() if k not in PRESETS.get(stated['preset'],{})}
    current.update(stated)
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,temporary=tempfile.mkstemp(dir=path.parent,prefix=path.name+'.',suffix='.tmp')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:json.dump(current,stream,ensure_ascii=False,indent=2)
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)
    return path


def resolve(request=None,*,saved=None):
    """Merge request > preferences > environment > defaults.

    Returns (stated, effective, warnings): `stated` holds the preference and request keys only, so environment
    values stay live for jobs that leave a key unstated; `effective` is the whole option set in effect now."""
    prefs,warnings=load_preferences() if saved is None else clean(saved,'preferences')
    asked,more=clean(request,'request');warnings+=more
    stated={**prefs,**asked}
    effective={**DEFAULTS,**environment_layer(),**stated}
    return stated,effective,warnings+consistency_warnings(effective)


def consistency_warnings(o):
    warnings=[]
    if o['reader_model']=='light':warnings.append(LIGHT_READER_WARNING)
    return warnings


def effective_of(stated):
    """Option set in effect for a job whose stated keys are `stated` (environment read now)."""
    return {**DEFAULTS,**environment_layer(),**(stated or {})}


def job_options(manifest):
    """Options of a prepared job. Jobs from before 2.8.3 kept only include_answers/include_figures."""
    stated=dict(manifest.get('options') or {})
    if 'figures' not in stated and manifest.get('include_figures') is False:stated['figures']='off'
    if 'answers' not in stated:stated['answers']='on' if manifest.get('include_answers',False) else 'off'  # read default of old jobs
    return effective_of(stated)


def model_for(tier):
    return dict(MODEL_TIERS.get(tier,MODEL_TIERS['inherit']))


SCHOOL=re.compile(r'([가-힣A-Za-z]{1,12}?(?:여자중학교|여자고등학교|중학교|고등학교|여중|여고|중|고))(?=$|[\s_\-\]\)\.,(]|\d)')
YEAR=re.compile(r'(?<!\d)(20\d\d)(?:\s*학년도|\s*년)?(?!\d)')
EXAM=re.compile(r'(?<!\d)([1-3])\s*[-_~]\s*([12])(?!\d)[\s_\-]*(중간|기말)?')
TERM=re.compile(r'(중간|기말)(?:고사)?')


def guess_metadata(name):
    """School, year and exam title read off a source file name such as `[2024년 기출] 예시고 1-2 중간.pdf` or
    `예시중_2-2_중간.pdf`. Only what the name shows; the cover is never read for it."""
    stem=Path(str(name)).stem.replace('_',' ');found={}
    year=YEAR.search(stem)
    if year:found['year']=year.group(1)
    rest=YEAR.sub(' ',stem)
    rest=re.sub(r'기출|복원|시험지|수학|문제|원본','',rest)
    school=SCHOOL.search(rest)
    if school and school.group(1) not in ('중','고'):found['school']=school.group(1)
    rest=re.sub(r'([1-3])\s*학년\s*([12])\s*학기',r'\1-\2',rest)  # 2학년 2학기 → 2-2
    exam=EXAM.search(rest);term=TERM.search(rest)
    if exam:found['exam_title']=f"{exam.group(1)}-{exam.group(2)}"+(f" {exam.group(3) or (term.group(1) if term else '')}".rstrip())
    elif term:found['exam_title']=term.group(1)
    return found


LABELS={
    'figures':{'restore':'다시 그리기(TikZ)','crop':'원본에서 잘라 붙이기','off':'넣지 않음(글·수식만)'},
    'figure_fallback':{'note':'안 되면 검수 노트에 적기','crop':'안 되면 원본 그림으로 대체'},
    'figure_check':{'on':'제작자 도형 대조 켬','off':'제작자 도형 대조 끔'},
    'answers':{'on':'만듦','off':'만들지 않음'},
    'review':{'full':'전체 검수','answers':'정답·누락만 검수','off':'검수 안 함'},
    'review_notes_page':{'on':'검수 노트 쪽 붙임','off':'검수 노트 쪽 안 붙임(결과 보고에만)'},
    'model':{'inherit':'메인과 같은 모델','standard':'flash 급','light':'flash lite 급'},
    'image_mode':{'color':'컬러 이미지','gray16':'흑백 16단계 이미지(비용 절약)'},
    'source_tiles':{'on':'확대 조각 켬','off':'확대 조각 끔'},
}


def settings_summary(o,pages,*,asked=(),saved=(),guessed=()):
    """The job's settings in plain Korean, for the main to show the user before producers start.
    Keys from this request are marked (이번 요청), from the saved defaults (저장된 기본값); metadata read off the
    file name is marked (파일 이름에서)."""
    def mark(*keys):
        if any(k in asked for k in keys):return ' (이번 요청)'
        if any(k in saved for k in keys):return ' (저장된 기본값)'
        return ''
    meta=o.get('metadata') or {}
    def field(key,label):
        value=meta.get(key)
        if not value:return f'{label} 미정'
        return f"{label} {value}"+(' (파일 이름에서)' if key in guessed else '')
    head=('**이번 작업 설정** — 바꾸고 싶은 것이 있으면 말씀해 주세요. 그대로면 "진행"이라고 답해 주세요.'
          if o.get('confirm_settings')=='on' else
          '**이렇게 작업을 시작합니다.** 바로 진행하니, 아래 설정에 문제가 있으면 작업을 멈추고 다시 지시해 주세요.')
    lines=[head,
           f"- 문제 쪽: {', '.join(map(str,pages))}쪽 ({len(pages)}쪽)",
           '- 머리말: '+' · '.join([field('school','학교'),field('year','연도'),field('exam_title','시험')])
           +(' — 미정인 칸은 `-`로 인쇄됩니다' if not all(meta.get(k) for k in ('school','year','exam_title')) else '')]
    if o['figures']=='off':lines.append('- 도형: '+LABELS['figures']['off']+mark('figures'))
    else:
        parts=[LABELS['figures'][o['figures']]]
        if o['figures']=='restore':
            parts+=[LABELS['figure_check'][o['figure_check']],f"도형당 수정 최대 {o['figure_fix_limit']}회",LABELS['figure_fallback'][o['figure_fallback']]]
        lines.append('- 도형: '+' · '.join(parts)+mark('figures','figure_check','figure_fix_limit','figure_fallback','preset'))
    lines.append('- 정답표: '+LABELS['answers'][o['answers']]+mark('answers'))
    review=[LABELS['review'][o['review']]]
    if o['review']!='off':review.append(f"실패 시 수정 {o['repair_rounds']}회" if o['repair_rounds'] else '수정 회차 없음(남은 문제는 노트로)')
    review.append(LABELS['review_notes_page'][o['review_notes_page']])
    lines.append('- 최종 검수: '+' · '.join(review)+mark('review','repair_rounds','review_notes_page','preset'))
    lines.append(f"- 모델: 제작자 {LABELS['model'][o['reader_model']]} · 검수자 {LABELS['model'][o['reviewer_model']]}"+mark('reader_model','reviewer_model'))
    extra=[LABELS['image_mode'][o['image_mode']],LABELS['source_tiles'][o['source_tiles']]]
    if o['figures']=='restore' and (o['figure_line_width'],o['figure_label_scale'])!=(DEFAULTS['figure_line_width'],DEFAULTS['figure_label_scale']):
        extra.append(f"선 {o['figure_line_width']}pt · 라벨 {o['figure_label_scale']}배")
    lines.append('- 그 밖: '+' · '.join(extra)+mark('image_mode','source_tiles','figure_line_width','figure_label_scale'))
    lines.append('- 저장 위치: '+(o['output_dir'] or '원본 PDF와 같은 폴더')+mark('output_dir'))
    lines.append('다시 지시하는 예: "그림은 원본에서 잘라 붙여", "검수 없이 빨리", "빠른 모드로", "연도는 2024", '
                 '"제작자는 flash로", "앞으로 항상 이렇게"(기본값으로 저장). 무엇을 바꿀 수 있는지 궁금하면 "설정 안내해줘"라고 하세요.')
    return '\n'.join(lines)
