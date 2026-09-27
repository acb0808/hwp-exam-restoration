# 문항 단위 편집 구조 (v2)

페이지 바깥 앵커는 문항당 하나다. 줄별 표/글상자를 문항 표 안에 다시 넣는 것은 금지한다. 본문은 일반 문단, 수식은 인라인, 선택지는 같은 문단의 탭으로 정렬한다. 보기/조건은 의미상 필요한 내부 표 하나로 만든다. 그림은 소속 문단에 붙인다.

페이지의 기존 최상위 키는 유지하고 version=2. regions는 그대로. blocks에는 question_id=null인 머리글/구분선 등만 둔다. questions 각 원소에 font_pt, font_family, content를 추가한다. id,region_id,bbox_mm은 기존 문항 영역이다.

content 순서대로 흐른다. 공통 선택 속성: before_mm,after_mm,left_mm,right_mm(기본0),font_pt(문항값),line_spacing_pct(기본150),align(LEFT/CENTER/RIGHT). 문항 내부에는 bbox_mm/x/y를 넣지 않는다.

- paragraph: id,kind="paragraph",runs. runs는 text/equation 또는 {"kind":"break"}. text/equation 계약은 기존과 같다. 본문 전체를 하나의 문단으로 구성하며 원본 줄 구분이 필요하면 break 사용. 문장 의미 없이 줄마다 별도 paragraph로 쪼개지 않는다. 자연 줄바꿈은 허용하며 실제 출력 확인.
- choices: id,kind="choices",columns(1~5),rows. rows는 각 행의 선택지 runs 배열들의 배열. 예: rows=[[[{"kind":"text","text":"① "},{"kind":"equation","latex":"1"}],[{"kind":"text","text":"② "},{"kind":"equation","latex":"2"}]]]. 행마다 하나의 문단이며 열은 탭, 동일행 수식 기준선은 한글이 정한다.
- box: id,kind="box",title,content(일반 paragraph만),stroke_mm(기본0.12),padding_mm(기본[3,2,3,2]). 제목은 가는 위 테두리 중앙에 걸친다. title 키는 필수이며 원본에 제목이 없는 일반 사각형은 title:""로 명시한다. 의미 있는 보기 전체가 하나의 내부 표다. width는 문항폭-left_mm-right_mm.
- paragraph.figure(선택): {path,sha256,size_mm:[w,h],offset_mm:[x,y],tikz:{render:{path,sha256},review:{path,sha256}}}. 소속 문단 기준의 그림 위치만 허용. 그림 옆 본문은 right_mm으로 공간을 확보한다. 삽입 PNG는 실제 TikZ 렌더 결과이며 [제작·인계 계약](tikz-exam.md)의 렌더/원본 대조 기록을 연결한다. 구조 호환 검사에서 옛 그림 필드를 읽을 수 있어도 `accept`/`assemble`/컴파일은 TikZ 근거 없는 그림을 거부한다.

choices의 열은 기본 균등 탭이다. 긴 선택지로 다른 간격이 필요하면 tab_stops_mm=[두번째열 시작,세번째열 시작,...]을 문단 내용 왼쪽 기준으로 지정한다. 별도 표나 선택지별 좌표 개체는 만들지 않는다. 배점처럼 나뉘면 읽기 어려운 짧은 표기는 필요한 경우 앞에 break를 넣어 같은 문단에서 유지한다.

font_pt로 임의 축소해 넘침을 숨기지 않는다. native에서 문항의 전체 높이·폭, 줄바꿈, 보기·그림과 본문 간섭을 확인한다. 원본 쪽/단/문항 영역은 그대로 유지하고 모든 줄의 절대좌표 복제보다 문항 단위 편집성을 우선한다(사용자의 현재 요청).

수식 주의: native 검수된 원본 문자열을 유지한다. 집합 구분자 |, {\\overline{AB}}={\\overline{BC}} 같은 명시적 그룹의 실제 출력 결과를 보존한다. 긴 판독 보류 표시를 임의 확정하지 않는다.
