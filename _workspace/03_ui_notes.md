# UI 구현 노트 (ui-dev)

> 작성: ui-dev · 2026-06-01 · 대상: QA / packager / core-engine-dev

## 1. 실행법

```powershell
pip install -r requirements.txt   # 또는: pip install "PySide6>=6.8,<6.10"
python -m mdviewer                 # 빈 창(웰컴 화면)
python -m mdviewer samples/demo.md # 파일 열기(CLI 인자 / 파일 연결프로그램)
```

진입점: `mdviewer.app:main() -> int`  (`__main__.py` → `app.main()`).
QApplication 의 app/org 이름을 **윈도우 생성 전에** 설정해 QSettings 경로를 고정한다.

## 2. 코어 호출 지점 (계약대로 호출, 재구현 안 함)

| 위치 (main_window.py) | 호출 | 비고 |
|---|---|---|
| `_reload_current()` | `read_markdown(path)` | FileNotFoundError/OSError 만 잡음(계약대로) |
| `_reload_current()` | `render(text, base_dir=path.parent)` | 예외 안 던짐 전제. RenderResult 반환 |
| `_set_document()` | `theme.wrap_document(result.html, dark)` | 본문 → 완전 문서 셸 조립 |
| `_populate_toc()` | `result.toc` (list[TocItem]) | level/text/anchor 사용 |
| `__init__` | `FileWatcher(on_changed=self._bridge.fileChanged.emit)` | 콜백=워커스레드 → Signal emit |

**graceful import**: `renderer`/`file_watcher` 가 없어도 앱은 뜬다(병렬 개발 대비).
- 코어 미존재 시 원문 미리보기 폴백 + 웰컴 화면에 안내 배너.
- **현재 상태: 코어 연결 확인됨(`render`/`read_markdown`/`pygments_css`/`FileWatcher` 모두 존재).**

## 3. 경계 규칙 준수

- renderer 는 `<body>` 본문만 반환 → **theme.py 의 `wrap_document(body, dark)` 가 셸 조립**.
  테마 전환 = `_reload_current(preserve_scroll=True)` 로 CSS만 바꿔 다시 감쌈.
- file_watcher 콜백은 워커 스레드 → `_WatchBridge.fileChanged` Signal → QueuedConnection
  으로 메인 스레드 `_on_file_changed()` → 120ms 디바운스 타이머 → `_reload_current()`.
  (UI 직접 조작 없음 — 크래시 방지.)
- 상대경로 이미지: `view.setHtml(html, QUrl.fromLocalFile(str(문서폴더)+"/"))`.
- 스크롤 보존: 외부 변경/테마 전환 시 `runJavaScript("[scrollX,scrollY]")` 로 캡처 후
  `loadFinished` 에서 `window.scrollTo(...)` 복원.

## 4. 단축키

| 단축키 | 동작 |
|---|---|
| Ctrl+O | 열기 |
| Ctrl+R | 새로고침 |
| Ctrl+T | 테마 전환(라이트/다크) |
| Ctrl+= / Ctrl++ | 확대 |
| Ctrl+- | 축소 |
| Ctrl+0 | 줌 100% |
| F11 | 전체화면 토글 |
| Ctrl+\\ | 목차(TOC) 패널 토글 |
| Ctrl+Q | 종료 |
| 최근파일 1~9 | 메뉴 가속키 &1..&9 |

메뉴: 파일(열기/최근 파일▸/새로고침/종료) · 보기(테마/줌3종/전체화면/목차) · 도움말(정보).
툴바: 열기·새로고침·줌·테마·목차. 드래그앤드롭으로 로컬 파일 열기 지원.

## 5. TOC 사이드 패널

좌측 `QListWidget`(스플리터). `result.toc` 로 채움. 항목 클릭 →
`document.getElementById(anchor).scrollIntoView()`. **anchor == 본문 헤딩 id**(계약 §3.1)
이므로 일치 확인됨(demo.md 의 한글 앵커 `#코드-하이라이트` 등 정상 동작).

## 6. 영속화 위치 (QSettings)

- Windows 레지스트리: `HKEY_CURRENT_USER\Software\MDViewer\MDViewer`.
- 키: `recent_files`(최대 10, 중복제거, 최신 위), `theme`(light|dark),
  `window/geometry`, `window/state`, `view/toc_visible`, `view/zoom`.
- 래퍼: `settings.py` 의 `Settings` 클래스(`mdviewer.settings`).

## 7. 리소스

- 본문 CSS: `resources/styles/github-light.css`, `github-dark.css`
  (GitHub 스타일, 토큰 색은 미포함 — Pygments CSS가 담당). `paths.resource_path()` 경유 로드.
- 아이콘: `resources/icons/app.ico` 없으면 graceful 무시(packager 가 처리).
- **Pygments CSS 출처 우선순위**(theme.py `_pygments_css`):
  1) `mdviewer.renderer.pygments_css(dark)` ← **현재 이것 사용 중(존재 확인)**
  2) Pygments 런타임 생성(`.highlight` 스코프)
  3) `styles/pygments-{light,dark}.css` 파일
  4) 빈 문자열(하이라이트만 비활성)

## 8. 스모크 결과 (offscreen)

`QT_QPA_PLATFORM=offscreen` 로 검증 완료:
- 앱 부팅 / MainWindow 생성 / demo.md 열기 OK.
- TOC 18개 생성, 한글 앵커 정상, 코드 하이라이트 토큰 span 적용, 다크/라이트 셸 조립 OK.
- 테마 전환·줌·TOC 토글·최근파일 동작 OK.

---

## ⚠️ core-engine-dev 에게 (경계면 확인 요청)

1. **`pygments_css` 스코프 차이**: 헬퍼가 반환하는 CSS 가 **`.highlight` 로 스코프되지
   않은 bare 셀렉터**(`pre { line-height:125% }`, `span.n {...}`, `.codehilite {...}`)를
   포함한다. 토큰 색(`.n`,`.k`,`.mi` 등)은 bare 라 동작하지만, `pre { line-height:125% }`
   같은 **전역 pre 규칙이 본문 CSS 와 섞일 수 있다**(현재는 무해). 또한 코드블록 래퍼가
   **`.codehilite`** 인데 내 본문 CSS 의 하이라이트 보정은 `.highlight` 기준이었다 →
   theme.py 는 core 헬퍼를 우선 쓰므로 색상엔 문제 없으나, 향후 일관성을 위해
   **래퍼 클래스(`.codehilite` vs `.highlight`)와 CSS 스코프를 통일**하면 좋겠다.
   → 현재 통합은 정상 동작하므로 blocker 아님. 합의만 부탁.

이 외 RenderResult / TocItem / read_markdown / FileWatcher 시그니처는 계약과 100% 일치 확인.
