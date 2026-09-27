# MCP 실행 절차

첫 hwp_prepare의 runtime_version은 **2.7.3**이어야 한다. 다른 버전이면 배정 전에 재연결을 요청한다. 한글 출력은 이 MCP만 사용한다. 자식 모델·effort는 메인 상속이다.

1. `hwp_prepare(source,job,question_pages,include_answers=true)`로 문제 쪽만 선택한다. 모르면 overview 한 번으로 선택한다. 표지·빈 면·안내·해설은 제외한다.
2. 쪽마다 기본 `TypeName=self`, `Role=hwp-restoration-reader`로 새 서브에이전트 한 명을 만든다. 초기 프롬프트에 이 스킬의 `agents/hwp-restoration-reader.md` 경로와 “배정될 task.md를 받은 뒤 직접 MCP 제출·도형 대조까지 accepted로 완료”를 명시한다. `hwp_assign(job,page,worker_id,evidence)`에 실제 ID와 원문 spawn 응답을 넣고 반환된 worker_instructions 경로를 그 담당자에게 보낸다. 여러 쪽은 items로 묶을 수 있다. 배정 전 제작을 시작시키거나 가짜 ID를 쓰지 않는다.
3. **제작자가 자기 페이지의 제출·오류 수정·렌더·도형 판정을 직접 처리한다.** 메인은 accepted 보고 또는 환경/판독 실패를 기다린다. 원고·이미지·TeX를 읽거나 도구 결과를 중계하지 않는다. 정상 대기는 호스트 알림을 이용하며 반복 상태 조회를 하지 않는다.
4. 전 쪽 accepted이면 `hwp_build(job,output,title,school,year,exam_title)`를 호출한다. 메타데이터는 사용자 제공값/파일명에서만 얻고, 없으면 중립값을 쓴다. 제목 때문에 표지를 읽지 않는다. 수정 빌드에도 동일 output 이름을 주면 기존 결과를 보존한다.
5. 처음 출력 후 제작에 참여하지 않은 `TypeName=self`, `Role=hwp-restoration-reviewer`로 새 서브에이전트 한 명을 만든다. 초기 프롬프트에는 이 스킬의 `agents/hwp-restoration-reviewer.md` 경로와 “실제 ID와 검수 배정을 받을 때까지 대기”를 보낸다. spawn 응답 뒤 **실제 reviewer_id, 반환된 review_tasks 전체, 실제 job 경로, job 내부 report_path**를 메시지 한 번으로 전달한다. 역할 이름을 reviewer_id로 대신하지 않는다. 검수자는 원본·실제 출력·정답을 독립 대조하고 보고서를 그 경로에 저장한다.
6. spawn으로 확인한 실제 reviewer_id와 검수자가 보고한 reviews, report_path만 이용해 `hwp_finish_review(job,reviewer_id,reviews,review_evidence_path=report_path)`를 호출한다. 보고서 내용을 메인이 다시 읽거나 작성할 필요 없다. 통과와 실패를 매 회차 **수정 전에** 기록한다. 잘못된 보고 형식이면 같은 검수자가 보고서만 고친다. 이미지 재열기·재빌드는 불필요하다.
7. 실패 문항의 담당자에게 관찰한 차이만 전달한다. 같은 제작자가 부분 수정과 필요한 MCP 작업을 accepted까지 마친다. 재빌드 후 **새 review_tasks만 같은 검수자**에게 전달하고 새 report_path를 쓴다. 엔진이 재사용한 도형·검산·출력은 재검수하지 않는다. complete일 때만 artifacts를 전달한다. 최종 사용자 보고는 결과 경로·남은 문제만 간결히 쓴다.

현재 CLI 커스텀 에이전트의 MCP 연결 제약 때문에 기본 self 타입을 사용한다(새 문맥과 도구 상속). define_subagent·설정 탐색·설치는 하지 않는다. 제작자/검수자의 작업 지침이 자동 적용됐다고 가정하지 말고 최초 배정 프롬프트에 해당 역할 파일 경로를 포함한다. 메인은 역할 파일의 내용을 길게 복사하지 않는다.

특정 기호가 흐리면 원본을 본 담당자가 `hwp_inspect(job,page,target,requests)`를 쓴다. 각 requests는 question_id,id,bbox_px=[왼쪽,위,폭,높이],reason이며 전 문항 crop은 금지다. 검수자는 필요한 범위를 지정해 메인에게 확대만 요청할 수 있다.

missing_file은 지정 파일 복구, environment는 작업 보존 후 실패 보고다. 같은 실패 호출의 반복, 내부 코드 탐색, 한글/COM/CLI 우회는 하지 않는다. MCP 호출·시간은 자동 계측된다. 제공되지 않은 토큰·할당량을 호출 수로 환산하지 않는다.

제작자의 작업 모드는 task.md의 include_answers에 확정되어 있습니다. 제작자는 작업 지침과 원본을 읽고 전체 reading.md를 먼저 제출합니다. 수식 사전 검사는 제출 검사로 통합되어 별도 호출하지 않습니다. 반환된 오류 설명으로 해결되지 않을 때만 hwp_help(job,page,topic="writing"/"equations"/"figures")를 사용합니다. 도움말은 읽기 전용이며 제출·검수 통과를 대신하지 않습니다.
