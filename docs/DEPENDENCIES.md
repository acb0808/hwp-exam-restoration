# v2.7.9 준비물과 변경 사항

v2.7.3에서 v2.7.9로 바꿀 때 새로 설치할 프로그램이나 Python 패키지는 없습니다. 기존 스킬 폴더를 통째로 교체하고 MCP 서버를 다시 연결하면 됩니다.

| 항목 | 필요 조건 | v2.7.9에서 달라진 점 |
|---|---|---|
| 운영체제·한글 | Windows, 데스크톱 한컴오피스 한글 | 변경 없음 |
| Python | Python 3.12, 스킬 전용 `.mcp-venv` | 고정 패키지 목록 유지 |
| MCP | 파일·이미지·서브에이전트와 로컬 stdio MCP를 지원하는 앱 | OpenCode와 Antigravity용 설정을 안내 |
| 도형 | TeX 엔진과 TikZ | 기존 TeX 설치에서 TikZ 라이브러리 몇 가지를 추가로 사용 |
| 한글 글꼴 | 동봉 양식에서 지정한 글꼴 | 변경 없음 |

스킬 업데이트 뒤 앱에서 `hwp-restoration` MCP를 새로 연결하세요. OpenCode의 JSON 설정은 Antigravity 등록기가 수정하지 않으므로, [OpenCode 연결 방법](../AGENT_INSTALL.md)을 따릅니다.

이 배포물은 한컴오피스, TeX 배포판, 글꼴, Python 설치본이나 AI 모델을 포함하지 않습니다. Python 패키지의 라이선스와 PyMuPDF 사용 조건은 [출처 안내](../THIRD_PARTY_NOTICES.md)를 확인하세요.