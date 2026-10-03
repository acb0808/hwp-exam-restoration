# 설치를 맡은 에이전트에게

사용자가 코드를 직접 편집하지 않아도 v2.7.9 스킬과 로컬 MCP를 쓸 수 있도록 설치한다. 먼저 현재 앱을 확인하고, 해당 앱의 설정만 백업·수정한다. 다른 MCP 항목, 기존 작업 폴더, 실행 중인 한글 문서는 보존한다.

## 준비물

- Windows PC와 데스크톱 한컴오피스 한글
- Python 3.12
- 파일·이미지 읽기, MCP, 페이지 작업용 서브에이전트를 지원하는 AI 에이전트
- 도형을 복원할 경우 TeX 배포판과 TikZ

한글, 글꼴, TeX 또는 유료 모델 서비스가 없으면 필요한 항목만 안내한다. 구매·구독, 보안 기능 해제, API 잔액 충전은 대신 하지 않는다. 설정 파일과 비밀 키 전체를 출력하거나 채팅에 붙여넣지 않는다.

## 스킬 설치

1. [v2.7.9 릴리스](https://github.com/acb0808/hwp-exam-restoration/releases/tag/v2.7.9)에서 `hwp-exam-restoration-2.7.9.zip`과 `SHA256SUMS.txt`를 받는다. SHA-256을 비교하고 압축 경로가 설치 폴더 밖으로 나가지 않는지 확인한다.
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

3. Antigravity에서 `hwp-restoration` 서버를 재연결한다.

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
      "enabled": true,
      "timeout": 15000
    }
  }
}
```

3. OpenCode를 다시 시작하고 `opencode mcp list`에서 `hwp-restoration connected`를 확인한다. 스킬은 전역 `.agents/skills/hwp-exam-restoration/SKILL.md` 경로에서 찾는다.

## 사용할 도구와 버전

연결된 도구는 아래 10개다.

`hwp_prepare`, `hwp_assign`, `hwp_inspect`, `hwp_help`, `hwp_submit_reading`, `hwp_render_figures`, `hwp_review_figures`, `hwp_build`, `hwp_finish_review`, `hwp_status`.

첫 `hwp_prepare` 응답의 `runtime_version`이 `2.7.9`인지 확인한다. 다르면 제작자를 배정하지 말고 해당 앱의 MCP 연결을 새로고침한다. 이 서버는 로컬 stdio MCP다. 브라우저 채팅만으로 한글 문서를 만들 수 없다.

OpenCode는 페이지 제작자를 `task` 일반 에이전트로 위임하고, Antigravity는 기본 `self` 유형을 사용한다. 두 경우 모두 첫 배정 메시지에서 스킬 폴더의 `agents/hwp-restoration-reader.md` 또는 `agents/hwp-restoration-reviewer.md` 경로를 읽도록 지정한다. 새 custom 도구나 에이전트 유형을 임의 등록하지 않는다.

## 설치 완료 보고

쉬운 한국어로 설치 폴더, 버전, MCP 연결 상태, 백업 경로, Python·한글·TeX 준비 상태와 사용자가 직접 해야 할 재연결만 알린다. 설치 확인과 실제 시험지 출력은 구분하고, 사용자가 요청하지 않은 시험지나 유료 벤치마크를 실행하지 않는다.