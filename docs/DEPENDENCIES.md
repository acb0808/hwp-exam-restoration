# v2.8.0 준비물과 변경 사항

v2.7.9에서 v2.8.0으로 바꿀 때 새로 설치할 프로그램은 없습니다. Python 패키지는 `numpy` 하나가 늘었습니다(선택). 기존 스킬 폴더를 통째로 교체하고, 기존 `.mcp-venv`에서 `pip install -r requirements-mcp.txt`를 한 번 더 실행한 뒤 MCP 서버를 다시 연결하면 됩니다.

| 항목 | 필요 조건 | v2.8.0에서 달라진 점 |
|---|---|---|
| 운영체제·한글 | Windows, 데스크톱 한컴오피스 한글 | 변경 없음 |
| Python | Python 3.12, 스킬 전용 `.mcp-venv` | `numpy` 추가. 없어도 복원은 동작하고, 엔진이 원본에서 도형 영역을 다시 찾는 기능과 선 종류 읽기만 꺼집니다 |
| MCP | 파일·이미지·서브에이전트와 로컬 stdio MCP를 지원하는 앱 | 도구가 11개가 됩니다(`hwp_submit_review` 추가). OpenCode 설정 예시에서 `timeout` 줄을 뺐습니다 |
| Antigravity | 등록기 `install_mcp_server.py` | 다시 실행하면 한 호출의 대기 시간이 120초로 등록됩니다. 전용 서브에이전트 정의(`install_reader_agent.py`)를 설치하면 한도가 덜 듭니다(선택) |
| 도형 | TeX 엔진과 TikZ | 변경 없음 |
| 한글 글꼴 | 동봉 양식에서 지정한 글꼴 | 변경 없음 |

스킬 업데이트 뒤 앱에서 `hwp-restoration` MCP를 새로 연결하세요. OpenCode의 JSON 설정은 Antigravity 등록기가 수정하지 않으므로, [OpenCode 연결 방법](../AGENT_INSTALL.md)을 따릅니다.

이 배포물은 한컴오피스, TeX 배포판, 글꼴, Python 설치본이나 AI 모델을 포함하지 않습니다. Python 패키지의 라이선스와 PyMuPDF 사용 조건은 [출처 안내](../THIRD_PARTY_NOTICES.md)를 확인하세요.
