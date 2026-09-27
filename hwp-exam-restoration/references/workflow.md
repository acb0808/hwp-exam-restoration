# 메인 실행 절차

`PYTHON`은 이 스킬의 `.venv/Scripts/python.exe`, `RESTORE`는 `scripts/restore.py`다. PowerShell에서는 `& 'PYTHON' -B -X utf8 'RESTORE' ...`로 실행한다. 파일 경로는 리터럴로 전달한다.

## 준비와 독립 전사

사용자가 지정한 원본과 새 작업 폴더로 시작한다. 원본이 없을 때만 PDF 목록을 한 번 확인한다. 다른 시험지·fields·evidence를 탐색하지 않는다.

```text
PYTHON RESTORE prepare SOURCE_PDF NEW_JOB
```

반환된 `next` 안내로 한 쪽당 실제 A/B 두 명을 만든다. Antigravity에서는 TypeName `hwp-restoration-reader`, 모델은 inherit를 사용한다. [동봉 정의](../agents/hwp-restoration-reader.md)를 전역 에이전트 위치 `~/.gemini/config/agents/hwp-restoration-reader.md`에 설치한 전용 에이전트다. 도구는 `view_file`, `write_to_file`, `send_message`만 허용한다. 등록되지 않았다면 정의를 설치하고 사용 가능 여부부터 확인하며 무제한 self 에이전트로 조용히 대체하지 않는다.

동시 실행 한도가 작으면 페이지 쌍 단위로 진행한다. 한 A가 여러 쪽을 맡거나 B를 다른 쪽의 A로 재사용하지 않는다. 생성 도구의 **실제 응답 원문**을 안내의 evidence 경로에 저장한다. 가짜 ID·요약으로 배정 증빙을 만들지 않는다.

```text
PYTHON RESTORE ab-assign JOB PAGE --worker-a ACTUAL_A_ID --worker-b ACTUAL_B_ID --evidence ACTUAL_SPAWN_RESPONSE
```

반환된 각 `handoffs[].message`를 해당 ID에게 그대로 보낸다. 전사 task에는 전체 원본·[전사 규칙](ab-reader.md)·저장 경로가 있다. 서로의 결과를 공유하거나 오래된 담당자 안내·도형 안내·명령 모음을 함께 주지 않는다. 메인이 세 번째 전체 OCR을 하거나 긴 입력 프롬프트를 다시 쓰지 않는다. 파일 경로·문항 목록·불확실성만 보고받고 실제 완료 알림으로 대기한다.

## 비교와 원본 판정

```text
PYTHON RESTORE ab-submit JOB PAGE a A_READING_JSON
PYTHON RESTORE ab-submit JOB PAGE b B_READING_JSON
PYTHON RESTORE ab-compare JOB PAGE
```

비교 결과와 생성된 승인 양식을 사용한다. 양식의 `comparison_sha256`는 그대로 보존한다. 재제출이나 의심 문항 추가 뒤에는 최신 반환 양식을 사용한다. 일치 항목의 전체 본문을 다시 출력할 필요는 없다. 정확히 같은 전사라도 두 담당자가 같은 내용을 빠뜨릴 수 있다. 메인은 전체 원본으로 문항 순서·좌우 단·선지/보기·도형 개수가 맞는지 확인하고, 불확실한 작은 기호·지수·부호·배점을 해소한다. 원본의 충분한 판독 없이 승인하지 않는다.

불일치·불확실성은 필요한 범위만 목록으로 요청한다. 이미 일치한 문항이 의심되면 `reason`에 실제 의심 근거를 남긴다. 원본 픽셀 기준 `[x,y,너비,높이]`를 쓰며 축소 표시 크기와 혼동하지 않는다.

```json
[{"id":"q3-sign","question_id":"q3","bbox_px":[100,200,400,150],"coordinate_space":"page"}]
```

```text
PYTHON RESTORE ab-crops JOB PAGE DISPUTE_SPEC_JSON
```

동봉 도구가 crop을 만든다. 문항마다 Python 코드를 새로 쓰거나 전체 문항 crop 목록을 먼저 만들지 않는다. 메인은 필요한 원본 확대를 보고 양식의 resolutions에 선택·원본 확인·판정 사유를 기록한다. 대다수/일치를 근거로 정답을 추정하지 않는다. 페이지가 미완성이거나 issues가 남았다면 해당 담당자가 전사를 고쳐 다시 제출해야 한다. 수정 전사를 제출하면 종전 비교·crop·승인·조합 결과는 무효가 되므로 다시 비교한다.

판정의 `reviewer_id`는 실제 메인 ID다. 세 overview/inventory 확인값은 실제 확인 후 true로 설정한다. resolutions는 문항별 `question_id`, `choice`(`a`/`b`/`custom`/`drop`), `source_checked:true`, `reason`을 기록한다. custom은 올바른 문항 객체를 `question`에 넣고 drop은 원본에 해당 문항이 없음을 확인했을 때만 쓴다. 문항 목록·순서가 달랐다면 승인할 전체 ID 배열 `question_order`도 명시한다. 동의한 문항을 추가로 의심해 표시했다면 그 문항도 판정에 포함한다.

```text
PYTHON RESTORE ab-approve JOB PAGE DECISION_JSON
```

이 승인은 내용 판정이며 최종 수락·출력 검수가 아니다. 반환된 제작 안내를 **기존 A에게만** 전달한다. B에게 제작을 시키거나 도형 담당자를 추가로 생성하지 않는다.

## 한 명의 도형 제작과 조합

A는 [제작 규칙](ab-production.md)에 따라 승인 내용은 그대로 두고 layout·필요한 TeX·그림 목록만 작성한다. 도형 crop이 실제 제작·대조에 필요하면 메인이 `source-views`로 필요한 도형 범위만 만든다. 판독 분쟁용 ab-crops와 도형 제작용 확대는 목적이 다르다. A/B 전사 중에는 어느 쪽도 도형을 렌더하지 않는다.

그림이 있을 때 메인만 `figures`를 실행하고 반환 PNG·검수 파일을 A에게 보낸다. A가 원본과 실제 렌더를 대조해 검수 파일을 작성한 뒤 메인이 `figures-finalize`를 실행한다. 명령·재사용·검수 규칙은 [그림 절차](tikz-exam.md)를 따른다. A는 이 문서의 명령을 직접 실행하지 않는다.

```text
PYTHON RESTORE figures JOB PAGE FIGURES_SPEC_JSON
PYTHON RESTORE figures-finalize JOB PAGE NEW_BATCH_DIR
PYTHON RESTORE ab-compose JOB PAGE LAYOUT_JSON NEW_RESULT_JSON --figures FINALIZED_FIGURES_JSON
PYTHON RESTORE accept JOB NEW_RESULT_JSON
```

그림이 없으면 그림 명령과 `--figures`를 생략한다. compose는 승인된 내용과 관찰한 배치를 연결하고 기존 전체 계약·수식·그림 근거를 검사한다. 메인이 출력 JSON을 손으로 수정하거나 compile-draft로 A/B 승인을 우회하지 않는다. 오류는 실제 문항·위치·메시지를 A에게 보내 해당 부분만 고친다. 본문이 바뀌면 다시 전사 제출·비교·원본 판정부터 진행한다.

## 출력과 실제 대조

`fields.json`에 이번 원본의 실제 정보만 저장한다: `{"school":"학교명","year":"연도","exam_title":"학년·학기·시험명"}`. 모든 쪽을 수락하면 순서대로 실행한다.

```text
PYTHON RESTORE build JOB NEW_RESULT.hwpx --title "실제 시험 제목" --template-fields fields.json
PYTHON RESTORE native JOB NEW_RESULT.build.json NEW_NATIVE_DIR
```

큰 build receipt는 읽지 않고 native에 경로를 전달한다. 새 출력 이름을 사용하며 native의 artifacts가 실제 결과다. 임시는 가능하면 동기화되지 않는 로컬 폴더를 사용한다. 환경 문제는 메인이 해결하고 사용자 한글을 종료하지 않는다.

native의 `review_inputs.pages[].message`를 해당 A에게 그대로 보낸다. A는 실제 원본과 출력 PDF의 누락·오독·잘림·겹침·도형을 대조한다. 수정본은 유효한 A/B 승인과 compose를 거쳐 `revise JOB CORRECTED_JSON`으로 수락하고 새 파일에 재출력·재검수한다. 모든 쪽을 실제 대조하고 미해결 내용이 없을 때 결과를 전달한다. 검수 자료만 실패하면 `review-pack JOB NATIVE_RECEIPT NEW_REVIEW_DIR`로 복구하며 성공한 native를 반복하지 않는다.

## 이전 작업 이어가기

A/B 배정이 없는 기존 작업은 기존 담당자·[작성 규칙](worker-guide.md)·compile-draft → accept/revise 경로를 유지한다. 이전 방식이 명시적으로 필요한 새 작업만 `prepare SOURCE_PDF NEW_JOB --legacy-workers`로 시작하고 반환 안내의 assign-many를 따른다. A/B 작업을 실패했다는 이유로 레거시 작업으로 바꿔 승인 검사를 피하지 않는다.

서브에이전트를 쓸 수 없고 사용자가 **해당 작업의 OCR 대행을 명시 허락한 경우에만** 레거시 assign에 `--mode main_exception --approval ACTUAL_APPROVAL_JSON`을 추가한다. JSON의 `user_message`, `reason`, `allowed_pages`는 실제 발언·사유·허용 쪽이며 `--evidence`는 실제 대화 기록이다. 일반 작업 승인은 대행 허락이 아니다.
