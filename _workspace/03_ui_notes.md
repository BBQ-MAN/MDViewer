# UI 구현 노트 (ui-dev)

> 작성: ui-dev · 2026-06-01 · 갱신: 2026-06-25(Phase 10 표 삽입 + 행/열 편집) · 대상: QA / packager / core-engine-dev

## 0-D. Phase 10 변경 요약 (표 삽입 + 행/열 편집 — 양 편집 surface)

설계서: `_workspace/10_table_feature_design.md`(계약, 그대로 구현). **소유권 준수**: `main_window.py` **단일 파일**만 수정. `renderer.py`/`settings.py`/`theme.py`/`file_watcher.py`/`requirements.txt`/`mdviewer.spec` **무변경**(core/settings/theme 변경 0, 신규 JS 자산 0). 모듈 상단 import 추가: `from dataclasses import dataclass, field`. 모듈 상수 추가: `_PIPE_LINE_RE`, `_SEP_CELL_RE`, `_TABLE_DEFAULT_ROWS=2`, `_TABLE_DEFAULT_COLS=2`, `_WYSIWYG_TABLE_OP_JS`(파이썬 문자열 리터럴, op 를 `%s` 로 주입).

**(1) 통합 디스패치 확장(기존 패턴 그대로)** — 신규 의미 슬롯 → 디스패처 → surface 분기. surface 판별은 기존 `_is_wysiwyg_surface`/`_is_source_editor_surface`/`_is_edit_surface` **재사용**(신규 판별 0).
  - 의미 슬롯: `fmt_table()` / `fmt_table_row_add()` / `fmt_table_row_del()` / `fmt_table_col_add()` / `fmt_table_col_del()`.
  - 디스패처: `_dispatch_table_insert()`(공통 행×열 다이얼로그 → surface 분기) / `_dispatch_table_op(op)`(`op∈{row_add,row_del,col_add,col_del}` → surface 분기).
  - 다이얼로그: `QInputDialog.getInt` 2회(행 본문수 기본 2/min 1/max 50, 열 기본 2/min 1/max 20). 둘 중 취소 시 전체 중단.

**(2) ★ 순수 함수(GUI 비의존 — QA 가 GUI 없이 단위 테스트)** — `main_window.py` 모듈 수준. import 경로: `from mdviewer.main_window import ...`.
  - `@dataclass TableBlock(top:int, bottom:int, header:list, aligns:list, body:list[list], ncols:int)`.
  - `build_gfm_table_skeleton(rows:int, cols:int) -> str` : 헤더 `열1..`, 구분행 `---`, 빈 본문행 rows 개. 양끝 파이프·균일 폭. rows/cols 최소 1 강제.
  - `_split_table_cells(line:str) -> list[str]` : strip→양끝 파이프 제거→`|` split→각 셀 strip.
  - `_is_separator_row(cells:list) -> bool` : 모든 셀이 `^:?-{1,}:?$` 인가.
  - `parse_table_lines(top:int, bottom:int, lines:list[str]) -> TableBlock|None` : 최소 2줄+구분행(index1) 필수, 없으면 None. ncols=헤더 셀 수, 본문/구분 사각형화(부족=빈셀, 초과=절단).
  - `render_table_block(tb:TableBlock) -> str` : 양끝 파이프·열폭 균일맞춤(`len()` 기반)·구분행 정렬 콜론 보존 재구성.
  - `apply_table_op(tb:TableBlock, op:str, line_idx:int, col_idx:int) -> bool` : in-place 변형. **거부=False**(row_del: 본문 1행뿐/헤더·구분행 커서 / col_del: 1열뿐). line_idx 0=헤더,1=구분,2..=본문. col_idx 범위밖=맨끝 보정.
  - `cursor_col_index(line_text:str, pos_in_block:int, ncols:int) -> int` : 커서 위치가 몇 번째 셀 구간인지(파이프 경계 기반, 0..ncols-1 클램프).

**(3) 소스 편집기(Editor/Split) 경로** — GUI 헬퍼(순수 함수 호출):
  - `_editor_insert_table(rows, cols)` : `build_gfm_table_skeleton` 삽입(beginEditBlock=undo 1스텝). 커서가 줄 중간/비빈 줄이면 표 앞 `\n` 보정(표는 블록). 삽입 후 헤더 첫 셀(`열1`)을 선택 상태로(바로 덮어쓰기). dirty/렌더는 `textChanged`→디바운스 자동.
  - `_find_table_block() -> TableBlock|None` : 커서 줄 포함 연속 파이프 라인(비어있지 않은) 위/아래 확장 → `parse_table_lines`. 표 밖/구분행 없으면 None.
  - `_editor_table_op(op)` : `_find_table_block`→`cursor_col_index`→`apply_table_op`→`render_table_block`→`_replace_table_block`. None/거부 시 **상태바 안내**(크래시 없음).
  - `_replace_table_block(tb, new_text)` : `[tb.top, tb.bottom]` 블록 범위 선택 치환(beginEditBlock=undo 1스텝). 커서는 표 시작 부근 best-effort.

**(4) WYSIWYG 경로** — DOM 직접 조작(JS 리터럴, 신규 자산 0):
  - `_wysiwyg_insert_table(rows, cols)` : 빈 `<table class='md-table'>`(thead/tbody, 셀=`&nbsp;`) + 뒤 `<p>` 를 `_exec_format("insertHTML", html)`(기존 래퍼 → 자동 캡처). 라운드트립: `html_to_markdown`→파이프표→`_doc_text`.
  - `_wysiwyg_table_op(op)` : `_WYSIWYG_TABLE_OP_JS % op` 단일 IIFE 실행(selection anchor→`closest('td,th')`→tr/table/colIndex→row_add 아래 tr / row_del 헤더·본문1행 거부 / col_add 모든 행 colIndex 다음(thead=th,tbody=td) / col_del 1열 거부) → `_capture_wysiwyg_once(final=False)`. 병합셀 미지원. 거부=조용한 no-op(비동기라 파이썬 안내 어려움 — §c 정책).

**(5) 서식 툴바 표 그룹** — `_build_format_toolbar` 끝(링크/서식지우기 다음)에 `addSeparator()` 후 `[표][행+][행−][열+][열−]`(기존 `_mk_fmt` 헬퍼, 텍스트 라벨+툴팁). 게이팅은 서식 툴바 포함이라 `_apply_view_mode` 의 `edit_surface` 가시성으로 **자동**(Preview 숨김) — 추가 게이팅 코드 0.

**(6) About 안내 1줄** : "표: 편집기/분할에선 GFM 파이프표로 정확히 편집되고, 라이브 편집(WYSIWYG)에선 정렬/병합셀이 단순화될 수 있습니다 — 정밀 표 작업은 편집기/분할을 권장합니다."

**라운드트립 한계(QA/문서)**: ① WYSIWYG 표 정렬(`text-align` 미부여)→라운드트립 시 정렬 콜론 미생성(기본 좌측). Editor 삽입도 v1 정렬 미지정(편집 시 콜론은 보존). ② 병합셀(rowspan/colspan) v1 미생성/미지원(단순 사각 표만). ③ WYSIWYG html2text 출력은 양끝 파이프·셀 공백·구분선 폭이 정규화(내용 보존). ④ 셀 내 리터럴 파이프(`\|`)·셀 내 줄바꿈/블록요소 v1 미지원. ⑤ **Editor 경로는 결정적 GFM**(파서 자체 재구성) — 라운드트립 손실 없음. 정밀 표 작업은 Editor/Split 권장.

**검증**: ast.parse OK. 순수 함수 12케이스(골격/파싱/round-trip 안정/row·col add·del/거부 3종/cursor_col_index/정렬보존/구분행없음→None) PASS. offscreen 스모크: Editor 삽입→`_doc_text` GFM·render `<table>`, 표안 행/열 add·del→블록 갱신, 표밖 op→안내(크래시 없음), WYSIWYG 삽입→`_doc_text` 파이프표·행/열 JS 예외 없음, 게이팅(Preview 숨김/Split 표시) 모두 PASS.

**계약 변경 없음** — core/settings/theme/spec/requirements 무변경, 설계 §9 그대로.

---

## 0-C. Phase 9 변경 요약 (새 문서 + 워드프로세서식 툴바 + Undo/Redo + 통합 서식 디스패치)

설계서: `_workspace/09_wordprocessor_ui_design.md`(계약). **소유권 준수**: `main_window.py` **단일 파일** 수정. `settings.py`/`theme.py`/`renderer.py`/`file_watcher.py`/`requirements.txt`/`mdviewer.spec` **무변경**(core/settings/theme 변경 0, 신규 리소스/JS 자산 0 → 패키징 안전). 모듈 상단 import 추가: `re`, `QComboBox`/`QStyle`(QtWidgets), `QTextCursor`(QtGui). 모듈 상수 추가: `_INLINE_MARK`/`_LINE_PREFIX`/`_HEADING_PREFIX`/`_HEADING_RE`.

**(1) 새 문서 `act_new`(Ctrl+N, `QKeySequence.StandardKey.New`)** — 파일 메뉴 맨 위 + 툴바 첫 버튼, 아이콘 `SP_FileIcon`. 슬롯 `new_document()`: `_maybe_discard()` 가드(취소면 중단) → `_leave_wysiwyg_for_document_change()`(WYSIWYG 탈출) → `_clear_external_change_banner()` → `_new_scratch()` → 현재 모드가 `MODE_PREVIEW` 면 `_apply_view_mode(MODE_SPLIT)`(편집 가능 보장) → `editor.setFocus()` + 상태바 안내. **`_new_scratch()`**(별도 헬퍼, `_set_scratch`(paste, dirty=True)와 분리): watcher.stop() → `_path=None`·`_doc_text=""`·`_pending_scroll=None` → `_render_doc(preserve_scroll=False)` 빈 렌더 → `_sync_editor_from_doc()` 편집기 비움 → **`_set_dirty(False)`(★ 빈 새 문서는 깨끗, 타이틀 "제목 없음")**.

**(2) 메인 툴바 워드프로세서식 재구성(`_build_toolbar`)**: `setToolButtonStyle(ToolButtonTextBesideIcon)`(아이콘+한글 라벨 병기). 그룹+`addSeparator()`+전 액션 `setToolTip("이름 (단축키)")`. 순서 = **[파일] 새 문서·열기·저장 | [편집] 실행취소·다시실행 | [보기] 편집기·미리보기·분할·라이브편집(QActionGroup 라디오) · 줌-·줌리셋·줌+ · 테마·목차**. 다른이름저장/새로고침/붙여넣기는 **툴바 제외(메뉴 유지)**. 아이콘 = `QStyle.standardIcon`만(신규 자산 0): new=`SP_FileIcon`, open=`SP_DialogOpenButton`, save=`SP_DialogSaveButton`, undo=`SP_ArrowBack`, redo=`SP_ArrowForward`. 표준아이콘 부재 액션(줌/테마/목차/모드)은 텍스트 라벨만(아이콘 강제 금지). 메뉴: 파일(새 문서 맨 위)·**편집(&E) 신규(실행취소/다시실행)**·보기(변경 없음).

**(3) Undo/Redo — 활성 surface 라우팅**: `act_undo`(`StandardKey.Undo`=Ctrl+Z)→`do_undo`, `act_redo`(`setShortcuts([StandardKey.Redo, Ctrl+Shift+Z, Ctrl+Y])`)→`do_redo`. 라우팅: `_is_wysiwyg_surface()` 면 `_wysiwyg_exec_simple("undo"/"redo")`(execCommand + `_capture_wysiwyg_once(final=False)` 로 `_doc_text` 동기화); `_is_source_editor_surface()` 면 `editor.undo()/redo()`; Preview 전용은 no-op. `_apply_view_mode` 에서 `edit_surface = mode in (EDITOR,SPLIT,WYSIWYG)` 일 때만 `act_undo/act_redo.setEnabled(True)`(Preview 비활성). **Ctrl+Z 편집기 내장 경합**: 라우팅 슬롯이 결국 `editor.undo()` 를 호출하므로 결과 동일(WindowShortcut 유지 — 버그 아님).

**(4) ★ 통합 서식 디스패치(surface-aware)**: 서식 툴바를 WYSIWYG 전용에서 **편집 surface 공통(Editor/Split/WYSIWYG)** 으로 확장. surface 판별 헬퍼: `_is_source_editor_surface()`=`_view_mode in (EDITOR,SPLIT)`, `_is_wysiwyg_surface()`=`_view_mode==WYSIWYG and _wysiwyg_active`, `_is_edit_surface()`. 의미 단위 슬롯(`fmt_bold/italic/strike/code/ul/ol/quote/link/clear/heading`) → `_dispatch_*` 가 활성 surface 분기:
  - **(a) 에디터(Editor/Split) = QTextCursor 마크다운**(`_editor_inline`/`_editor_block`/`_editor_heading`/`_editor_link`/`_editor_clear`): 굵게=선택 `**` 감쌈(토글: 양끝 이미 마커면 제거), 기울임=`*`, 취소선=`~~`, 인라인코드=백틱; 선택 없으면 마커 쌍 삽입+커서 가운데. 블록=줄머리 접두 토글(`_editor_apply_line_prefix`, `beginEditBlock/endEditBlock` 로 undo 1스텝): 불릿 `"- "`, 번호 `"1. "`, 인용 `"> "`; H1/H2/H3 `#/##/### `(exclusive, 기존 `#` 접두 제거 후 부여=토글), 본문(0)=`#` 제거. 링크=`[선택](url)`/선택 없으면 `[링크 텍스트](url)`+placeholder 선택. **`insertText`/`endEditBlock` 가 `textChanged`→dirty+디바운스 렌더 자동(별도 호출 없음; `_suppress_editor_signal` 은 `setPlainText` 에만 켜짐)**. QPlainTextEdit 선택의 단락구분자 U+2029(공백 표시)를 `.replace(" ","\n")` 로 환원. 에디터 '서식 지우기'는 v1 비활성/안내.
  - **(b) WYSIWYG = execCommand**(`_wysiwyg_inline`/`_wysiwyg_block`/`_wysiwyg_heading`/`_wysiwyg_inline_code`/`_wysiwyg_clear`): 기존 `_exec_format`/인라인코드 JS/createLink/removeFormat 경로를 **그대로 래핑**(동작 무변경). 기존 `_fmt_inline_code`/`_fmt_insert_link`/`_fmt_clear` 메서드명을 `_wysiwyg_inline_code`/디스패치/`_wysiwyg_clear` 로 정리.

**(5) heading 드롭다운(`QComboBox` `cmb_heading`)**: 항목 `["본문","제목 1","제목 2","제목 3"]`, `activated`→`fmt_heading(idx)`(idx=level, 0=본문). 에디터=`#` 접두 토글, WYSIWYG=`formatBlock H1/H2/H3/P`. 서식 툴바 맨 앞에 배치. (콤보 상태 동기화=커서 줄 현재 레벨 표시는 v1 범위 외 — 적용 트리거로만.)

**(6) 서식 툴바 재작성(`_build_format_toolbar`)**: `cmb_heading` | 굵게/기울임/취소선/코드 | • 목록/1. 목록/인용 | 링크/서식지우기. `_mk_fmt(label, slot, tip="")`(시그니처 확장 — tip 추가). 굵게/기울임 버튼은 `tb.widgetForAction(act).setStyleSheet("font-weight/style")` 로 B/I 강조(외부 자산 0). `tb.setVisible(False)` 초기 숨김.

**(7) `_apply_view_mode` 게이팅 확장(1곳)**: `edit_surface = mode in (EDITOR,SPLIT,WYSIWYG)`; `show_format_toolbar = edit_surface`(현행 `==WYSIWYG` 에서 확장) → `format_toolbar.setVisible(편집 surface)`. 같은 자리에서 `act_undo/act_redo.setEnabled(edit_surface)`, `act_fmt_clear.setEnabled(mode==MODE_WYSIWYG)`(에디터 surface 서식지우기 모호 → 비활성). TOC/포커스/persist/액션체크/WYSIWYG 전이 기존 로직 무변경.

**단축키 비충돌**: Ctrl+N/Z/Shift+Z/Y 모두 기존(O,S,R,T,V,Q,=,-,0,1~4,\\,F11)과 비충돌. 서식 명령은 단축키 없음(툴바 클릭). About 다이얼로그에 Ctrl+N/Z/Y + 서식 툴바 안내 갱신.

**검증(offscreen)**: `QT_QPA_PLATFORM=offscreen` + `QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu --in-process-gpu --no-sandbox'` 스모크 **36/36 PASS** — 액션/단축키 존재(act_new Ctrl+N, act_undo Ctrl+Z, act_redo Ctrl+Shift+Z·Ctrl+Y, cmb_heading), 새 문서(빈 `_doc_text`/`_path=None`/dirty=False/타이틀 "제목 없음"/Preview→Split 편집 가능), 에디터 인라인 토글(`**hello**`↔`hello`, 빈 선택 `**` 쌍), 에디터 헤딩(`# `↔`## `↔본문 strip), 에디터 블록 다중줄 불릿 토글, **undo/redo 에디터 라우팅(typed→undo→redo)**, 게이팅(Preview 서식툴바 숨김·undo/redo 비활성 / Editor·Split 서식툴바 표시·undo 활성·fmt_clear 비활성), surface 판별 헬퍼. 기존 `pytest tests/` **78 passed, 1 skipped**(회귀 없음). `ast.parse` 구문검사 OK.

**frozen 자산**: 신규 .js/datas/hiddenimports/런타임 의존성 **0**. `QStyle.StandardPixmap`(Qt 내장 아이콘 — 번들 누락 위험 0), `QComboBox`/`QTextCursor` 모두 PySide6 기존. `mdviewer.spec`/`requirements.txt` 무변경 → packager 작업 = 회귀 스모크 1회만.

---

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
| Ctrl+N | 새 문서(빈 scratch, dirty=False) — Phase 9 |
| Ctrl+O | 열기 |
| Ctrl+Shift+V | 클립보드를 마크다운으로 붙여넣기(→ scratch 임시 문서) |
| Ctrl+S | 저장(scratch 면 Save As) |
| Ctrl+Shift+S | 다른 이름으로 저장 |
| Ctrl+Z | 실행취소(활성 surface 라우팅) — Phase 9 |
| Ctrl+Shift+Z / Ctrl+Y | 다시실행(활성 surface 라우팅) — Phase 9 |
| Ctrl+1 / Ctrl+2 / Ctrl+3 / Ctrl+4 | 편집기 / 미리보기 / 분할 / 라이브 편집 모드(상호배타) |
| Ctrl+R | 새로고침 |
| Ctrl+T | 테마 전환(라이트/다크) |
| Ctrl+= / Ctrl++ | 확대 |
| Ctrl+- | 축소 |
| Ctrl+0 | 줌 100% |
| F11 | 전체화면 토글 |
| Ctrl+\\ | 목차(TOC) 패널 토글 |
| Ctrl+Q | 종료 |
| 최근파일 1~9 | 메뉴 가속키 &1..&9 |
| (서식 명령) | 단축키 없음 — 서식 툴바 클릭(편집 surface 에서만 표시) |

메뉴(Phase 9): 파일(**새 문서**/열기/최근 파일▸/─/붙여넣기/저장/다른이름저장/─/새로고침/─/종료) · **편집(실행취소/다시실행)** · 보기(모드4종/테마/줌3종/전체화면/목차) · 도움말(정보).
메인 툴바(Phase 9, ToolButtonTextBesideIcon): [파일]새 문서·열기·저장 ─ [편집]실행취소·다시실행 ─ [보기]모드4종 ─ 줌3종 ─ 테마·목차. 서식 툴바(별도, 편집 surface 에서만 표시): 문단스타일 콤보 ─ 굵게·기울임·취소선·코드 ─ •목록·1.목록·인용 ─ 링크·서식지우기. 드래그앤드롭으로 로컬 파일 열기 지원.

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
