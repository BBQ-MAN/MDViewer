# MDViewer 클립보드 붙여넣기 · 임시 문서 저장 설계 (Phase 6)

> 작성: architect · 2026-06-24
> 범위 확장: (1) 클립보드 내용을 "임시(scratch) 문서"로 붙여넣어 미리보기,
> (2) 그 내용을 `.md` 파일로 저장(Save / Save As).
> 기준 문서: `_workspace/01_architect_blueprint.md` (렌더 API 계약), 현행
> `renderer.py` / `main_window.py` / `theme.py` / `settings.py`.

이 문서는 **계약(contract)**이다. core-engine-dev 는 §2 의 순수 함수 2개를,
ui-dev 는 §3 의 문서 모델·메뉴·붙여넣기/저장 흐름을 구현한다. §2 의 시그니처는
core/ui 병렬 구현의 경계이므로 **모호함 없이 확정**한다. 변경 시 architect 가
양쪽 모두에게 통지한다.

---

## 0. 핵심 설계 결정 요약

| 항목 | 결정 |
|------|------|
| HTML→Markdown 라이브러리 | **html2text** (순수 파이썬, 무의존 → PyInstaller 안전) |
| core 신규 함수 | `html_to_markdown(html: str) -> str`, `write_markdown(path: Path, text: str) -> None` |
| core 모듈 위치 | `renderer.py` (기존 `render`/`read_markdown` 와 같은 파일, 같은 패키지 export) |
| UI 문서 모델 | `_doc_text: str`, `_path: Path | None`(None=scratch), `_dirty: bool` |
| render 입력원 | **`_path` 가 아니라 `_doc_text`** 를 렌더(파일 재독은 reload 경로에서만) |
| 붙여넣기 | `Ctrl+Shift+V` — HTML 있으면 `html_to_markdown`, 없으면 plain text → scratch 전환 |
| 저장 | `Ctrl+S` = scratch면 Save As / 파일연결이면 같은 경로 저장 · `Ctrl+Shift+S` = 항상 Save As |
| 영향받는 파일 | core: `renderer.py`, `__init__.py` · UI: `main_window.py` · meta: `requirements.txt`, `pyproject.toml`(선택) |

---

## 1. 의존성 결정 — HTML→Markdown 라이브러리

### 결정: `html2text` 채택

`requirements.txt` 에 코어 계층으로 1줄 추가:

```
# --- HTML → Markdown 변환 (코어 엔진; 클립보드 붙여넣기) ---
html2text>=2024.2.26     # 순수 파이썬, 런타임 무의존 → PyInstaller 번들 안전
```

### 사유 (대안 비교)

| 후보 | 런타임 의존성 | PyInstaller | 판정 |
|------|--------------|-------------|------|
| **html2text** | **없음**(stdlib `html.parser.HTMLParser` 자체 사용) | hidden import 이슈 없음, hook 불필요 | **채택** |
| markdownify | `beautifulsoup4`(+ soupsieve) 필요 | bs4 트랜지티브 의존 번들 필요 | 탈락 |
| pandoc | 외부 바이너리(별도 설치) | 바이너리 동봉/경로 문제 | 탈락 |

- **PyInstaller 안전성이 결정 기준이다.** MDViewer 는 exe/.app 로 배포된다. `html2text`
  는 bs4/lxml 같은 네이티브·트랜지티브 의존이 없어 spec 의 `hiddenimports`/`datas` 추가
  없이 그대로 번들된다(packager 작업 0). markdownify 는 bs4 를 끌고 들어와 번들 표면을
  넓힌다.
- **코어 무의존 원칙 유지:** `html_to_markdown` 은 `renderer.py`(PySide6 무의존 코어)에
  들어간다. `html2text` 도 PySide6 무관 → 코어 단위 테스트가 GUI 없이 그대로 돈다.
- 버전 정책: 코어 라이브러리 관례대로 **하한만** 고정(`>=2024.2.26`), 상한 느슨.

> packager 통지: spec 변경 불필요(추가 hiddenimports 없음). 단, 빌드 후 frozen exe 에서
> `import html2text` 가 되는지 한 번 확인할 것(순수 파이썬이라 정상일 것으로 예상).

---

## 2. core 순수 함수 계약 (★ core-engine-dev 구현, ui-dev 호출 — 이대로 확정)

위치: **`src/mdviewer/renderer.py`** (기존 `render`/`read_markdown` 와 동거).
패키지 루트에도 export: `from mdviewer import html_to_markdown, write_markdown`.
`renderer.__all__` 와 `mdviewer.__all__` 에 두 이름 추가.

**PySide6 무의존 유지.** 두 함수 모두 `QClipboard`/`QApplication` 을 import 하지 않는다.
클립보드 접근(어떤 포맷이 있는지 판단, HTML/plain 추출)은 **전적으로 UI(§3.4) 책임**이다.
core 는 "이미 추출된 문자열"만 받는다. 이 경계가 코어 테스트 가능성을 지킨다.

### 2.1 `html_to_markdown`

```python
def html_to_markdown(html: str) -> str:
    """HTML 문자열을 Markdown 텍스트로 변환한다.

    클립보드의 ``text/html`` 조각(브라우저/워드 등에서 복사)을 마크다운 소스로
    바꾸는 데 쓴다. 변환기는 html2text 를 사용한다(순수 파이썬, 무의존).

    Args:
        html: HTML 문자열. 완전한 문서(<html>…)든 조각(fragment)이든 허용.
              None/빈 문자열도 허용(아래 반환 규칙 참조).

    Returns:
        Markdown 텍스트(str). **항상 str 을 반환하며 절대 None 을 반환하지 않는다.**
        - 입력이 None 또는 공백뿐이면 ``""`` 반환.
        - 변환 결과 끝의 잉여 공백/개행은 정리하되, 본문 내부 개행 구조는 보존한다
          (구현 권장: ``result.strip("\n")`` 수준의 가벼운 트림. 과도한 재포맷 금지).

    동작 규칙(html2text 설정 — 마크다운 충실도 우선):
        - ``body_width = 0``  → 자동 줄바꿈(wrap) 비활성. 긴 줄을 임의로 접지 않음
          (뷰어/에디터에서 줄 깨짐 방지. **반드시 0**).
        - ``ignore_images = False``  → 이미지를 ``![alt](src)`` 로 보존.
        - ``ignore_links = False``   → 링크를 ``[text](href)`` 로 보존.
        - ``ignore_emphasis = False``→ 굵게/기울임 보존.
        - 단위 텍스트 변환에 실패할 수 있는 극단 입력에도 **예외를 던지지 않는다.**
          내부에서 예외 발생 시 빈 문자열 또는 escape 된 평문으로 복구한다
          (render() 의 "절대 크래시 금지" 정책과 동일 철학).

    Thread-safety:
        순수 함수(전역 가변 상태 없음). 워커 스레드에서 호출 가능.
        ⚠️ html2text 의 ``HTML2Text`` 인스턴스는 **호출마다 새로 생성**한다
        (인스턴스 재사용 시 내부 상태가 누적될 수 있으므로 모듈 전역 공유 금지).

    Raises:
        없음(계약상 예외 비전파). 모든 내부 오류는 복구.
    """
    ...
```

**반환 shape 못박기:** `str` (None 불가). 빈/None 입력 → `""`. 예외 비전파.

구현 스케치(참고용, 구현 자유):

```python
def html_to_markdown(html: str) -> str:
    if not html or not html.strip():
        return ""
    try:
        import html2text
        h = html2text.HTML2Text()      # 호출마다 새 인스턴스
        h.body_width = 0
        h.ignore_images = False
        h.ignore_links = False
        h.ignore_emphasis = False
        return h.handle(html).strip("\n")
    except Exception:
        # 최후 폴백: 태그 제거 평문(크래시 금지). 구현 재량.
        from html import unescape
        import re
        text = re.sub(r"<[^>]+>", "", html)
        return unescape(text).strip()
```

### 2.2 `write_markdown`

```python
def write_markdown(path: Path, text: str) -> None:
    """Markdown 텍스트를 파일에 기록한다. read_markdown 과 대칭.

    Args:
        path: 저장 대상 파일 경로(`.md`). 부모 디렉터리가 없으면 생성한다.
        text: 기록할 마크다운 텍스트. None 이면 빈 문자열로 취급.

    동작 규칙:
        - 인코딩 **UTF-8 (BOM 없음)**. read_markdown 이 UTF-8 우선이므로 대칭.
        - **개행 보존:** ``newline=""`` 로 열어 파이썬의 universal-newlines 자동
          변환을 끄고 ``text`` 의 개행을 **있는 그대로** 기록한다(CRLF/LF 혼용
          입력을 임의로 바꾸지 않음). 즉 round-trip(``read_markdown(write 결과)``)
          시 본문이 보존되어야 한다.
        - **부모 디렉터리 보장:** ``path.parent.mkdir(parents=True, exist_ok=True)``.
        - 기존 파일이 있으면 **덮어쓴다**(truncate). (덮어쓰기 확인 UI 는 §3.5 의
          QFileDialog 가 담당. core 는 묻지 않고 기록.)

    Returns:
        None.

    Raises:
        OSError: 디렉터리 생성/쓰기 실패(권한·디스크 등) 시 **그대로 전파**한다.
                 (read_markdown 이 OSError 를 전파하는 것과 대칭. UI 가 잡아
                  QMessageBox 로 사용자에게 알린다. §3.5.)
        ⚠️ write_markdown 은 html_to_markdown/render 와 달리 I/O 예외를 **삼키지
           않는다.** "저장 실패"는 사용자가 반드시 알아야 하는 사건이기 때문이다.
    """
    ...
```

**예외 정책 대조표 (경계면 버그 방지):**

| 함수 | 입력 오류 | I/O 오류 | None 반환 |
|------|----------|---------|----------|
| `render` | 비전파(의미 있는 결과) | n/a | 안 함 |
| `read_markdown` | 디코딩 실패는 복구 | `FileNotFoundError`/`OSError` 전파 | 안 함(str) |
| `html_to_markdown` | 비전파(`""` 복구) | n/a | 안 함(str) |
| `write_markdown` | None→"" | **`OSError` 전파** | n/a(None) |

구현 스케치:

```python
def write_markdown(path: Path, text: str) -> None:
    path = Path(path)
    if text is None:
        text = ""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:  # BOM 없음, 개행 보존
        f.write(text)
```

### 2.3 코어 테스트 지점 (QA 용 — GUI 불필요)

- `html_to_markdown("<h1>제목</h1><p><b>굵게</b> <a href='x'>링크</a></p>")`
  → `# 제목` / `**굵게** [링크](x)` 형태 포함, str 반환.
- `html_to_markdown("")` / `html_to_markdown(None)` → `""`.
- `write_markdown(tmp/"a"/"b.md", "x\r\ny")` → 부모 생성, 바이트에 `\r\n` 보존.
- round-trip: `read_markdown(write 결과) == 원본`(개행 포함, UTF-8).
- `write_markdown(읽기전용경로, "x")` → `OSError` 전파 확인.

---

## 3. UI 동작 상세 (ui-dev — `main_window.py`)

### 3.1 문서 모델 도입 (★ 현행 흐름 정리)

**현행 문제:** `MainWindow` 의 상태는 `self._path` 뿐이다. `_reload_current()` 가
**매번 `read_markdown(self._path)` 로 파일을 다시 읽어** 렌더한다. 클립보드 scratch
문서는 **디스크에 없으므로** 이 흐름으로는 렌더할 수 없다.

**해결:** 보관 소스 텍스트를 일급 상태로 둔다.

```python
# MainWindow.__init__ 에 추가
self._doc_text: str = ""            # 현재 표시 중인 마크다운 소스(항상 최신)
self._path: Path | None = None      # 연결된 파일. None = scratch(미저장 임시 문서)
self._dirty: bool = False           # 마지막 저장 이후 변경 여부
```

**렌더 입력원 변경:** render 는 `_path` 가 아니라 **`_doc_text`** 를 렌더한다.
파일 재독(`read_markdown`)은 **"디스크에서 새로 불러오는 경로"에서만** 일어난다:
열기(`open_path`), 외부 변경 reload, 수동 새로고침(Ctrl+R). 그 외(테마 전환,
붙여넣기 직후 재렌더)는 `_doc_text` 를 그대로 렌더한다.

기존 `_reload_current(preserve_scroll)` 를 **두 책임으로 분리**한다:

```python
def _load_from_disk(self, *, preserve_scroll: bool) -> bool:
    """디스크에서 현재 _path 를 다시 읽어 _doc_text 갱신 후 렌더.
    (열기/외부변경/Ctrl+R 전용. scratch 면 아무 것도 안 함→False.)"""
    if self._path is None:
        return False                 # scratch 는 디스크 소스가 없음
    try:
        self._doc_text = read_markdown(self._path)
    except FileNotFoundError:
        self.statusBar().showMessage(f"파일이 사라졌습니다: {self._path}")
        return False
    except OSError as exc:
        QMessageBox.warning(self, "읽기 오류", f"파일을 읽을 수 없습니다:\n{exc}")
        return False
    self._render_doc(preserve_scroll=preserve_scroll)  # 디스크와 동기화됨
    self._set_dirty(False)
    return True

def _render_doc(self, *, preserve_scroll: bool) -> None:
    """_doc_text 를 렌더해 화면에 반영(파일 재독 없음).
    base_dir = _path.parent if _path else 적절한 기본값(아래 3.6)."""
    base_dir = self._path.parent if self._path else self._scratch_base_dir()
    result = render(self._doc_text, base_dir=base_dir)   # 예외 안 던짐(계약)
    if preserve_scroll:
        self._capture_scroll_then_render(result)
    else:
        self._set_document(result, restore_scroll=False)
```

- 기존 `_reload_current` 호출처를 위 둘로 치환:
  - `open_path` 내부: `_load_from_disk(preserve_scroll=False)`
  - 외부 변경(`_on_file_changed`→타이머→) : `_load_from_disk(preserve_scroll=True)`
    (단 **scratch 면 watch 가 꺼져 있으므로** 이 경로 자체가 안 탐 — 3.3 참조)
  - `act_reload`(Ctrl+R): `_load_from_disk(preserve_scroll=True)`
    (scratch 면 디스크 소스 없음 → no-op + 상태바 "임시 문서는 새로고침 대상이 없습니다")
  - `toggle_theme`: 파일/스크래치 무관하게 **`_render_doc(preserve_scroll=True)`**
    (디스크 재독 불필요, `_doc_text` 만 다시 감싸 렌더 → scratch 에서도 테마 전환 동작)
  - `_set_document` 의 `base` 계산도 `_path` None 일 때 §3.6 기본값을 쓰도록 정렬.

> 통지: 이 분리는 **동작 동등**을 유지한다(파일 열기/외부변경 동작 불변). 단 테마 전환이
> 더 이상 디스크를 재독하지 않으므로 약간 빨라지고 scratch 에서도 동작한다.

### 3.2 dirty 플래그 & 타이틀 규칙

```python
def _set_dirty(self, dirty: bool) -> None:
    self._dirty = dirty
    self._update_title()

def _update_title(self) -> None:
    if self._path is not None:
        name = self._path.name
    else:
        name = "제목 없음"          # scratch 문서 표시명
    mark = "•" if self._dirty else ""   # 미저장 표시(앞에 점)
    self.setWindowTitle(f"{mark}{name} — MDViewer")
```

타이틀 표 (★ ui-dev 가 이 표대로):

| 상태 | _path | _dirty | 타이틀 |
|------|-------|--------|--------|
| 파일 열림(동기화) | 있음 | False | `demo.md — MDViewer` |
| 파일, 편집/외부변경 후 미저장 | 있음 | True | `•demo.md — MDViewer` |
| scratch 붙여넣기 직후 | None | True | `•제목 없음 — MDViewer` |
| scratch 저장 후 | (새 경로) | False | `note.md — MDViewer` |

- **기존 `open_path` 의 `setWindowTitle(f"{path.name} — MDViewer")` 직접 호출은
  제거**하고 `_set_dirty(False)`(→`_update_title`)로 일원화한다.
- scratch 는 디스크 소스가 없으므로 붙여넣기 시 `_dirty=True` 로 시작한다(저장 유도).

### 3.3 watch 생명주기 (scratch ↔ 파일 전환 규칙 — ★ 통합 버그 단골)

| 전환 | watch 동작 |
|------|-----------|
| 파일 열기(`open_path`) | `watcher.watch(path)` (현행 유지) |
| **scratch 로 전환(붙여넣기)** | **`watcher.stop()`** — 감시할 디스크 파일이 없음 |
| scratch → 파일(저장 성공) | `watcher.watch(new_path)` 시작 |
| 파일 → 다른 파일 저장(Save As) | `watcher.watch(new_path)` 로 교체 |

- 헬퍼 권장:
  ```python
  def _set_scratch(self, text: str) -> None:
      """현재 문서를 경로 없는 scratch 로 전환."""
      if self._watcher is not None:
          try: self._watcher.stop()
          except Exception: pass
      self._path = None
      self._doc_text = text
      self._pending_scroll = None           # 새 문서는 상단부터
      self._render_doc(preserve_scroll=False)
      self._set_dirty(True)                 # 미저장 표시
      self.statusBar().showMessage("임시 문서(붙여넣기) — 저장하려면 Ctrl+S")

  def _attach_path(self, path: Path) -> None:
      """저장/열기 후 문서를 디스크 파일에 연결(watch/recent/title)."""
      self._path = Path(path)
      if self._watcher is not None:
          try: self._watcher.watch(self._path)
          except Exception: pass
      self.settings.add_recent_file(str(self._path))
      self._refresh_recent_menu()
      self._set_dirty(False)                # 저장 직후 = 동기화됨
      self.statusBar().showMessage(str(self._path))
  ```

### 3.4 붙여넣기 동작 — `Ctrl+Shift+V`

신규 액션 `act_paste`("클립보드 붙여넣기", `Ctrl+Shift+V`), 파일 메뉴 & 툴바에 추가.

```python
def paste_clipboard(self) -> None:
    cb = QGuiApplication.clipboard()           # PySide6 import (UI 책임)
    md = cb.mimeData()
    text: str = ""
    if md.hasHtml():
        html = md.html()                       # text/html 조각
        text = html_to_markdown(html)          # ← core 호출 (예외 없음, str)
        if not text.strip() and md.hasText():  # HTML 변환이 비면 plain 폴백
            text = md.text()
    elif md.hasText():
        text = md.text()                       # plain text 그대로 마크다운 취급
    else:
        self.statusBar().showMessage("클립보드에 붙여넣을 텍스트가 없습니다.", 3000)
        return

    if not text.strip():
        self.statusBar().showMessage("클립보드 내용이 비어 있습니다.", 3000)
        return

    self._set_scratch(text)                    # scratch 전환 + 렌더 + dirty + watch off
```

규칙(요지):
- **HTML 우선**: 클립보드에 `text/html` 이 있으면 `html_to_markdown` 사용.
- **plain 폴백**: HTML 없거나 변환 결과가 공백뿐이면 `mimeData().text()` 사용.
- 둘 다 없으면 상태바 안내 후 **현재 문서 유지**(scratch 전환 안 함).
- 변환된 텍스트는 **경로 없는 scratch 문서**가 되고(`_path=None`), 즉시 렌더,
  watch 중지, 타이틀에 미저장 표시(`•제목 없음`).
- ⚠️ `cb.mimeData()` 의 HTML 은 워드/브라우저가 넣은 잡다한 wrapper(`<!--StartFragment-->`,
  Office 네임스페이스 등)를 포함할 수 있다. `html2text` 가 대체로 잘 처리하나, core 가
  크래시하지 않는 것이 계약으로 보장되므로 UI 는 결과를 그대로 신뢰한다.

### 3.5 저장 동작 — `Ctrl+S` / `Ctrl+Shift+S`

신규 액션 2개:
- `act_save`("저장", `QKeySequence.StandardKey.Save` = Ctrl+S)
- `act_save_as`("다른 이름으로 저장...", `Ctrl+Shift+S`)

```python
def save(self) -> bool:
    """Ctrl+S: scratch 면 Save As, 파일연결이면 같은 경로에 저장."""
    if self._path is None:
        return self.save_as()
    return self._write_to(self._path)

def save_as(self) -> bool:
    """Ctrl+Shift+S: 항상 파일 대화상자. *.md 기본."""
    start = str(self._path) if self._path else \
            str(Path(self._suggest_name()))         # 3.6 기본 파일명
    path_str, _ = QFileDialog.getSaveFileName(
        self, "다른 이름으로 저장", start,
        "Markdown (*.md);;모든 파일 (*.*)",
    )
    if not path_str:
        return False                               # 사용자 취소
    path = Path(path_str)
    if path.suffix == "":                          # 확장자 없으면 .md 보정
        path = path.with_suffix(".md")
    return self._write_to(path)

def _write_to(self, path: Path) -> bool:
    try:
        write_markdown(path, self._doc_text)       # ← core 호출(OSError 전파)
    except OSError as exc:
        QMessageBox.warning(self, "저장 실패", f"파일을 저장할 수 없습니다:\n{exc}")
        return False
    was_scratch = self._path is None
    if self._path != path:
        # 새 경로(또는 scratch→파일): watch 교체 + recent + 연결
        self._attach_path(path)                    # 3.3: watch on, recent, title, dirty off
    else:
        self._set_dirty(False)                     # 같은 경로 재저장
    self.statusBar().showMessage(
        f"저장됨: {path}" + ("  (임시 문서를 파일로 저장)" if was_scratch else ""),
        3000,
    )
    return True
```

저장 흐름 규칙(요지):
- **Ctrl+S**:
  - scratch(`_path is None`) → `save_as()` 위임(대화상자).
  - 파일연결 → **같은 경로**에 `write_markdown` (대화상자 없음).
- **Ctrl+Shift+S**: 항상 `QFileDialog.getSaveFileName`(`*.md`), 확장자 없으면 `.md` 보정.
- 저장 성공 후 **scratch→파일 전환**: `_attach_path` 가 watch 시작 · recent 추가 ·
  타이틀 갱신 · `_dirty=False`. (`_doc_text` 는 이미 최신이므로 재렌더 불필요.)
- 저장 실패(`OSError`) → QMessageBox 경고, dirty 유지, watch/연결 변경 없음.
- QFileDialog 가 기존 파일 덮어쓰기 확인을 담당(OS 표준). core 는 묻지 않고 기록(§2.2).

### 3.6 scratch 의 base_dir 와 기본 파일명

- scratch 는 디스크 경로가 없어 **상대 이미지/링크 해석 기준이 없다.** `_scratch_base_dir()`
  는 **현재 작업 디렉터리(`Path.cwd()`)** 또는 최근 파일의 부모(있으면)를 반환한다.
  (붙여넣기 내용의 이미지는 대개 절대 URL `http(s)://` 라서 base_dir 무관. 상대경로
  이미지는 scratch 에선 깨질 수 있음 — 표준 범위로 수용.)
- `_suggest_name()`: 기본 저장 파일명 `"제목 없음.md"` 또는 `_doc_text` 의 첫 헤딩 슬러그
  기반 이름(선택). 최소 구현은 `"untitled.md"` 로 충분.

### 3.7 종료 시 미저장 경고 (권장 — 표준 동작)

`closeEvent` 앞에 dirty 가드를 둔다(데이터 유실 방지). 표준 범위 내 권장 사항:

```python
def _maybe_discard(self) -> bool:
    """미저장 변경이 있으면 저장/버림/취소 묻기. True=계속 진행 가능."""
    if not self._dirty:
        return True
    btn = QMessageBox.question(
        self, "저장하지 않은 변경",
        "변경 내용을 저장하시겠습니까?",
        QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
        | QMessageBox.StandardButton.Cancel,
    )
    if btn == QMessageBox.StandardButton.Save:
        return self.save()                    # 저장 성공 시에만 진행
    if btn == QMessageBox.StandardButton.Discard:
        return True
    return False                              # Cancel
```

- `closeEvent`: `if not self._maybe_discard(): event.ignore(); return` 를 맨 앞에.
- (선택) `open_path`/`paste_clipboard` 진입 시에도 `_maybe_discard()` 가드 권장 —
  단 현행 동작과의 호환을 위해 **최소 구현에선 closeEvent 만 필수**, 나머지는 후보.

---

## 4. 단축키 표 (충돌 확인 — 신규 3개 모두 비충돌)

| 기능 | 단축키 | 상태 |
|------|--------|------|
| 열기 | Ctrl+O | 기존 |
| 새로고침 | Ctrl+R | 기존 |
| 테마 전환 | Ctrl+T | 기존 |
| 줌 인/아웃/리셋 | Ctrl+= / Ctrl+- / Ctrl+0 | 기존 |
| 전체화면 | F11 | 기존 |
| 목차 토글 | Ctrl+\\ | 기존 |
| 종료 | Ctrl+Q | 기존 |
| **붙여넣기(클립보드→scratch)** | **Ctrl+Shift+V** | **신규(비충돌)** |
| **저장** | **Ctrl+S** | **신규(비충돌)** |
| **다른 이름으로 저장** | **Ctrl+Shift+S** | **신규(비충돌)** |

메뉴 배치:
- 파일(&F): 열기 → 최근 파일 → **붙여넣기** → ─ → **저장 / 다른 이름으로 저장** → 새로고침 → ─ → 종료
- 툴바: 열기 · 새로고침 · **붙여넣기** · **저장** · ─ · 줌… · 테마 · 목차
- About 다이얼로그의 단축키 안내 문자열에 신규 3개 추가.

---

## 5. 영향 파일 & 담당

| 파일 | 변경 | 담당 |
|------|------|------|
| `src/mdviewer/renderer.py` | `html_to_markdown`, `write_markdown` 추가 · `__all__` 갱신 | core-engine-dev |
| `src/mdviewer/__init__.py` | 두 함수 패키지 루트 export(`__all__`) | core-engine-dev |
| `requirements.txt` | `html2text>=2024.2.26` 추가 | architect ✓(이 문서) → core-engine-dev 반영 |
| `src/mdviewer/main_window.py` | 문서 모델(_doc_text/_dirty), `_load_from_disk`/`_render_doc` 분리, paste/save/save_as 액션·메뉴·툴바, watch 생명주기, 타이틀, closeEvent 가드 | ui-dev |
| `pyproject.toml` | (선택) 의존성 미러 시 `html2text` 추가 | core-engine-dev |
| `tests/` | `html_to_markdown`/`write_markdown` 단위 테스트, round-trip | QA |
| `mdviewer.spec` | 변경 없음(검증만) | packager |

---

## 6. 빌드 순서 & 통합 지점

```
Step 1  architect    ──▶ 본 설계(06) 확정 → core/ui 에 §2 시그니처 통지
Step 2  (병렬)
  ├ core-engine-dev ──▶ renderer.py: html_to_markdown / write_markdown
  │                     __init__ export, requirements.txt 반영
  └ ui-dev          ──▶ main_window.py: 문서 모델 + paste/save 흐름
                        (core 미연결이어도 graceful import 패턴으로 선개발 가능)
Step 3  QA           ──▶ 코어 단위(변환/쓰기/round-trip/예외) + 경계면 shape 대조
                         + 스모크(붙여넣기→렌더→저장→watch 전환→타이틀/ dirty)
Step 4  packager     ──▶ html2text 번들 확인(빌드 후 import 검증), 회귀 스모크
```

**통합 지점:**
- A. `html_to_markdown` 반환은 **항상 str**(None 불가) — UI 는 `.strip()` 안전.
- B. `write_markdown` 은 **`OSError` 전파** — UI 는 반드시 try/except 로 감싸 QMessageBox.
- C. **render 입력원 = `_doc_text`**(파일 아님) — scratch 가 디스크 없이 렌더되는 근거.
- D. **watch 생명주기**: scratch=stop, 저장/열기=watch. 한쪽만 지키면 "임시 문서가
     유령 파일 변경 이벤트로 덮어써짐" 또는 "저장 후 외부변경 감지 안 됨" 버그.

---

## 7. graceful import (ui-dev 선개발용 — 기존 패턴 확장)

`main_window.py` 의 기존 try/except 코어 import 블록에 두 함수도 폴백을 둔다:

```python
try:
    from .renderer import read_markdown, render, html_to_markdown, write_markdown
    _CORE_AVAILABLE = True
except Exception as exc:
    _CORE_AVAILABLE = False
    # 기존 read_markdown/render 폴백 +
    def html_to_markdown(html: str) -> str:   # type: ignore
        import re; from html import unescape
        return unescape(re.sub(r"<[^>]+>", "", html or "")).strip()
    def write_markdown(path, text) -> None:    # type: ignore
        from pathlib import Path as _P
        p = _P(path); p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(text or "")
```

(폴백은 개발 편의용. 실제 동작은 core-engine-dev 구현으로 대체된다.)

---

## 8. 확장 후보 (범위 외 — 표기만)

- scratch 문서 인라인 편집(텍스트 에디터 패널), 다중 scratch 탭,
  붙여넣기 시 이미지(`text/uri-list`/비트맵)를 로컬에 저장 후 상대링크화,
  "최근 붙여넣기" 히스토리, 자동 저장. 표준 뷰어 범위 밖이므로 후보로만 둔다.
