# MDViewer 워드프로세서 UI: 새 문서 + 단축 버튼 툴바 + 통합 서식 (Phase 9)

> 작성: architect · 2026-06-25
> 범위 확장: (1) **새 문서 만들기(Ctrl+N)** 추가, (2) 상단 툴바를 **워드프로세서식
> 단축 버튼 툴바**(그룹+구분선+툴팁+아이콘)로 재구성, (3) **Undo/Redo** 를 활성
> surface 로 라우팅, (4) ★ **통합 서식 디스패치** — 서식 툴바를 WYSIWYG 전용에서
> **편집 surface 공통(Editor/Split/WYSIWYG)** 으로 확장(에디터=마크다운 삽입/토글,
> WYSIWYG=execCommand).
> 기준 문서: `_workspace/01_architect_blueprint.md`(렌더 API 계약),
> `06_clipboard_feature_design.md`(문서 모델 `_doc_text/_path/_dirty`,
> `_set_scratch`/`_attach_path`/`_maybe_discard`),
> `07_editor_feature_design.md`(뷰모드 상태기계·디바운스·편집↔감시),
> `08_wysiwyg_feature_design.md`(WYSIWYG 진입/이탈·폴링·서식 툴바·역렌더 게이트),
> 현행 `main_window.py` / `settings.py` / `theme.py` / `renderer.py`.

이 문서는 **계약(contract)**이다. **ui-dev** 가 이 계약대로 `main_window.py` 를
구현한다. **core(renderer/file_watcher) 변경은 불필요**하다(§9). 모든 신규 동작은
기존 문서 모델(`_doc_text` 단일 진실원), 뷰모드 상태기계, 편집↔감시 충돌 정책,
self-write 억제, WYSIWYG 역렌더 게이트와 **모순 없이** 얹는다.

빌드 인터프리터: `C:\Users\BBQMAN\miniconda3\python.exe` (PySide6 6.11.1).

---

## 0. 핵심 설계 결정 요약 (못박음)

| 항목 | 결정 |
|------|------|
| 새 문서 | **`act_new`(Ctrl+N)** — 파일 메뉴 맨 위 + 툴바 첫 버튼. `_maybe_discard` 가드 → 빈 scratch(`_doc_text=""`,`_path=None`,`_dirty=False`,watcher stop) → 편집기 비우고 빈 렌더 → 타이틀 "제목 없음" → **편집 가능한 모드로 전환**(Preview 전용이면 Split) → 편집기 포커스 |
| 새 문서 dirty | **`_dirty=False`**(빈 새 문서는 깨끗). `_set_scratch`(paste, dirty=True)와 의도적으로 다름 → 새 함수 `_new_scratch()` 분리 |
| 툴바 재구성 | 워드프로세서식: [파일] 새로·열기·저장 \| [편집] 실행취소·다시실행 \| [보기] 모드4종·줌·테마·목차 \| 그룹마다 `addSeparator()` + 모든 액션에 `setToolTip` |
| 다른이름저장 | **메뉴 전용**(툴바에서 제외 — 요청대로). 저장은 툴바 포함 |
| Undo/Redo | **`act_undo`(Ctrl+Z)/`act_redo`(Ctrl+Shift+Z, 추가 Ctrl+Y)**. 활성 surface 라우팅 — Editor/Split→`editor.undo()/redo()`, WYSIWYG→`execCommand('undo'/'redo')`, Preview 전용→**비활성** |
| ★ 통합 서식 | 서식 툴바를 **편집 surface 공통**으로. 활성 surface 판별 → 에디터면 **QTextCursor 마크다운 토글/삽입**, WYSIWYG면 **execCommand**. 동일 QAction 이 surface 에 따라 분기 |
| heading 드롭다운 | `QComboBox`(본문/제목1/제목2/제목3) — 에디터=`#` 접두 토글, WYSIWYG=`formatBlock`. 서식 툴바에 배치 |
| 서식 툴바 게이팅 | **편집 surface 활성(Editor/Split/WYSIWYG)→표시·활성, Preview 전용→숨김**. `_apply_view_mode` 와 연동(기존 `show_format_toolbar = (mode==WYSIWYG)` → `mode in 편집surface` 로 확장) |
| 아이콘 | **신규 자산 0**. 파일/편집 액션=`QStyle.StandardPixmap`, 서식=스타일 텍스트(B/I…)/유니코드. 패키징 안전(datas 추가 0) |
| 단축키 충돌 | Ctrl+N/Z/Shift+Z/Y 모두 **기존과 비충돌**(§7 표). 서식 명령은 v1 단축키 없음(툴바 클릭) |
| core 변경 | **불필요**(렌더/변환/IO 시그니처 불변). §9 |
| 영향 파일 | **`main_window.py` 만**. settings/theme/renderer/file_watcher/requirements/spec **무변경** |

---

## 1. 새 문서 만들기 — `act_new` (Ctrl+N)

### 1.1 동작 명세 (요청 기준 확정)

새 문서는 **현재 문서를 빈 임시 문서로 교체**한다. 붙여넣기(`_set_scratch`,
dirty=True)와 달리 **빈 새 문서는 dirty=False**(아직 변경 없음 → 즉시 저장 유도 불필요,
저장 시 Save As 로 자연 흐름).

| 단계 | 동작 | 근거 |
|------|------|------|
| 1 | `_maybe_discard()` 가드 — 미저장이면 저장/버림/취소 | 데이터 유실 방지(기존 정책 일관) |
| 2 | `_leave_wysiwyg_for_document_change()` — WYSIWYG 면 빠져나옴 | 새 문서는 일반 렌더(§4.6 정합) |
| 3 | `_clear_external_change_banner()` | 새 문서 진입 → 이전 외부변경 안내 클리어 |
| 4 | watcher stop (`_path=None` 로 가는 scratch) | watch 생명주기: scratch=stop |
| 5 | `_doc_text=""`, `_path=None`, `_pending_scroll=None` | 빈 문서 상태 |
| 6 | `_render_doc(preserve_scroll=False)` — 빈 본문 렌더 | 프리뷰 비움 |
| 7 | `_sync_editor_from_doc()` — 편집기 비움(신호 억제) | 편집기=빈 문자열 |
| 8 | `_set_dirty(False)` → 타이틀 "제목 없음" | 빈 새 문서는 깨끗 |
| 9 | **편집 가능한 모드 보장**: 현재 모드가 `MODE_PREVIEW` 면 `MODE_SPLIT` 로 전환 | 새 문서는 바로 타이핑할 수 있어야 함(요청) |
| 10 | 편집기 포커스 + 상태바 안내 | 즉시 입력 가능 |

> **모드 전환 규칙(9단계) 상세:** 현재 모드가 **Preview 전용**이면 편집 불가하므로
> Split 로 전환한다(편집기+프리뷰 동시 — 워드프로세서다운 기본). Editor/Split/WYSIWYG
> 면 이미 편집 가능하므로 **모드 유지**(사용자 선호 보존). 단 WYSIWYG 는 §2 진입에서
> 이미 `_leave_wysiwyg_for_document_change()` 로 Preview 로 강등되므로, 그 직후 Split
> 로 올린다(즉 WYSIWYG→새문서→Split). 요약: **"편집 surface 가 아니면 Split 로."**

### 1.2 구현 계약 — `_new_scratch()` + `new_document()`

`_set_scratch(text)`(paste, dirty=True)와 분리한 **빈 새 문서 전용 헬퍼**를 둔다.
중복을 줄이려면 `_set_scratch` 에 `dirty` 인자를 추가하는 안도 가능하나(아래),
**의도 명확성·기존 호출부 무변경**을 위해 별도 메서드를 권장한다.

```python
def new_document(self) -> None:
    """Ctrl+N: 빈 임시(scratch) 문서로 교체한다(미저장 가드 후).

    빈 새 문서는 dirty=False(아직 변경 없음). 편집 불가 모드(Preview)면 Split 로
    전환해 즉시 타이핑할 수 있게 하고 편집기에 포커스한다.
    """
    if not self._maybe_discard():
        return                                   # 사용자 취소 → 중단(데이터 보호)
    # 문서 교체 → WYSIWYG 라이브 편집을 빠져나온다(새 문서는 일반 렌더, §4.6 정합).
    self._leave_wysiwyg_for_document_change()
    self._clear_external_change_banner()
    self._new_scratch()
    # 편집 가능 모드 보장: Preview 전용이면 Split 로(편집기+프리뷰).
    if self._view_mode == MODE_PREVIEW:
        self._apply_view_mode(MODE_SPLIT)        # 내부에서 _sync_editor_from_doc 호출
    self.editor.setFocus()
    self.statusBar().showMessage("새 문서 — 입력 후 Ctrl+S 로 저장", 4000)

def _new_scratch(self) -> None:
    """현재 문서를 '빈' scratch(미저장 임시 문서)로 전환(dirty=False).

    _set_scratch(paste, dirty=True)와 달리 빈 새 문서는 깨끗하다. watch 중지
    (디스크 파일 없음) → _path=None → _doc_text="" → 빈 렌더 → 편집기 비움.
    """
    if self._watcher is not None:
        try:
            self._watcher.stop()                 # watch 생명주기: scratch=stop
        except Exception:
            pass
    self._path = None
    self._doc_text = ""
    self._pending_scroll = None
    self._render_doc(preserve_scroll=False)      # 빈 본문 렌더(프리뷰 비움)
    self._sync_editor_from_doc()                 # 편집기 비움(신호 억제)
    self._set_dirty(False)                       # ★ 빈 새 문서는 깨끗(타이틀 "제목 없음")
```

> **대안(중복 제거, ui-dev 재량):** `_set_scratch(self, text, *, dirty=True)` 로
> 시그니처를 확장하고 `new_document` 가 `_set_scratch("", dirty=False)` 를 호출.
> 기존 `_set_scratch` 호출부(paste)는 `dirty` 기본값으로 무변경. 단 paste 의 상태바
> 메시지("임시 문서(붙여넣기)…")가 빈 새 문서엔 부적절하므로, 메시지 분기가 필요해
> **권장은 별도 `_new_scratch`**(메시지·dirty 의도가 다름).

### 1.3 액션 정의

```python
# _build_actions():
from PySide6.QtWidgets import QStyle    # 표준 아이콘(신규 자산 0)

self.act_new = QAction("새 문서", self)
self.act_new.setShortcut(QKeySequence.StandardKey.New)        # Ctrl+N(표준)
self.act_new.setToolTip("새 문서 (Ctrl+N)")
self.act_new.setIcon(self.style().standardIcon(
    QStyle.StandardPixmap.SP_FileIcon))
self.act_new.triggered.connect(self.new_document)
```

- `QKeySequence.StandardKey.New` = Windows 에서 Ctrl+N(플랫폼 표준). 명시
  `QKeySequence("Ctrl+N")` 도 동일하나 표준 키 사용이 일관적.

---

## 2. 상단 툴바 재구성 (워드프로세서식)

### 2.1 메인 툴바 그룹 배치 (★ 이 순서대로)

현행 `_build_toolbar` 를 **그룹+구분선** 구조로 재작성. 모든 액션에 `setToolTip`.

```
[파일]   새 문서 · 열기 · 저장
  ──(separator)──
[편집]   실행취소 · 다시실행
  ──(separator)──
[보기]   편집기 · 미리보기 · 분할 · 라이브편집     (모드 4종 — QActionGroup 라디오)
  ──(separator)──
         축소 · 줌초기화 · 확대
  ──(separator)──
         테마 · 목차
```

```python
def _build_toolbar(self) -> None:
    tb = self.addToolBar("메인")
    tb.setObjectName("mainToolbar")
    tb.setMovable(False)
    # 텍스트가 긴 모드 액션 가독성: 아이콘 옆 텍스트(워드프로세서 느낌).
    from PySide6.QtCore import Qt as _Qt
    tb.setToolButtonStyle(_Qt.ToolButtonStyle.ToolButtonTextBesideIcon)

    # [파일] (다른이름저장은 메뉴 전용 — 요청)
    tb.addAction(self.act_new)
    tb.addAction(self.act_open)
    tb.addAction(self.act_save)
    tb.addSeparator()
    # [편집]
    tb.addAction(self.act_undo)
    tb.addAction(self.act_redo)
    tb.addSeparator()
    # [보기] 모드 4종
    tb.addAction(self.act_mode_editor)
    tb.addAction(self.act_mode_preview)
    tb.addAction(self.act_mode_split)
    tb.addAction(self.act_mode_wysiwyg)
    tb.addSeparator()
    # 줌
    tb.addAction(self.act_zoom_out)
    tb.addAction(self.act_zoom_reset)
    tb.addAction(self.act_zoom_in)
    tb.addSeparator()
    # 테마/목차
    tb.addAction(self.act_toggle_theme)
    tb.addAction(self.act_toggle_toc)
```

- **새로고침(Ctrl+R)/붙여넣기(Ctrl+Shift+V) 는 툴바에서 제외**(메뉴 유지). 워드
  프로세서 표준 단축 버튼에 가깝게 슬림화. (현행 툴바는 reload/paste 를 포함했으나
  요청 구성은 새로/열기/저장 중심.) → **메뉴에는 그대로 유지**(§6).
- `ToolButtonTextBesideIcon`: 표준 아이콘 + 한글 라벨 병기 → 워드프로세서 느낌.
  (아이콘만 원하면 `ToolButtonIconOnly`; ui-dev 재량. 권장은 TextBesideIcon.)

### 2.2 액션 아이콘 — QStyle 표준 아이콘 (신규 자산 0)

외부 아이콘 파일을 추가하지 않고 `self.style().standardIcon(...)` 만 사용한다.

| 액션 | StandardPixmap | 비고 |
|------|----------------|------|
| `act_new` | `SP_FileIcon` | 빈 문서 |
| `act_open` | `SP_DialogOpenButton` (또는 `SP_DirOpenIcon`) | 열기 |
| `act_save` | `SP_DialogSaveButton` | 저장 |
| `act_undo` | `SP_ArrowBack` | 실행취소 |
| `act_redo` | `SP_ArrowForward` | 다시실행 |
| `act_zoom_in` / `act_zoom_out` | (아이콘 없음 — 텍스트 "+"/"−") | 표준 줌 아이콘 부재 → 라벨 |
| `act_zoom_reset` | (텍스트 "100%") | |
| `act_toggle_theme` | `SP_BrowserReload`/(없음) | 표준 테마 아이콘 없음 → 텍스트 권장 |
| `act_toggle_toc` | (없음 — 텍스트 "목차") | |
| 모드 4종 | (없음 — 텍스트 라벨) | 라디오 체크 표시로 구분 |

```python
st = self.style()
self.act_open.setIcon(st.standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton))
self.act_save.setIcon(st.standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
self.act_undo.setIcon(st.standardIcon(QStyle.StandardPixmap.SP_ArrowBack))
self.act_redo.setIcon(st.standardIcon(QStyle.StandardPixmap.SP_ArrowForward))
```

> 표준 아이콘이 부적절/부재인 액션은 **텍스트 라벨만** 둔다(아이콘 강제 금지 — 깨진
> 아이콘 회피). `setToolTip` 은 모든 액션에 부여(호버 안내).

### 2.3 툴팁 일괄 부여

각 액션의 `setToolTip("이름 (단축키)")` 를 `_build_actions` 에서 부여한다. 예:
`act_open.setToolTip("열기 (Ctrl+O)")`, `act_save.setToolTip("저장 (Ctrl+S)")`,
`act_undo.setToolTip("실행취소 (Ctrl+Z)")` 등. (QAction 의 텍스트만으론 단축키가
툴팁에 자동 표기되지 않을 수 있어 명시.)

---

## 3. Undo / Redo — 활성 surface 라우팅 (★)

### 3.1 라우팅 규칙

| 활성 surface(현재 모드) | Undo | Redo |
|------------------------|------|------|
| `MODE_EDITOR` / `MODE_SPLIT` | `self.editor.undo()` | `self.editor.redo()` |
| `MODE_WYSIWYG` | `execCommand('undo')` | `execCommand('redo')` |
| `MODE_PREVIEW`(편집 불가) | **비활성**(no-op) | **비활성** |

- 활성 surface 판별은 `self._view_mode` 로 한다(§5 공통 판별 함수와 공유).
- WYSIWYG 의 undo/redo 후에는 **즉시 1회 캡처**로 `_doc_text` 동기화(`_exec_format`
  의 캡처 패턴 재사용 — execCommand 경로이므로 자연 통합).

### 3.2 액션 정의

```python
self.act_undo = QAction("실행취소", self)
self.act_undo.setShortcut(QKeySequence.StandardKey.Undo)      # Ctrl+Z
self.act_undo.setToolTip("실행취소 (Ctrl+Z)")
self.act_undo.triggered.connect(self.do_undo)

self.act_redo = QAction("다시실행", self)
# Ctrl+Shift+Z(표준 Redo) + 추가로 Ctrl+Y(Windows 관습) 둘 다 수용.
self.act_redo.setShortcuts([QKeySequence.StandardKey.Redo, QKeySequence("Ctrl+Y")])
self.act_redo.setToolTip("다시실행 (Ctrl+Shift+Z / Ctrl+Y)")
self.act_redo.triggered.connect(self.do_redo)
```

> ⚠️ **충돌 점검:** `QKeySequence.StandardKey.Redo` 는 Windows 에서 보통 Ctrl+Y
> (또는 Ctrl+Shift+Z)로 매핑된다. 둘을 모두 `setShortcuts` 로 등록하되 **중복 등록**
> 되어도 Qt 가 무해 처리. Ctrl+Z/Ctrl+Y/Ctrl+Shift+Z 는 기존 단축키와 비충돌(§7).

### 3.3 슬롯 구현

```python
def do_undo(self) -> None:
    """활성 편집 surface 로 undo 라우팅(Preview 전용이면 no-op)."""
    if self._is_wysiwyg_surface():
        self._wysiwyg_exec_simple("undo")
    elif self._is_source_editor_surface():
        self.editor.undo()
    # Preview 전용 → no-op(액션은 §3.4 로 비활성).

def do_redo(self) -> None:
    if self._is_wysiwyg_surface():
        self._wysiwyg_exec_simple("redo")
    elif self._is_source_editor_surface():
        self.editor.redo()

def _wysiwyg_exec_simple(self, command: str) -> None:
    """WYSIWYG 에서 execCommand 실행 후 즉시 캡처(undo/redo 공용)."""
    if not self._wysiwyg_active:
        return
    try:
        self.view.page().runJavaScript("document.execCommand(%r,false,null);" % command)
    except Exception:
        pass
    self._capture_wysiwyg_once(final=False)     # _doc_text 동기화(폴링 보조)
```

surface 판별 헬퍼(§5 에서 정의 — 서식 디스패치와 공유):

```python
def _is_source_editor_surface(self) -> bool:
    """활성 편집 대상이 소스 편집기인가(Editor/Split)."""
    return self._view_mode in (MODE_EDITOR, MODE_SPLIT)

def _is_wysiwyg_surface(self) -> bool:
    """활성 편집 대상이 WYSIWYG webview 인가."""
    return self._view_mode == MODE_WYSIWYG and self._wysiwyg_active

def _is_edit_surface(self) -> bool:
    """편집 가능한 surface 가 활성인가(Editor/Split/WYSIWYG)."""
    return self._is_source_editor_surface() or self._view_mode == MODE_WYSIWYG
```

### 3.4 Undo/Redo 활성/비활성 게이팅

Preview 전용에서는 undo/redo 가 의미 없으므로 **비활성**한다.
`_apply_view_mode` 끝에 한 줄(§4 의 서식 툴바 게이팅과 함께):

```python
edit_surface = mode in (MODE_EDITOR, MODE_SPLIT, MODE_WYSIWYG)
self.act_undo.setEnabled(edit_surface)
self.act_redo.setEnabled(edit_surface)
```

> v1 단순화: undo 스택의 비어있음/가득참까지 추적해 미세 토글하지 않는다(에디터
> `undoAvailable` 시그널 연동은 §10 확장 후보). Preview 만 게이팅으로 충분.

---

## 4. 서식 툴바 게이팅 확장 — WYSIWYG 전용 → 편집 surface 공통 (★)

### 4.1 게이팅 변경 (`_apply_view_mode` 1줄)

현행: `show_format_toolbar = mode == MODE_WYSIWYG` → **편집 surface 공통**으로 확장.

```python
# 변경 전:
# show_format_toolbar = mode == MODE_WYSIWYG
# 변경 후:
show_format_toolbar = mode in (MODE_EDITOR, MODE_SPLIT, MODE_WYSIWYG)
...
self.format_toolbar.setVisible(show_format_toolbar)
```

| 모드 | 서식 툴바 | 서식 동작 대상 |
|------|:---:|------|
| `MODE_EDITOR` | **표시** | 소스 편집기(QTextCursor 마크다운) |
| `MODE_SPLIT` | **표시** | 소스 편집기(QTextCursor 마크다운) |
| `MODE_WYSIWYG` | **표시** | webview(execCommand) |
| `MODE_PREVIEW` | **숨김** | (편집 불가) |

> Split 는 편집기+프리뷰 동시 표시이며 **편집 입력원은 편집기**이므로 서식은
> 에디터 경로로 간다(WYSIWYG 가 아님). 즉 "서식 대상 = 활성 편집 surface"는
> WYSIWYG 여부로만 갈린다(§5 판별).

### 4.2 서식 액션 라벨 — 스타일 텍스트(신규 자산 0)

execCommand 전용이던 라벨을 **surface 무관 공통 라벨**로 유지하되, 아이콘은 외부
자산 없이 **스타일 텍스트/유니코드**로 표현한다.

| 액션 | 라벨(텍스트) | 비고 |
|------|------|------|
| `act_fmt_bold` | **B**(굵게) | `setFont` 으로 bold, 툴팁 "굵게" |
| `act_fmt_italic` | *I*(기울임) | italic 폰트, 툴팁 "기울임" |
| `act_fmt_strike` | S̶(취소선) | 텍스트 "취소선" 또는 유니코드 |
| `act_fmt_code` | `</>` 또는 "코드" | 인라인 코드 |
| `act_fmt_ul` | "• 목록" | 불릿 |
| `act_fmt_ol` | "1. 목록" | 번호 |
| `act_fmt_quote` | "❝ 인용" | 인용 |
| `act_fmt_link` | "🔗 링크" | URL 입력 |
| `act_fmt_clear` | "서식 지우기" | |
| heading 콤보 | `QComboBox` | §5.4 |

- B/I 스타일 텍스트 적용 예:
  ```python
  f = self.act_fmt_bold.font(); f.setBold(True); ... # QAction.font 직접 미지원 →
  ```
  QAction 은 폰트 API 가 제한적이므로, **툴바 위젯에 직접** 스타일을 주려면
  `tb.widgetForAction(act).setStyleSheet("font-weight:bold")` 패턴을 쓴다(ui-dev
  재량). 단순화하려면 라벨 텍스트("굵게","기울임")만으로도 충분(아이콘 강제 아님).
- **신규 datas/리소스 0** 유지(패키징 안전 — §8).

---

## 5. ★ 통합 서식 디스패치 (워드프로세서 핵심)

동일 QAction 이 **활성 surface 에 따라 분기**한다. WYSIWYG 면 기존 execCommand
경로(`_exec_format` 등), 에디터(Editor/Split)면 **QTextCursor 로 마크다운 삽입/토글**.

### 5.1 디스패치 진입점 — 기존 슬롯을 surface 분기로 교체

현행 서식 슬롯들(`_exec_format`/`_fmt_inline_code`/`_fmt_insert_link`/`_fmt_clear`)은
`if not self._wysiwyg_active: return` 으로 **WYSIWYG 아니면 no-op**였다. 이를
**surface 분기**로 바꾼다. 각 서식은 "의미(intent)" 단위 슬롯으로 통일:

```python
# 의미 단위 슬롯(툴바 액션이 연결). 내부에서 surface 분기.
def fmt_bold(self)   -> None: self._dispatch_inline("bold")
def fmt_italic(self) -> None: self._dispatch_inline("italic")
def fmt_strike(self) -> None: self._dispatch_inline("strike")
def fmt_code(self)   -> None: self._dispatch_inline("code")
def fmt_ul(self)     -> None: self._dispatch_block("ul")
def fmt_ol(self)     -> None: self._dispatch_block("ol")
def fmt_quote(self)  -> None: self._dispatch_block("quote")
def fmt_link(self)   -> None: self._dispatch_link()
def fmt_clear(self)  -> None: self._dispatch_clear()
def fmt_heading(self, level: int) -> None: self._dispatch_heading(level)  # 0=본문,1~3
```

```python
def _dispatch_inline(self, kind: str) -> None:
    """인라인 서식(bold/italic/strike/code)을 활성 surface 로 분기."""
    if self._is_wysiwyg_surface():
        self._wysiwyg_inline(kind)              # execCommand 경로(§5.2)
    elif self._is_source_editor_surface():
        self._editor_inline(kind)               # QTextCursor 마크다운(§5.3)
    # Preview → no-op(툴바 숨김이라 도달 드묾, 방어적).

def _dispatch_block(self, kind: str) -> None:
    if self._is_wysiwyg_surface():
        self._wysiwyg_block(kind)
    elif self._is_source_editor_surface():
        self._editor_block(kind)

def _dispatch_heading(self, level: int) -> None:
    if self._is_wysiwyg_surface():
        self._wysiwyg_heading(level)
    elif self._is_source_editor_surface():
        self._editor_heading(level)

def _dispatch_link(self) -> None:
    url, ok = QInputDialog.getText(self, "링크 삽입", "URL:")
    if not ok or not url.strip():
        return
    url = url.strip()
    if self._is_wysiwyg_surface():
        self._exec_format("createLink", url)    # 기존 execCommand 경로 재사용
    elif self._is_source_editor_surface():
        self._editor_link(url)

def _dispatch_clear(self) -> None:
    if self._is_wysiwyg_surface():
        self._fmt_clear_wysiwyg()               # removeFormat + formatBlock P(기존)
    elif self._is_source_editor_surface():
        self._editor_clear()                    # v1: no-op 또는 선택 표식 제거(§5.5)
```

> **기존 WYSIWYG 헬퍼 재사용:** `_wysiwyg_inline`/`_wysiwyg_block`/`_wysiwyg_heading`
> 는 현행 `_exec_format(...)`/`_fmt_inline_code` 호출을 그대로 감싸면 된다(이름만
> 정리). 즉 WYSIWYG 동작은 **변경 없음** — surface 분기 한 겹만 추가.

### 5.2 WYSIWYG 경로 (기존 동작 — 래퍼만)

```python
def _wysiwyg_inline(self, kind: str) -> None:
    cmd = {"bold":"bold", "italic":"italic", "strike":"strikeThrough"}.get(kind)
    if cmd:
        self._exec_format(cmd)                  # 기존
    elif kind == "code":
        self._fmt_inline_code()                 # 기존(선택을 <code> 로)

def _wysiwyg_block(self, kind: str) -> None:
    if kind == "ul":   self._exec_format("insertUnorderedList")
    elif kind == "ol": self._exec_format("insertOrderedList")
    elif kind == "quote": self._exec_format("formatBlock", "BLOCKQUOTE")

def _wysiwyg_heading(self, level: int) -> None:
    tag = {0:"P", 1:"H1", 2:"H2", 3:"H3"}.get(level, "P")
    self._exec_format("formatBlock", tag)
```

### 5.3 ★ 에디터(QTextCursor) 마크다운 인라인 토글/삽입

소스 편집기에서 선택을 마크다운 마커로 **감싼다(토글)**. 토글: 선택 양끝이 이미
마커면 제거, 아니면 추가. 선택이 없으면 마커 한 쌍을 삽입하고 **커서를 가운데**.

```python
_INLINE_MARK = {"bold": "**", "italic": "*", "strike": "~~", "code": "`"}

def _editor_inline(self, kind: str) -> None:
    """선택을 마크다운 인라인 마커로 토글(없으면 자리표시 + 커서 가운데)."""
    mark = _INLINE_MARK.get(kind)
    if mark is None:
        return
    cur = self.editor.textCursor()
    sel = cur.selectedText()                     # QChar 0x2029(단락 구분) 주의 — 아래
    if sel:
        # QPlainTextEdit 선택은 단락 구분자로 U+2029 를 쓰므로 \n 으로 환원.
        text = sel.replace(" ", "\n")
        if text.startswith(mark) and text.endswith(mark) and len(text) >= 2 * len(mark):
            new = text[len(mark):len(text) - len(mark)]     # 토글 해제
        else:
            new = f"{mark}{text}{mark}"                      # 토글 적용
        cur.insertText(new)                       # 선택 치환(undo 1스텝)
    else:
        cur.insertText(mark + mark)               # 빈 마커 쌍
        pos = cur.position() - len(mark)          # 가운데로 커서 이동
        cur.setPosition(pos)
        self.editor.setTextCursor(cur)
    self.editor.setFocus()
    # 편집기 textChanged 가 디바운스 렌더+dirty 를 자동 처리(별도 호출 불필요).
```

> **dirty/렌더 자동 처리:** `cur.insertText(...)` 는 `editor.textChanged` 를 발화 →
> 기존 `_on_editor_text_changed` 가 `_set_dirty(True)` + 디바운스 렌더를 돈다.
> 따라서 **서식 적용이 곧 편집**으로 일관 처리됨(별도 dirty/렌더 코드 불필요).
> `_suppress_editor_signal` 은 프로그램적 '채움'(setPlainText)에만 켜지므로,
> 사용자 의도의 `insertText` 는 정상적으로 신호를 낸다(억제 안 함).

### 5.4 ★ 에디터 블록 서식 — 줄 머리 접두 토글(목록/인용/제목)

블록 서식은 **선택된 각 줄(또는 현재 줄)의 머리에 접두**를 붙이거나 뗀다.

```python
_LINE_PREFIX = {"ul": "- ", "ol": "1. ", "quote": "> "}
_HEADING_PREFIX = {1: "# ", 2: "## ", 3: "### "}   # 0(본문)은 모든 # 제거

def _editor_block(self, kind: str) -> None:
    """불릿/번호/인용: 선택된 각 줄 머리에 접두 토글."""
    prefix = _LINE_PREFIX.get(kind)
    if prefix is None:
        return
    self._editor_apply_line_prefix(prefix, exclusive=False)

def _editor_heading(self, level: int) -> None:
    """제목1~3 = 줄 머리에 #/##/### (기존 헤딩 접두 교체). level 0 = 본문(# 제거)."""
    if level == 0:
        self._editor_strip_heading()
    else:
        self._editor_apply_line_prefix(_HEADING_PREFIX[level], exclusive=True,
                                       is_heading=True)
```

선택 줄 범위에 접두를 토글하는 공통 헬퍼(QTextCursor 블록 순회):

```python
def _editor_apply_line_prefix(self, prefix: str, *, exclusive: bool,
                              is_heading: bool = False) -> None:
    """선택(또는 현재 줄)의 각 줄 머리에 prefix 를 토글한다.

    exclusive=True(헤딩): 기존 헤딩 접두(#,##,###)를 먼저 제거 후 새 접두 부여
        (이미 같은 레벨이면 제거 = 토글). 목록/인용(exclusive=False)은 단순 토글.
    번호 목록('1. ')은 v1 에서 모든 줄에 '1. ' 부여(자동 증가는 §10 후보 — 마크다운
        렌더러가 1. 반복도 순번으로 렌더하므로 표시상 정상).
    """
    cur = self.editor.textCursor()
    cur.beginEditBlock()                          # 다중 줄 변경을 undo 1스텝으로
    try:
        start = cur.selectionStart()
        end = cur.selectionEnd()
        cur.setPosition(start)
        start_block = cur.blockNumber()
        cur.setPosition(end)
        end_block = cur.blockNumber()
        # 각 블록(줄) 순회.
        block = self.editor.document().findBlockByNumber(start_block)
        # 토글 판단: 선택 첫 줄이 이미 prefix(헤딩이면 동일 레벨)면 제거 모드.
        first_text = block.text()
        remove = first_text.startswith(prefix) if not is_heading \
            else first_text.startswith(prefix)
        for n in range(start_block, end_block + 1):
            blk = self.editor.document().findBlockByNumber(n)
            line = blk.text()
            edit = QTextCursor(blk)
            edit.movePosition(QTextCursor.MoveOperation.StartOfBlock)
            if is_heading:
                # 기존 헤딩 마커(# 들 + 공백) 제거.
                import re
                m = re.match(r"^(#{1,6}\s+)", line)
                if m:
                    edit.movePosition(QTextCursor.MoveOperation.Right,
                                      QTextCursor.MoveMode.KeepAnchor, len(m.group(1)))
                    edit.removeSelectedText()
                if not remove:
                    edit.insertText(prefix)        # 새 헤딩 레벨
            else:
                if remove and line.startswith(prefix):
                    edit.movePosition(QTextCursor.MoveOperation.Right,
                                      QTextCursor.MoveMode.KeepAnchor, len(prefix))
                    edit.removeSelectedText()
                elif not remove and not line.startswith(prefix):
                    edit.insertText(prefix)
    finally:
        cur.endEditBlock()
    self.editor.setFocus()

def _editor_strip_heading(self) -> None:
    """현재 줄/선택의 헤딩 접두(#,##,…)를 제거(본문 전환)."""
    import re
    cur = self.editor.textCursor()
    cur.beginEditBlock()
    try:
        start_block = self.editor.document().findBlock(cur.selectionStart()).blockNumber()
        end_block = self.editor.document().findBlock(cur.selectionEnd()).blockNumber()
        for n in range(start_block, end_block + 1):
            blk = self.editor.document().findBlockByNumber(n)
            m = re.match(r"^(#{1,6}\s+)", blk.text())
            if m:
                edit = QTextCursor(blk)
                edit.movePosition(QTextCursor.MoveOperation.StartOfBlock)
                edit.movePosition(QTextCursor.MoveOperation.Right,
                                  QTextCursor.MoveMode.KeepAnchor, len(m.group(1)))
                edit.removeSelectedText()
    finally:
        cur.endEditBlock()
    self.editor.setFocus()
```

> **구현 주의(ui-dev):** 위 블록 순회는 `beginEditBlock/endEditBlock` 으로 묶어
> **undo 한 스텝**으로 만든다. `import re` 는 메서드 상단으로 끌어올려도 무방.
> `textChanged` 가 endEditBlock 시 1회(또는 편집마다) 발화 → 디바운스가 합쳐 렌더.
> dirty 는 자동(§5.3 와 동일 원리).

### 5.5 에디터 링크 / 서식 지우기

```python
def _editor_link(self, url: str) -> None:
    """[선택텍스트](url) 삽입. 선택이 없으면 [링크 텍스트](url) + 커서 배치."""
    cur = self.editor.textCursor()
    sel = cur.selectedText().replace(" ", "\n")
    if sel:
        cur.insertText(f"[{sel}]({url})")
    else:
        placeholder = "링크 텍스트"
        cur.insertText(f"[{placeholder}]({url})")
        # 커서를 placeholder 선택 상태로(바로 덮어쓰기 가능).
        pos = cur.position() - (len(url) + 3 + len(placeholder))  # ](url) 뒤 보정
        cur.setPosition(pos)
        cur.setPosition(pos + len(placeholder), QTextCursor.MoveMode.KeepAnchor)
        self.editor.setTextCursor(cur)
    self.editor.setFocus()

def _editor_clear(self) -> None:
    """에디터 서식 지우기 — v1: no-op(소스에서 마커 자동 제거는 모호).

    마크다운 소스 편집기에서 '서식 지우기'는 의미가 불명확(어떤 마커를 지울지).
    v1 은 안내만(또는 액션 비활성). 확장: 선택 영역의 **/*/~~/` 마커 제거(§10).
    """
    self.statusBar().showMessage(
        "서식 지우기는 라이브 편집(WYSIWYG)에서 동작합니다.", 3000)
```

> **서식 지우기 게이팅(권장):** 에디터 surface 에선 `act_fmt_clear` 를 **비활성**
> (`setEnabled(False)`)하는 편이 깔끔하다. `_apply_view_mode` 에서
> `self.act_fmt_clear.setEnabled(self._view_mode == MODE_WYSIWYG)` 로 처리(ui-dev
> 재량 — no-op 안내보다 비활성 권장).

### 5.6 heading 드롭다운 (QComboBox)

서식 툴바에 본문/제목1/제목2/제목3 콤보를 둔다. 선택 시 `_dispatch_heading`.

```python
from PySide6.QtWidgets import QComboBox

def _build_format_toolbar(self) -> None:
    tb = self.addToolBar("서식")
    tb.setObjectName("formatToolbar")
    tb.setMovable(False)
    self.format_toolbar = tb

    # heading 드롭다운(본문=0, 제목1~3).
    self.cmb_heading = QComboBox(self)
    self.cmb_heading.addItems(["본문", "제목 1", "제목 2", "제목 3"])
    self.cmb_heading.setToolTip("문단 스타일")
    self.cmb_heading.activated.connect(
        lambda idx: self.fmt_heading(idx))        # idx 0=본문,1~3=Hn
    tb.addWidget(self.cmb_heading)
    tb.addSeparator()

    # 인라인.
    self.act_fmt_bold   = self._mk_fmt("굵게",   self.fmt_bold,   "굵게 (**)")
    self.act_fmt_italic = self._mk_fmt("기울임", self.fmt_italic, "기울임 (*)")
    self.act_fmt_strike = self._mk_fmt("취소선", self.fmt_strike, "취소선 (~~)")
    self.act_fmt_code   = self._mk_fmt("코드",   self.fmt_code,   "인라인 코드 (`)")
    tb.addAction(self.act_fmt_bold); tb.addAction(self.act_fmt_italic)
    tb.addAction(self.act_fmt_strike); tb.addAction(self.act_fmt_code)
    tb.addSeparator()
    # 블록.
    self.act_fmt_ul    = self._mk_fmt("• 목록",  self.fmt_ul,    "불릿 목록 (- )")
    self.act_fmt_ol    = self._mk_fmt("1. 목록", self.fmt_ol,    "번호 목록 (1. )")
    self.act_fmt_quote = self._mk_fmt("인용",    self.fmt_quote, "인용 (> )")
    tb.addAction(self.act_fmt_ul); tb.addAction(self.act_fmt_ol)
    tb.addAction(self.act_fmt_quote)
    tb.addSeparator()
    self.act_fmt_link  = self._mk_fmt("링크", self.fmt_link, "링크 삽입")
    self.act_fmt_clear = self._mk_fmt("서식 지우기", self.fmt_clear, "서식 지우기(WYSIWYG)")
    tb.addAction(self.act_fmt_link); tb.addAction(self.act_fmt_clear)

    tb.setVisible(False)   # _apply_view_mode 가 편집 surface 에서만 표시

def _mk_fmt(self, label: str, slot, tip: str = "") -> QAction:
    a = QAction(label, self)
    if tip:
        a.setToolTip(tip)
    a.triggered.connect(slot)
    return a
```

> ⚠️ `_mk_fmt` 시그니처가 현행(`label, slot`)에서 `(label, slot, tip="")` 로 확장됨
> — 기존 호출부도 함께 교체(서식 슬롯 통합 §5.1 로 어차피 전부 재작성).
> **콤보 동기화(선택):** 커서 이동 시 현재 줄의 헤딩 레벨을 콤보에 반영하는 것은
> v1 범위 외(§10) — 콤보는 '적용' 트리거로만 사용(상태 표시 안 함).

---

## 6. 메뉴 정합 (파일 메뉴 + 보기 메뉴)

### 6.1 파일 메뉴 (새 문서 맨 위)

```
파일(&F)
  새 문서            (Ctrl+N)        ← 신규, 맨 위
  열기...            (Ctrl+O)
  최근 파일(&R) ▶
  ──
  클립보드를 마크다운으로 붙여넣기  (Ctrl+Shift+V)
  저장               (Ctrl+S)
  다른 이름으로 저장... (Ctrl+Shift+S)
  ──
  새로고침           (Ctrl+R)
  ──
  종료               (Ctrl+Q)
```

### 6.2 편집 메뉴 (신규 — 선택)

워드프로세서답게 **편집(&E) 메뉴**를 추가해 실행취소/다시실행을 둔다(메뉴바 일관성).

```
편집(&E)
  실행취소           (Ctrl+Z)
  다시실행           (Ctrl+Shift+Z / Ctrl+Y)
```

> 메뉴 위치: 파일과 보기 사이. 메뉴 없이 툴바·단축키만 둬도 동작하나, 발견성·관습상
> 편집 메뉴 권장(ui-dev 재량).

### 6.3 보기 메뉴

변경 없음(기존 모드 4종 + 테마 + 줌 + 전체화면 + 목차). 서식은 **툴바 전용**(메뉴
미추가 — v1 단순화. 서식 메뉴는 §10 확장 후보).

### 6.4 About 다이얼로그 단축키 안내 갱신

```
Ctrl+N 새 문서 · Ctrl+O 열기 · Ctrl+S 저장 · Ctrl+Shift+S 다른 이름 ·
Ctrl+Z 실행취소 · Ctrl+Shift+Z/Ctrl+Y 다시실행 ·
Ctrl+1/2/3/4 편집기/미리보기/분할/라이브 편집 · …(기존)
```

---

## 7. 단축키 충돌 점검 (신규 4개 모두 비충돌)

| 기능 | 단축키 | 상태 |
|------|--------|------|
| **새 문서** | **Ctrl+N** | **신규(비충돌)** |
| **실행취소** | **Ctrl+Z** | **신규(비충돌)** |
| **다시실행** | **Ctrl+Shift+Z / Ctrl+Y** | **신규(비충돌)** |
| 열기 / 저장 / 다른이름 | Ctrl+O / Ctrl+S / Ctrl+Shift+S | 기존 |
| 붙여넣기 | Ctrl+Shift+V | 기존 |
| 새로고침 / 테마 | Ctrl+R / Ctrl+T | 기존 |
| 줌 인/아웃/리셋 | Ctrl+= / Ctrl+- / Ctrl+0 | 기존 |
| 모드 4종 | Ctrl+1 / 2 / 3 / 4 | 기존 |
| 전체화면 / 목차 / 종료 | F11 / Ctrl+\\ / Ctrl+Q | 기존 |
| 서식 명령(굵게 등) | **단축키 없음**(툴바 클릭) | v1 |

- 기존 사용 문자: O,S,R,T,V,Q,=,-,0,1,2,3,4,\\,F11. **N,Z,Y 는 미사용** → 충돌 없음.
- ⚠️ **Ctrl+Z 와 편집기 자체 단축키:** `QPlainTextEdit` 는 자체적으로 Ctrl+Z(undo)를
  처리한다. `act_undo`(Ctrl+Z)를 윈도우 레벨 QAction 으로 등록하면 **편집기 내장
  단축키와 경합**할 수 있다. 해결: `act_undo`/`act_redo` 의 `setShortcutContext`
  를 `Qt.ShortcutContext.WindowShortcut`(기본)로 두되, **슬롯이 surface 라우팅으로
  결국 `editor.undo()` 를 부르므로 동작은 동일**(편집기 포커스 시 Qt 가 어느 쪽을
  먼저 잡든 결과가 같음). WYSIWYG 에선 편집기가 포커스를 못 받으므로 QAction 이
  잡아 execCommand 로 라우팅 — 정상. **결론: 라우팅 슬롯이 양쪽을 흡수하므로
  경합이 버그가 되지 않는다.** (만약 더블 undo 가 관측되면 §10: 편집기 포커스 시
  QAction 을 `ApplicationShortcut` 대신 비활성화하는 미세 조정.)

---

## 8. 패키징 친화 (신규 자산 0 — 확정)

| 항목 | 결정 |
|------|------|
| 신규 리소스/아이콘 파일 | **0개.** 아이콘은 `QStyle.standardIcon`(Qt 내장), 서식은 텍스트/유니코드 |
| `mdviewer.spec` datas/hiddenimports | **추가 0** → spec 무변경(packager 회귀 스모크만) |
| 신규 런타임 의존성 | **0.** `QStyle`/`QComboBox`/`QTextCursor` 모두 PySide6 기존 |
| `requirements.txt` | **변경 없음** |
| paths.resource_path 신규 호출 | **없음**(아이콘이 파일이 아니므로 base path 의존 0) |

> `QStyle.StandardPixmap` 아이콘은 OS/스타일에 따라 모양이 다를 수 있으나 **항상
> 존재**(번들 누락 위험 0). 이것이 외부 .ico/.png 를 추가하지 않는 핵심 이유 —
> frozen exe 에서 깨질 자산이 없다.

---

## 9. core(renderer/file_watcher/theme/settings) 변경 여부

**모두 불필요.** 근거:
- 새 문서 = `_doc_text=""` + 기존 `_render_doc`/`_sync_editor_from_doc`. core 무관.
- Undo/Redo = `QPlainTextEdit.undo/redo`(Qt) + `execCommand`(JS). core 무관.
- 에디터 서식 = `QTextCursor` 문자열 조작(UI). core 무관.
- WYSIWYG 서식 = 기존 `_exec_format`/`html_to_markdown` 경로. core 무관.
- 아이콘 = `QStyle` 내장. theme.py/settings.py 무관.
- view_mode 유효값 변경 없음(새 모드 없음) → **settings.py 무변경**.

> ui-dev 가 구현 중 core 변경이 꼭 필요하다 판단하면(예: 에디터 서식 결과를 더
> 충실히 렌더하려 renderer 옵션 조정) architect 에게 통지해 계약을 갱신한다.
> 현 설계 목표는 **core/settings/theme 변경 0, main_window.py 단일 파일 변경**.

---

## 10. 영향 파일 & 담당

| 파일 | 변경 | 담당 |
|------|------|------|
| `src/mdviewer/main_window.py` | `act_new`/`new_document`/`_new_scratch`; 툴바 재구성(`_build_toolbar` 그룹+아이콘+툴팁); `act_undo`/`act_redo`/`do_undo`/`do_redo`/`_wysiwyg_exec_simple`; surface 판별 헬퍼(`_is_source_editor_surface`/`_is_wysiwyg_surface`/`_is_edit_surface`); 통합 서식 디스패치(`fmt_*`/`_dispatch_*`/`_wysiwyg_*`/`_editor_*` + heading 콤보); `_build_format_toolbar` 재작성(콤보·툴팁·라벨); `_apply_view_mode` 게이팅 확장(서식툴바 편집surface 공통 + undo/redo enable); 편집(&E) 메뉴; About 갱신 | **ui-dev** |
| `src/mdviewer/settings.py` | **변경 없음** | — |
| `src/mdviewer/theme.py` | **변경 없음** | — |
| `src/mdviewer/renderer.py` | **변경 없음** | — |
| `src/mdviewer/file_watcher.py` | **변경 없음** | — |
| `requirements.txt` / `mdviewer.spec` | **변경 없음**(신규 의존성·자산 0) | — / packager(검증만) |
| `tests/` | 새 문서(빈 `_doc_text`/dirty=False/Preview→Split), undo/redo 라우팅(편집기 vs no-op), 에디터 인라인 토글(`**선택**`/해제), 줄 머리 접두(목록/인용/헤딩 토글), 서식 툴바 게이팅(Preview 숨김), 단축키 비충돌 | QA |

---

## 11. 빌드 순서 & 통합 지점

```
Step 1  architect  ──▶ 본 설계(09) 확정 → ui-dev 에 계약 통지(core/settings/theme 불변)
Step 2  ui-dev     ──▶ main_window.py 단일 파일: 새 문서 + 툴바 재구성 + undo/redo 라우팅
                        + 통합 서식 디스패치(에디터 QTextCursor / WYSIWYG execCommand)
                      (core 변경 없음 → core-engine-dev 대기 불필요)
Step 3  QA         ──▶ §10 tests + 스모크(실앱): 새 문서, undo/redo, 서식 토글, 게이팅
Step 4  packager   ──▶ 회귀 스모크(신규 자산/의존성 0 → spec 변경 없음 확인만)
```

**통합 지점(경계면 버그 단골 — QA 집중 검증 4개):**
- **A. 서식 surface 분기 정확성:** 같은 버튼이 Editor 면 마크다운 마커, WYSIWYG 면
  execCommand 로 동작. `_is_*_surface` 판별이 모드/`_wysiwyg_active` 와 정합(§5).
- **B. 새 문서 상태 일관:** 빈 `_doc_text`·dirty=False·타이틀 "제목 없음"·편집 가능
  모드·편집기 비움. `_maybe_discard`/WYSIWYG 탈출과 모순 없음(§1).
- **C. Undo/Redo 라우팅:** Editor=`editor.undo`, WYSIWYG=execCommand+캡처(`_doc_text`
  동기화), Preview=비활성. Ctrl+Z 편집기 내장 경합이 버그로 안 나타남(§7).
- **D. 에디터 서식 dirty/렌더 자동화:** `insertText`/`beginEditBlock` 가 `textChanged`
  를 통해 dirty+디바운스 렌더를 자동 유발(별도 호출 없이 일관, §5.3/§5.4).

---

## 12. 확장 후보 (범위 외 — 표기만)

- 서식 단축키(편집 surface 활성 시 Ctrl+B/I/K 동적 바인딩), 서식 메뉴(&O).
- heading 콤보 상태 동기화(커서 줄의 현재 레벨 표시), 인라인 마커 상태 토글 표시.
- 번호 목록 자동 증가(1. 2. 3.), 에디터 서식 지우기(선택 영역 마커 제거).
- 에디터 `undoAvailable`/`redoAvailable` 시그널로 undo/redo 미세 토글.
- 표 삽입, 이미지 삽입(파일 선택), 마크다운 신택스 하이라이트(QSyntaxHighlighter).
- 새 문서를 곧장 특정 모드로(설정), 새 문서 템플릿.
- 표준 워드프로세서 범위 밖이므로 후보로만 둔다.
```
