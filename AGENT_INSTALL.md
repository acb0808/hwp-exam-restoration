# 설치를 맡은 에이전트에게

사용자가 코드를 직접 편집하지 않아도 v2.8.0 스킬과 로컬 MCP를 쓸 수 있도록 설치한다. 먼저 현재 앱을 확인하고, 해당 앱의 설정만 백업·수정한다. 다른 MCP 항목, 기존 작업 폴더, 실행 중인 한글 문서는 보존한다.

## 준비물

- Windows PC와 데스크톱 한컴오피스 한글
- Python 3.12
- 파일·이미지 읽기, MCP, 페이지 작업용 서브에이전트를 지원하는 AI 에이전트
- 도형을 복원할 경우 TeX 배포판과 TikZ

한글, 글꼴, TeX 또는 유료 모델 서비스가 없으면 필요한 항목만 안내한다. 구매·구독, 보안 기능 해제, API 잔액 충전은 대신 하지 않는다. 설정 파일과 비밀 키 전체를 출력하거나 채팅에 붙여넣지 않는다.

## 스킬 설치

1. [v2.8.0 릴리스](https://github.com/acb0808/hwp-exam-restoration/releases/tag/v2.8.0)에서 `hwp-exam-restoration-2.8.0.zip`과 `SHA256SUMS.txt`를 받는다. SHA-256을 비교하고 압축 경로가 설치 폴더 밖으로 나가지 않는지 확인한다.
2. ZIP 안의 `hwp-exam-restoration` 폴더 전체를 사용자 전역 스킬 경로 `~/.agents/skills/hwp-exam-restoration`에 설치한다. 기존 폴더가 있으면 덮기 전에 별도 백업을 만든다. `.mcp-venv`, 사용자 시험지와 작업 결과는 새로 복사하지 않는다.
3. 설치 폴더에서 전용 MCP 환경을 만든다. 전역 Python에는 패키지를 설치하지 않는다.

```powershell
py -3.12 -m venv .mcp-venv
& .\.mcp-venv\Scripts\python.exe -m pip install -r requirements-mcp.txt
& .\.mcp-venv\Scripts\python.exe scripts\doctor.py
& .\.mcp-venv\Scripts\python.exe scripts\tikz_render.py doctor
```

TeX가 PATH에 없다면 등록 환경에 실제 TeX 실행 파일을 지정한다. PC에서 실제 한글 파일을 열어 보지 않았다면 설치 보고에 한글 출력까지 확인했다고 쓰지 않는다.

## Antigravity 연결

1. 사용 중인 MCP 설정 파일 `~/.gemini/config/mcp_config.json`이 맞는지 확인한다. 기존 파일과 다른 서버 항목을 보존한다.
2. 새 백업 폴더를 정하고 스킬 폴더에서 등록기를 실행한다.

```powershell
& .\.mcp-venv\Scripts\python.exe scripts\install_mcp_server.py --backup "BACKUP_DIR" --dry-run
& .\.mcp-venv\Scripts\python.exe scripts\install_mcp_server.py --backup "BACKUP_DIR"
```

3. 제작자·검수자 전용 서브에이전트 정의를 설치한다(선택, 권장). 설치하면 서브에이전트의 고정 프롬프트가 약 1/4로 줄어 한도가 덜 든다. 설치하지 않으면 기존 `self` 유형으로 동작한다. 스킬을 업데이트할 때마다 다시 실행한다.

```powershell
& .\.mcp-venv\Scripts\python.exe scripts\install_reader_agent.py --backup "BACKUP_DIR_1"
& .\.mcp-venv\Scripts\python.exe scripts\install_reader_agent.py --agent-name hwp-restoration-reviewer --backup "BACKUP_DIR_2"
```

4. Antigravity에서 `hwp-restoration` 서버를 재연결한다.

## 이전 버전에서 업데이트할 때

v2.7.9는 보통 `%USERPROFILE%\.agents\skills\hwp-exam-restoration`에, v2.7.3의 Antigravity 스킬은 보통 `%USERPROFILE%\.gemini\config\skills\hwp-exam-restoration`에 있다. 설치 위치를 추측하지 말고 현재 선택된 `SKILL.md`와 Antigravity가 실제로 검색하는 경로를 확인한다. 진행 중인 복원은 현재 버전으로 마친 뒤 업데이트한다.

먼저 설치된 버전을 스킬 폴더의 `BUNDLE-MANIFEST.json`에서 확인한다. 이미 v2.8.0이면 바꾸지 않는다. 저장소의 `docs/CHANGELOG-v2.8.0.md`를 읽고(설치된 버전이 v2.7.9보다 오래됐으면 `docs/CHANGELOG-v2.7.9.md`도 읽는다), 사용자의 버전에서 달라지는 점을 쉬운 한국어로 알린다: 새로 할 수 있는 일, 고쳐진 문제, 비용과 시간의 변화, 사용할 때 달라지는 점, 알려진 한계. 문서에 없는 내용은 덧붙이지 않는다.

v2.8.0에서 달라진 설치 사항:

- Python 패키지 `numpy`가 하나 늘었다(선택). 기존 `.mcp-venv`를 그대로 쓰면 `pip install -r requirements-mcp.txt`를 한 번 더 실행한다. 없어도 복원은 동작하고, 엔진이 원본에서 도형 영역을 다시 찾는 기능과 선 종류 읽기만 꺼진다.
- Antigravity는 등록기(`install_mcp_server.py`)를 다시 실행한다. 한 호출의 대기 시간이 120초로 등록되어 출력 뒤의 상태 조회가 줄어든다. 전용 서브에이전트 정의(`install_reader_agent.py`)도 위 3번대로 설치한다.
- OpenCode는 `mcp.hwp-restoration` 항목에 `"timeout": 15000`이 있으면 그 줄을 지운다(아래 OpenCode 연결 참고).
- 이미 v2.7.9가 `%USERPROFILE%\.agents\skills`에 있으면 그 폴더를 백업한 뒤 같은 자리에서 `.mcp-venv`는 남기고 나머지를 v2.8.0 전체 폴더로 교체한 다음 MCP를 재연결한다.

아래 두 경우는 v2.7.3의 위치(`%USERPROFILE%\.gemini\config\skills`)에서 올 때의 절차다.

- **Antigravity가 `%USERPROFILE%\.agents\skills`를 검색하는 것이 확인된 경우:** 기존 스킬 폴더와 `%USERPROFILE%\.gemini\config\mcp_config.json`을 날짜가 있는 백업 위치(검색 경로 밖)에 먼저 보관한다. v2.8.0 전체 폴더를 `%USERPROFILE%\.agents\skills\hwp-exam-restoration`에 설치하고, 이전 `%USERPROFILE%\.gemini\config\skills\hwp-exam-restoration` 사본은 검색 경로에서 제거한다. 새 위치에서 `.mcp-venv`를 만들고 등록기를 실행해 MCP 경로를 갱신한 뒤 앱을 재시작·재연결한다. 기존 `.mcp-venv`는 백업에 보존하되, 새 경로에서 재사용하지 않는다.
- **`%USERPROFILE%\.agents\skills` 검색 여부를 확인할 수 없는 경우:** 새 위치를 추가하지 않는다. 실제로 검색되는 기존 스킬 폴더와 MCP 설정을 백업한 뒤 그 위치의 스킬 폴더를 v2.8.0 전체 폴더로 교체한다. 기존 `.mcp-venv`와 다른 MCP 항목은 유지하고 등록기를 실행해 `hwp-restoration` 경로만 갱신한다.

두 경우 모두 검색 가능한 경로에는 활성 스킬 폴더를 하나만 둔다. MCP 재연결만으로 스킬 선택 위치가 바뀌지는 않으므로 앱이 새 `SKILL.md` 경로를 읽는지 확인한다. 설치 중 시험지를 열거나 `hwp_prepare`를 실행하지 않는다. 다음 복원의 첫 응답에서 `runtime_version=2.8.0`을 확인하고, 사용자 작업 폴더와 결과물은 이동·삭제하지 않는다. 백업과 다른 MCP 항목은 그대로 보존한다.

## OpenCode 연결

OpenCode의 전역 설정은 Windows에서 `%USERPROFILE%\.config\opencode\opencode.json`이다. OpenCode는 Antigravity와 설정 형식이 다르므로 `install_mcp_server.py`를 OpenCode 설정에 사용하지 않는다.

1. 설정 파일을 별도 백업한다. 이미 `mcp` 객체가 있으면 그 안에 서버 항목 하나만 합친다. 기존 파일에 주석이 있거나 `opencode.jsonc`를 쓰면 파일 전체를 JSON으로 다시 저장하지 말고 기존 형식을 보존한다.
2. `<SKILL>`을 설치 폴더의 실제 절대 경로로 바꿔 아래 항목을 `mcp` 객체에 추가한다. JSON에서는 경로의 역슬래시를 두 번 쓴다.

```json
{
  "mcp": {
    "hwp-restoration": {
      "type": "local",
      "command": [
        "<SKILL>\\.mcp-venv\\Scripts\\python.exe",
        "-B",
        "-X",
        "utf8",
        "<SKILL>\\scripts\\restoration_mcp.py"
      ],
      "cwd": "<SKILL>\\scripts",
      "environment": { "PYTHONUTF8": "1" },
      "enabled": true
    }
  }
}
```

이 항목에 `timeout`을 넣지 않는다. OpenCode 1.18에서는 이 값이 도구 호출 하나의 제한 시간으로도 쓰여, 이전 안내의 `"timeout": 15000`을 두면 15초를 넘는 `hwp_build`와 `hwp_status`가 `Request timed out`으로 끊긴다. 이미 넣었다면 그 줄을 지운다.

3. OpenCode를 다시 시작하고 `opencode mcp list`에서 `hwp-restoration connected`를 확인한다. 스킬은 전역 `.agents/skills/hwp-exam-restoration/SKILL.md` 경로에서 찾는다.

## 사용할 도구와 버전

연결된 도구는 아래 11개다(v2.8.0에서 `hwp_submit_review`가 늘었다).

`hwp_prepare`, `hwp_assign`, `hwp_inspect`, `hwp_help`, `hwp_submit_reading`, `hwp_render_figures`, `hwp_review_figures`, `hwp_build`, `hwp_submit_review`, `hwp_finish_review`, `hwp_status`.

첫 `hwp_prepare` 응답의 `runtime_version`이 `2.8.0`인지 확인한다. 다르면 제작자를 배정하지 말고 해당 앱의 MCP 연결을 새로고침한다. 이 서버는 로컬 stdio MCP다. 브라우저 채팅만으로 한글 문서를 만들 수 없다.

OpenCode는 페이지 제작자를 `task` 일반 에이전트로 위임한다. Antigravity는 엔진이 돌려준 유형을 쓴다: 위의 전용 정의를 설치했으면 `hwp-restoration-reader`·`hwp-restoration-reviewer`, 아니면 기본 `self`다. OpenCode와 `self`에서는 첫 배정 메시지에서 스킬 폴더의 `agents/hwp-restoration-reader.md` 또는 `agents/hwp-restoration-reviewer.md` 경로를 읽도록 지정한다. 설치기가 만드는 두 정의 말고는 새 custom 도구나 에이전트 유형을 임의 등록하지 않는다.

## 설치 완료 보고

쉬운 한국어로 설치 폴더, 버전, MCP 연결 상태, 백업 경로, Python·한글·TeX 준비 상태와 사용자가 직접 해야 할 재연결만 알린다. 업데이트였다면 이전 버전과 새 버전, 변경 내용 문서에서 읽은 달라지는 점도 함께 알린다. 설치 확인과 실제 시험지 출력은 구분하고, 사용자가 요청하지 않은 시험지나 유료 벤치마크를 실행하지 않는다.
