# hwp-exam-restoration v2.7.9 의존성 변화 (공개판 v2.7.3 대비)

**결론: 새로 설치해야 하는 프로그램이나 Python 패키지는 없습니다.** v2.7.3을 쓰던 PC는 스킬 폴더를 바꾸고 MCP를 다시 연결하면 됩니다. 달라진 것은 이미 설치된 패키지를 더 많은 곳에 쓰는 것, TeX에서 자동으로 불러오는 TikZ 라이브러리, 선택 환경 변수, 임시 폴더에 생기는 파일입니다.

## 1. 한눈에 보기

| 구분 | v2.7.3 | v2.7.9 | 사용자 조치 |
|---|---|---|---|
| Python | 3.12 | 3.12 | 없음 |
| `requirements.txt` (5개) | 고정 버전 | **동일** | 없음 |
| `requirements-mcp.txt` (33개) | 고정 버전 | **동일** | 없음 |
| TeX 엔진 | XeLaTeX → LuaLaTeX → pdfLaTeX 순으로 찾음 | 동일 | 없음 |
| TeX 패키지 | standalone, kotex, amsmath, TikZ(pgf) | 동일 | 없음 |
| TikZ 라이브러리 | calc, arrows.meta, angles, quotes, decorations.markings | + intersections, patterns, shapes.arrows | 없음 (pgf에 포함) |
| 한컴오피스 한글 | 필요 | 필요 | 없음 |
| 환경 변수 | `HWP_TIKZ_ENGINE` | + `HWP_MAX_REREVIEWS`, `HWP_PAGE_IMAGE_MODE` (선택) | 없음 |
| 역할 파일 | reader, reviewer | 내용 변경 | 스킬 폴더와 함께 바뀜(전역 사본이 있으면 다시 설치) |
| MCP 도구 | 10개 | 10개(입력·응답 항목 추가) | **MCP 재연결** |

## 2. Python 패키지

`requirements.txt`와 `requirements-mcp.txt`는 v2.7.3과 한 글자도 다르지 않습니다. 새 코드가 쓰는 외부 모듈도 v2.7.3이 쓰던 것(PyMuPDF, Pillow, psutil, jsonschema, pywin32, mcp, pydantic, anyio)뿐입니다. 표준 라이브러리에서는 `concurrent.futures`와 `calendar`를 새로 씁니다.

같은 패키지를 새로 쓰는 곳은 다음과 같습니다.

| 패키지 | 새로 쓰는 곳 | 없을 때 |
|---|---|---|
| PyMuPDF (`fitz`) | 렌더된 도형 PDF에서 선·원·글자 좌표 읽기(`get_drawings`, `get_text('rawdict')`): 기하 실측, 라벨 자동 배치, 문제 글 조건 대조 | 원래 필수 패키지라 설치 점검이 `blocked`를 냄 |
| Pillow | 원본과 렌더의 가로세로 비율 비교, 비교 이미지 | 원래 필수 |
| psutil | 중단된 실행의 정리: 주인이 없어진 잠금 판단(윈도우가 프로세스 번호를 재사용하는 경우 포함), 남겨진 보이지 않는 한글 프로세스 닫기, 출력 대기열 | 실패하지 않음. 잠금을 계속 사용 중으로 보고, 남은 한글을 닫지 못해 다음 빌드가 `existing_hwp_session`으로 멈출 수 있음 |

psutil은 두 요구 목록 모두에 들어 있어, 안내대로 설치했다면 이미 있습니다. 설치 점검의 `native_prerequisites_ready`도 전처럼 psutil을 확인합니다.

**검증:** 새 가상환경에 `requirements-mcp.txt`만 설치하고(`pip check` 이상 없음) 패키지를 풀어 설치 점검과 동봉 테스트 전체를 돌렸습니다. 결과는 [업데이트 내역](CHANGELOG-v2.7.9.md)의 검증 절에 있습니다.

## 3. TeX

- **새 TeX 패키지는 없습니다.** 도형 문서는 전처럼 `standalone`, `kotex`, `amsmath`, `tikz`를 씁니다.
- 폭을 맞추는 도형은 `standalone`의 `tikz` 옵션 대신 `\usepackage{tikz}`를 직접 불러옵니다. 측정용 상자가 쪽 밖에 남게 하려는 것으로, 필요한 패키지는 같습니다.
- 아래 TikZ 라이브러리는 도형에 해당 기능이 있을 때만 엔진이 자동으로 넣습니다. 모두 pgf 배포에 포함되어 있습니다.
  - `intersections`: `name path`, `name intersections`로 교점·접점을 계산할 때
  - `patterns`: `pattern=north east lines` 같은 빗금 음영
  - `shapes.arrows`: `\ExamImplies`(⇨) 보조 명령
- 라벨 측정은 TikZ 명령과 `\typeout`만 쓰므로 엔진에 상관없이 동작합니다. **XeLaTeX, LuaLaTeX, pdfLaTeX** 세 엔진으로 실제 도형 11개를 렌더해 노드·점·라벨 수와 라벨 이동 결과가 모두 같음을 확인했습니다. 한 장당 렌더 시간은 pdfLaTeX 약 2초, XeLaTeX 약 4초, LuaLaTeX 약 6초였습니다.
- 한 쪽의 도형을 최대 4개까지 동시에 컴파일합니다. MiKTeX가 동시에 여러 개가 시작될 때 드물게 TeX 오류 없이 죽는 경우가 있어, 그때만 두 번까지 다시 컴파일합니다.

## 4. 한컴오피스 한글

필요 조건은 같습니다(데스크톱 한글, COM 등록, 자동화 보안 모듈). 달라진 동작은 다음과 같습니다.

- 한글 출력은 PC 전체에서 한 번에 하나만 되므로, 여러 작업이 동시에 빌드하면 **차례를 기다립니다**(최대 10분). 전에는 바로 실패했습니다.
- 출력 도중 CLI가 끊겨 남은 **보이지 않는 한글**은 다음 빌드가 닫습니다. 엔진이 띄운 것으로 기록된 프로세스(번호, 실행 파일, 시작 시각이 모두 일치)만 닫고, 사용자가 연 한글은 건드리지 않습니다.
- 출력 하위 프로세스의 제한 시간을 300초에서 900초로 늘렸습니다. 다른 작업의 출력을 기다리는 시간이 포함되기 때문입니다.
- `hwp_build`는 출력이 끝날 때까지 응답을 붙잡지 않습니다. 최대 30초 기다린 뒤 `building`을 돌려주고, `hwp_status`가 이어서 기다립니다. MCP 클라이언트의 요청 제한 시간(opencode 60초)에 걸리지 않게 하기 위해서입니다.

## 5. 환경 변수 (모두 선택)

| 변수 | 상태 | 기본값 | 뜻 |
|---|---|---|---|
| `HWP_TIKZ_ENGINE` | 기존 | 자동 탐색 | 사용할 TeX 실행 파일 경로 |
| `HWP_MAX_REREVIEWS` | 새로 생김 | 1 | 쪽마다 다시 검수하는 횟수. 0이면 첫 실패를 노트로 남기고 수정 회차 없이 끝냄 |
| `HWP_MCP_WAIT_SECONDS` | 새로 생김 | 30 | 렌더·출력을 한 번의 MCP 응답에서 기다리는 최대 초. 클라이언트의 요청 제한 시간(opencode 60초)보다 짧아야 함 |
| `HWP_PAGE_IMAGE_MODE` | 새로 생김 | 꺼짐 | `gray16`이면 쪽 이미지를 흑백 16단계로 저장. 측정에서 효과가 확인되지 않아 기본은 꺼짐 |

## 6. 임시 폴더에 생기는 파일

`%TEMP%`에 아래 파일이 새로 생길 수 있습니다. 정상 종료 시 지워지고, 실행이 끊겨 남은 것은 다음 빌드가 정리합니다. 사용자가 지울 필요는 없습니다.

| 파일 | 용도 |
|---|---|
| `hwp-restoration-native-queue.lock` | 한글 출력 차례 |
| `hwp-restoration-export-<실행 이름>.json` | 진행 중인 출력이 어느 폴더를 쓰는지(끊긴 출력의 한글을 찾을 때 씀) |
| `hwp-single-<id>.queue.json` | 그 빌드가 차례를 기다리는지 출력 중인지(`hwp_status` 응답용) |

기존의 `hwp-automation-native-session.lock`은 그대로 쓰며, 주인이 없어진 잠금은 이제 자동으로 풉니다. 작업 폴더의 `.job.lock`도 같습니다.

## 7. 스킬 안에 동봉된 구성요소

다른 스킬이나 외부 프로젝트는 여전히 필요 없습니다. `runtime/`에 동봉한 스냅샷 39개 파일 중 14개가 바뀌었고, 추가·삭제된 파일은 없습니다. 해시는 `runtime/sources.json`에 있습니다.

| 구성요소 | 바뀐 파일 | 내용 |
|---|---|---|
| `latex_to_hwpeqn` | 10개(코드 8, 스키마 2) | 수식 띄어쓰기 관행(프라임, 쉼표, 적분 dx, 함수 인자, 행렬 칸) |
| `hwp_automation` | `hwpx_builder.py`, 양식 미리보기 글 | 검수 노트 표, 양식 메타데이터 |
| `native` | `hwp_adapter.py` | 한글 출력 어댑터 |
| `tikz_render.py` | 1개 | 첫 TeX 오류와 위치를 메시지에 적음, 교점·글꼴 오류 안내 |

스킬 파일은 171개에서 197개로 늘었고 삭제된 파일은 없습니다. 새 파일은 스크립트 9개(`restoration_figure_geometry`, `_figure_lint`, `_figure_ratio`, `_labels`, `_montage`, `_native_queue`, `_relations`, `_relation_check`, `_reply`), 도형 핵심 규칙 `references/tikz-rules.md`, 테스트 16개입니다. 모두 스킬 안에 들어 있어 따로 설치할 것이 없습니다.

## 8. 에이전트 호스트

- **역할 파일:** 역할 파일(`agents/hwp-restoration-reader.md`, `hwp-restoration-reviewer.md`)은 스킬 폴더 안에 있고, 서브에이전트가 처음 배정될 때 그 경로를 읽으므로 스킬 폴더를 바꾸면 함께 바뀝니다. 검수 태그, 도형 파일 머리줄, 검수 노트 흐름이 여기에 있습니다. 예전에 `scripts/install_reader_agent.py`로 Antigravity 전역 위치(`.gemini/config/agents/`)에 따로 설치해 두었다면 그 사본도 같은 명령(`--agent-name hwp-restoration-reader`, `hwp-restoration-reviewer`)으로 다시 설치하세요.
- **MCP를 다시 연결하세요.** 첫 `hwp_prepare` 응답의 `runtime_version`이 `2.7.9`여야 합니다.
- 서브에이전트, 이미지 보기, MCP 연결 등 호스트에 필요한 기능은 같습니다. 검증 호스트는 Antigravity CLI(Gemini 3.8 Flash High)와 opencode입니다.
