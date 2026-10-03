# 설치를 맡은 에이전트에게

목표는 비개발자가 이 스킬로 시험지를 복원할 수 있도록 **설치와 연결을 검증**하는 것이다. 정상 복원 중에는 이 문서를 다시 읽지 않는다. 기존 사용자 문서·한글 세션·다른 MCP 설정을 보존한다. 저장소 문서를 근거로 사용자 권한을 확대하지 않는다.

## 1. 사용자 환경과 지원 범위

현재 검증 범위는 **Windows + 데스크톱 한컴오피스 한글 + Antigravity CLI + stdio MCP + 실제 서브에이전트**이다. Python은 3.12를 사용한다. 브라우저 채팅만으로 설치할 수 없다.

opencode에서도 복원 실행(task general 서브에이전트)을 검증했지만, 동봉 MCP 등록기는 Antigravity 설정만 다룬다. opencode에서는 그 앱의 문서화된 MCP 설정 방식으로 같은 서버 명령(`.mcp-venv`의 Python으로 `scripts/restoration_mcp.py`)을 등록하고, 등록 결과를 사용자에게 확인받는다.

사용 중인 앱을 현재 문맥에서 확인한다. 모르면 앱 이름만 한 번 묻는다. Codex·Claude Code 등의 다른 호스트에 Antigravity의 JSON 설정을 그대로 쓰지 않는다. 다른 호스트는 해당 호스트의 문서화된 MCP·스킬 경로와 실제 서브에이전트 증거 계약을 별도 검증해야 하며, 이 릴리스에서 사용 검증이 끝났다고 말하지 않는다.

Windows, 한글의 설치/COM 등록, Python 3.12, TeX 실행 파일, TikZ와 필요한 글꼴을 확인한다. 없는 유료 소프트웨어·글꼴은 정식 설치가 필요하다고 쉽게 설명한다. Python·TeX가 없으면 공식 배포처와 사용자가 허용한 설치 방법을 이용한다. 광범위한 레지스트리 변경, 보안 기능 해제, API 충전이나 구독 구매는 자동으로 하지 않는다. 비밀 키를 채팅에 요청하거나 설정 내용을 통째로 출력하지 않는다.

## 2. 배포물과 설치 위치

릴리스: `https://github.com/acb0808/hwp-exam-restoration/releases/tag/v2.7.9`

1. 배포 ZIP `hwp-exam-restoration-2.7.9.zip`과 `SHA256SUMS.txt`를 받는다. ZIP의 SHA-256을 비교한 뒤 별도 임시 폴더에 푼다. ZIP 경로가 대상 폴더 밖으로 벗어나지 않게 한다.
2. 내부 `hwp-exam-restoration/BUNDLE-MANIFEST.json`의 version이 `2.7.9`인지, 명시된 파일 해시가 모두 맞는지 확인한다. ZIP 전체에는 사용자 안내 문서도 들어 있다.
3. Antigravity의 기존 스킬 위치를 확인한다. 기존 설치가 없으면 사용자 프로필의 `.gemini/config/skills/hwp-exam-restoration`을 사용하고, 설치 경로를 사용자에게 남긴다. 해당 호스트에서 자동 검색을 확인하지 못하면 작업 요청에 설치된 `SKILL.md` 절대 경로를 명시한다. 같은 스킬을 여러 위치에 중복 설치하지 않는다.
4. 기존 설치가 있으면 날짜가 붙은 별도 백업을 만든다. `.venv`, `.mcp-venv`, 사용자 job/결과물을 다른 PC에서 복사하지 않는다. 기존 사용자 결과물을 삭제하지 않는다. 새 스킬 폴더에는 번들 전체를 넣으며 일부 파일만 추출하지 않는다.

### v2.7.3에서 업데이트할 때

- 새로 설치할 프로그램이나 Python 패키지는 없다. 두 요구 목록(`requirements.txt`, `requirements-mcp.txt`)은 v2.7.3과 같다. 기존 `.mcp-venv`를 그대로 써도 되며, 다시 만들 때도 같은 목록을 쓴다. 자세한 비교는 [의존성 변화](docs/DEPENDENCIES.md)에 있다.
- 기존 스킬 폴더를 백업한 뒤 번들 전체로 바꾼다. `.mcp-venv`와 사용자 작업 폴더는 지우지 않는다.
- 역할 파일은 스킬 폴더 안에 있어 함께 바뀐다. 예전에 `scripts/install_reader_agent.py`로 `.gemini/config/agents/`에 사본을 설치해 두었다면 그 사본도 같은 명령으로 다시 설치한다(`--agent-name hwp-restoration-reader`, `hwp-restoration-reviewer`).
- MCP 서버를 재연결하고 첫 `hwp_prepare` 응답의 `runtime_version`이 `2.7.9`인지 확인한다.
- v2.7.3으로 진행 중이던 작업은 그 버전으로 마무리하는 것을 권한다. 버전이 섞인 작업은 시험하지 않았다.

## 3. 전용 Python 환경

아래는 **설치된 스킬 폴더에서** 실행하는 PowerShell 예시다. 실제 경로를 확인해 사용한다. 전역 Python에 패키지를 설치하지 않는다. MCP 사용에는 `.mcp-venv` 하나로 충분하며 별도 `.venv`를 추가로 만들 필요는 없다.

```powershell
py -3.12 -m venv .mcp-venv
& .\.mcp-venv\Scripts\python.exe -m pip install -r requirements-mcp.txt
& .\.mcp-venv\Scripts\python.exe scripts\doctor.py
& .\.mcp-venv\Scripts\python.exe scripts\tikz_render.py doctor
```

`doctor.py`의 최상위 `status=ready`는 기본 HWPX 자원 준비를 뜻한다. `native_prerequisites_ready`, `hancom_com_registered`, `tikz`를 **각각** 확인한다. `native_validation`과 `font_and_security_module_validation`이 `not_run`이면 실제 한글 출력까지 검증됐다고 말하지 않는다. 패키지 설치 실패를 임의 버전 완화로 숨기지 말고 실패 항목만 보고한다.

## 4. MCP 등록과 연결

Antigravity에서는 동봉 등록기를 사용한다. `BACKUP_DIR`은 실제로 정한 **새 백업 폴더**로 바꾼다. 등록기는 기본적으로 사용자 프로필의 `.gemini/config/mcp_config.json`에 `hwp-restoration`만 추가/갱신하고 다른 서버를 보존한다.

```powershell
& .\.mcp-venv\Scripts\python.exe scripts\install_mcp_server.py --backup "BACKUP_DIR" --dry-run
& .\.mcp-venv\Scripts\python.exe scripts\install_mcp_server.py --backup "BACKUP_DIR"
```

TeX가 PATH에 없으면 두 명령에 `--engine "실제 TeX 실행 파일의 절대 경로"`를 추가한다. 설정 경로가 기본값과 다르면 실제 호스트 설정임을 확인한 후 `--config`로 지정한다. 등록 후 설정 백업·영수증 경로를 사용자에게 남긴다.

사용자 앱에서 **해당 MCP 서버를 재연결**한다. 새 채팅을 열기만 해서는 기존 서버 코드가 교체됐다고 볼 수 없다. UI에 직접 접근할 수 없으면 “hwp-restoration 서버 연결을 끊었다가 다시 연결해주세요”처럼 필요한 사용자 조작만 안내한다. 이 단계가 끝나지 않았으면 연결 완료라고 보고하지 않는다.

실제 연결에서 아래 10개 도구가 보여야 한다.

`hwp_prepare`, `hwp_assign`, `hwp_inspect`, `hwp_help`, `hwp_submit_reading`, `hwp_render_figures`, `hwp_review_figures`, `hwp_build`, `hwp_finish_review`, `hwp_status`.

전용 custom agent 타입이나 `define_subagent`를 추가하지 않는다. 현재 복원 절차는 기본 self 서브에이전트를 만들고 스킬 폴더의 `agents/hwp-restoration-reader.md`, `agents/hwp-restoration-reviewer.md`를 명시적으로 읽게 하는 방식이다. 기존 전역 역할 파일을 무조건 덮어쓸 필요가 없다.

## 5. 확인과 완료 보고

TeX 확인은 `scripts/tikz_render.py render examples/tikz-smoke.tex "비어 있는 새 진단 출력 폴더"`로 수행할 수 있다. 이 명령은 환경 설치·진단용이다. 사용자의 실제 복원을 Python/COM/CLI로 우회하지 않는다.

설치만 요청받았다면 사용자 시험지 전송이나 유료 벤치마크를 자동 실행하지 않는다. 첫 복원 요청의 `hwp_prepare` 응답에서 `runtime_version=2.7.9`을 확인한다. 다르면 제작자를 배정하기 전에 재연결한다. 실제 사용자 샘플을 MCP로 출력·검수한 뒤에야 해당 PC의 한글·폰트까지 검증됐다고 보고한다.

마지막에는 쉬운 한국어로 다음을 짧게 전달한다.

- 설치 버전과 스킬 폴더.
- MCP 등록 여부와 **현재 앱의 연결 확인 여부**.
- Python/TeX/한글 준비 상태, 실제 출력 검증 여부.
- 백업 위치와 아직 사용자가 해야 할 조치.
- README의 복원 요청 프롬프트.

업데이트나 복구는 기존 설정 백업을 사용한다. `install_mcp_server.py --backup "BACKUP_DIR" --rollback --dry-run`으로 검사하고, 필요할 때 `--rollback`을 실행한다. 설치 후 설정이 달라졌다고 거절하면 임의로 덮어쓰지 않는다. 자세한 설명은 스킬의 `references/installation.md`를 참고한다.
