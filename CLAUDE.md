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
