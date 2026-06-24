# MDViewer 인라인 편집기 + 뷰 모드(Editor/Preview/Split) 설계 (Phase 7)

> 작성: architect · 2026-06-25
> 범위 확장: 원본 마크다운을 **편집 가능한 편집기 창** + **미리보기 창**을
> (1) 에디터 전용 (2) 프리뷰 전용 (3) 동시 표시(Split) 로 전환. 편집 시 프리뷰
> 라이브 갱신.
> 기준 문서: `_workspace/01_architect_blueprint.md`(렌더 API 계약),
> `_workspace/06_clipboard_feature_design.md`(문서 모델 `_doc_text/_path/_dirty`,
> `_render_doc`/`_load_from_disk`/`_set_scratch`/`_attach_path`/`_maybe_discard`),
> 현행 `main_window.py` / `renderer.py` / `settings.py` / `theme.py`.

이 문서는 **계약(contract)**이다. **ui-dev** 가 이 계약대로 `main_window.py` 를
구현한다. core(renderer/file_watcher) 변경은 **불필요**하다(§9 참조). Phase 6 가
이미 `_doc_text` 단일 진실원과 렌더 분리를 만들어 두었으므로, 이번 작업은 그 위에
**편집기 위젯·뷰 모드 상태기계·편집↔감시 충돌 정책**을 얹는 것이다.

---

## 0. 핵심 설계 결정 요약 (못박음)

| 항목 | 결정 |
|------|------|
| 편집기 위젯 | **`QPlainTextEdit`** (고정폭 글꼴, 줄바꿈 끔, 마크다운 소스 전용) |
| 단일 진실원 | **`_doc_text`** 유지. 편집기 텍스트 ↔ `_doc_text` 동기화는 명시 지점에서만 |
| 라이브 프리뷰 | `editor.textChanged` → **~300ms 디바운스 QTimer** → `_doc_text = editor.toPlainText()` → `_render_doc(preserve_scroll=True)` + `_set_dirty(True)` |
| 뷰 모드 | **3종**: `Editor` / `Preview` / `Split`. View 메뉴 + 툴바 + 단축키 |
| 단축키 | `Ctrl+1`=Editor, `Ctrl+2`=Preview, `Ctrl+3`=Split (기존과 비충돌, §6) |
| 기본 모드 | **Preview**(기존 동작 보존). QSettings 마지막 모드 복원 |
| 레이아웃 | 기존 `splitter[toc_list, view]` → **`splitter[toc_list, editor_preview_split]`** 중첩 스플리터. `editor_preview_split = [editor, view]` |
| 편집기 채움 시 | open/paste/load 가 편집기를 채울 때 **`editor.blockSignals(True)`** 로 `textChanged` 차단 → dirty 오염·이중 렌더 방지 |
| 저장 입력원 | `write_markdown(path, self._doc_text)` 유지(편집기 내용=`_doc_text`) |
| ★ 편집↔감시 충돌 | 편집 중(dirty) 외부 변경 시 **자동 reload 금지** → 배너/상태바 안내만(Ctrl+R 수동) |
| ★ self-write 억제 | 자기 저장(`write_markdown`)이 유발하는 watcher 이벤트 **무시**(저장 직전 suppress 플래그 + 디스크내용==`_doc_text` 비교 이중 가드) |
| `_maybe_discard` | reload/open/paste/close 에는 유지. **모드 전환에는 불필요**(데이터 변경 아님) |
| core 변경 | **불필요**. `render`/`read_markdown`/`write_markdown` 이미 존재 |
| 영향 파일 | UI: `main_window.py` · meta: `settings.py`(키 1개 추가, 선택) |

---

## 1. 편집기 위젯 결정 — `QPlainTextEdit`

### 결정: `QPlainTextEdit` 채택 (`QTextEdit` 아님)

```python
from PySide6.QtWidgets import QPlainTextEdit
from PySide6.QtGui import QFontDatabase
from PySide6.QtCore import Qt

self.editor = QPlainTextEdit(self)
self.editor.setObjectName("sourceEditor")
self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)   # 소스 줄 보존
self.editor.setTabChangesFocus(False)                            # Tab = 들여쓰기
self.editor.setAcceptDrops(False)                                # 드롭은 메인 윈도우
# 고정폭 글꼴(플랫폼 기본 monospace; 없으면 Qt 가 대체).
mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
self.editor.setFont(mono)
self.editor.setVisible(False)                                    # 초기 모드에 따라 결정
self.editor.textChanged.connect(self._on_editor_text_changed)
```

### 사유 (대안 비교)

| 후보 | 판정 | 사유 |
|------|------|------|
| **QPlainTextEdit** | **채택** | 대용량 평문에 최적화(서식 없는 마크다운 소스에 정확히 부합), 고정폭/줄바꿈 끔 자연스러움, 가벼움 |
| QTextEdit | 탈락 | rich-text 엔진 → 대용량 소스 편집에서 무겁고 서식 오염 위험 |
| QScintilla | 탈락 | 외부 의존(QScintilla) 추가 → 표준 범위/패키징 표면 확대. 표준 뷰어 범위 밖 |

- **드롭 비활성:** `self.editor.setAcceptDrops(False)`. 기존 `view.setAcceptDrops(False)`
  와 동일하게, 파일 드롭은 **메인 윈도우의 `dropEvent`** 가 받아 `open_path` 로 처리한다
  (편집기에 텍스트로 떨궈지는 것 방지). 단 메인 윈도우 `dropEvent` 는 현행 그대로 둔다.

> 확장 후보(범위 외): 라인 넘버 거터, 마크다운 신택스 하이라이트(`QSyntaxHighlighter`).
> 표준 범위가 아니므로 §10 후보로만 둔다.

---

## 2. 뷰 모드 상태기계 (★ 핵심)

### 2.1 모드 정의

```python
# 모드 식별자(문자열 상수 — QSettings 직렬화 친화)
MODE_EDITOR  = "editor"    # 편집기 전용 (view 숨김)
MODE_PREVIEW = "preview"   # 프리뷰 전용 (editor 숨김) — 기존 동작
MODE_SPLIT   = "split"     # 동시 표시 (editor + view 둘 다)
VALID_MODES  = (MODE_EDITOR, MODE_PREVIEW, MODE_SPLIT)
```

`MainWindow.__init__` 에 상태 추가:

```python
self._view_mode: str = MODE_PREVIEW         # 현재 뷰 모드(아래 복원으로 덮어씀)
self._render_timer: QTimer                  # 라이브 프리뷰 디바운스(§3)
self._suppress_editor_signal: bool = False  # 편집기 프로그램적 채움 시 textChanged 억제(§4)
```

### 2.2 위젯 가시성 표 (★ ui-dev 가 이 표대로)

| 모드 | `editor` | `view`(프리뷰) | `toc_list` | 비고 |
|------|:---:|:---:|:---:|------|
| `MODE_EDITOR` | **show** | hide | hide(강제) | TOC 는 프리뷰 부속 → 편집 전용에선 숨김 |
| `MODE_PREVIEW` | hide | **show** | 사용자 토글값 | 기존 동작과 동일 |
| `MODE_SPLIT` | **show** | **show** | 사용자 토글값 | 편집 + 라이브 프리뷰 |

- **TOC 가시성 규칙:** TOC 는 프리뷰의 목차이므로 `MODE_EDITOR` 에서는 강제로 숨긴다.
  단 **사용자의 TOC 토글 설정(`act_toggle_toc`/QSettings)은 보존**한다 — Preview/Split 로
  돌아오면 사용자가 마지막에 선택한 TOC 가시성으로 복귀. (즉 모드가 TOC 설정을 덮어쓰지
  않고, "표시 여부"만 모드에 따라 게이팅한다. §2.4 `_apply_view_mode` 참조.)

### 2.3 모드 전환 상태기계

```
                 Ctrl+1                  Ctrl+2                 Ctrl+3
   (any mode) ───────────▶ EDITOR   (any) ──────▶ PREVIEW  (any) ──────▶ SPLIT

전환 시 공통 동작(_apply_view_mode):
  1. _view_mode = new_mode  (먼저 저장)
  2. editor/view show|hide  (§2.2 표)
  3. toc_list 가시성 = (모드가 TOC 허용) AND (사용자 토글값)
  4. EDITOR/SPLIT 진입 & 편집기가 _doc_text 와 다르면 편집기 동기화(§4 _sync_editor_from_doc)
  5. 스크롤/포커스 보존(§2.5)
  6. 액션 체크 상태 갱신(상호배타 그룹)
  7. QSettings 에 모드 저장(§7)
  8. 상태바 안내(선택)
```

**전환은 데이터 변경이 아니다** → `_maybe_discard` **호출 금지**(§5.4). 모드 전환으로
dirty 가 바뀌어선 안 된다.

### 2.4 `_apply_view_mode` 구현 계약

```python
def _apply_view_mode(self, mode: str, *, persist: bool = True) -> None:
    """뷰 모드를 적용한다(가시성·동기화·스크롤·포커스·액션·저장).

    데이터(_doc_text/_dirty)를 변경하지 않는다. _maybe_discard 를 호출하지 않는다.
    """
    if mode not in VALID_MODES:
        mode = MODE_PREVIEW
    self._view_mode = mode

    show_editor  = mode in (MODE_EDITOR, MODE_SPLIT)
    show_preview = mode in (MODE_PREVIEW, MODE_SPLIT)

    # 4) EDITOR/SPLIT 진입 시 편집기를 _doc_text 와 동기화(신호 억제로 dirty 오염 방지).
    if show_editor:
        self._sync_editor_from_doc()        # §4 — blockSignals 보호

    # 2) 가시성.
    self.editor.setVisible(show_editor)
    self.view.setVisible(show_preview)

    # 3) TOC: 모드가 허용할 때만, 사용자 토글값을 반영.
    toc_allowed = show_preview               # 프리뷰가 보일 때만 TOC 의미 있음
    self.toc_list.setVisible(toc_allowed and self.settings.toc_visible())
    self.act_toggle_toc.setEnabled(toc_allowed)   # Editor 모드에선 TOC 토글 비활성

    # 6) 상호배타 액션 체크.
    self.act_mode_editor.setChecked(mode == MODE_EDITOR)
    self.act_mode_preview.setChecked(mode == MODE_PREVIEW)
    self.act_mode_split.setChecked(mode == MODE_SPLIT)

    # 5) 포커스: 편집기가 보이면 편집기에, 아니면 프리뷰에.
    if show_editor:
        self.editor.setFocus()
    else:
        self.view.setFocus()

    # 7) 저장.
    if persist:
        self.settings.set_view_mode(mode)

    # 8) 안내(선택).
    label = {MODE_EDITOR: "편집기", MODE_PREVIEW: "미리보기", MODE_SPLIT: "분할"}[mode]
    self.statusBar().showMessage(f"보기: {label}", 1500)
```

전용 슬롯(액션 연결용):

```python
def set_mode_editor(self)  -> None: self._apply_view_mode(MODE_EDITOR)
def set_mode_preview(self) -> None: self._apply_view_mode(MODE_PREVIEW)
def set_mode_split(self)   -> None: self._apply_view_mode(MODE_SPLIT)
```

### 2.5 전환 시 내용·스크롤·포커스 보존 규칙

| 보존 대상 | 규칙 |
|----------|------|
| **내용** | `_doc_text` 가 단일 진실원이므로 모드 전환으로 내용은 절대 소실되지 않음. EDITOR/SPLIT 진입 시 편집기를 `_doc_text` 로 재동기화(이미 같으면 no-op) |
| **편집기 스크롤** | `QPlainTextEdit` 위젯이 자체 스크롤을 보유 → show/hide 만으로 위치 유지(별도 처리 불필요) |
| **프리뷰 스크롤** | 프리뷰를 **재렌더하지 않으면** `QWebEngineView` 가 위치 유지. 모드 전환은 **재렌더하지 않는다**(아래 주의) |
| **포커스** | 편집기 표시 모드 → 편집기 포커스, 아니면 프리뷰 포커스(§2.4) |

> ⚠️ **모드 전환 시 프리뷰를 재렌더하지 말 것.** 모드 전환은 가시성만 바꾼다.
> `_render_doc` 를 호출하면 `setHtml` 이 페이지를 다시 로드해 스크롤이 튄다. 프리뷰
> 내용은 마지막 편집 디바운스(§3)에서 이미 최신이다. **단 하나의 예외**: Preview→Split
> 또는 Editor→(편집발생)→Split 처럼 **편집 후 프리뷰가 한 번도 안 그려진 상태**가
> 생길 수 있다(예: EDITOR 모드에서만 편집하다 Split 진입). 디바운스가 EDITOR 모드에서도
> 계속 도는 정책(§3.3)을 채택하므로 이 예외는 발생하지 않는다 — **프리뷰는 모드와
> 무관하게 항상 최신으로 유지된다.**

---

## 3. 라이브 프리뷰 — 디바운스 데이터 흐름 (★ 핵심)

### 3.1 타이머 구성

```python
_LIVE_PREVIEW_DEBOUNCE_MS = 300   # 입력 멈춤 후 렌더까지 대기(권장 300ms)

# __init__:
self._render_timer = QTimer(self)
self._render_timer.setSingleShot(True)
self._render_timer.setInterval(_LIVE_PREVIEW_DEBOUNCE_MS)
self._render_timer.timeout.connect(self._commit_editor_to_preview)
```

### 3.2 데이터 흐름 (단방향, `_doc_text` 가 허브)

```
사용자 타이핑
   │ (QPlainTextEdit.textChanged 발화)
   ▼
_on_editor_text_changed()
   │ if _suppress_editor_signal: return        # ← 프로그램적 채움이면 무시(§4)
   │ _set_dirty(True)                          # 즉시 미저장 표시(타이틀 •)
   │ _render_timer.start()                     # 디바운스 재시작(연속 입력 합침)
   ▼ (입력 멈춘 뒤 300ms)
_commit_editor_to_preview()
   │ _doc_text = editor.toPlainText()          # ← 단일 진실원 갱신
   │ _render_doc(preserve_scroll=True)         # ← 프리뷰 라이브 갱신(스크롤 보존)
   ▼
프리뷰(QWebEngineView) 갱신 + TOC 갱신(_set_document 내부)
```

```python
def _on_editor_text_changed(self) -> None:
    """편집기 textChanged 슬롯. 프로그램적 채움이면 무시, 사용자 입력이면 디바운스 시작."""
    if self._suppress_editor_signal:
        return
    self._set_dirty(True)            # 사용자 편집 = 즉시 dirty(저장 유도)
    self._render_timer.start()       # 연속 입력은 마지막 1회로 합쳐 렌더

def _commit_editor_to_preview(self) -> None:
    """디바운스 만료 시: 편집기 내용을 _doc_text 에 반영하고 프리뷰 재렌더."""
    self._doc_text = self.editor.toPlainText()
    self._render_doc(preserve_scroll=True)   # 계약상 예외 없음, 스크롤 보존
```

### 3.3 디바운스 정책 세부

- **`_doc_text` 갱신 시점:** 디바운스 만료 시(`_commit_editor_to_preview`)에만 갱신한다.
  타이핑 중간마다 갱신하지 않는다(불필요한 비용 회피). **단, 저장 직전에는 강제 flush 필요**
  — §5.1 `save()` 가 `_flush_pending_edit()` 를 먼저 호출한다(아래).
- **dirty 는 즉시:** 사용자가 한 글자라도 치면 `textChanged` 에서 곧바로 `_set_dirty(True)`.
  렌더는 디바운스해도 미저장 표시는 지연 없이 보여준다.
- **프리뷰는 모드와 무관하게 갱신:** EDITOR 전용 모드에서 타이핑해도 디바운스 타이머는
  돌고 `_render_doc` 가 (숨겨진) 프리뷰를 갱신한다. 이렇게 해야 Split/Preview 로 전환했을 때
  프리뷰가 이미 최신이다(§2.5 예외 제거). `setHtml` 은 위젯이 숨겨져 있어도 동작한다.
- **저장 직전 flush(필수):**

```python
def _flush_pending_edit(self) -> None:
    """대기 중인 디바운스를 즉시 반영(저장·reload·모드 의존 작업 전 호출).

    디바운스 만료 전 사용자가 저장하면 _doc_text 가 한 박자 오래된 상태일 수 있다.
    저장은 _doc_text 를 쓰므로, 편집기가 보이고 타이머가 대기 중이면 강제 commit.
    """
    if self._render_timer.isActive():
        self._render_timer.stop()
        self._doc_text = self.editor.toPlainText()   # 렌더는 생략 가능(저장만이면)
```

  - `save()`/`save_as()`/`_write_to()` 진입 시 **반드시** `_flush_pending_edit()` 선행.
    (안 하면 "마지막 타이핑이 저장 안 됨" 데이터 유실 버그.)
  - `_maybe_discard()` 의 dirty 판단 자체는 `_dirty` 플래그(즉시 갱신)라 정확하나,
    저장 경로로 가면 `save()` 내부 flush 로 최신 내용이 보장된다.

### 3.4 `_render_doc` 와의 정합 (기존 코드 변경 없음)

- `_render_doc(preserve_scroll=True)` 는 현행 그대로 동작한다(`_capture_scroll_then_render`
  → JS 로 현재 스크롤 읽고 → `_set_document` → `_on_load_finished` 에서 복원).
- **라이브 편집에서 스크롤 보존은 프리뷰 깜빡임을 줄인다.** 단 `setHtml` 전체 재로딩은
  편집 중 시각적 점프가 있을 수 있다 — 표준 범위에서 수용(증분 DOM 패치는 §10 후보).
- base_dir 은 `_render_doc` 가 이미 `_path.parent` 또는 `_scratch_base_dir()` 로 계산한다.

---

## 4. 편집기 ↔ `_doc_text` 동기화 규율 (★ 신호 차단)

**원칙:** `_doc_text` 가 단일 진실원. 편집기는 두 방향으로 동기화된다.

- **편집기 → `_doc_text`** : 사용자 입력 시에만(디바운스 commit, §3).
- **`_doc_text` → 편집기** : open/paste/load 가 `_doc_text` 를 바꿀 때 편집기를 다시 채움.
  이때 **반드시 신호 억제**해야 `textChanged` 가 발화해 dirty 가 오염되거나 디바운스
  렌더가 이중 실행되는 것을 막는다.

```python
def _sync_editor_from_doc(self) -> None:
    """_doc_text 를 편집기에 반영한다(프로그램적 채움 — textChanged 억제).

    이미 동일하면 setPlainText 를 건너뛰어 커서/스크롤/undo 스택을 보존한다.
    """
    if self.editor.toPlainText() == self._doc_text:
        return                                   # 동일 → no-op(커서/undo 보존)
    self._suppress_editor_signal = True
    self.editor.blockSignals(True)               # 이중 가드(억제 플래그 + blockSignals)
    try:
        self.editor.setPlainText(self._doc_text)
    finally:
        self.editor.blockSignals(False)
        self._suppress_editor_signal = False
```

### 4.1 동기화 호출 지점 (★ 통합 버그 단골 — 이 표대로)

| 이벤트 | `_doc_text` 변경? | 편집기 동기화 | 비고 |
|--------|:---:|:---:|------|
| `open_path` → `_load_from_disk` | ✅ | ✅ (편집기 보일 때) | 파일 내용으로 편집기 채움 |
| 외부변경 reload(Ctrl+R/자동) | ✅ | ✅ (편집기 보일 때) | §5.3 충돌 정책 준수 후 |
| `paste_clipboard` → `_set_scratch` | ✅ | ✅ (편집기 보일 때) | 클립보드 내용으로 채움 |
| 사용자 타이핑(디바운스 commit) | ✅ | ❌ (편집기가 원천) | 역방향이라 동기화 불필요 |
| 모드 전환(EDITOR/SPLIT 진입) | ❌ | ✅ | `_doc_text` 가 편집기보다 최신일 수 있음 |
| 테마 전환 | ❌ | ❌ | `_doc_text` 불변 → 편집기 손대지 않음 |

**핵심 구현 규칙:** `_doc_text` 를 바꾸는 모든 경로 끝에서 `_sync_editor_from_doc()` 를
호출하되, **편집기가 숨겨진 모드(Preview)에서는 호출을 미뤄도 된다.** 단순화를 위해
**항상 호출**해도 무방하다(`setPlainText` 비용은 무시 가능, 신호 억제로 부작용 없음).
권장: `_load_from_disk` / `_set_scratch` 가 렌더 직후 `_sync_editor_from_doc()` 호출.

### 4.2 기존 헬퍼에 동기화 삽입 (ui-dev 패치 지점)

```python
# _load_from_disk: read 성공 후, _render_doc 호출 다음 줄에 추가
    self._render_doc(preserve_scroll=preserve_scroll)
    self._sync_editor_from_doc()          # ← 추가: 편집기를 디스크 내용과 동기화
    self._set_dirty(False)

# _set_scratch: _render_doc(False) 다음 줄에 추가
    self._render_doc(preserve_scroll=False)
    self._sync_editor_from_doc()          # ← 추가: 편집기를 붙여넣기 내용과 동기화
    self._set_dirty(True)
```

> 이 두 줄이 "파일 열면 편집기에도 내용이 뜬다 / 붙여넣으면 편집기에 소스가 뜬다"를
> 보장한다. 신호 억제로 dirty 는 오염되지 않는다(`_load_from_disk` 는 직후 `_set_dirty(False)`,
> `_set_scratch` 는 `_set_dirty(True)` 로 의도된 상태를 명시 설정).

---

## 5. ★ 편집 ↔ 파일 감시 충돌 정책 (★★ 통합 버그 1순위)

Phase 6 의 watch 생명주기(scratch=stop, 파일=watch)를 유지하되, **편집 중 외부 변경**과
**자기 저장이 유발하는 watcher 이벤트**를 정확히 다룬다. 이 둘을 틀리면
"편집 내용이 외부 이벤트로 덮어써짐" 또는 "저장이 자기 자신을 reload 함" 버그가 난다.

### 5.1 자기 저장 억제 (self-write suppression)

자기 `write_markdown` 은 watcher 의 파일 변경 이벤트를 유발한다. 이를 reload 로 오인하면
**무한 루프/스크롤 튐**이 생긴다. **이중 가드**로 막는다.

**가드 A — suppress 플래그(시간 창):**

```python
_SELF_WRITE_SUPPRESS_MS = 700   # 저장 후 이 시간 내 watcher 이벤트는 무시

# __init__:
self._suppress_watch_until: float = 0.0   # time.monotonic() 기준 만료 시각

# _write_to(): write_markdown 직전(또는 직후)에 창 설정
import time
self._suppress_watch_until = time.monotonic() + (_SELF_WRITE_SUPPRESS_MS / 1000.0)
```

**가드 B — 디스크 내용 == `_doc_text` 비교(내용 동일성):**

```python
def _on_file_changed(self) -> None:
    """watcher 콜백(메인 스레드). self-write 억제 후 충돌 정책 적용."""
    import time
    # 가드 A: 자기 저장 직후의 이벤트는 무시.
    if time.monotonic() < self._suppress_watch_until:
        return
    # (이후 §5.2 디바운스 타이머로)
    self._reload_timer.start()
```

> 가드 A(시간 창)를 1차 방어로 쓰고, 가드 B(내용 비교)는 reload 시점에서 한 번 더 확인해
> "디스크 내용이 이미 `_doc_text` 와 같으면 reload 자체를 생략"하는 보조 방어다(§5.3).
> 두 가드를 함께 두는 이유: 느린 디스크/AV 스캐너로 watcher 이벤트가 700ms 보다 늦게
> 도착할 수 있는데, 그때 가드 B 가 내용 동일성으로 불필요한 reload 를 막는다.

### 5.2 외부 변경 디바운스(기존 `_reload_timer` 재사용 — 단, 콜백 변경)

현행 `__init__` 의 `_reload_timer.timeout` 은 **무조건** `_load_from_disk(preserve_scroll=True)`
로 연결돼 있다. 이것을 **충돌 정책을 적용하는 새 슬롯**으로 교체한다:

```python
# 변경 전(현행):
self._reload_timer.timeout.connect(
    lambda: self._load_from_disk(preserve_scroll=True)
)
# 변경 후:
self._reload_timer.timeout.connect(self._on_external_change_settled)
```

### 5.3 ★ 편집 중(dirty) 외부 변경 = 자동 reload 금지

```python
def _on_external_change_settled(self) -> None:
    """디바운스 만료 후 외부 변경 처리(충돌 정책 적용)."""
    if self._path is None:
        return                                   # scratch — watch 대상 아님
    # 가드 B: 디스크 내용이 이미 _doc_text 와 같으면 reload 불필요(self-write 잔향).
    try:
        disk_text = read_markdown(self._path)
    except OSError:
        self.statusBar().showMessage("외부에서 파일이 변경/삭제되었습니다.", 5000)
        return
    if disk_text == self._doc_text:
        return                                   # 내용 동일 → 무시(자기 저장 등)

    if self._dirty:
        # ★ 편집 중 미저장 변경이 있다 → 자동 reload 금지(편집 내용 보호).
        self._show_external_change_banner()      # §5.5 배너 + 상태바 안내
        return

    # dirty 아님(편집 안 함) → 안전하게 자동 reload(기존 동작 보존).
    self._doc_text = disk_text
    self._render_doc(preserve_scroll=True)
    self._sync_editor_from_doc()
    self._set_dirty(False)
    self.statusBar().showMessage("외부 변경을 반영했습니다.", 2000)
```

**정책 요지:**

| 상황 | 동작 |
|------|------|
| scratch(`_path None`) | watch 안 함 → 도달 안 함 |
| 디스크 내용 == `_doc_text` | 무시(자기 저장 잔향/무변경) |
| **dirty(편집 중)** + 외부 변경 | **자동 reload 금지**. 배너/상태바로 "외부에서 변경됨(Ctrl+R로 새로고침)" 안내. 사용자가 명시적으로 Ctrl+R 해야 덮어씀 |
| not dirty + 외부 변경 | 자동 reload(스크롤 보존) — 기존 순수 뷰어 동작 보존 |

- `read_markdown` 을 메인 스레드에서 직접 호출(작은 마크다운 파일 — 블로킹 무시 가능).
  대용량 우려 시 §10 후보(워커 스레드)로 둔다.

### 5.4 수동 새로고침(Ctrl+R) 은 충돌 정책의 탈출구

`reload_current()`(Ctrl+R)는 **사용자의 명시적 의사**다. dirty 여도 reload 하되,
**`_maybe_discard` 가드 유지**(데이터 유실 경고). 현행 `reload_current` 에 가드만 추가:

```python
def reload_current(self) -> bool:
    if self._path is None:
        self.statusBar().showMessage("임시 문서는 새로고침 대상이 없습니다.", 3000)
        return False
    if self._dirty and not self._maybe_discard():
        return False                              # 사용자가 취소 → reload 중단
    self._clear_external_change_banner()          # 배너 내림(§5.5)
    ok = self._load_from_disk(preserve_scroll=True)
    # _load_from_disk 내부에서 _sync_editor_from_doc 호출됨(§4.2)
    return ok
```

> 주의: `_maybe_discard` 의 "저장" 선택은 `save()` 를 부르고, `save()` 는 디스크에 현재
> `_doc_text` 를 쓴 뒤 `_dirty=False` 로 만든다. 그 직후 `_load_from_disk` 가 방금 쓴
> 내용을 다시 읽으므로 안전하다(가드 A/B 로 watcher 잡음도 억제됨).

### 5.5 외부 변경 배너 (비차단 안내)

자동 reload 를 막은 경우, 사용자가 알 수 있게 **비차단 안내**를 띄운다. 모달
QMessageBox 는 편집 흐름을 끊으므로 **지양**. 두 가지 중 택1(ui-dev 재량):

- **간단안(권장):** 상태바 영구 메시지 + 창 타이틀 보조 표기.
  ```python
  def _show_external_change_banner(self) -> None:
      self.statusBar().showMessage(
          "⚠ 외부에서 파일이 변경되었습니다 — Ctrl+R 로 새로고침(편집 내용은 덮어쓰여집니다)"
      )   # timeout 없이 영구 표시(다음 액션까지 유지)
      self._external_changed = True

  def _clear_external_change_banner(self) -> None:
      self._external_changed = False
      self.statusBar().clearMessage()
  ```
- **배너안(선택):** `QMainWindow` 상단에 `QFrame` 경고 바(노랑 배경 + "새로고침" 버튼 +
  "무시" 버튼)를 표시. 새로고침 버튼 = `reload_current`, 무시 = 배너만 닫음. 표준 범위
  내 권장이나 필수는 아님.

`reload_current`/`save`(자기 저장으로 동기화됨)/`open_path` 진입 시
`_clear_external_change_banner()` 로 배너를 내린다.

### 5.6 watch 생명주기 — Phase 6 표 유지(변경 없음)

| 전환 | watch 동작 | 출처 |
|------|-----------|------|
| 파일 열기 | `watcher.watch(path)` | `_attach_path`(현행) |
| scratch 전환(붙여넣기) | `watcher.stop()` | `_set_scratch`(현행) |
| scratch→파일(저장) | `watcher.watch(new_path)` | `_attach_path`(현행) |
| 파일→다른파일(Save As) | `watcher.watch(new_path)` | `_attach_path`(현행) |

> 이 표는 Phase 6 에서 이미 구현됨. Phase 7 은 watch **이벤트 처리 정책**(§5.1~5.3)만
> 바꾼다. `_set_scratch`/`_attach_path` 자체는 §4.2 동기화 한 줄 추가 외 변경 없음.

---

## 6. 단축키 & 메뉴/툴바 (충돌 확인 — 신규 3개 모두 비충돌)

### 6.1 단축키 표

| 기능 | 단축키 | 상태 |
|------|--------|------|
| 열기 | Ctrl+O | 기존 |
| 붙여넣기 | Ctrl+Shift+V | 기존(Phase 6) |
| 저장 / 다른 이름 | Ctrl+S / Ctrl+Shift+S | 기존(Phase 6) |
| 새로고침 | Ctrl+R | 기존 |
| 테마 전환 | Ctrl+T | 기존 |
| 줌 인/아웃/리셋 | Ctrl+= / Ctrl+- / Ctrl+0 | 기존 |
| 전체화면 | F11 | 기존 |
| 목차 토글 | Ctrl+\\ | 기존 |
| 종료 | Ctrl+Q | 기존 |
| **편집기 모드** | **Ctrl+1** | **신규(비충돌)** |
| **미리보기 모드** | **Ctrl+2** | **신규(비충돌)** |
| **분할 모드** | **Ctrl+3** | **신규(비충돌)** |

- `Ctrl+1/2/3` 은 기존 어떤 단축키와도 겹치지 않는다(기존은 0 만 사용 — 줌 리셋).
  `Ctrl+0` 은 줌 리셋이므로 모드에 0 을 쓰지 않는다.

### 6.2 액션 정의(상호배타 — `QActionGroup`)

```python
from PySide6.QtGui import QActionGroup

# _build_actions():
self.act_mode_editor = QAction("편집기", self, checkable=True)
self.act_mode_editor.setShortcut(QKeySequence("Ctrl+1"))
self.act_mode_editor.triggered.connect(self.set_mode_editor)

self.act_mode_preview = QAction("미리보기", self, checkable=True)
self.act_mode_preview.setShortcut(QKeySequence("Ctrl+2"))
self.act_mode_preview.triggered.connect(self.set_mode_preview)

self.act_mode_split = QAction("분할(편집+미리보기)", self, checkable=True)
self.act_mode_split.setShortcut(QKeySequence("Ctrl+3"))
self.act_mode_split.triggered.connect(self.set_mode_split)

self._mode_group = QActionGroup(self)            # 상호배타(라디오 형태)
self._mode_group.setExclusive(True)
for a in (self.act_mode_editor, self.act_mode_preview, self.act_mode_split):
    self._mode_group.addAction(a)
```

### 6.3 메뉴/툴바 배치

- **보기(&V) 메뉴**(상단에 모드 그룹 추가):
  `편집기 / 미리보기 / 분할` → ─ → 테마 전환 → ─ → 줌… → ─ → 전체화면 → 목차 표시
- **툴바**: 열기 · 새로고침 · ─ · 붙여넣기 · 저장 · ─ · **편집기 · 미리보기 · 분할** ·
  ─ · 줌… · ─ · 테마 · 목차
- **About 다이얼로그** 단축키 안내에 `Ctrl+1/2/3 보기 모드` 추가.

---

## 7. QSettings 영속화 (키 1개 추가)

`settings.py` 에 뷰 모드 키를 추가한다(다른 보기 옵션과 동일 패턴).

```python
# settings.py
_KEY_VIEW_MODE = "view/mode"        # ← 신규

def view_mode(self) -> str:
    """저장된 뷰 모드. 유효하지 않으면 'preview'(기본)."""
    val = self._s.value(_KEY_VIEW_MODE, "preview", type=str)
    return val if val in ("editor", "preview", "split") else "preview"

def set_view_mode(self, value: str) -> None:
    if value in ("editor", "preview", "split"):
        self._s.setValue(_KEY_VIEW_MODE, value)
```

> 모드 상수 문자열(`"editor"/"preview"/"split"`)은 `main_window` 의 `MODE_*` 와
> **반드시 동일**해야 한다. settings 는 문자열만 알고, 검증/매핑은 양쪽이 같은 리터럴을
> 쓰는 것으로 보장한다(순환 import 회피 — settings 가 main_window 를 import 하지 않음).

### 7.1 QSettings 키 전체 (기존 + 신규)

| 키 | 타입 | 용도 | 상태 |
|----|------|------|------|
| `recent_files` | list[str] | 최근 파일 | 기존 |
| `theme` | str | light/dark | 기존 |
| `window/geometry` | bytes | 창 위치/크기 | 기존 |
| `window/state` | bytes | 툴바/도킹 상태 | 기존 |
| `view/toc_visible` | bool | TOC 가시성(사용자 의도) | 기존 |
| `view/zoom` | float | 줌 배율 | 기존 |
| **`view/mode`** | **str** | **마지막 뷰 모드** | **신규** |

### 7.2 복원 시점

`__init__` 의 위젯/액션 생성 **후**, `_show_welcome()` 전에 복원:

```python
# __init__ 끝부분(액션 빌드 후):
restored_mode = self.settings.view_mode()
self._apply_view_mode(restored_mode, persist=False)   # 복원은 다시 저장하지 않음
```

- `persist=False` 로 복원해 복원 자체가 불필요한 쓰기를 일으키지 않게 한다.
- 복원이 EDITOR/SPLIT 면 `_apply_view_mode` 가 `_sync_editor_from_doc()` 를 부른다.
  초기엔 `_doc_text == ""` 라 편집기도 비어 있어 안전(no-op).

---

## 8. 레이아웃 — 중첩 스플리터

### 8.1 위젯 트리

```
QMainWindow.centralWidget = self.splitter (QSplitter Horizontal)
 ├─ self.toc_list (QListWidget)                  ← 좌측 TOC(기존)
 └─ self.editor_preview_split (QSplitter Horizontal)   ← 신규 중첩
      ├─ self.editor (QPlainTextEdit)            ← 편집기(신규)
      └─ self.view   (QWebEngineView)            ← 프리뷰(기존)
```

### 8.2 `__init__` 레이아웃 패치 (현행 코드 → 변경)

```python
# 현행:
#   self.splitter = QSplitter(Horizontal)
#   self.splitter.addWidget(self.toc_list)
#   self.splitter.addWidget(self.view)
#   ...
# 변경:
self.editor_preview_split = QSplitter(Qt.Orientation.Horizontal, self)
self.editor_preview_split.setObjectName("editorPreviewSplit")
self.editor_preview_split.addWidget(self.editor)
self.editor_preview_split.addWidget(self.view)
self.editor_preview_split.setStretchFactor(0, 1)   # editor
self.editor_preview_split.setStretchFactor(1, 1)   # preview
self.editor_preview_split.setSizes([550, 550])     # split 기본 50:50

self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
self.splitter.addWidget(self.toc_list)
self.splitter.addWidget(self.editor_preview_split)
self.splitter.setStretchFactor(0, 0)
self.splitter.setStretchFactor(1, 1)
self.splitter.setSizes([240, 860])
self.setCentralWidget(self.splitter)
```

### 8.3 가시성 메커니즘

- 모드별 show/hide 는 **`editor`/`view`/`toc_list` 위젯 단위**로 한다(§2.2). 스플리터
  자체는 항상 표시. 자식 위젯을 `setVisible(False)` 하면 `QSplitter` 가 핸들을 자동
  접어 남은 위젯이 영역을 채운다 — 별도 사이즈 재계산 불필요.
- `editor_preview_split` 의 핸들 위치는 `window/state` 의 영향을 받지 않으므로
  (objectName 부여했으나 `restoreState` 는 도킹/툴바만 복원) split 비율 영속은
  **범위 외**로 둔다. 매 실행 50:50 시작(원하면 §10 후보).
- `QWebEngineView` 를 `setVisible(False)` 했다가 다시 `True` 로 해도 마지막 `setHtml`
  내용이 유지된다(재렌더 불필요 — §2.5 보존 근거).

---

## 9. core(renderer/file_watcher) 변경 여부

**불필요.** 근거:

- 라이브 프리뷰는 기존 `render(self._doc_text, base_dir=...)` 를 그대로 호출한다.
  렌더는 이미 (a) 예외 비전파, (b) 빈 입력 처리, (c) `_doc_text` 기반 — 편집 흐름에
  필요한 모든 계약을 만족한다.
- 저장은 기존 `write_markdown(path, self._doc_text)`.
- 외부 변경 감지/억제는 **UI 의 watcher 이벤트 처리 정책**(§5)으로 해결 — `FileWatcher`
  API(`watch`/`stop`/`on_changed`) 변경 불필요.
- self-write 억제도 UI 레벨(시간 창 + 내용 비교)에서 처리 — core 가 "누가 썼는지" 알
  필요 없음(경계 유지).

> 만약 ui-dev 가 구현 중 core 변경이 꼭 필요하다고 판단하면(예: `read_markdown` 이
> 디스크 내용 비교용으로 부족하다거나), architect 에게 알려 계약을 갱신한다. 현 설계로는
> **core 변경 0**이 목표다.

---

## 10. 영향 파일 & 담당

| 파일 | 변경 | 담당 |
|------|------|------|
| `src/mdviewer/main_window.py` | `QPlainTextEdit` 편집기 위젯, 중첩 스플리터, 뷰모드 상태기계(`_apply_view_mode`/모드 액션/액션그룹), 라이브 프리뷰 디바운스(`_render_timer`/`_on_editor_text_changed`/`_commit_editor_to_preview`/`_flush_pending_edit`), 편집기 동기화(`_sync_editor_from_doc` + open/paste/load 삽입), 편집↔감시 충돌 정책(`_on_file_changed`/`_on_external_change_settled`/self-write 억제/배너), 메뉴·툴바·About·저장 flush | **ui-dev** |
| `src/mdviewer/settings.py` | `view/mode` 키 + `view_mode()`/`set_view_mode()` | **ui-dev**(또는 core-engine-dev — settings 는 UI 계층) |
| `src/mdviewer/renderer.py` | **변경 없음** | — |
| `src/mdviewer/file_watcher.py` | **변경 없음** | — |
| `requirements.txt` | **변경 없음**(신규 의존성 0) | — |
| `mdviewer.spec` | **변경 없음**(검증만) | packager |
| `tests/` | 모드 전환 상태(가시성), 디바운스 후 `_doc_text` 동기화, self-write 억제(저장이 reload 안 유발), dirty 중 외부변경 시 자동 reload 안 함, flush 후 저장 내용 최신 | QA |

---

## 11. 빌드 순서 & 통합 지점

```
Step 1  architect    ──▶ 본 설계(07) 확정 → ui-dev 에 계약 통지
Step 2  ui-dev       ──▶ main_window.py: 편집기 위젯 + 뷰모드 + 라이브 프리뷰 + 충돌 정책
                          settings.py: view/mode 키
                        (core 변경 없음 → core-engine-dev 대기 불필요)
Step 3  QA           ──▶ 모드 전환/라이브 갱신/저장 flush/충돌 정책 스모크 + 단위
Step 4  packager     ──▶ 회귀 스모크(신규 의존성 없음 → spec 변경 없음 확인만)
```

**통합 지점(경계면 버그 단골 — 이 4개를 QA 가 집중 검증):**

- **A. 단일 진실원 일관성:** 어떤 모드에서든 저장 시 디스크 내용 == 편집기 내용.
  → `save()`/`_write_to()` 진입 시 `_flush_pending_edit()` 선행이 핵심(§3.3).
- **B. 신호 억제:** open/paste/load/모드전환이 편집기를 채울 때 `textChanged` 가 dirty 를
  오염시키지 않는다(`_suppress_editor_signal` + `blockSignals`, §4).
- **C. self-write 억제:** 저장이 자기 자신을 reload 하지 않는다(시간 창 + 내용 비교, §5.1).
- **D. 편집 중 외부변경:** dirty 일 때 자동 reload 금지, 배너만(§5.3). not-dirty 면 기존처럼
  자동 reload(순수 뷰어 동작 보존).

---

## 12. 확장 후보 (범위 외 — 표기만)

- 마크다운 신택스 하이라이트(`QSyntaxHighlighter`), 라인 넘버 거터.
- 프리뷰 증분 DOM 패치(전체 `setHtml` 대신 JS 로 변경분만 — 편집 중 깜빡임 제거).
- 편집기↔프리뷰 양방향 스크롤 동기화(소스 라인 ↔ 렌더 위치 매핑).
- split 비율 QSettings 영속, 편집기 폰트 크기/패밀리 설정.
- 에디터 텍스트 검색/치환(Ctrl+F), 자동 저장, 다중 탭.
- 표준 뷰어 범위 밖이므로 후보로만 둔다.
</content>
</invoke>
