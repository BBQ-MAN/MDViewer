# UI 구현 노트 (ui-dev)

> 작성: ui-dev · 2026-06-01 · 갱신: 2026-06-24(Phase 6 클립보드/저장) · 대상: QA / packager / core-engine-dev

## 0. Phase 6 변경 요약 (클립보드 붙여넣기 · scratch 저장)

설계서: `_workspace/06_clipboard_feature_design.md`. `main_window.py` 만 수정(소유권).

**문서 모델 도입** — `MainWindow` 상태가 `_path` 단일에서 3개로 확장:
- `_doc_text: str` — 현재 표시 중 마크다운 소스. **렌더 입력원**(과거엔 매번 `read_markdown(_path)` 재독 → scratch 렌더 불가였음).
- `_path: Path | None` — `None` = scratch(미저장 임시 문서).
- `_dirty: bool` — 미저장 변경 여부(타이틀 `•` 표시).

**렌더 경로 분리** — 기존 `_reload_current(preserve_scroll)` 제거, 두 책임으로 분리:
- `_load_from_disk(preserve_scroll=...)` — 디스크에서 `read_markdown`→`_doc_text` 갱신 후 렌더(+`_set_dirty(False)`). 열기/외부변경/Ctrl+R 전용. scratch 면 no-op→False.
- `_render_doc(preserve_scroll=...)` — `_doc_text` 만 렌더(재독 없음). 테마전환·붙여넣기 직후 사용 → scratch 에서도 동작, 디스크 재독 제거로 약간 빠름.
- `reload_current()` — Ctrl+R 슬롯. scratch 면 안내만(새로고침 대상 없음).

**신규 액션 3개**(단축키 충돌 없음):
- `act_paste`(Ctrl+Shift+V) → `paste_clipboard()` : 클립보드 HTML 있으면 `html_to_markdown(core)`, 변환 결과 공백이면 plain `text()` 폴백, 둘 다 없으면 현재 문서 유지+안내. 결과를 `_set_scratch()` 로 scratch 전환.
- `act_save`(Ctrl+S) → `save()` : scratch 면 `save_as()` 위임, 파일연결이면 같은 경로 즉시 저장.
- `act_save_as`(Ctrl+Shift+S) → `save_as()` : 항상 `QFileDialog.getSaveFileName`(*.md), 확장자 없으면 `.md` 보정.
- 둘 다 `_write_to(path)` 경유 → `write_markdown(core)` 를 **try/except OSError** 로 감싸 실패 시 `QMessageBox` + dirty 유지.

**watch 생명주기**(통합 버그 단골 — `_set_scratch`/`_attach_path` 헬퍼로 일원화):
- 열기/저장성공 = `watcher.watch(path)` (`_attach_path`), scratch 전환(붙여넣기) = `watcher.stop()` (`_set_scratch`).

**타이틀/dirty 일원화** — `_set_dirty(b)`→`_update_title()`. `_path` 있으면 파일명, 없으면 "제목 없음", dirty 면 앞에 `•`. 기존 `open_path` 직접 `setWindowTitle` 제거.

**closeEvent 가드** — 맨 앞에 `_maybe_discard()`(저장/버림/취소). open_path·paste 진입 시에도 동일 가드. 데이터 유실 방지.

---

> 아래는 초기(Phase 1~5) 노트. Phase 6 변경점은 §0 우선.

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
| `_load_from_disk()` | `read_markdown(path)` → `_doc_text` | FileNotFoundError/OSError 만 잡음(계약대로) |
| `_render_doc()` | `render(_doc_text, base_dir=...)` | 예외 안 던짐 전제. base_dir=`_path.parent` 또는 scratch 기준. RenderResult 반환 |
| `paste_clipboard()` | `html_to_markdown(mime.html())` | **항상 str**(None 불가) → `.strip()` 안전. plain 폴백은 `mime.text()` |
| `_write_to()` | `write_markdown(path, _doc_text)` | **OSError 전파** → try/except 로 잡아 QMessageBox |
| `_set_document()` | `theme.wrap_document(result.html, dark)` | 본문 → 완전 문서 셸 조립 |
| `_populate_toc()` | `result.toc` (list[TocItem]) | level/text/anchor 사용 |
| `__init__` | `FileWatcher(on_changed=self._bridge.fileChanged.emit)` | 콜백=워커스레드 → Signal emit |

**graceful import**: `renderer`/`file_watcher` 가 없어도 앱은 뜬다(병렬 개발 대비).
- 코어 미존재 시 원문 미리보기 폴백 + 웰컴 화면에 안내 배너. `html_to_markdown`/`write_markdown` 도 폴백 정의(태그제거 평문 / utf-8 newline="" 쓰기).
- **현재 상태: 코어 연결 확인됨(`render`/`read_markdown`/`html_to_markdown`/`write_markdown`/`pygments_css`/`FileWatcher` 모두 존재, `_CORE_AVAILABLE=True`).**
- **경계면 스모크 통과**: `html_to_markdown("<h1>Title</h1>...")` → `"# Title\n\n**bold** [link](x)"`(str, 링크/강조 보존), `""`/`None`→`""`. `write_markdown` → 부모 자동생성·BOM 없음·CRLF 보존, `read_markdown` 라운드트립 일치.

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
| Ctrl+Shift+V | 클립보드를 마크다운으로 붙여넣기(→ scratch 임시 문서) |
| Ctrl+S | 저장(scratch 면 Save As) |
| Ctrl+Shift+S | 다른 이름으로 저장 |
| Ctrl+R | 새로고침 |
| Ctrl+T | 테마 전환(라이트/다크) |
| Ctrl+= / Ctrl++ | 확대 |
| Ctrl+- | 축소 |
| Ctrl+0 | 줌 100% |
| F11 | 전체화면 토글 |
| Ctrl+\\ | 목차(TOC) 패널 토글 |
| Ctrl+Q | 종료 |
| 최근파일 1~9 | 메뉴 가속키 &1..&9 |

메뉴: 파일(열기/최근 파일▸/─/붙여넣기/저장/다른이름저장/─/새로고침/─/종료) · 보기(테마/줌3종/전체화면/목차) · 도움말(정보).
툴바: 열기·새로고침·─·붙여넣기·저장·─·줌·테마·목차. 드래그앤드롭으로 로컬 파일 열기 지원.

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
