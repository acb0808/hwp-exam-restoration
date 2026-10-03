# 인라인 수식과 문항 편집 구조

문항 하나의 본문 전체는 일반 paragraph와 순서 있는 runs로 작성한다. 수식은 text 사이의 equation run이다. 원본 줄을 유지해야 할 때만 같은 문단의 break run을 사용한다. 줄별 표/글상자나 수식별 페이지 좌표를 만들지 않는다. 구체적 문항/선지/보기 구조는 [v2 계약](question-flow.md)을 따른다.

```json
{"kind":"paragraph","id":"q1-body","runs":[
  {"kind":"text","text":"원 "},
  {"kind":"equation","latex":"x^2+y^2=9"},
  {"kind":"text","text":"의 반지름을 구하시오."}
]}
```

수식은 builder의 treatAsChar=1, width=height=0, protect=0, baseLine=0, baseUnit=font_pt*100을 유지한다. 크기·기준선을 한글이 계산한다. 분수 선택지와 정수 선택지는 같은 행의 탭 문단에 두므로 번호마다 개별 y 보정이 필요하지 않다. 자동 줄바꿈을 허용하고 글자 압축으로 폭을 맞추지 않는다.

변환 성공만으로 인쇄 기호를 보장하지 않는다. 집합 조건의 |, 선분 윗줄 뒤 +/= 등도 실제 한글에서 확인한다. 검증된 입력의 예는 직접 | 구분자와 {\overline{AB}}={\overline{BC}}처럼 각 윗줄 항의 명시적 그룹이다. raw LaTeX나 이미지로 수식을 대체하지 않는다.

페이지 담당자가 기존 line/runs JSON을 문항 단위 content로 재작성하고 메인은 수정 없이 revise한다. 이전 개별 줄/선지 bbox는 문항 안의 떠 있는 표로 재사용하지 않는다. 네이티브 검증은 문항 전체의 실제 높이·폭, 필요한 내부 상자만 존재하는지, 수식 크기를 검사한다. 최종 PDF의 문항 넘침·그림 간섭·선지 정렬은 시각 검수한다. Markdown 검수 PASS는 출력 검수를 대신하지 않는다.
