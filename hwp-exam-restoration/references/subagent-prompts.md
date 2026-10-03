# 담당자에게 넘길 현재 경로

최신 single-review MCP에서 `hwp_prepare.spawn_requests`는 쪽·역할만 반환한다. `next.spawn_requests[].message`는 없다. 각 문제 쪽에 hwp-restoration-reader를 한 명 생성하고, 실제 반환된 worker ID와 생성 응답을 `hwp_assign`에 전달한다.

`hwp_assign.worker_instructions` 경로를 해당 담당자에게 보내면 task.md에 원본 이미지·Markdown 규칙·정답 블록 지침이 있다. 메인은 그 파일이나 제작 결과를 다시 읽지 않고, 담당자가 보고한 reading.md·TeX 경로만 해당 MCP 도구에 제출한다.

도형 렌더 실패 시 `errors`의 ID·진단을 같은 제작자에게 보낸다. TeX/컴파일 오류만 수정·재렌더하고 환경·무결성 오류는 반복하지 않고 보고한다. 성공하면 반환된 `review_tasks`만 같은 제작자에게 보낸다. 최종 출력 뒤에는 `review_tasks`만 독립 검수자에게 보낸다. TeX를 수정했다는 보고가 있으면 옛 렌더 batch를 검수 제출하지 않고 현재 도형 목록으로 다시 렌더한다. 수정 후 제출하더라도 엔진이 원본 TeX 해시가 바뀐 옛 batch를 거절한다.
