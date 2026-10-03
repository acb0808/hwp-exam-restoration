# 담당자 작성 형식

새 배정은 task.md에 포함된 [작성 규칙](worker-guide.md)과 명령으로 진행한다. 이 문서는 v1.3 작업의 입력 파일을 이어서 사용할 때의 호환 안내다.

새 작업은 배정 입력의 `draft_path`를 채우고 `compile_command`를 실행한다. [2단 예제](../examples/ocr-draft.json)는 구조 예시이며 실제 문항으로 복사하지 않는다. 빈 초안은 검증하지 않는다. 이 형식은 작성량만 줄이며 기존 version=2 검사를 모두 유지한다.

최상위 필드는 `schema: "restoration-draft/1"`, `regions`, `questions`, `issues` 네 개다. 미해결 판독은 `issues`에 남긴다. 컴파일은 내용을 추측하거나 issues를 지우지 않는다.

- `regions`: 원본 단별 `id`, `bbox_mm: [x,y,width,height]`. 문항 목록 `question_ids`는 생략하면 아래 문항 순서에서 만들어진다. 직접 쓰면 그 순서와 일치해야 한다.
- `questions`: 원본 순서대로 `id`, `region_id`, `bbox_mm`, `content`. 좌표는 원본 페이지 기준 mm이며 단·문항 전체에만 기록한다. 문항이 단 경계를 넘으면 실제 초과량이 오류에 나온다. 원본을 확인해 고치며 임의 축소하지 않는다.
- `font_family`, `font_pt`는 생략하면 배정 입력에 표시된 템플릿 기본값을 사용한다. 별도 지시가 없는 한 재지정하지 않는다.
- `content` 각 항목의 `id`는 생략할 수 있다. 컴파일러가 충돌 없는 ID를 만든다. 원문·LaTeX·좌표·배열 순서는 바꾸지 않는다.

v1.5 이상은 단·문항의 `bbox_mm` 대신 `bbox_px`와 명시적 `coordinate_space`를 지원한다. `page`는 원본 전체 픽셀, 준비된 탐색 이미지 절대 경로는 그 이미지의 픽셀이다. 컴파일러가 원본 이미지와 등록 위치를 검사하고 mm로 환산한다. 두 단위의 bbox를 동시에 쓰거나 경계·좌표공간을 생략할 수 없다.

내용 항목은 다음 세 종류다.

```json
{"kind":"paragraph","runs":[{"kind":"text","text":"1. 다음 식의 값은? "},{"kind":"equation","latex":"\\frac{1}{2}"}]}
{"kind":"box","title":"〈보 기〉","before_mm":3,"after_mm":3,"content":[{"kind":"paragraph","runs":[{"kind":"text","text":"ㄱ. 주어진 조건"}]}]}
{"kind":"choices","columns":2,"rows":[[[{"kind":"text","text":"① "},{"kind":"equation","latex":"1"}],[{"kind":"text","text":"② "},{"kind":"equation","latex":"2"}]]]}
```

본문과 선지의 `runs`는 비워두지 않는다. 그림만 있는 문단은 `{"kind":"paragraph","runs":[],"figure_ref":"q9-figure"}`로 작성한다. 그림과 텍스트는 별도 문단에 두는 것을 기본으로 한다. `figure_ref`는 [TikZ 절차](tikz-exam.md)의 실제 검수 완료 ID여야 한다. 연결 파일의 큰 해시 객체를 다시 작성하지 않는다.

```text
PYTHON RESTORE compile-draft JOB PAGE draft.json compiled.json
PYTHON RESTORE compile-draft JOB PAGE draft.json compiled.json --figures BATCH_DIR/figures.json
```

`compiled.json`은 새 파일이어야 한다. 성공은 `status: compiled`, `accepted: false`, `visual_status: not_verified`다. 기존 계약·수식·배정·원본·그림 검수 연결을 모두 검사했으므로 성공 직후 같은 파일에 `validate-page`를 반복하지 않는다. 메인은 제출 파일을 `accept`로 별도 검사·수락하고 실제 출력 대조를 진행한다. 실패 시 결과 파일을 만들지 않으므로 초안의 지적된 부분만 고치고 같은 명령을 재실행한다. 이미 생성된 결과를 수정할 때는 새 결과 파일명을 쓴다.

필요한 추가 확대는 배정 입력의 `source_views_command`로 준비한다. 목록 형식은 `[{"id":"q9-detail","bbox_mm":[100,150,70,60]}]`이다. 결과의 `index.json`에는 원본 픽셀, 실제 crop 경계와 mm 변환이 있다. 원본 자체는 바꾸지 않으며 crop 경계는 도형의 점·선 위치를 추정한 값이 아니다. 좌표 측정 프로그램을 새로 만들 필요가 없다.

기존 version=2 제출물을 이어서 고칠 때만 [전체 계약](page-contract.md)과 `validate-page`를 사용한다. `result_path`와 `validation_command`는 그 호환 경로다. 배정 안내 `input.json`은 제출물이 아니다.
