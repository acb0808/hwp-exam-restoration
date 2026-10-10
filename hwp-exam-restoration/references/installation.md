# 단일 폴더 배포와 설치

ZIP의 `hwp-exam-restoration` 폴더 전체를 에이전트의 스킬 디렉터리에 복사한다. `SKILL.md`뿐 아니라 `assets`, `scripts`, `runtime`, `references`, `examples`를 함께 유지한다. 다른 위치로 이동해도 내부 리소스는 설치 폴더 기준으로 찾는다. **외부 pdf2HWP 저장소나 다른 스킬 설치는 필요 없다.**

- `assets/templates/pdf2hwp-grid`: 동봉 양식·스타일·병합 정보와 자산 해시.
- `runtime`: HWPX builder, 수식 변환기, 소유권 검사 한글 자동화 코드, TikZ 실행기. 출처와 해시는 `runtime/sources.json`에 기록한다.
- `requirements.txt`: Python 라이브러리 버전. 가상환경·한글·TeX·글꼴 설치본은 배포물에 포함하지 않는다.

`pdf2HWP`는 양식의 출처명이다. 외부 프로젝트 경로를 찾거나 `sample/template.hwp`를 별도로 받는 절차가 아니다. 배포물에 개발자의 문서·실행 기록·개인 계정 경로·기존 시험지 원본을 넣지 않는다.

## v2.7.3에서 Antigravity 업데이트

v2.7.3 설치본은 보통 `%USERPROFILE%\.gemini\config\skills\hwp-exam-restoration`에 있다. 먼저 Antigravity가 실제로 검색하는 위치와 현재 사용하는 `SKILL.md`를 확인하고, 기존 스킬 폴더와 `mcp_config.json`을 검색 경로 밖의 날짜별 백업 폴더에 보관한다.

v2.7.3부터 v2.7.9까지 `requirements.txt` 및 `requirements-mcp.txt` 내용은 같고, v2.8.0에서 `numpy` 하나가 늘었다. 기존 설치 폴더를 교체하는 경우 `.mcp-venv`를 유지하고 그 안에서 `pip install -r requirements-mcp.txt`를 한 번 더 실행한다. numpy는 원본 쪽에서 도형 영역을 다시 찾는 데만 쓰이며, 없어도 복원은 동작한다(제작자가 적은 영역을 그대로 쓴다). 새 경로로 옮기는 경우 기존 가상환경은 백업에 남겨두고, 절대 경로가 새 설치 위치에 맞도록 새 `.mcp-venv`를 만든다.

Antigravity가 `%USERPROFILE%\.agents\skills`를 검색한다고 확인된 경우에만 v2.8.0를 그곳에 설치한다. 기존 스킬과 MCP 설정의 백업을 검색 경로 밖에 보관한 채, 이전 `%USERPROFILE%\.gemini\config\skills\hwp-exam-restoration` 폴더를 검색 경로에서 제거해 활성 사본을 하나만 둔다. 새 폴더에서 `.mcp-venv`를 만들고 그 위치에서 등록기를 실행해 MCP 서버 경로를 바꾼 다음 앱을 재시작·재연결한다. 새 위치의 스킬이 선택되고 서버가 연결됐는지 확인한다. 새 위치가 작동하지 않으면 백업을 복구하고 확인된 기존 위치를 교체한다.

새 경로가 검색되는지 확인할 수 없다면 두 번째 위치를 추가하지 않는다. 실제로 검색되는 기존 폴더를 백업하고 v2.8.0 전체 폴더로 교체한 뒤, 그 위치에서 MCP를 다시 등록한다. 검색 가능한 곳에 두 버전의 복사본을 함께 두지 않는다. 사용자 job·원본·결과물은 스킬 설치 폴더와 별도로 보존한다. 설치만 진행할 때는 시험지를 열지 않으며, 다음 복원 작업에서 첫 MCP 응답의 `runtime_version=2.8.0`를 확인한다.

## Python 환경

Python 3.12로 **설치한 스킬 폴더 안에서** 다음을 실행한다. 전역 환경에는 설치하지 않는다.

```powershell
py -3.12 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
& .\.venv\Scripts\python.exe scripts\doctor.py
```

다른 PC의 `.venv`를 복사하지 말고 새로 만든다. 아래 설치 명령의 PYTHON은 전용 가상환경 인터프리터다. 복원 작업의 실행은 연결된 MCP만 사용한다. 작업 결과는 스킬 폴더 밖에 보관하며 업데이트 전 기존 스킬 폴더를 백업한다.

## 로컬 MCP 연결

기존 `.venv`와 분리된 `.mcp-venv`를 설치 폴더 안에 만든다. `requirements-mcp.txt`는 검증한 SDK와 엔진 의존성 버전 목록이다.

```powershell
py -3.12 -m venv .mcp-venv
& .\.mcp-venv\Scripts\python.exe -m pip install -r requirements-mcp.txt
& .\.mcp-venv\Scripts\python.exe scripts\install_mcp_server.py --backup BACKUP_DIR
```

등록기는 기존 `~/.gemini/config/mcp_config.json`을 정확히 백업한 뒤 `hwp-restoration` 서버만 추가/갱신하며 다른 서버 설정을 보존한다. TeX가 PATH에 없으면 `--engine`에 실제 실행 파일 경로를 준다. `--dry-run`은 변경 없이 검사한다. 같은 backup에 `--rollback --dry-run`으로 복구 가능 여부를, `--rollback`으로 실제 복구를 수행한다. 설치 후 변경된 설정은 덮어쓰지 않는다.

### OpenCode에 연결

OpenCode는 Antigravity와 다른 설정 파일을 사용한다. 위 `install_mcp_server.py`는 Antigravity용이므로 OpenCode 설정에 실행하지 않는다. 먼저 OpenCode 설정 파일 `%USERPROFILE%\.config\opencode\opencode.json`을 백업하고, 기존 `mcp` 객체의 다른 서버를 유지하면서 아래 항목을 합친다. `<SKILL>`은 실제 설치 폴더의 절대 경로로 바꾼다. JSON 문자열의 `\`는 `\\`로 쓴다.

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

이 서버 항목에 `timeout`을 넣지 않는다. OpenCode 1.18에서는 이 값이 도구 호출 하나의 제한 시간으로도 쓰여, 예전 안내의 `"timeout": 15000`을 두면 15초를 넘는 `hwp_build`·`hwp_status`가 `Request timed out`으로 끊기고 도구가 사라진다(2026-10-10 실측). 이미 넣었다면 그 줄을 지운다.

이 블록을 기존 설정에 병합한 뒤 OpenCode를 다시 시작하고 `opencode mcp list`에서 `hwp-restoration connected`를 확인한다. 설정 파일이 `opencode.jsonc`라서 주석이 있으면 JSON 도구로 통째로 다시 저장하지 말고, 기존 주석과 항목을 보존해 직접 병합한다. OpenCode는 사용자 스킬 폴더 `~/.agents/skills/hwp-exam-restoration/SKILL.md`도 검색한다. 스킬 폴더가 다른 곳이면 앱의 스킬 경로 설정이나 해당 호스트의 공식 스킬 경로를 따른다.

한 번의 `hwp_build`·`hwp_render_figures` 호출이 결과를 기다리는 시간은 MCP 서버 env의 `HWP_MCP_WAIT_SECONDS`(초)다. 엔진 기본값은 30(렌더는 45)이며 60초에 서버를 끊는 OpenCode에 맞춘 값이므로 OpenCode에서는 바꾸지 않는다. Antigravity 등록기(`install_mcp_server.py`)는 120으로 등록한다: 출력이 그 호출 안에서 끝나 `hwp_status`를 다시 부르지 않는다.

재검수 상한은 MCP 서버 env의 `HWP_MAX_REREVIEWS`로 정한다(쪽마다 재검수 횟수, 기본 1). 상한을 넘겨 다시 실패한 지적은 수정 회차 없이 `미해결`로 검수 노트에 남는다. 0이면 첫 실패를 바로 노트로 남긴다.

업데이트 후 해당 호스트에서 MCP 서버를 재연결한다. 새 채팅이나 도구 목록 갱신만으로 실행 중 Python 코드가 교체됐다고 판단하지 않는다. 첫 `hwp_prepare` 응답의 `runtime_version=2.8.0`를 확인한다. 별도 상태 조회는 필요 없다. 서버 등록·별도 stdio 검사 성공은 현재 채팅의 연결 갱신을 보장하지 않는다. 제작자는 자기 페이지의 제출·렌더·도형 검수를 직접 MCP로 처리하고 메인은 준비·배정·빌드·최종 검수 기록을 맡는다. 공식 문서가 보장하지 않는 MCP 이름을 custom agent tools에 추정해서 넣지 않는다.

MCP는 일반 명령 실행이나 Python 파일 읽기를 노출하지 않는다. 호스트의 별도 view_file까지 파일 종류별로 차단하는 보안 경계는 아니므로 담당자 지침도 함께 설치한다. 원본·검수 보고의 실제성은 호스트 실행 기록으로 확인해야 하며, 로컬 영수증만으로 모델의 시각 확인을 증명하지 않는다.

## 제작자·검수자 역할 지침

Antigravity에서는 두 역할 파일을 전용 서브에이전트 타입으로 설치해 쓴다. 전용 타입은 기본 프롬프트와 기본 도구를 빼고(`excludeDefaultComponents: true`) 필요한 도구와 MCP만 받으므로(`inheritMcp: true`), 서브에이전트의 고정 프롬프트가 약 25,500토큰에서 약 6,000토큰(역할 지침 포함)으로 줄어든다. 이 프롬프트는 호출마다 다시 읽히므로 5시간 한도가 그만큼 덜 든다. 모델은 메인을 상속한다.

```powershell
& .\.mcp-venv\Scripts\python.exe scripts\install_reader_agent.py --backup BACKUP_DIR_1
& .\.mcp-venv\Scripts\python.exe scripts\install_reader_agent.py --agent-name hwp-restoration-reviewer --backup BACKUP_DIR_2
```

설치기는 `~/.gemini/config/agents/`의 두 파일만 바꾸고 이전 파일을 백업한다(`--rollback`으로 복구). 스킬을 업데이트하면 두 명령을 다시 실행한다. 엔진은 설치된 정의가 이 스킬의 파일과 바이트 단위로 같을 때만 전용 타입을 돌려주고, 설치하지 않았거나 이전 버전의 정의가 남아 있으면 기존의 `TypeName=self`(새 문맥과 MCP 상속, 역할 파일을 첫 프롬프트에서 읽음)로 동작한다. 따라서 이 설치는 선택이며, 하지 않아도 복원은 된다. OpenCode는 task general을 그대로 쓴다. define_subagent는 쓰지 않는다. 전용 타입은 Antigravity CLI 1.2.16에서 확인했다(`excludeDefaultComponents`는 CLI 1.2.1 이상).

`tools` 목록에 `call_mcp_tool`을 직접 적으면 레지스트리 오류로 서브에이전트가 시작되지 않는다. MCP는 `inheritMcp: true`로 켠다. 상세 배정·검수 ID 전달 순서는 [SKILL.md의 MCP 절차](../SKILL.md)를 따른다. 이 역할 지침은 도구의 보안 권한을 강제로 제한하는 경계가 아니다. 이전 작업을 새 MCP로 강제로 이어서 처리하지 않는다. 설치 안내의 명령은 환경 설치·진단용이며 복원 중 우회 실행용이 아니다.

## 여러 시험지를 동시에 돌릴 때

CLI를 여러 개 띄워 시험지마다 따로 돌릴 수 있다(5개 동시까지 확인). job 폴더와 출력 경로는 작업마다 달라야 한다.

- 쪽 전사와 도형 렌더는 작업끼리 독립이다. 도형 컴파일은 작업마다 4개까지 동시에 돌아, 5개 작업이면 컴파일 하나가 약 6초에서 20초로 느려진다(제한 60초).
- 한글 출력(`hwp_build`)은 PC 전체에서 한 번에 하나만 된다. 다른 작업이 출력 중이면 차례를 기다렸다가 이어서 진행하며(한 번에 약 20~60초, 최대 10분 대기), 응답은 그동안 돌아오지 않는다. 10분을 넘기면 `native_export_queue_timeout`으로 끝나고 같은 출력 경로로 다시 `hwp_build`를 부르면 된다.
- 사용자가 한글을 직접 열어 둔 상태는 기다리지 않는다. 지금처럼 `existing_hwp_session`으로 알리므로 한글을 닫고 다시 빌드한다.
- TeX 엔진이 TeX 오류 없이 죽으면(MiKTeX가 동시에 여러 개 시작될 때 드물게 발생) 같은 컴파일을 두 번까지 다시 한다.

## 작업을 중단했다가 다시 시작할 때

CLI를 끄거나 작업을 멈춘 뒤 같은 요청(같은 job 폴더, 같은 출력 경로)으로 다시 시작해도 된다.

- `hwp_prepare`가 `already_assigned`를 돌려준다. 모든 쪽이 이미 끝난 뒤에 멈춘 경우(`continue_with=hwp_build`)에는 그 job에서 바로 빌드와 검수만 다시 한다. 쪽 작업이 남은 채 멈춘 경우에는 `restart_job` 경로에 새 job을 만들어 처음부터 다시 한다(멈춘 제작자는 이어받을 수 없다).
- 한글 출력 도중에 멈추면 엔진이 띄운 보이지 않는 한글이 남는다. 다음 빌드가 시작할 때 그 프로세스를 닫는다. 엔진이 시작한 것으로 기록된 프로세스(번호, 실행 파일, 시작 시각이 모두 일치)만 닫으며, 사용자가 연 한글은 건드리지 않는다.
- 멈춘 프로세스가 남긴 잠금 파일(job 폴더의 `.job.lock`, 임시 폴더의 출력 잠금)은 주인이 없어진 것이 확인되면 자동으로 풀린다. 윈도우는 끝난 프로세스의 번호를 곧바로 다시 쓰므로, 번호가 살아 있어도 잠금보다 늦게 시작한 프로세스면 주인이 아닌 것으로 본다.
- 출력이 끊기거나 다른 작업과 겹쳐 실패한 뒤에도 같은 job에서 `hwp_build`를 다시 부를 수 있다. 출력 파일 이름은 쓰지 않은 이름(`_v2` 등)이 자동으로 선택된다.

## 한글과 TikZ 실행 환경

HWP 저장과 실제 출력 검수에는 Windows, 설치된 한컴오피스 한글, COM 자동화/보안 모듈과 템플릿 글꼴이 필요하다. 프로그램·글꼴의 설치본이나 라이선스는 포함하지 않는다. 자동화는 실행 중인 사용자 한글에 연결하거나 종료하지 않는다.

도형 제작에는 XeLaTeX/LuaLaTeX/pdfLaTeX와 TikZ 패키지가 필요하다. PATH에 없다면 `--engine "실제 TeX 실행 파일 경로"` 또는 `HWP_TIKZ_ENGINE`으로 설정한다. PC별 경로를 코드에 고정하지 않는다.

```text
PYTHON scripts/tikz_render.py doctor
PYTHON scripts/tikz_render.py render examples/tikz-smoke.tex EMPTY_PROBE_DIR
```

`doctor`는 자산·동봉 코드·Python 모듈·프로그램 등록 여부를 확인한다. 한글을 실행하거나 문서를 열지 않으므로 실제 출력·폰트·보안 모듈 검증은 별도다. TeX 패키지 검증도 실제 샘플 컴파일이 필요하다. `EMPTY_PROBE_DIR`은 비어 있는 새 출력 폴더로 지정한다.
