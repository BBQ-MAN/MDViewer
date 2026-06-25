# MDViewer

Python/PySide6 기반 크로스플랫폼(Windows·macOS) 데스크톱 마크다운 뷰어.

## 하네스: MDViewer 빌드

**목표:** 마크다운 파일을 열어서 보는 Windows 데스크톱 앱을, 설계→코어/UI 병렬 구현→QA→exe 패키징의 에이전트 팀으로 개발한다.

**트리거:** MDViewer 개발 관련 작업(앱 생성, 기능 추가/수정/보완, 렌더링·PySide6 UI·exe 빌드/패키징, "다시 실행/재실행/업데이트") 요청 시 `mdviewer-orchestrator` 스킬을 사용하라. 단순 질문은 직접 응답 가능.

**기술 스택:** PySide6 (QWebEngineView), markdown-it-py / Pygments, watchdog, PyInstaller. 범위: 파일 열기·실시간 렌더링·코드 하이라이트·목차/링크·다크모드·최근 파일.

**변경 이력:**
| 날짜 | 변경 내용 | 대상 | 사유 |
|------|----------|------|------|
| 2026-06-01 | 초기 구성 (에이전트 5 + 스킬 6) | 전체 | - |
| 2026-06-01 | 초기 빌드 실행 완료 (설계→구현→QA 53/53→exe) | src/, tests/, dist/ | 첫 전체 파이프라인 |
| 2026-06-01 | frozen exe 진입점 상대 import 크래시 수정 | run_mdviewer.py, mdviewer.spec | exit 1 → 정상 실행 |
| 2026-06-01 | 원격(http) 이미지 표시 수정 (LocalContentCanAccessRemoteUrls) | main_window.py | file:// 출처 페이지가 외부 이미지 차단하던 문제 |
| 2026-06-01 | macOS 빌드 지원 추가 (.app/.dmg, QFileOpenEvent, 크로스플랫폼 spec, CI, 스킬 macOS 레퍼런스) | app.py, mdviewer.spec, build_macos.sh, .github/, packager 스킬·에이전트 | 맥용 배포 요청 |
| 2026-06-24 | 클립보드 붙여넣기 미리보기 + .md 저장 기능 (Workflow 오케스트레이션: 설계→core/ui 병렬→QA→적대 검증) | renderer.py, main_window.py, __init__.py, requirements.txt, tests/test_convert.py | 다른 프로그램 복사본을 임시로 보고 저장 요청 |
| 2026-06-25 | 인라인 소스 편집기 + Editor/Preview/Split 뷰 모드 + 라이브 디바운스 프리뷰 (편집중 외부변경 보호·self-write 억제) | main_window.py, settings.py | 원본 편집·프리뷰 전환/동시 표시 요청 |
| 2026-06-25 | WYSIWYG 워드프로세서 모드 (Ctrl+4): 프리뷰=contentEditable 편집surface + 서식 툴바 + execCommand/innerHTML 폴링 → html_to_markdown 동기화 (재렌더 게이트로 커서 보존·flush·루프방지, JS자산 0) | main_window.py, settings.py | 프리뷰에서 실시간 문서 꾸미기 요청 |
| 2026-06-25 | 새 문서(Ctrl+N) + 워드프로세서식 상단 툴바(파일/편집/보기 그룹·QStyle 아이콘·툴팁) + Undo/Redo(활성 surface 라우팅) + 통합 서식 디스패치(에디터=마크다운 토글/WYSIWYG=execCommand)·제목 드롭다운 | main_window.py | 새 파일 기능·워드프로세서 UI 요청 |
| 2026-06-25 | Word(.docx)/PDF 내보내기 (설계→core/ui 병렬→QA→BUG-01 1회 수정 재검증→패키징→적대 검증). Word=신규 코어 exporter.py(python-docx, HTML walk 매핑, 변환 비전파/OSError 전파, lxml 지연 import 격리). PDF=UI 오프스크린 QWebEnginePage.printToPdf(라이트 강제·A4·_pdf_page/_pdf_busy 생명주기). 파일▸내보내기 서브메뉴+툴바 PDF 버튼·빈문서 게이팅·flush/WYSIWYG 캡처 | exporter.py(신규), main_window.py, __init__.py, requirements.txt, pyproject.toml, mdviewer.spec(collect_all docx/lxml), tests/test_export.py(신규) | Word·PDF 내보내기 요청 |
| 2026-06-25 | 표(table) 삽입 + 행/열 편집 (서식 툴바 "표" 버튼→행×열 다이얼로그, Editor=GFM 골격/표블록 파싱·재구성, WYSIWYG=DOM JS). 설계→ui 구현→QA(단위 41/41)→적대 검증 | main_window.py(TableBlock+순수함수 8종·액션), tests/test_table.py(신규) | 마크다운 표 작성/편집 요청 |
