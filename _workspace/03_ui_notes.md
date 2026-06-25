# UI 구현 노트 (ui-dev)

> 작성: ui-dev · 2026-06-01 · 갱신: 2026-06-25(Phase 8 WYSIWYG 라이브 편집) · 대상: QA / packager / core-engine-dev

## 0-B. Phase 8 변경 요약 (WYSIWYG 라이브 편집 — 4번째 뷰 모드)

설계서: `_workspace/08_wysiwyg_feature_design.md`(계약). **소유권 준수**: `main_window.py` 수정 + `settings.py` 에 `_VALID_VIEW_MODES` 1개 값 추가. **`renderer.py`/`file_watcher.py`/`theme.py`/`requirements.txt`/`mdviewer.spec` 무변경**(core 변경 0, theme 무변경 = 런타임 querySelector 권장안 채택, §2.1 A). **JS 자산 0**(모든 JS 는 파이썬 문자열 리터럴 → `runJavaScript`).

**4번째 뷰 모드 `MODE_WYSIWYG="wysiwyg"`(Ctrl+4)** — 프리뷰(`self.view`)를 contentEditable 편집 surface 로 사용. 기존 `_mode_group` QActionGroup 에 `act_mode_wysiwyg` 합류(4개 상호배타). 보기 메뉴/메인 툴바/About 갱신(About 에 Ctrl+4 + WYSIWYG 정규화 안내 1줄).

**`_apply_view_mode` 전이 훅(★)**: prev_mode 보관 → (A) `prev==WYSIWYG and mode!=WYSIWYG` 면 `_view_mode` 갱신 **전에** `_exit_wysiwyg()` → 가시성/툴바/액션체크 → (B) `mode==WYSIWYG and prev!=WYSIWYG` 면 setVisible **후** `_enter_wysiwyg()`. `prev==mode` 가드로 동일모드 재클릭 시 enter/exit 둘 다 skip(폴링 유지). 가시성: WYSIWYG=editor hide / view show / format_toolbar show / toc 사용자토글값.

**진입 `_enter_wysiwyg()`**: `_flush_pending_edit()`(소스 디바운스) → **front-matter 가드**(`_has_front_matter` = `^\s*---\r?\n`; 있으면 QMessageBox Yes/No, No 면 `_apply_view_mode(PREVIEW)` 강등) → `render(_doc_text, base_dir)` → `theme.wrap_document` → `_wysiwyg_active=True` / `_wysiwyg_last_html=None` / `_wysiwyg_pending_setup=True` → `view.setHtml`(★ `_render_doc` 미경유, 1회 프로그램적 렌더) → `_populate_toc`(진입 1회만). `loadFinished`(`_on_load_finished`)에서 `_wysiwyg_pending_setup` 면 `_activate_editable_then_baseline()` 호출 → JS 로 `article.markdown-body` 에 `id=md-editable`+`contenteditable=true`+focus 부여, 콜백에서 초기 innerHTML 을 베이스라인으로 저장 + `_wysiwyg_poll.start()`.

**편집 캡처(폴링)**: `_wysiwyg_poll` QTimer 400ms(WYSIWYG 동안만 active) → `_wysiwyg_poll_tick` → `_capture_wysiwyg_once(final=False)` → `runJavaScript(getElementById('md-editable').innerHTML)` 콜백 → `_ingest_wysiwyg_html`. **`_ingest` 규율(★★)**: 베이스라인(`_wysiwyg_last_html`)과 **다를 때만** `html_to_markdown(html)`→`_doc_text` 갱신+`_set_dirty(True)`. **절대 `_render_doc`/`_set_document`/`setHtml`/`_populate_toc` 안 부름**(커서/선택/스크롤 보존). 진입 직후 무변화는 베이스라인 동일 → no-op(dirty 오염·무한진동 차단).

**서식 툴바 `formatToolbar`(신규 QToolBar, WYSIWYG 에서만 setVisible)**: `_build_format_toolbar` 에서 `act_fmt_*` 생성. 명령→`_exec_format`→`page().runJavaScript`: 굵게 `bold`·기울임 `italic`·취소선 `strikeThrough`; H1/H2/H3/본문 `formatBlock(H1/H2/H3/P)`; 불릿 `insertUnorderedList`·번호 `insertOrderedList`; 인용 `formatBlock(BLOCKQUOTE)`; 인라인코드=커스텀 JS(`getSelection`/`extractContents`/`insertNode <code>`, 선택없으면 no-op); 링크=`QInputDialog.getText`→`createLink`; 서식지우기 `removeFormat`+`formatBlock(P)`. 각 명령 후 `_capture_wysiwyg_once(final=False)` 즉시 1회 캡처(폴링 백업). **포맷 액션엔 단축키 부여 안 함**(전역 Ctrl+B 충돌 회피, v1 클릭 전용).

**역렌더 게이트(★★ 무한루프/커서 방지)**: `_render_doc` 맨 앞 `if self._wysiwyg_active: return`. `_commit_editor_to_preview` 도 동일 방어(소스 편집기 숨김이라 도달 안 하지만). `_on_external_change_settled` 에 **가드 C**: `if self._wysiwyg_active:` → 배너만(편집 surface 덮어쓰기 금지). 단 진입 시 1회 setHtml 은 게이트를 우회(`_enter_wysiwyg` 직접 호출).

**이탈/flush(★ 비동기)**: `_exit_wysiwyg()` = `poll.stop()` → `_wysiwyg_active=False` → `_capture_wysiwyg_once(final=True)`(마지막 flush, 비동기 콜백) → editable 해제 JS. final 콜백 안에서 다음 모드가 Editor/Split 면 `_sync_editor_from_doc()` 보정(§2.4 경합 — 마지막 글자 유실 방지). **이탈은 화면을 재렌더하지 않음**(편집 결과가 이미 화면에 있으므로 가시성만 전환, flush 는 `_doc_text` 만 확정).

**저장(★ WYSIWYG 비동기)**: `save()` 가 `_wysiwyg_active` 면 `_save_after_wysiwyg_capture()`(캡처 콜백 **안에서** `_write_to`/`save_as` 수행) → 마지막 타이핑까지 반영(폴링 tick 전 저장해도 유실 없음). `_write_to`/`write_markdown` 무변경.

**테마 전환(WYSIWYG, 편집 보존)**: `toggle_theme` 가 `_wysiwyg_active` 면 `poll.stop()` → 캡처 콜백 안에서 `_doc_text` 확정 → `_wysiwyg_active=False` → `_enter_wysiwyg()`(새 테마로 재진입, setHtml 1회). 커서가 문서 처음으로 이동할 수 있음(v1 수용, 상태바 안내).

**문서 교체(open/paste/Ctrl+R)**: 각 진입부 `_maybe_discard()` 통과 후 `_leave_wysiwyg_for_document_change()`(=`_apply_view_mode(PREVIEW)` → `_exit_wysiwyg` 자동) 호출 → 새 문서는 일반 렌더. **진입은 오직 Ctrl+4(또는 QSettings 복원)로만**.

**종료/discard 경합 안전**: `closeEvent` 가 `_wysiwyg_active` 면 `_exit_wysiwyg()` 후 `processEvents(~200ms)` 로 캡처 콜백 안착 → 이후 `_maybe_discard` 의 Save 가 최신 `_doc_text` 사용. `_maybe_discard` 의 Save 분기도 WYSIWYG 면 `save()` 후 `processEvents` 로 비동기 write 안착 + `return not self._dirty`(문서 교체 전 옛 _path 로 쓰기 완료 보장).

**복원 가드**: `__init__` 복원부 `if restored==MODE_WYSIWYG and not self._doc_text: restored=MODE_PREVIEW`(빈 문서 WYSIWYG 어색함 방지). `_show_welcome()` 를 `_apply_view_mode` 전에 호출(빈 첫 실행은 항상 Preview 로 강등되므로 무해).

**라운드트립 한계(★ QA/문서)**: `innerHTML → html_to_markdown(html2text) → _doc_text` 는 비가역 정규화 가능 — 목록 마커(`-`/`*`/`+`) 통일, 단락/줄바꿈 재배치(`body_width=0` 로 wrap 만 방지), 코드블록 언어 힌트 일부 유실, **front-matter 유실(→ 진입 경고/차단으로 방어)**. 내용은 보존. WYSIWYG↔소스 반복 전환 시 표기 통일됨. About 다이얼로그에 1줄 안내.

**검증(offscreen)**: `QT_QPA_PLATFORM=offscreen` + `QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu --in-process-gpu --no-sandbox'` 에서 WYSIWYG 스모크 **20/20 PASS** — 진입(모드/active/툴바/가시성), loadFinished 후 `contenteditable==true` 확인, 베이스라인 캡처, 진입 직후 not-dirty, 편집 주입→폴링 캡처→`**BOLD-ADDED**` 마크다운 변환→`_doc_text`+dirty, execCommand bold no-crash, 이탈 후 active=False/poll stop/툴바 hide/`_doc_text` 보존, 역렌더 게이트, front-matter 감지. 기존 `pytest tests/` **78 passed, 1 skipped**(회귀 없음). 모드 리터럴 settings↔main 동일 확인.

**frozen 자산**: 신규 .js/datas/hiddenimports/런타임 의존성 **0**. `mdviewer.spec`/`requirements.txt` 무변경. packager 작업 = WYSIWYG setHtml→editable→execCommand frozen 스모크 1회만(인라인 JS 라 경로의존 없음).

---

## 0-A. Phase 7 변경 요약 (인라인 편집기 + 뷰 모드 Editor/Preview/Split + 라이브 프리뷰)

설계서: `_workspace/07_editor_feature_design.md`. **소유권 준수**: `main_window.py` 수정 + `settings.py` 에 뷰모드 키 1개 추가. `renderer.py`/`file_watcher.py`/`requirements.txt`/`mdviewer.spec` **무변경**(core 변경 0 목표 달성).

**신규 위젯 — `QPlainTextEdit` 편집기**: `self.editor`(objectName `sourceEditor`). 고정폭 글꼴(`QFontDatabase.systemFont(FixedFont)`), `LineWrapMode.NoWrap`, `setTabChangesFocus(False)`, `setAcceptDrops(False)`(파일 드롭은 메인 윈도우 `dropEvent`→`open_path` 가 처리, 현행 유지). `textChanged`→`_on_editor_text_changed`.

**레이아웃 — 중첩 스플리터**: `centralWidget = splitter[toc_list, editor_preview_split]`, `editor_preview_split(objectName editorPreviewSplit) = [editor, view]` (stretch 1:1, 기본 sizes [550,550]). 바깥 splitter sizes [240,860]. 모드별 가시성은 `editor`/`view`/`toc_list` **위젯 단위 setVisible**(스플리터 자체는 항상 표시, 자식 hide 시 핸들 자동 접힘). split 비율 영속은 범위 외(매 실행 50:50).

**뷰 모드 3종(상호배타 라디오)**:
- 상수 `MODE_EDITOR="editor"` / `MODE_PREVIEW="preview"` / `MODE_SPLIT="split"` (settings 의 `_VALID_VIEW_MODES` 리터럴과 동일 — 순환 import 회피).
- 액션 `act_mode_editor`(Ctrl+1) / `act_mode_preview`(Ctrl+2) / `act_mode_split`(Ctrl+3), `QActionGroup setExclusive(True)`. 슬롯 `set_mode_editor/preview/split` → `_apply_view_mode`.
- `_apply_view_mode(mode, persist=True)`: (1)`_view_mode` 저장 (2)EDITOR/SPLIT 진입 시 `_sync_editor_from_doc` (3)editor/view setVisible (4)TOC 게이팅(프리뷰 보일 때만 + 사용자 토글값, `act_toggle_toc.setEnabled(show_preview)`) (5)액션 체크 갱신 (6)포커스(편집기 보이면 editor, 아니면 view) (7)QSettings 저장 (8)상태바 안내. **`_maybe_discard` 미호출**(모드 전환=데이터 변경 아님, dirty 불변). **프리뷰 재렌더 안 함**(setHtml 재로딩이 스크롤 튐 유발 — 가시성만 변경).
- 기본 Preview, `__init__` 마지막에 `_apply_view_mode(settings.view_mode(), persist=False)` 로 복원. 초기 `_doc_text==""` 라 EDITOR/SPLIT 복원돼도 편집기 동기화 no-op.

**라이브 프리뷰(디바운스, `_doc_text` 단일 진실원)**:
- `_render_timer`(SingleShot, 300ms) → `_commit_editor_to_preview`.
- 사용자 타이핑 → `_on_editor_text_changed`: `if _suppress_editor_signal: return`; `_set_dirty(True)`(즉시 미저장 표시); `_render_timer.start()`(연속 입력 합침).
- 디바운스 만료 → `_commit_editor_to_preview`: `_doc_text = editor.toPlainText()`; `_render_doc(preserve_scroll=True)`. `_doc_text` 갱신은 디바운스 만료 시에만, dirty 는 즉시.
- 역방향(`_doc_text`→편집기) `_sync_editor_from_doc`: **이중 가드**(`_suppress_editor_signal=True` + `editor.blockSignals(True)`)로 textChanged 억제. 표시상 동일하면 `setPlainText` 생략(커서/undo 보존). 삽입 지점: `_load_from_disk`·`_set_scratch` 의 `_render_doc` 다음 줄.

**★ CRLF 라운드트립 주의(QA 필독)**: `read_markdown` 은 바이트 충실 반환이라 Windows CRLF 파일은 `_doc_text` 에 `\r\n` 으로 들어온다. 그러나 **`QPlainTextEdit.toPlainText()` 는 CRLF/CR 을 LF 로 정규화**해 돌려준다. 그래서 (a) `_sync_editor_from_doc` 의 no-op 가드는 `_normalize_newlines(_doc_text)` 와 비교(CRLF 문서에서도 불필요한 재채움/커서 손실 방지), (b) **사용자가 Editor/Split 에서 실제로 편집 후 저장하면 디바운스 commit/flush 가 `_doc_text = editor.toPlainText()` 로 LF 정규화** → 저장 파일이 LF 로 바뀐다(일반 마크다운 에디터의 표준 동작, 의도됨). **편집하지 않은 순수 모드 전환만으로는 `_doc_text` 가 변하지 않으므로 CRLF 가 보존**된다(모드 전환은 `toPlainText()` 를 `_doc_text` 에 되쓰지 않음).

**★ 저장 직전 flush(데이터 유실 방지)**: `_flush_pending_edit` — `_render_timer.isActive()` 면 stop + `_doc_text = editor.toPlainText()`. `_write_to()` 진입 선행(save/save_as 모두 `_write_to` 경유 → 한 곳에서 커버). 안 하면 "마지막 타이핑이 저장 안 됨" 버그.

**★ 편집↔감시 충돌 정책**:
- self-write 억제 **가드 A(시간 창)**: `_write_to` 에서 `_suppress_watch_until = monotonic()+700ms`. `_on_file_changed` 가 시간 창 내 watcher 이벤트는 무시(reload 타이머 시작 안 함).
- `_reload_timer.timeout` 연결을 기존 lambda(`_load_from_disk`) → **`_on_external_change_settled`** 로 교체.
- `_on_external_change_settled`: scratch(`_path None`)면 return; **가드 B(내용 비교)** `read_markdown(_path)==_doc_text` 면 return(self-write 잔향/무변경); **dirty 면 자동 reload 금지** + `_show_external_change_banner()`(상태바 영구 메시지, 모달 지양) + return; **not-dirty 면** 기존처럼 `_doc_text=disk_text`/`_render_doc(preserve_scroll=True)`/`_sync_editor_from_doc`/`_set_dirty(False)`(순수 뷰어 동작 보존).
- Ctrl+R(`reload_current`)은 명시적 탈출구: dirty 면 `_maybe_discard` 가드 후 reload + 배너 클리어.
- 배너 클리어 지점: `reload_current`/`open_path`/`_write_to`(저장 성공).

**메뉴/툴바/About**: 보기 메뉴 맨 위에 편집기/미리보기/분할 그룹 추가. 툴바에 붙여넣기·저장 뒤 모드 3종 추가. About 단축키 안내에 `Ctrl+1/2/3` 추가.

**settings.py 추가**: `_KEY_VIEW_MODE="view/mode"`, `view_mode()`(기본 "preview", 유효성 검증), `set_view_mode()`(editor/preview/split 만 저장). 리터럴은 main_window `MODE_*` 와 동일.

**검증(offscreen)**: `smoke_phase7.py` 40개 체크 전부 PASS — 모드 전환/가시성/TOC 게이팅, 디바운스(즉시 dirty + 지연 `_doc_text`), flush 후 저장 일치, 신호 억제(sync 가 dirty 오염 안 함), self-write 가드 A/B, dirty 중 외부변경 자동 reload 금지+배너, not-dirty 자동 reload, 모드 영속화. 기존 `pytest tests/` **78 passed, 1 skipped**(회귀 없음).

빌드 인터프리터: `C:\Users\BBQMAN\miniconda3\python.exe`(PySide6 6.11.1).

---

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
| Ctrl+1 / Ctrl+2 / Ctrl+3 | 편집기 / 미리보기 / 분할 모드(상호배타) |
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
  `window/geometry`, `window/state`, `view/toc_visible`, `view/zoom`,
  `view/mode`(editor|preview|split|wysiwyg, 기본 preview — Phase 7, wysiwyg Phase 8).
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
