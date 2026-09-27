# 출처와 외부 구성요소

이 저장소의 프로젝트 코드와 문서는 루트 LICENSE에 따라 제공합니다. 외부 프로그램·서비스·라이브러리·글꼴의 권리를 함께 부여하는 것은 아닙니다.

- 동봉 런타임에는 이 개발 작업에서 사용하던 `hwp-automation`, `hwp-exam-studio`, `using-math-edu`, `diagram-restoration`, `latex_to_hwpeqn` 구성요소의 스냅샷이 포함됩니다. 원본 상대 경로와 파일 해시는 `hwp-exam-restoration/runtime/sources.json`에 기록되어 있습니다. 다른 스킬 설치는 요구하지 않습니다.
- 시험지 양식의 내부 출처명은 `pdf2hwp-grid`입니다. 양식 파일과 해시는 `assets/templates/pdf2hwp-grid/profile.json`에 있습니다. 실제 학생 자료·시험지 원본을 배포하는 것이 아닙니다.
- Python 패키지는 ZIP에 설치본을 포함하지 않고 `requirements-mcp.txt`를 통해 별도로 설치합니다. 각 패키지의 라이선스는 해당 프로젝트의 조건을 따릅니다. 특히 PyMuPDF는 AGPL 또는 상용 라이선스로 제공되므로 사용·재배포 형태에 맞게 해당 조건을 확인하세요: https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright
- 한컴오피스 한글, 자동화/보안 모듈, 한컴 글꼴은 포함하지 않습니다. 각 제품의 정식 이용 조건을 따릅니다.
- TeX 배포판·TikZ, AI 에이전트와 Gemini 등 모델 서비스는 별도 설치·계정이 필요하며 각 제공자의 조건을 따릅니다.

프로그램 이름과 상표는 기능과 호환 환경을 설명하기 위한 것입니다. 이 배포물이 해당 회사의 공식 제품이거나 제휴 제품이라는 뜻은 아닙니다.
