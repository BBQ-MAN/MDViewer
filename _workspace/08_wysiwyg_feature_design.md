# MDViewer WYSIWYG(라이브 편집) 모드 설계 (Phase 8)

> 작성: architect · 2026-06-25
> 범위 확장: 라이브 프리뷰 화면 자체를 **편집 surface** 로 만들어, 사용자가
> 프리뷰에서 직접 굵게/제목/목록 등을 적용하며 워드프로세서식으로 편집하고
> 그 결과가 마크다운 소스(`_doc_text`)로 동기화되어 저장되는 **WYSIWYG 모드**.
> 기준 문서: `_workspace/01_architect_blueprint.md`(렌더 API 계약),
> `_workspace/06_clipboard_feature_design.md`(문서 모델 `_doc_text/_path/_dirty`),
> `_workspace/07_editor_feature_design.md`(뷰모드 상태기계·디바운스·편집↔감시 충돌),
> 현행 `main_window.py` / `renderer.py`(`render`/`html_to_markdown`) / `theme.py`
> (`wrap_document`) / `settings.py`.

이 문서는 **계약(contract)**이다. **ui-dev** 가 이 계약대로 `main_window.py` 와
`settings.py` 를 구현한다. **core(renderer/file_watcher) 변경은 불필요**하다(§7).
Phase 6~7 이 이미 (a) `_doc_text` 단일 진실원, (b) `render`/`html_to_markdown`
양방향 변환, (c) 뷰모드 상태기계, (d) 편집↔감시 충돌 정책, (e) self-write 억제를
만들어 두었으므로, 이번 작업은 그 위에 **4번째 뷰 모드(WYSIWYG)와 그 모드만의
편집 캡처·포맷 툴바·역렌더 비활성 규율**을 얹는 것이다.

---

## 0. 핵심 설계 결정 요약 (못박음)

| 항목 | 결정 |
|------|------|
| 뷰 모드 추가 | **4번째 모드 `MODE_WYSIWYG="wysiwyg"`**, 단축키 **Ctrl+4**. 기존 Editor/Preview/Split 과 동일한 `QActionGroup` 라디오 |
| 편집 surface | 기존 `self.view`(QWebEngineView) **재사용**. 진입 시 `render(_doc_text)` 본문을 `wrap_document` 로 감싸 `setHtml` 후 편집 컨테이너에 `contentEditable=true` 부여 |
| ★ 편집 캡처 | **QWebChannel 미사용.** 포맷 = `document.execCommand`(Chromium 동작), 편집 내용 캡처 = **`innerHTML` 폴링**(`runJavaScript` 콜백, ~400ms 디바운스, WYSIWYG 동안만). 사유: qwebchannel.js 번들 리스크 회피 — html2text 채택과 동일 PyInstaller-안전 철학(§6) |
| 포맷 명령 | 굵게/기울임/취소선 · H1/H2/H3/본문 · 불릿/번호 목록 · 인용 · 인라인코드 · 링크삽입 · 서식지우기. 각각 `page().runJavaScript(execCommand 또는 동등 JS)`. **WYSIWYG 모드에서만 활성/표시**(§3) |
| ★ md 동기화 | WYSIWYG 편집 → (폴링) 편집 컨테이너 `innerHTML` 취득 → `html_to_markdown(html)` → `_doc_text` 갱신 → `_set_dirty(True)`. 저장은 기존 `_doc_text` 경로 사용(이미 구현) |
| ★ 루프/커서 안전 (1순위) | **WYSIWYG 모드에선 `_doc_text`→webview 역렌더 절대 금지**(커서/선택 파괴). source-editor 의 `_render_doc` 와 달리 WYSIWYG 진입 중에는 `_render_doc`/`_commit_editor_to_preview`/외부변경 자동reload 가 webview 를 다시 그리지 않는다. 진입 시 1회 프로그램적 `setHtml`(베이스라인) 후, 변화는 폴링으로 `_doc_text` 만 갱신 |
| 진입/이탈 | 진입: `render(_doc_text)`→editable HTML 로 `setHtml`, 폴링 시작. 이탈: **마지막 폴링 1회(flush)로 최신 편집 반영** 후 폴링 중지, 일반 모드 복귀 |
| 기존 정합 | dirty/타이틀/`_maybe_discard`/외부변경(편집중 자동reload 금지)/self-write 억제와 모순 없게. WYSIWYG 진입도 편집 행위 → dirty 규칙 일관 |
| 라운드트립 한계 | `html2text` 변환은 비가역 손실 가능(목록 마커·줄바꿈·front-matter 등). v1 정책: WYSIWYG↔소스 반복 전환 시 마크다운이 정규화될 수 있음을 문서화(§8) |
| core 변경 | **불필요**(`render`/`html_to_markdown` 존재). §7 참조 |
| 영향 파일 | UI: `main_window.py` · meta: `settings.py`(view_mode 유효값에 `wysiwyg` 추가) |
| 패키징 | **JS 자산 0**(폴링+execCommand 는 인라인 JS 문자열만 사용). qwebchannel.js 미번들 → spec 변경 없음(§6) |

---

## 1. 뷰 모드 통합 — 4번째 모드 추가

### 1.1 모드 상수 (main_window.py 상단 — Phase 7 블록 확장)

```python
MODE_EDITOR  = "editor"
MODE_PREVIEW = "preview"
MODE_SPLIT   = "split"
MODE_WYSIWYG = "wysiwyg"        # ← 신규: 프리뷰가 편집 surface
VALID_MODES  = (MODE_EDITOR, MODE_PREVIEW, MODE_SPLIT, MODE_WYSIWYG)
```

> ⚠️ 이 리터럴은 `settings._VALID_VIEW_MODES` 와 **반드시 동일**해야 한다(§5).

`__init__` 상태 추가:

```python
# WYSIWYG 폴링/베이스라인 상태(Phase 8)
self._wysiwyg_active: bool = False          # 현재 WYSIWYG 진입 중인가(역렌더 게이트)
self._wysiwyg_poll = QTimer(self)           # innerHTML 폴링 타이머
self._wysiwyg_poll.setInterval(_WYSIWYG_POLL_MS)   # 400ms (§4.1)
self._wysiwyg_poll.timeout.connect(self._wysiwyg_poll_tick)
self._wysiwyg_last_html: str | None = None  # 마지막으로 본 편집 컨테이너 innerHTML(변화 감지용)
```

상수:

```python
_WYSIWYG_POLL_MS = 400         # 편집 캡처 폴링 주기(디바운스 효과 — 변화 없으면 비용 0)
_WYSIWYG_ROOT_ID = "md-editable"   # contentEditable 컨테이너 element id(아래 JS 와 동일)
```

### 1.2 가시성 표 (Phase 7 §2.2 확장)

| 모드 | `editor` | `view`(프리뷰/편집surface) | `toc_list` | 포맷 툴바 |
|------|:---:|:---:|:---:|:---:|
| `MODE_EDITOR` | show | hide | hide(강제) | hide |
| `MODE_PREVIEW` | hide | **show** | 사용자 토글값 | hide |
| `MODE_SPLIT` | show | **show** | 사용자 토글값 | hide |
| `MODE_WYSIWYG` | **hide** | **show(편집 surface)** | 사용자 토글값 | **show** |

- WYSIWYG 는 `view` 를 **편집 가능 상태**로 보여준다. `editor`(소스 QPlainTextEdit)는
  숨긴다(소스와 WYSIWYG 동시 편집 = 두 입력원 충돌이므로 v1 에선 배타).
- TOC 는 `view` 가 보이므로 허용(사용자 토글값 게이팅 — Phase 7 규칙 그대로).
  단 WYSIWYG 편집으로 헤딩이 바뀌어도 **TOC 는 자동 갱신하지 않는다**(역렌더 금지
  정책상 `_set_document`/`_populate_toc` 를 부르지 않음 — 커서 보존 우선). 이탈 시
  일반 모드로 돌아오면 다음 정상 렌더에서 TOC 가 최신화된다. v1 수용(§8).

### 1.3 `_apply_view_mode` 확장 (★ 진입/이탈 훅이 핵심)

Phase 7 의 `_apply_view_mode` 에 **모드 전이(transition) 훅**을 추가한다. 핵심은
"**이전 모드가 WYSIWYG 였으면 떠나기 전에 flush+정리**, **새 모드가 WYSIWYG 면
진입 셋업**"이다. 가시성/포커스/액션체크/persist 는 기존 로직 재사용.

```python
def _apply_view_mode(self, mode: str, *, persist: bool = True) -> None:
    if mode not in VALID_MODES:
        mode = MODE_PREVIEW

    prev_mode = self._view_mode

    # ── (A) WYSIWYG 이탈: 다른 모드로 가기 전 반드시 flush + 정리. ──
    if prev_mode == MODE_WYSIWYG and mode != MODE_WYSIWYG:
        self._exit_wysiwyg()              # §2.3 — 마지막 폴링 flush → _doc_text 확정

    self._view_mode = mode

    show_editor  = mode in (MODE_EDITOR, MODE_SPLIT)
    show_preview = mode in (MODE_PREVIEW, MODE_SPLIT, MODE_WYSIWYG)   # ← WYSIWYG 추가
    show_format_toolbar = mode == MODE_WYSIWYG

    # EDITOR/SPLIT 진입 시 편집기를 _doc_text 와 동기화(기존).
    if show_editor:
        self._sync_editor_from_doc()

    self.editor.setVisible(show_editor)
    self.view.setVisible(show_preview)

    # 포맷 툴바: WYSIWYG 에서만 표시(§3.3).
    self.format_toolbar.setVisible(show_format_toolbar)

    # TOC: 프리뷰류가 보일 때만 의미. (WYSIWYG 도 view 표시 → 허용)
    toc_allowed = show_preview
    self.toc_list.setVisible(toc_allowed and self.settings.toc_visible())
    self.act_toggle_toc.setEnabled(toc_allowed)

    # 상호배타 액션 체크(4개).
    self.act_mode_editor.setChecked(mode == MODE_EDITOR)
    self.act_mode_preview.setChecked(mode == MODE_PREVIEW)
    self.act_mode_split.setChecked(mode == MODE_SPLIT)
    self.act_mode_wysiwyg.setChecked(mode == MODE_WYSIWYG)

    # ── (B) WYSIWYG 진입: editable setHtml + 폴링 시작(이전이 WYSIWYG 가 아닐 때만). ──
    if mode == MODE_WYSIWYG and prev_mode != MODE_WYSIWYG:
        self._enter_wysiwyg()            # §2.2 — render(_doc_text)→editable, 폴링 on

    # 포커스: WYSIWYG/프리뷰 → view, 편집기류 → editor.
    if show_editor:
        self.editor.setFocus()
    else:
        self.view.setFocus()

    if persist:
        self.settings.set_view_mode(mode)

    label = {MODE_EDITOR:"편집기", MODE_PREVIEW:"미리보기",
             MODE_SPLIT:"분할", MODE_WYSIWYG:"라이브 편집"}[mode]
    self.statusBar().showMessage(f"보기: {label}", 1500)
```

> ⚠️ **순서 주의:** (A) WYSIWYG 이탈 flush 는 **`self._view_mode` 갱신 전에** 수행해야
> `_exit_wysiwyg` 의 가드(`self._wysiwyg_active`)와 정합한다. (B) 진입은 가시성/`setVisible`
> 이후에 호출해 `view` 가 보이는 상태에서 `setHtml` 하게 한다(숨김 상태 setHtml 도 동작
> 하지만 진입 직후 포커스/커서 위치를 위해 표시 후 셋업 권장).

전용 슬롯:

```python
def set_mode_editor(self)  -> None: self._apply_view_mode(MODE_EDITOR)
def set_mode_preview(self) -> None: self._apply_view_mode(MODE_PREVIEW)
def set_mode_split(self)   -> None: self._apply_view_mode(MODE_SPLIT)
def set_mode_wysiwyg(self) -> None: self._apply_view_mode(MODE_WYSIWYG)   # ← 신규
```

### 1.4 모드 전이 매트릭스 (★ 통합 버그 단골 — 이 표대로)

| from \ to | editor/preview/split | wysiwyg |
|-----------|----------------------|---------|
| **editor/preview/split** | 기존 동작(가시성만) | `_enter_wysiwyg()`: `_flush_pending_edit()`(소스 편집기 잔여 디바운스 먼저) → `render(_doc_text)`→editable `setHtml` → 폴링 on |
| **wysiwyg** | `_exit_wysiwyg()`: 폴링 stop → **마지막 폴링 flush 1회** → `_doc_text` 확정 → `_set_document`/`_render_doc` 로 일반 렌더 복귀(editable 해제) | (자기 자신 — no-op, `_enter` 재호출 금지: §1.3 `prev != mode` 가드) |

- WYSIWYG **진입 전** 소스 편집기 디바운스가 대기 중일 수 있으므로 `_enter_wysiwyg`
  맨 앞에서 `_flush_pending_edit()`(Phase 7) 를 호출해 `_doc_text` 를 최신화한 뒤 렌더한다.
- WYSIWYG↔WYSIWYG(같은 모드 재클릭)은 `_apply_view_mode` 가 `prev==mode` 가드로
  `_enter/_exit` 둘 다 건너뛴다(폴링 유지, 깜빡임 없음).

---

## 2. WYSIWYG 진입/이탈 — editable setHtml & 베이스라인

### 2.1 편집 컨테이너 구조 (theme.wrap_document 와의 정합)

현행 `wrap_document` 는 본문을 `<article class="markdown-body">…</article>` 로 감싼다.
WYSIWYG 는 **이 article 을 contentEditable 컨테이너로 지정**한다. 별도 HTML 셸을
새로 만들 필요 없이 **기존 `wrap_document` 결과에 JS 로 editable 속성만 부여**한다
(테마 CSS 그대로 적용 — WYSIWYG 가 프리뷰와 시각적으로 동일).

- 편집 루트 element 식별: `article.markdown-body`. JS 에서 안정적으로 찾기 위해
  **id 를 부여**한다. 두 가지 중 택1(ui-dev 재량):
  - **권장:** 진입 JS 가 `document.querySelector('article.markdown-body')` 를 찾아
    `id="md-editable"` + `contentEditable="true"` 를 런타임 부여(`theme.py` 무변경).
  - 대안: `wrap_document` 가 article 에 `id="md-editable"` 를 항상 부여(무해, 일반
    모드 영향 없음). theme.py 1줄 변경이지만 JS 가 단순해짐. **둘 중 무엇을 택하든
    JS 의 `_WYSIWYG_ROOT_ID` 와 일치만 보장.**

> core/theme 경계: **theme.py 변경은 선택**이다. 권장안(런타임 querySelector)을 쓰면
> theme.py 무변경. 대안을 택하면 architect 통지 후 article id 1줄만 추가(가시 동작 불변).

### 2.2 진입 — `_enter_wysiwyg()`

```python
def _enter_wysiwyg(self) -> None:
    """WYSIWYG 진입: _doc_text 를 렌더해 editable surface 로 띄우고 폴링 시작.

    ★ 이 setHtml 은 '프로그램적' 렌더다. 이후 사용자 편집은 폴링으로만 캡처하며
       webview 를 다시 그리지 않는다(역렌더 금지 — 커서 보존).
    """
    self._flush_pending_edit()                 # 소스 편집기 잔여 디바운스 반영(Phase 7)
    # 진입은 '편집 가능 1회 렌더'. 일반 _render_doc 와 동일하게 본문을 만들되,
    # setHtml 직후 contentEditable 부여 + 베이스라인 캡처를 위해 별도 경로 사용.
    base_dir = self._path.parent if self._path else self._scratch_base_dir()
    result = render(self._doc_text, base_dir=base_dir)     # 계약상 예외 없음
    body = getattr(result, "html", "") or ""
    dark = self._theme == theme_mod.DARK
    html = theme_mod.wrap_document(body, dark)
    base = QUrl.fromLocalFile(str(base_dir) + "/")

    self._wysiwyg_active = True                 # ★ 역렌더 게이트 ON(이후 _render_doc no-op)
    self._wysiwyg_last_html = None             # 베이스라인 미설정 표시
    self._pending_scroll = None
    # loadFinished 후 editable 부여 + 베이스라인 캡처 + 폴링 시작.
    self._wysiwyg_pending_setup = True         # _on_load_finished 에서 1회 셋업 트리거
    self.view.setHtml(html, base)
    self._populate_toc(getattr(result, "toc", []) or [])   # 진입 시 1회 TOC 갱신
    self.statusBar().showMessage(
        "라이브 편집(WYSIWYG) — 툴바로 서식 적용, 변경은 자동 저장 대상", 4000
    )
```

`loadFinished` 훅에서 editable 부여 + 베이스라인 + 폴링 시작:

```python
def _on_load_finished(self, ok: bool) -> None:
    # (기존 스크롤 복원 로직 유지)
    if ok and self._pending_scroll is not None:
        x, y = self._pending_scroll
        self._pending_scroll = None
        try: self.view.page().runJavaScript(f"window.scrollTo({x}, {y});")
        except Exception: pass
    # WYSIWYG 진입 셋업(페이지 로드 완료 후에만 DOM 조작 가능).
    if getattr(self, "_wysiwyg_pending_setup", False) and ok:
        self._wysiwyg_pending_setup = False
        self._activate_editable_then_baseline()

def _activate_editable_then_baseline(self) -> None:
    """article 을 contentEditable 로 만들고 초기 innerHTML 을 베이스라인으로 캡처."""
    js_enable = (
        "(function(){"
        " var a = document.querySelector('article.markdown-body');"
        " if(!a) return '';"
        " a.id = %r;"
        " a.setAttribute('contenteditable','true');"
        " a.focus();"
        " return a.innerHTML;"
        "})()" % _WYSIWYG_ROOT_ID
    )
    def _cb(initial_html) -> None:
        # 베이스라인 = 진입 시점 innerHTML. 이후 폴링이 이 값과 다르면 사용자 편집으로 간주.
        self._wysiwyg_last_html = initial_html if isinstance(initial_html, str) else ""
        if self._wysiwyg_active:
            self._wysiwyg_poll.start()         # 폴링 시작(이탈/모드전환 시 stop)
    try:
        self.view.page().runJavaScript(js_enable, 0, _cb)
    except Exception:
        # editable 부여 실패 → WYSIWYG 무력화(일반 프리뷰로 강등). 크래시 금지.
        self._wysiwyg_active = False
```

### 2.3 이탈 — `_exit_wysiwyg()`

```python
def _exit_wysiwyg(self) -> None:
    """WYSIWYG 이탈: 폴링 중지 + 마지막 1회 flush(최신 편집 반영) + editable 해제.

    ★ flush 는 동기적으로 끝나지 않는다(runJavaScript 콜백 비동기). 따라서:
       - 폴링을 멈추고 즉시 1회 innerHTML 을 요청한다.
       - 콜백에서 html_to_markdown → _doc_text 갱신(필요 시 dirty).
       - 모드 전환의 후속(다음 모드의 가시성/렌더)은 _apply_view_mode 가 이미
         _doc_text 를 단일 진실원으로 쓰므로, flush 콜백이 늦게 와도
         '이탈 후 일반 렌더'가 최신 _doc_text 를 반영하도록 순서를 맞춘다(아래 주의).
    """
    self._wysiwyg_poll.stop()
    self._wysiwyg_active = False                # 역렌더 게이트 OFF(이제 _render_doc 허용)
    self._capture_wysiwyg_once(final=True)      # 마지막 동기화(아래)
    # editable 해제(다음 일반 렌더가 setHtml 로 덮으므로 필수는 아니나 명시적 정리).
    try:
        self.view.page().runJavaScript(
            "(function(){var a=document.getElementById(%r);"
            "if(a) a.removeAttribute('contenteditable');})()" % _WYSIWYG_ROOT_ID
        )
    except Exception:
        pass
```

> ⚠️ **이탈 후 일반 모드의 첫 렌더는 flush 콜백이 `_doc_text` 를 갱신한 뒤여야 한다.**
> `runJavaScript` 콜백은 비동기다. 가장 안전한 패턴은 **이탈 시 webview 재렌더를
> 강제하지 않는 것**이다: WYSIWYG→Preview 로 가도 `view` 는 이미 같은 본문을 보여주고
> 있으므로(편집 결과가 화면에 그대로 있음), `_apply_view_mode` 는 가시성만 바꾸고
> **재렌더하지 않는다**(Phase 7 §2.5 규칙 — 모드 전환은 재렌더 안 함). flush 콜백은
> 백그라운드에서 `_doc_text` 만 최신화한다. 그 다음 사용자가 저장/Ctrl+R/테마전환 등을
> 하면 그때 최신 `_doc_text` 로 정상 렌더된다. → **즉, 이탈은 화면을 건드리지 않고
> `_doc_text` 만 확정한다.** (WYSIWYG→Editor/Split 로 갈 때만 `_sync_editor_from_doc`
> 가 편집기를 채우는데, 이는 flush 콜백 이후 `_doc_text` 기준으로 동작 — §2.4 경합 처리.)

### 2.4 비동기 flush 경합 처리 (★ 미묘한 버그 — 명시)

문제: `_exit_wysiwyg()` 의 마지막 innerHTML 캡처는 비동기(runJavaScript 콜백)인데,
`_apply_view_mode` 는 그 직후 동기적으로 다음 모드의 `_sync_editor_from_doc()`(WYSIWYG→
Editor/Split 시)를 호출한다. 콜백이 아직 안 와서 `_doc_text` 가 한 박자 오래될 수 있다.

**해결(권장, 단순·견고): 이탈 시 마지막 캡처를 "동기 우선 + 콜백 보정" 2단으로.**

- `_exit_wysiwyg` 는 폴링이 직전 tick 에서 이미 `_doc_text` 를 최신화해 둔 상태를
  활용한다(400ms 폴링이라 대부분 최신). 추가로 final 캡처를 쏘되, **WYSIWYG→Editor/Split
  전환 시에는 final 콜백 안에서 `_sync_editor_from_doc()` 를 한 번 더 호출**해 편집기가
  확실히 최신 `_doc_text` 를 받게 한다:

```python
def _capture_wysiwyg_once(self, *, final: bool) -> None:
    """편집 컨테이너 innerHTML 1회 취득 → html_to_markdown → _doc_text 갱신.

    final=True(이탈)면 콜백에서 편집기 동기화까지(다음 모드가 편집기류일 때) 보정한다.
    """
    js_get = ("(function(){var a=document.getElementById(%r);"
              "return a ? a.innerHTML : null;})()" % _WYSIWYG_ROOT_ID)
    def _cb(html) -> None:
        if not isinstance(html, str):
            return                                  # 페이지 미준비/실패 → 무시
        self._ingest_wysiwyg_html(html)             # §4.2 — 변화 시에만 _doc_text/dirty
        if final and self._view_mode in (MODE_EDITOR, MODE_SPLIT):
            self._sync_editor_from_doc()            # 편집기에 최신 _doc_text 반영(보정)
    try:
        self.view.page().runJavaScript(js_get, 0, _cb)
    except Exception:
        pass
```

> 이 2단 처리로 "WYSIWYG 에서 마지막에 친 글자가 소스 편집기/저장에 누락" 버그를 막는다.
> 저장 경로(§4.4)는 추가로 자체 flush 가드를 둔다.

---

## 3. 포맷 툴바 — execCommand 매핑

### 3.1 편집 캡처/포맷 방식 결정 (★ QWebChannel vs 폴링+execCommand)

**결정: QWebChannel 미사용. 포맷=`document.execCommand`, 캡처=`innerHTML` 폴링.**

| 후보 | 번들 자산 | frozen 안전성 | 판정 |
|------|----------|--------------|------|
| **execCommand + innerHTML 폴링** | **0** (인라인 JS 문자열만) | hidden import/datas 추가 없음 → spec 무변경 | **채택** |
| QWebChannel(`qwebchannel.js` + QWebChannel py) | `qwebchannel.js` 를 페이지에 주입 필요 | js 자산을 datas 로 번들 + 경로 해석(`paths.resource_path`) 책임 발생. PySide6 가 제공하는 qwebchannel.js 를 frozen 에서 찾아 번들해야 함 → 검증 표면 ↑ | 미채택(아래 조건부) |

**사유:** MDViewer 의 일관된 PyInstaller-안전 철학(html2text 무의존 채택과 동일)을
따른다. execCommand 는 Chromium(QtWebEngine, PySide6 6.11.1) 에서 contentEditable
포맷 명령으로 여전히 동작한다(표준상 deprecated 이나 Chromium 구현 잔존). 폴링은
타이머 1개로 끝나고 자산이 0 이라 packager 작업이 없다.

> **만약 ui-dev 가 QWebChannel 이 꼭 낫다고 판단하면**(예: 폴링 비용/지연이 체감되거나,
> execCommand 가 특정 명령에서 불안정), architect 에게 통지하고 **사유 + frozen 번들
> 검증 책임**을 명시한다. 그 경우 (a) qwebchannel.js 를 `resources/js/` 에 두고
> `paths.resource_path("js","qwebchannel.js")` 로 로드, (b) `mdviewer.spec` 의 `datas`
> 에 추가, (c) frozen exe 에서 채널 핸드셰이크 동작을 packager 가 회귀 검증 — 이 3개를
> 계약에 추가한다. **v1 기본은 execCommand+폴링.**

### 3.2 포맷 명령 집합과 JS 트리거

각 버튼 → `self.view.page().runJavaScript(<JS>)`. JS 는 편집 루트에 포커스가 있다는
전제(WYSIWYG 모드에서만 툴바 활성)에서 `document.execCommand` 또는 동등 DOM 조작.

| 명령 | 버튼/액션 | JS (요지) | 비고 |
|------|----------|-----------|------|
| 굵게 | `act_fmt_bold` | `document.execCommand('bold')` | 토글 |
| 기울임 | `act_fmt_italic` | `document.execCommand('italic')` | 토글 |
| 취소선 | `act_fmt_strike` | `document.execCommand('strikeThrough')` | 토글 |
| 제목 H1 | `act_fmt_h1` | `document.execCommand('formatBlock', false, 'H1')` | 블록 |
| 제목 H2 | `act_fmt_h2` | `formatBlock … 'H2'` | 블록 |
| 제목 H3 | `act_fmt_h3` | `formatBlock … 'H3'` | 블록 |
| 본문(P) | `act_fmt_p` | `formatBlock … 'P'` | 헤딩/인용 해제 |
| 불릿 목록 | `act_fmt_ul` | `document.execCommand('insertUnorderedList')` | 토글 |
| 번호 목록 | `act_fmt_ol` | `document.execCommand('insertOrderedList')` | 토글 |
| 인용 | `act_fmt_quote` | `formatBlock … 'BLOCKQUOTE'` | 블록 |
| 인라인코드 | `act_fmt_code` | 선택 텍스트를 `<code>` 로 감싸는 DOM 조작(§3.4) | execCommand 미지원 → 커스텀 JS |
| 링크 삽입 | `act_fmt_link` | URL 입력 후 `document.execCommand('createLink', false, url)` | URL 은 Qt 입력 다이얼로그(§3.5) |
| 서식 지우기 | `act_fmt_clear` | `document.execCommand('removeFormat')` (+ 블록을 P 로) | 인라인 서식 제거 |

공통 트리거 헬퍼:

```python
def _exec_format(self, command: str, value: str | None = None) -> None:
    """WYSIWYG 편집 루트에 execCommand 적용 후, 즉시 1회 캡처(폴링 보조).

    WYSIWYG 모드가 아니면 no-op(툴바는 모드에서만 활성이라 방어적).
    """
    if not self._wysiwyg_active:
        return
    if value is None:
        js = "document.execCommand(%r, false, null);" % command
    else:
        js = "document.execCommand(%r, false, %r);" % (command, value)
    try:
        self.view.page().runJavaScript(js)
    except Exception:
        pass
    # 서식 적용 직후 한 번 더 캡처해 _doc_text/dirty 를 빠르게 반영(폴링 대기 없이).
    self._capture_wysiwyg_once(final=False)
```

> ⚠️ execCommand 후 즉시 `_capture_wysiwyg_once` 를 호출하지만, DOM 반영이 한 틱 늦을
> 수 있어 폴링이 백업한다(다음 tick 에서 잡힘). 즉시 캡처는 반응성 향상용 best-effort.

### 3.3 포맷 툴바 정의 (별도 QToolBar, WYSIWYG 에서만 표시)

```python
def _build_format_toolbar(self) -> None:
    tb = self.addToolBar("서식")
    tb.setObjectName("formatToolbar")
    tb.setMovable(False)
    self.format_toolbar = tb
    # 액션 생성(텍스트 라벨; 아이콘은 선택). 모두 WYSIWYG 전용.
    self.act_fmt_bold   = self._mk_fmt("굵게",   lambda: self._exec_format("bold"))
    self.act_fmt_italic = self._mk_fmt("기울임", lambda: self._exec_format("italic"))
    self.act_fmt_strike = self._mk_fmt("취소선", lambda: self._exec_format("strikeThrough"))
    self.act_fmt_h1 = self._mk_fmt("H1", lambda: self._exec_format("formatBlock","H1"))
    self.act_fmt_h2 = self._mk_fmt("H2", lambda: self._exec_format("formatBlock","H2"))
    self.act_fmt_h3 = self._mk_fmt("H3", lambda: self._exec_format("formatBlock","H3"))
    self.act_fmt_p  = self._mk_fmt("본문", lambda: self._exec_format("formatBlock","P"))
    self.act_fmt_ul = self._mk_fmt("• 목록", lambda: self._exec_format("insertUnorderedList"))
    self.act_fmt_ol = self._mk_fmt("1. 목록", lambda: self._exec_format("insertOrderedList"))
    self.act_fmt_quote = self._mk_fmt("인용", lambda: self._exec_format("formatBlock","BLOCKQUOTE"))
    self.act_fmt_code = self._mk_fmt("코드", self._fmt_inline_code)     # §3.4
    self.act_fmt_link = self._mk_fmt("링크", self._fmt_insert_link)     # §3.5
    self.act_fmt_clear = self._mk_fmt("서식 지우기", self._fmt_clear)   # §3.6
    for a in (...):  # 위 순서대로 addAction + 사이사이 addSeparator
        tb.addAction(a)
    tb.setVisible(False)   # 초기 숨김; _apply_view_mode 가 WYSIWYG 에서만 표시

def _mk_fmt(self, label: str, slot) -> QAction:
    a = QAction(label, self)
    a.triggered.connect(slot)
    return a
```

- **표시/숨김은 `_apply_view_mode` 가 `format_toolbar.setVisible(mode==WYSIWYG)` 로
  제어**(§1.3). 액션 자체의 enable 토글은 불필요(툴바가 숨으면 단축키도 안 보임).
- 포맷 액션에 단축키는 **부여하지 않는다**(기존 Ctrl+B 등과의 전역 충돌·모드 의존성
  복잡도 회피). v1 은 툴바 클릭 전용. (확장 후보 §9: WYSIWYG 활성 시에만 Ctrl+B/I 바인딩.)

### 3.4 인라인 코드(execCommand 미지원 — 커스텀 JS)

`<code>` 인라인은 표준 execCommand 명령이 없다. 선택 영역을 `<code>` 로 감싸는 JS:

```python
def _fmt_inline_code(self) -> None:
    if not self._wysiwyg_active:
        return
    js = (
        "(function(){"
        " var sel=window.getSelection();"
        " if(!sel || sel.rangeCount===0 || sel.isCollapsed) return;"
        " var r=sel.getRangeAt(0);"
        " var code=document.createElement('code');"
        " try{ code.appendChild(r.extractContents()); r.insertNode(code); }"
        " catch(e){}"
        "})()"
    )
    try: self.view.page().runJavaScript(js)
    except Exception: pass
    self._capture_wysiwyg_once(final=False)
```

> `html_to_markdown`(html2text)는 `<code>`→`` `code` `` 로 변환하므로 라운드트립 성립.
> 선택이 없으면 no-op(빈 코드 삽입 방지). 토글 해제(코드 풀기)는 v1 범위 외(§9).

### 3.5 링크 삽입 (URL 은 Qt 입력 다이얼로그)

```python
def _fmt_insert_link(self) -> None:
    if not self._wysiwyg_active:
        return
    from PySide6.QtWidgets import QInputDialog
    url, ok = QInputDialog.getText(self, "링크 삽입", "URL:")
    if not ok or not url.strip():
        return
    # createLink 는 현재 선택을 링크로. 선택이 없으면 URL 텍스트를 삽입 후 링크화.
    self._exec_format("createLink", url.strip())
```

- 선택이 비어 있으면 일부 Chromium 에서 no-op 일 수 있다 → v1 수용(사용자가 텍스트
  선택 후 링크 적용). 확장(§9): 선택 없을 때 URL 을 텍스트로 삽입 후 링크화.

### 3.6 서식 지우기

```python
def _fmt_clear(self) -> None:
    if not self._wysiwyg_active:
        return
    # 인라인 서식 제거 + 블록을 본문(P)으로 환원.
    self._exec_format("removeFormat")
    self._exec_format("formatBlock", "P")
```

---

## 4. md 동기화 — innerHTML 폴링 → html_to_markdown → _doc_text

### 4.1 폴링 루프 (디바운스 효과)

```python
def _wysiwyg_poll_tick(self) -> None:
    """폴링 주기마다 편집 컨테이너 innerHTML 을 비동기 취득해 변화 시 동기화."""
    if not self._wysiwyg_active:
        self._wysiwyg_poll.stop()
        return
    self._capture_wysiwyg_once(final=False)
```

- 400ms 고정 주기. `runJavaScript` 는 비동기·논블로킹이라 UI 멈춤 없음.
- **변화 없으면 비용 0**(§4.2 가 innerHTML 동일성으로 조기 반환). 큰 문서라도
  innerHTML 직렬화 1회/400ms 는 무시 가능. (체감 지연 우려 시 주기 하향은 §9 후보.)

### 4.2 변화 감지 + 동기화 (★ 핵심: 사용자 편집만 _doc_text 로)

```python
def _ingest_wysiwyg_html(self, html: str) -> None:
    """편집 컨테이너 innerHTML 을 받아 '변화가 있을 때만' _doc_text/dirty 갱신.

    ★ 베이스라인(_wysiwyg_last_html)과 비교해 프로그램적 setHtml(진입) 직후의
       무변화를 사용자 편집으로 오인하지 않는다(dirty 오염·무한 동기화 방지).
    """
    if self._wysiwyg_last_html is None:
        # 아직 베이스라인 미설정(진입 셋업 콜백 전) → 이번 값을 베이스라인으로만.
        self._wysiwyg_last_html = html
        return
    if html == self._wysiwyg_last_html:
        return                                  # 변화 없음 → no-op(루프/비용 차단)
    self._wysiwyg_last_html = html              # 새 베이스라인
    md = html_to_markdown(html)                 # core 호출(항상 str, 예외 비전파)
    # ★ WYSIWYG 는 webview 를 재렌더하지 않는다 → _doc_text 만 갱신(커서 보존).
    self._doc_text = md
    self._set_dirty(True)                       # 사용자 편집 = 미저장 표시
```

**핵심 규율(★★ 1순위):**
- `_ingest_wysiwyg_html` 은 **절대 `_render_doc`/`_set_document`/`setHtml` 을 호출하지
  않는다.** `_doc_text` 만 갱신한다. webview 는 사용자가 직접 편집한 그 DOM 을
  그대로 유지 → 커서/선택/스크롤 보존.
- 베이스라인 비교로 (a) 진입 직후 무편집 상태에서 dirty 오염 방지, (b) 폴링이
  같은 내용을 반복 변환하는 낭비 방지.

### 4.3 ★ 역렌더 게이트 — `_render_doc`/외부변경/테마전환과의 정합 (★★ 무한루프 방지)

WYSIWYG 활성 중 `_doc_text` 가 바뀌어도 webview 를 다시 그리면 커서가 파괴되고,
최악의 경우 `setHtml`→폴링이 새 innerHTML 을 또 잡아 `_doc_text` 를 갱신하는 진동이
생길 수 있다. **`_wysiwyg_active` 게이트로 모든 역렌더 경로를 차단**한다.

| 경로 | WYSIWYG 활성 시 동작 |
|------|----------------------|
| `_commit_editor_to_preview`(소스 편집기 디바운스) | WYSIWYG 에선 소스 편집기 숨김 → textChanged 안 옴 → 도달 안 함. 방어적으로 `if self._wysiwyg_active: return` 추가 권장 |
| `_render_doc(preserve_scroll=...)` | **게이트:** `if self._wysiwyg_active: return`(맨 앞). 라이브 편집 중엔 역렌더 금지 |
| 외부변경 자동 reload(`_on_external_change_settled`) | WYSIWYG = 편집 행위 → dirty=True 이므로 기존 정책상 **자동 reload 금지**(배너만). 추가로 `_wysiwyg_active` 면 무조건 배너만(편집 surface 덮어쓰기 방지) |
| 테마 전환(`toggle_theme`) | WYSIWYG 중 테마 전환은 **편집 내용 보존이 필요** → §4.5 특수 처리(flush→재진입) |
| Ctrl+R / open / paste | `_maybe_discard` 가드 후 진행. 이들은 **명시적 문서 교체**라 WYSIWYG 를 빠져나오는 것이 자연스러움 → §4.6 |

`_render_doc` 게이트 구현:

```python
def _render_doc(self, *, preserve_scroll: bool) -> None:
    if self._wysiwyg_active:
        return                  # ★ WYSIWYG 라이브 편집 중엔 역렌더 금지(커서 보존)
    base_dir = self._path.parent if self._path else self._scratch_base_dir()
    result = render(self._doc_text, base_dir=base_dir)
    if preserve_scroll:
        self._capture_scroll_then_render(result)
    else:
        self._set_document(result, restore_scroll=False)
```

> 단, **진입 시의 1회 setHtml 은 `_render_doc` 를 거치지 않는다**(§2.2 `_enter_wysiwyg`
> 가 직접 `setHtml`). 게이트는 "활성 중 외부 트리거에 의한 재렌더"만 막는다.

### 4.4 저장 — _flush 보강 (WYSIWYG 캡처는 비동기)

기존 `_write_to` 는 `_flush_pending_edit()`(소스 편집기 디바운스 flush) 후 `_doc_text`
를 쓴다. WYSIWYG 의 편집은 폴링으로 `_doc_text` 에 반영되지만 **마지막 타이핑 직후
저장하면 폴링 tick 전일 수 있다.** 폴링 캡처는 비동기라 저장 시점에 동기 보장이 어렵다.

**v1 정책(권장, 견고):**
- WYSIWYG 활성 중 저장(Ctrl+S) 시 **즉시 1회 동기 의도의 캡처를 쏘고, 캡처 콜백에서
  실제 write 를 수행**한다(비동기 저장). 또는 더 단순하게:
- **폴링 주기를 충분히 짧게(400ms) 유지 + 저장 직전 `_capture_wysiwyg_once(final=False)`
  를 한 번 더 쏜 뒤, 짧은 지연 없이 현재 `_doc_text` 로 저장**(대부분 폴링이 이미 최신).
  마지막 1글자 유실 가능성은 폴링 주기 내(<400ms)로 한정 — v1 수용 가능하나,
  **데이터 안전 우선이면 아래 비동기 저장 채택.**

권장 구현(비동기 저장 — 마지막 편집까지 확실히 반영):

```python
def save(self) -> bool:
    if self._wysiwyg_active:
        # WYSIWYG: 최신 innerHTML 을 캡처한 뒤(콜백) 저장. 동기 반환값은 best-effort.
        self._save_after_wysiwyg_capture()
        return True
    if self._path is None:
        return self.save_as()
    return self._write_to(self._path)

def _save_after_wysiwyg_capture(self) -> None:
    """WYSIWYG 최신 편집을 캡처해 _doc_text 확정 후 저장(비동기)."""
    js_get = ("(function(){var a=document.getElementById(%r);"
              "return a ? a.innerHTML : null;})()" % _WYSIWYG_ROOT_ID)
    def _cb(html) -> None:
        if isinstance(html, str):
            self._ingest_wysiwyg_html(html)        # _doc_text 최신화(변화 시 dirty)
        # 캡처 반영 후 저장(scratch면 save_as 다이얼로그).
        if self._path is None:
            self.save_as()
        else:
            self._write_to(self._path)
    try:
        self.view.page().runJavaScript(js_get, 0, _cb)
    except Exception:
        # 캡처 실패 시에도 현재 _doc_text 로 저장(폴링이 잡아둔 최신).
        (self.save_as() if self._path is None else self._write_to(self._path))
```

> `_write_to` 자체는 변경 없음(이미 `_flush_pending_edit` + self-write 억제 보유).
> WYSIWYG 캡처는 그 앞단에서 `_doc_text` 를 확정하는 역할만 한다.
> `_maybe_discard` 의 "저장" 분기가 `save()` 를 부르므로 WYSIWYG 에서도 일관 동작.

### 4.5 WYSIWYG 중 테마 전환 (편집 보존)

테마 전환은 CSS 교체를 위해 `setHtml` 재호출이 필요하지만, WYSIWYG 중엔 그게
편집 DOM 을 덮는다. **flush→재진입 패턴**으로 편집을 보존한다:

```python
def toggle_theme(self) -> None:
    self._theme = theme_mod.toggle_theme(self._theme)
    self.settings.set_theme(self._theme)
    if self._wysiwyg_active:
        # 최신 편집을 _doc_text 로 확정(캡처)한 뒤, 새 테마로 WYSIWYG 재진입.
        self._capture_wysiwyg_once(final=False)
        self._wysiwyg_active = False          # 게이트 잠시 내려 재진입 허용
        self._wysiwyg_poll.stop()
        self._enter_wysiwyg()                 # render(_doc_text)→editable(새 테마)
        ... (상태바)
        return
    # (기존 일반 경로)
    if self._path is not None or self._doc_text:
        self._render_doc(preserve_scroll=True)
    else:
        self._show_welcome()
```

> 테마 전환 시엔 **커서 위치가 문서 처음으로 리셋**될 수 있다(재진입 = 새 setHtml).
> v1 수용(테마 전환은 드문 조작). 캡처가 비동기라 `_capture` 직후 `_enter_wysiwyg`
> 가 옛 `_doc_text` 를 쓸 수 있는 미세 경합은, 캡처 콜백 안에서 `_enter_wysiwyg` 를
> 호출하도록 재배치하면 제거된다(ui-dev 재량 — 권장).

### 4.6 open/paste/Ctrl+R 진입 시 WYSIWYG 탈출

`open_path`/`paste_clipboard`/`reload_current` 는 **문서를 교체**한다. WYSIWYG 활성 중
이들이 호출되면:
- 기존 `_maybe_discard()` 가드가 먼저 동작(dirty 면 저장/버림/취소). WYSIWYG 편집도
  dirty 이므로 데이터 유실 경고가 일관되게 뜬다.
- 진행이 확정되면 **WYSIWYG 를 빠져나와야** 새 문서가 editable 이 아닌 일반 렌더로 뜬다.
  → 이 헬퍼들 진입부에 가드:

```python
def _leave_wysiwyg_for_document_change(self) -> None:
    """문서 교체(open/paste/reload) 전 WYSIWYG 를 정리하고 Preview 로 강등.

    데이터(_doc_text)는 _exit_wysiwyg 의 flush 로 보존된다. 새 문서는 Preview 에서
    뜬다(WYSIWYG 유지하려면 사용자가 다시 Ctrl+4).
    """
    if self._wysiwyg_active:
        self._apply_view_mode(MODE_PREVIEW)   # _exit_wysiwyg() 자동 호출(§1.3 A)
```

- `open_path`: `_maybe_discard()` 통과 후, `_clear_external_change_banner()` 전에
  `self._leave_wysiwyg_for_document_change()` 호출. 그 다음 기존 흐름(`_load_from_disk`
  → `_attach_path`) 진행. `_load_from_disk` 는 게이트가 내려간 상태라 정상 렌더.
- `paste_clipboard`: `_maybe_discard()` 통과 후 `_leave_wysiwyg_for_document_change()`
  → `_set_scratch(text)`(기존). scratch 렌더는 일반 모드.
- `reload_current`: `_maybe_discard()` 통과 후 `_leave_wysiwyg_for_document_change()`
  → `_load_from_disk`(기존).

> 설계 단순화: **문서 교체는 항상 WYSIWYG 를 빠져나온다.** "새 문서를 곧장 WYSIWYG 로"
> 는 v1 범위 외(§9). 이로써 진입은 오직 Ctrl+4(또는 복원)로만 일어나 상태가 단순해진다.

---

## 5. QSettings — view_mode 유효값 확장

`settings.py` 의 뷰 모드 유효값에 `wysiwyg` 추가(키 추가 없음, 기존 `view/mode` 재사용):

```python
# settings.py
_VALID_VIEW_MODES = ("editor", "preview", "split", "wysiwyg")   # ← wysiwyg 추가
_DEFAULT_VIEW_MODE = "preview"
# view_mode()/set_view_mode() 로직은 그대로(유효값 집합만 확장).
```

> ⚠️ `main_window.VALID_MODES` 의 리터럴과 **반드시 동일**. settings 는 문자열만 알고
> 검증은 양쪽이 같은 리터럴을 쓰는 것으로 보장(Phase 7 규칙 유지, 순환 import 없음).

**복원 시점(Phase 7 §7.2 그대로):** `__init__` 끝의
`self._apply_view_mode(self.settings.view_mode(), persist=False)` 가 자동으로 WYSIWYG
복원도 처리한다. 단 **초기 `_doc_text==""`** 라 WYSIWYG 복원 시 빈 문서를 editable 로
띄운다 — 안전(빈 article 편집 가능, 첫 타이핑부터 동기화). 환영 화면 대신 빈 편집
surface 가 뜨는 것이 어색하면, **복원이 WYSIWYG 이고 `_doc_text==""` 면 Preview 로
강등**하는 1줄 가드를 `__init__` 복원부에 둘 수 있다(ui-dev 재량, 권장):

```python
restored = self.settings.view_mode()
if restored == MODE_WYSIWYG and not self._doc_text:
    restored = MODE_PREVIEW          # 빈 문서를 WYSIWYG 로 복원하지 않음(어색함 방지)
self._apply_view_mode(restored, persist=False)
```

---

## 6. 패키징 — JS 자산 전략 & frozen 안전성

| 항목 | 결정 |
|------|------|
| 번들 JS 자산 | **0개.** execCommand/폴링/인라인 코드/링크 JS 는 모두 **파이썬 문자열 리터럴**로 `runJavaScript` 에 전달 → 외부 .js 파일 없음 |
| qwebchannel.js | **미번들**(QWebChannel 미사용). v1 spec 변경 없음 |
| `mdviewer.spec` | **변경 없음.** datas/hiddenimports 추가 0 (packager 는 회귀 스모크만) |
| 신규 런타임 의존성 | **0.** PySide6/QtWebEngine 기존 번들로 충분(`page().runJavaScript`, `QInputDialog` 모두 기존) |
| `requirements.txt` | **변경 없음** |

- **frozen 검증 책임(packager):** WYSIWYG 진입 시 `setHtml`→editable→execCommand 가
  frozen exe 의 QtWebEngine 에서도 동작하는지 스모크 1회. (인라인 JS 라 경로 의존이
  없어 정상 예상. QtWebEngine 리소스는 기존 spec 이 이미 번들 — Phase 1~5 검증됨.)
- **QWebChannel 채택 시(조건부, §3.1)에만** packager 작업 발생: `qwebchannel.js` 를
  `resources/js/` 에 두고 `paths.resource_path` 로 로드 + spec `datas` 추가 + frozen
  핸드셰이크 검증. **v1 기본 경로에선 packager 무작업.**

---

## 7. core(renderer/file_watcher) 변경 여부

**불필요.** 근거:
- WYSIWYG 진입 렌더 = 기존 `render(self._doc_text, base_dir=...)` 그대로.
- 편집 캡처 동기화 = 기존 `html_to_markdown(html)`(Phase 6 구현, 항상 str, 예외 비전파).
- 저장 = 기존 `write_markdown(path, self._doc_text)`(변경 없음).
- editable 부여/execCommand/폴링은 **전적으로 UI(JS via runJavaScript) 책임** — core 는
  "이미 추출된 HTML 문자열"만 받는다(Phase 6 경계 유지).
- theme.py: **선택적** 1줄(article id) — 권장안(런타임 querySelector)이면 무변경(§2.1).

> ui-dev 가 구현 중 core 변경이 꼭 필요하다 판단하면(예: html2text 옵션을 WYSIWYG
> 라운드트립용으로 조정 — 단락 줄바꿈/목록 마커 충실도) architect 에게 통지해 계약을
> 갱신한다. 현 설계 목표는 **core 변경 0**.

---

## 8. 라운드트립 한계 & v1 데이터 안전 정책 (★ 문서화 필수)

`innerHTML → html_to_markdown(html2text) → _doc_text` 변환은 **비가역적 손실/정규화**가
있을 수 있다. v1 정책으로 명시한다:

| 손실/정규화 항목 | 현상 | v1 정책 |
|------|------|---------|
| 목록 마커 | `-`/`*`/`+` 가 html2text 기본 마커로 통일됨 | 수용(정규화) |
| 줄바꿈/단락 | 하드 브레이크·연속 개행이 재배치될 수 있음 | `body_width=0` 로 wrap 은 막음. 단락 구조는 변할 수 있음 |
| front-matter | WYSIWYG 진입 시 `render` 가 front-matter 를 본문에 안 그림 → 캡처 시 **유실 위험** | ★ §8.1 가드 필수 |
| 원시 마크다운 표현 | 동일 결과를 내는 여러 소스 표기가 하나로 정규화 | 수용 |
| 코드블록 펜스 언어 | 일부 언어 힌트가 보존 안 될 수 있음 | 수용(코드 내용은 보존) |

**v1 핵심 메시지(사용자/문서):** "WYSIWYG 로 편집하면 마크다운 소스가 **정규화**될 수
있습니다. WYSIWYG↔소스 모드를 반복 전환하면 표기가 통일됩니다(내용은 보존)."
→ About 다이얼로그/상태바 안내에 1줄 반영(ui-dev).

### 8.1 ★ front-matter 유실 가드 (데이터 안전 — 필수)

`render` 는 front-matter(`---\n…\n---`)를 본문 HTML 로 그리지 않으므로, WYSIWYG 진입→
캡처 라운드트립에서 front-matter 가 **사라진다.** 데이터 유실이므로 가드한다.

**v1 정책(택1, ui-dev 재량 — 권장 A):**
- **A(권장, 안전): front-matter 가 있는 문서는 WYSIWYG 진입을 막거나 경고.**
  진입 시 `_doc_text` 가 `---` 로 시작하면 QMessageBox 로 "이 문서는 front-matter 를
  포함해 WYSIWYG 편집 시 머리말이 유실될 수 있습니다. 계속하시겠습니까?" 확인. 취소
  시 Preview 유지. (정규식: `^\s*---\r?\n` 로 시작 판단.)
- B(보존): 진입 시 front-matter 블록을 분리 보관(`self._wysiwyg_front_matter`)하고,
  캡처 결과 앞에 다시 붙여 `_doc_text` 재구성. 더 친절하나 구현 복잡 → §9 후보로 권장.

> v1 최소 안전선은 **A(경고/차단)**. front-matter 없는 일반 문서는 무경고로 즉시 진입.

---

## 9. 데이터/루프 안전 체크리스트 (★ QA 가 이 표대로 검증)

| # | 위험 | 방어 | 검증 |
|---|------|------|------|
| 1 | WYSIWYG 편집이 webview 재렌더로 커서 파괴 | `_render_doc` 게이트(`if _wysiwyg_active: return`), `_ingest` 가 setHtml 안 함 | 타이핑 중 커서가 튀지 않음 |
| 2 | 진입 setHtml→폴링→재변환 무한 진동 | 베이스라인 비교(`_wysiwyg_last_html`), 진입은 `_render_doc` 미경유 1회 setHtml | 진입 직후 dirty 안 켜짐, _doc_text 안정 |
| 3 | 마지막 타이핑 저장 누락 | 저장 시 `_save_after_wysiwyg_capture`(캡처 콜백서 write) | 한 글자 치고 즉시 Ctrl+S → 디스크에 반영 |
| 4 | 이탈 시 최신 편집 유실 | `_exit_wysiwyg` 가 final 캡처 flush + (편집기류면) `_sync_editor_from_doc` | WYSIWYG→Editor 전환 시 소스에 최신 반영 |
| 5 | 외부 변경이 편집 surface 덮어씀 | WYSIWYG=dirty → 자동 reload 금지(배너만), `_wysiwyg_active` 시 무조건 배너 | 편집 중 외부 변경 → 화면 안 덮임 |
| 6 | self-write 가 reload 유발 | 기존 self-write 억제(시간 창+내용 비교) 그대로 | 저장 후 자기 reload 안 함 |
| 7 | front-matter 유실 | §8.1 경고/차단 | front-matter 문서 진입 시 경고 |
| 8 | 모드 빠른 토글 시 폴링 누수 | `_apply_view_mode` 의 enter/exit 가드(`prev!=mode`), exit 가 항상 `poll.stop()` | Ctrl+4↔Ctrl+2 빠른 전환 후 타이머 1개 |
| 9 | 빈 문서 WYSIWYG 복원 어색 | 복원 가드(빈 `_doc_text` → Preview 강등) | 첫 실행 WYSIWYG 복원 시 Preview |

---

## 10. 단축키 & 메뉴/툴바 (Ctrl+4 신규 — 비충돌)

### 10.1 단축키 표

| 기능 | 단축키 | 상태 |
|------|--------|------|
| 편집기 / 미리보기 / 분할 | Ctrl+1 / Ctrl+2 / Ctrl+3 | 기존(Phase 7) |
| **라이브 편집(WYSIWYG)** | **Ctrl+4** | **신규(비충돌)** |
| 저장 / 다른 이름 | Ctrl+S / Ctrl+Shift+S | 기존 |
| 붙여넣기 | Ctrl+Shift+V | 기존 |
| 새로고침 / 테마 / 줌 / 전체화면 / 목차 / 종료 | (변경 없음) | 기존 |
| 포맷 명령(굵게 등) | **단축키 없음**(툴바 클릭 전용, §3.3) | 신규 |

- `Ctrl+4` 는 기존 어떤 단축키와도 겹치지 않는다(기존은 1/2/3 만 모드, 0 은 줌 리셋).

### 10.2 액션/그룹 (Phase 7 `_mode_group` 에 추가)

```python
self.act_mode_wysiwyg = QAction("라이브 편집", self)
self.act_mode_wysiwyg.setShortcut(QKeySequence("Ctrl+4"))
self.act_mode_wysiwyg.setCheckable(True)
self.act_mode_wysiwyg.triggered.connect(self.set_mode_wysiwyg)
# 기존 _mode_group 에 추가(4개 상호배타):
self._mode_group.addAction(self.act_mode_wysiwyg)
```

### 10.3 메뉴/툴바 배치

- **보기(&V) 메뉴**: `편집기 / 미리보기 / 분할 / 라이브 편집` → ─ → 테마 → … (기존 순서에 4번째 추가).
- **메인 툴바**: 모드 그룹에 `라이브 편집` 추가(편집기·미리보기·분할·**라이브 편집**).
- **서식 툴바(신규)**: WYSIWYG 에서만 표시(§3.3). 메인 툴바와 별도 행/영역.
- **About 다이얼로그** 단축키 안내에 `Ctrl+4 라이브 편집` 추가 + §8 정규화 안내 1줄.

---

## 11. 영향 파일 & 담당

| 파일 | 변경 | 담당 |
|------|------|------|
| `src/mdviewer/main_window.py` | `MODE_WYSIWYG` 상수, `_apply_view_mode` enter/exit 훅, `_enter_wysiwyg`/`_exit_wysiwyg`/`_activate_editable_then_baseline`/`_capture_wysiwyg_once`/`_ingest_wysiwyg_html`/`_wysiwyg_poll_tick`, `_render_doc` 게이트, 서식 툴바(`_build_format_toolbar`/`_exec_format`/`_fmt_*`), Ctrl+4 액션·메뉴, `save` WYSIWYG 캡처 저장, `toggle_theme` WYSIWYG 재진입, open/paste/reload 의 `_leave_wysiwyg_for_document_change`, `_on_load_finished` 진입 셋업, About 안내 | **ui-dev** |
| `src/mdviewer/settings.py` | `_VALID_VIEW_MODES` 에 `"wysiwyg"` 추가(키/메서드 무변경) | **ui-dev** |
| `src/mdviewer/theme.py` | **선택적** article id 1줄(권장안 쓰면 무변경) | ui-dev(필요 시) |
| `src/mdviewer/renderer.py` | **변경 없음** | — |
| `src/mdviewer/file_watcher.py` | **변경 없음** | — |
| `requirements.txt` / `mdviewer.spec` | **변경 없음**(신규 의존성·자산 0) | — / packager(검증만) |
| `tests/` | §9 체크리스트(역렌더 게이트, 베이스라인 무변화, 캡처→_doc_text, front-matter 가드, 모드 토글 타이머 누수). WYSIWYG 는 JS/webview 의존이라 단위보다 **스모크(실앱 실행) 중심** | QA |

---

## 12. 빌드 순서 & 통합 지점

```
Step 1  architect    ──▶ 본 설계(08) 확정 → ui-dev 에 계약 통지(렌더/변환 시그니처는 불변)
Step 2  ui-dev       ──▶ main_window.py: WYSIWYG 모드 + 진입/이탈 + 폴링 캡처 + 서식 툴바
                          settings.py: wysiwyg 유효값
                        (core 변경 없음 → core-engine-dev 대기 불필요)
Step 3  QA           ──▶ §9 안전 체크리스트 스모크(실앱) + 라운드트립 정규화 확인
Step 4  packager     ──▶ frozen 스모크(WYSIWYG setHtml→editable→execCommand 동작)
                          신규 자산/의존성 0 → spec 변경 없음 확인만
```

**통합 지점(경계면 버그 단골 — QA 집중 검증 4개):**
- **A. 역렌더 게이트:** WYSIWYG 활성 중 `_render_doc`/외부변경/테마전환이 webview 를
  덮지 않는다(커서 보존). → `_wysiwyg_active` 게이트가 핵심(§4.3).
- **B. 베이스라인 동기화:** 진입 직후 무편집에서 dirty 안 켜지고, 사용자 편집만
  `_doc_text` 로 흐른다(§4.2). 무한 진동 없음.
- **C. flush 보장:** 이탈/저장 직전 마지막 innerHTML 캡처가 `_doc_text` 를 확정
  (비동기 콜백 순서 — §2.3/§2.4/§4.4). 마지막 글자 유실 없음.
- **D. 문서 교체 정합:** open/paste/reload 가 WYSIWYG 를 안전히 빠져나오고(`_maybe_discard`
  + `_leave_wysiwyg_for_document_change`), 새 문서가 일반 렌더로 뜬다(§4.6).

---

## 13. 확장 후보 (범위 외 — 표기만)

- QWebChannel 기반 즉시 동기화(폴링 제거) — frozen 검증 책임 동반(§3.1).
- 포맷 단축키(WYSIWYG 활성 시에만 Ctrl+B/I/K 등 동적 바인딩).
- 인라인 코드/링크 토글 해제, 표 삽입/편집, 이미지 드래그 삽입.
- front-matter 보존 라운드트립(§8.1 B안), 코드블록 언어 보존.
- 증분 DOM 동기화(전체 innerHTML 대신 변경분), 커서 위치 보존형 테마 전환.
- WYSIWYG 에서 새 문서를 곧장 WYSIWYG 로 열기(문서 교체 후 모드 유지).
- 표준 워드프로세서 범위 밖이므로 후보로만 둔다.
```