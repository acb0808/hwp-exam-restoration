# 단일 폴더 배포와 설치

ZIP의 `hwp-exam-restoration` 폴더 전체를 에이전트의 스킬 디렉터리에 복사한다. `SKILL.md`뿐 아니라 `assets`, `scripts`, `runtime`, `references`, `examples`를 함께 유지한다. 다른 위치로 이동해도 내부 리소스는 설치 폴더 기준으로 찾는다. **외부 pdf2HWP 저장소나 다른 스킬 설치는 필요 없다.**

- `assets/templates/pdf2hwp-grid`: 동봉 양식·스타일·병합 정보와 자산 해시.
- `runtime`: HWPX builder, 수식 변환기, 소유권 검사 한글 자동화 코드, TikZ 실행기. 출처와 해시는 `runtime/sources.json`에 기록한다.
- `requirements.txt`: Python 라이브러리 버전. 가상환경·한글·TeX·글꼴 설치본은 배포물에 포함하지 않는다.

`pdf2HWP`는 양식의 출처명이다. 외부 프로젝트 경로를 찾거나 `sample/template.hwp`를 별도로 받는 절차가 아니다. 배포물에 개발자의 문서·실행 기록·개인 계정 경로·기존 시험지 원본을 넣지 않는다.

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

업데이트 후 Antigravity의 해당 MCP 서버를 재연결한다. 새 채팅이나 도구 목록 갱신만으로 실행 중 Python 코드가 교체됐다고 판단하지 않는다. 첫 hwp_prepare 응답의 runtime_version=2.7.3로 실제 로드된 구현을 확인한다. 별도 상태 조회는 필요 없다. 서버 등록·별도 stdio 검사 성공은 현재 채팅의 연결 갱신을 보장하지 않는다. 제작자는 자기 페이지의 제출·렌더·도형 검수를 직접 MCP로 처리하고 메인은 준비·배정·빌드·최종 검수 기록을 맡는다. 공식 문서가 보장하지 않는 MCP 이름을 custom agent tools에 추정해서 넣지 않는다.

MCP는 일반 명령 실행이나 Python 파일 읽기를 노출하지 않는다. 호스트의 별도 view_file까지 파일 종류별로 차단하는 보안 경계는 아니므로 담당자 지침도 함께 설치한다. 원본·검수 보고의 실제성은 호스트 실행 기록으로 확인해야 하며, 로컬 영수증만으로 모델의 시각 확인을 증명하지 않는다.

## 제작자·검수자 역할 지침

v2.7.3은 기본 `TypeName=self`의 새 문맥과 MCP 상속을 사용한다. 각각 `agents/hwp-restoration-reader.md`, `agents/hwp-restoration-reviewer.md`를 최초 배정에서 읽도록 한다. 모델·effort는 메인을 상속한다. 전용 custom 타입 등록이나 define_subagent는 정상 복원에 필요 없다. 기존 전역 역할 파일이 있어도 이 실행 경로는 동봉된 역할 지침을 명시해서 사용한다.

현재 CLI에서 custom tools 목록의 `call_mcp_tool` 등록은 레지스트리 오류를 일으킬 수 있다. self 방식의 실제 자식 MCP 호출을 검증했으며, 상세 배정·검수 ID 전달 순서는 [MCP 절차](mcp-workflow.md)를 따른다. 이 역할 지침은 도구의 보안 권한을 강제로 제한하는 경계가 아니다. 이전 작업을 새 MCP로 강제로 이어서 처리하지 않는다. 설치 안내의 명령은 환경 설치·진단용이며 복원 중 우회 실행용이 아니다.

## 한글과 TikZ 실행 환경

HWP 저장과 실제 출력 검수에는 Windows, 설치된 한컴오피스 한글, COM 자동화/보안 모듈과 템플릿 글꼴이 필요하다. 프로그램·글꼴의 설치본이나 라이선스는 포함하지 않는다. 자동화는 실행 중인 사용자 한글에 연결하거나 종료하지 않는다.

도형 제작에는 XeLaTeX/LuaLaTeX/pdfLaTeX와 TikZ 패키지가 필요하다. PATH에 없다면 `--engine "실제 TeX 실행 파일 경로"` 또는 `HWP_TIKZ_ENGINE`으로 설정한다. PC별 경로를 코드에 고정하지 않는다.

```text
PYTHON scripts/tikz_render.py doctor
PYTHON scripts/tikz_render.py render examples/tikz-smoke.tex EMPTY_PROBE_DIR
```

`doctor`는 자산·동봉 코드·Python 모듈·프로그램 등록 여부를 확인한다. 한글을 실행하거나 문서를 열지 않으므로 실제 출력·폰트·보안 모듈 검증은 별도다. TeX 패키지 검증도 실제 샘플 컴파일이 필요하다. `EMPTY_PROBE_DIR`은 비어 있는 새 출력 폴더로 지정한다.
