# MDViewer Word(.docx) / PDF 내보내기 설계 (Phase 10)

> 작성: architect · 2026-06-25
> 범위 확장: 현재 문서(`_doc_text`)를 (1) **PDF** 파일로, (2) **Word(.docx)** 파일로
> 내보낸다. 화면에 보이는 뷰가 아니라 **`_doc_text` 를 기준 소스**로 새로 렌더해
> 깨끗한 결과물을 만든다.
> 기준 문서: `_workspace/01_architect_blueprint.md`(렌더 API 계약),
> `06_clipboard_feature_design.md`(core 순수 함수·예외 정책 선례), 현행
> `renderer.py` / `theme.py` / `main_window.py` / `mdviewer.spec`.

이 문서는 **계약(contract)**이다. 두 개발자가 **추측 없이 병렬 구현**할 수 있도록
경계면을 못박는다. 변경 시 architect 가 양쪽 모두에게 통지한다.

- **core-engine-dev**: §2 의 신규 모듈 `exporter.py`(Word 변환, PySide6 무의존 순수 함수).
- **ui-dev**: §3 의 PDF 오프스크린 내보내기 + §4 의 액션/메뉴/툴바 + §5 의 flush 규칙.

두 작업의 **유일한 코드 경계면**은 §2 의 단 한 함수
`exporter.markdown_to_docx(...)` 와 §3 의 UI 가 호출하는 `renderer.render(...)`
(이미 존재) 이다. 그 외엔 서로 만나지 않는다.

---

## 0. 핵심 설계 결정 요약

| 항목 | 결정 |
|------|------|
| PDF 내보내기 계층 | **UI 계층** — `QWebEnginePage.printToPdf`(오프스크린 페이지) |
| PDF 입력원 | `_doc_text` → `render()` → `theme_mod.wrap_document(body, dark=False)`(인쇄용 **라이트 강제**) |
| PDF 페이지 | A4 세로, 여백 12.7mm(0.5in) 균일, 배경색 인쇄 on |
| Word 내보내기 계층 | **core 계층** — 신규 `src/mdviewer/exporter.py` |
| Word 라이브러리 | **python-docx**(1.2.0 설치 확인; lxml 의존) |
| Word 변환 방식 | `render()` 본문 HTML → stdlib `html.parser` walk → python-docx 요소 매핑 (**bs4/lxml 직접 파싱 금지**) |
| core 신규 함수 | `markdown_to_docx(markdown_text, out_path, base_dir, *, title=None) -> None` |
| core 예외 정책 | **변환은 절대 크래시 금지**(render 철학) · **파일 I/O(OSError)만 전파**(write_markdown 철학) |
| UI 신규 메서드 | `export_pdf()`, `export_docx()`, `_export_pdf_async(out_path)` 등(§3·§4) |
| flush 규칙 | 내보내기 전 `_flush_pending_edit()` + WYSIWYG 활성 시 innerHTML 캡처(§5) |
| requirements | `python-docx>=1.1` 1줄 추가 |
| spec 영향 | `collect_all("docx")` + lxml 수집(§7) |
| 영향 파일 | 신규 `exporter.py` · 수정 `main_window.py`/`__init__.py`/`requirements.txt`/`mdviewer.spec` · 추가 `tests/test_export.py` |

---

## 1. 개요·범위

### 1.1 무엇을 / 왜

사용자가 보고 있는 마크다운 문서를 **공유 가능한 산출물**(PDF, Word)로 저장한다.
PDF 는 인쇄·배포용 고정 레이아웃, Word 는 추가 편집용 편집 가능 문서다. 둘 다
**화면 상태(테마/줌/스크롤)와 무관하게** `_doc_text` 를 기준으로 새로 렌더해
**결정적이고 깨끗한** 결과를 만든다(다크 테마로 보고 있어도 PDF 는 라이트).

### 1.2 입력원 원칙 (★ 두 경로 공통)

- 내보내기의 **단일 진실 원천은 `_doc_text`** 다. `self.view` 의 현재 DOM 이나
  `_path` 의 디스크 내용이 아니다(편집 중이면 디스크와 다를 수 있음).
- 따라서 두 경로 모두 시작 전에 **`_doc_text` 최신화**가 선행돼야 한다(§5).
- `base_dir` = `_path.parent`(파일 연결) 또는 `_scratch_base_dir()`(scratch).
  상대 이미지/링크 해석 기준. `_render_doc`/`_set_document` 의 기존 규칙과 동일.

### 1.3 비범위 (이번 Phase 에서 하지 않음 — 표기만)

- 페이지 머리글/꼬리말(header/footer), 페이지 번호, 표지/목차 페이지 자동 생성.
- 사용자 지정 용지 크기/여백 UI(A4 고정·여백 고정으로 시작).
- docx 스타일 템플릿 선택, 글꼴/색 커스터마이즈, 워터마크.
- PDF 책갈피(bookmark)/하이퍼링크 네비게이션 트리, PDF 암호화.
- 코드블록의 **구문 색상**을 docx 에 재현(텍스트만 추출, 음영 단락으로 표현).
- 일괄(여러 파일) 내보내기, 내보내기 후 자동 열기.

이들은 표준 뷰어 범위 밖이므로 **확장 후보(§11)**로만 둔다.

---

## 2. 계약 — core `exporter.py` (★ core-engine-dev 구현, ui-dev 호출)

위치: **신규 `src/mdviewer/exporter.py`**. `renderer.py` 와 별도 모듈로 둔다
(렌더 vs 내보내기 책임 분리, python-docx 의존을 렌더 코어에서 격리).

**PySide6 무의존 유지.** `exporter.py` 는 `PySide6` / `QtWidgets` 를 절대 import
하지 않는다. 클립보드·파일 대화상자·페이지 인쇄는 전적으로 UI(§3·§4) 책임이다.
core 는 "이미 추출된 마크다운 문자열 + 출력 경로 + base_dir" 만 받는다. 이 경계가
**GUI 없는 단위 테스트**(§10)를 가능하게 한다.

### 2.1 공개 시그니처 (이대로 확정)

```python
# src/mdviewer/exporter.py
from __future__ import annotations
from pathlib import Path

__all__ = ["markdown_to_docx"]


def markdown_to_docx(
    markdown_text: str,
    out_path: Path,
    base_dir: Path,
    *,
    title: str | None = None,
) -> None:
    """마크다운 텍스트를 Word(.docx) 문서로 내보낸다.

    내부적으로 ``renderer.render(markdown_text, base_dir)`` 로 본문 HTML 을 만든 뒤,
    표준 라이브러리 ``html.parser`` 로 walk 하며 python-docx 요소로 매핑한다
    (bs4/lxml 직접 파싱 금지 — 추가 의존성·번들 표면 회피).

    Args:
        markdown_text: 원본 마크다운 소스(``_doc_text``). None 이면 빈 문서로 취급.
        out_path: 저장 대상 ``.docx`` 경로. 부모 디렉터리가 없으면 생성한다.
        base_dir: 상대 이미지/링크 해석 기준 디렉터리(보통 열린 파일의 부모).
            renderer 가 상대경로를 ``file:///`` URI 로 치환할 때 사용하는 것과 동일.
        title: (선택) 문서 제목. 지정 시 docx 코어 속성(``core_properties.title``)에
            기록한다. 본문에 별도 제목 단락을 추가하지는 않는다(본문 h1 이 이미 제목).

    Returns:
        None.

    Raises:
        OSError: ``out_path`` 디렉터리 생성/파일 저장 실패(권한·디스크·잠금 등) 시
            **그대로 전파**한다(``write_markdown`` 과 대칭 — "저장 실패"는 사용자가
            반드시 알아야 하는 사건. UI 가 try/except 로 잡아 QMessageBox).
        그 외 예외는 던지지 않는다 — 아래 "예외 정책" 참조.

    예외 정책 (★ 두 철학의 결합 — 경계면 버그 단골):
        - **변환 자체는 절대 크래시하지 않는다**(``render`` 의 "어떤 입력에도 안전"
          철학). 깨진/빈/비정상 HTML, 알 수 없는 태그, 깨진 이미지에도 예외를 던지지
          않고 **최선의 docx 를 만들어 저장**한다. 매핑 불가 태그는 텍스트만 보존하거나
          건너뛴다. 빈 입력이면 빈(또는 사실상 빈) docx 를 저장한다.
        - **단, 최종 ``Document.save(out_path)`` 의 I/O 실패(OSError)만 전파**한다.
          변환 단계의 내부 오류는 삼키되, "파일을 못 썼다"는 전파한다.

    Thread-safety:
        순수 함수(전역 가변 상태 없음). 매 호출 새 ``docx.Document()`` 인스턴스 생성.
        ui-dev 는 메인 스레드에서 동기 호출한다(전형적 문서 크기에서 충분히 빠름).
        대용량 대비 워커 스레드 호출도 안전(인스턴스 비공유). v1 은 동기로 충분.
    """
    ...
```

**반환·예외 shape 못박기:** 반환 `None`. **변환 비전파 / I/O(OSError) 전파.**
None 입력 → 빈 문서. UI 는 호출을 `try/except OSError` 로만 감싸면 충분하다.

### 2.2 예외 정책 대조표 (기존 core 함수와의 정합)

| 함수 | 입력 오류 | I/O 오류 | 반환 |
|------|----------|---------|------|
| `render` | 비전파(의미 있는 결과) | n/a | `RenderResult`(None 아님) |
| `read_markdown` | 디코딩 실패는 복구 | `FileNotFoundError`/`OSError` 전파 | `str` |
| `write_markdown` | None→"" | **`OSError` 전파** | `None` |
| `html_to_markdown` | 비전파(`""` 복구) | n/a | `str` |
| **`markdown_to_docx`** | **비전파(최선 docx)** | **`OSError` 전파** | **`None`** |

`markdown_to_docx` 는 `render`(변환 안전) + `write_markdown`(I/O 전파)의 **결합**이다.

### 2.3 구현 가이드 — HTML walk → docx 매핑

`render()` 가 만드는 본문 HTML 의 **실제 shape**(현행 renderer 확인 결과)을 전제로
매핑한다. 아래는 실제 출력 예시다(주의 깊게 보고 매핑하라):

```html
<h1 id="title">Title</h1>
<p>Para with <strong>bold</strong> <em>em</em> <code>inline code</code>
   and <a href="file:///D:/.../sub/page.md">link</a>.</p>
<ul><li>a</li><li>b<ul><li>nested</li></ul></li></ul>
<ol><li>one</li><li>two</li></ol>
<blockquote><p>quote line</p></blockquote>
<table>
  <thead><tr><th>H1</th><th>H2</th></tr></thead>
  <tbody><tr><td>a</td><td>b</td></tr></tbody>
</table>
<pre><code class="language-python"><div class="codehilite"><pre><span></span><span class="k">def</span>...</pre></div></code></pre>
<p><img src="file:///D:/.../img/pic.png" alt="alt" /></p>
<ul class="contains-task-list">
  <li class="task-list-item enabled"><input class="task-list-item-checkbox" type="checkbox"> task undone</li>
</ul>
<hr />
```

#### 매핑표 (블록 요소)

| HTML | python-docx 매핑 |
|------|------------------|
| `h1`~`h6` | `doc.add_heading(text, level=n)` (h1→level 1 … h6→level 6) |
| `p` | `doc.add_paragraph()` + 인라인 run 들 |
| `ul > li` | `doc.add_paragraph(style="List Bullet")`. 중첩은 `List Bullet 2/3`(깊이별, 최대 3) |
| `ol > li` | `doc.add_paragraph(style="List Number")`. 중첩은 `List Number 2/3` |
| `blockquote` | 내부 `p` 들을 `style="Intense Quote"`(없으면 `"Quote"`) 단락으로 |
| `pre > code` (코드블록) | **텍스트만 추출**(내부 `codehilite` `<div><pre>` 의 모든 `<span>` 텍스트 이어붙임) → monospace(Consolas) run + 옅은 음영 단락(§2.4). Pygments 색상은 버림 |
| `table` | `doc.add_table(rows, cols)`; `thead`/`tbody` 의 `tr/th/td` 를 셀로. `style="Light Grid Accent 1"`(없으면 `"Table Grid"`) |
| `hr` | 빈 단락 + 하단 테두리(또는 `"─"` 반복 단락으로 간략 표현) |
| `img` | §2.5 참조 |
| `li.task-list-item` | 자식 `<input>` 의 `checked` 유무로 `☑`/`☐` 접두 + 텍스트(List Bullet) |

#### 매핑표 (인라인 요소 → run)

| HTML | run 속성 |
|------|----------|
| `text` 노드 | 일반 run |
| `strong`, `b` | `run.bold = True` |
| `em`, `i` | `run.italic = True` |
| `s`, `del`, `strike` | `run.font.strike = True`(취소선; 없으면 무시) |
| `code`(인라인) | monospace(Consolas) run + (선택) 옅은 회색 음영 |
| `a` | §2.6 하이퍼링크. 최소 구현은 **링크 텍스트 run + (괄호 URL)** 허용 |
| `br` | run 에 `run.add_break()` |
| `input`(task checkbox) | run 아님 — 상위 `li` 가 `☑/☐` 접두로 처리 |

> 인라인 서식은 **중첩**될 수 있다(`<strong><em>x</em></strong>`). walk 시 활성 서식
> 집합(bold/italic/code…)을 스택으로 들고 내려가며, `text` 노드를 만나면 현재 서식
> 집합을 적용한 run 을 추가하는 방식을 권장한다.

#### 2.4 코드블록 처리 상세

- 코드블록 HTML 은 `<pre><code class="language-XXX"><div class="codehilite"><pre>…</pre></div></code></pre>`
  로 **이중 `<pre>`** + Pygments `<span>` 토큰이다.
- **텍스트만** 추출한다: 내부 모든 텍스트 노드를 순서대로 이어붙이면 원본 코드가
  **개행 포함** 복원된다(`<span>` 은 구조만, 텍스트가 코드 글자). `language-XXX`
  클래스에서 언어명을 뽑아 코드블록 위 작은 캡션 run(선택)으로 둘 수 있다.
- 각 코드 라인을 monospace(Consolas, 10pt) 단락으로. 단락에 옅은 배경 음영을 주려면
  `w:shd`(셀/단락 shading)을 OXML 로 주입한다(python-docx 직접 API 없음 — 헬퍼 작성).
  음영이 과하면 **monospace 단락만으로도 충분**(음영은 선택).

#### 2.5 이미지 처리 (★ renderer 의 file:// 치환 전제)

- renderer 는 **상대 이미지 src 를 `file:///<base_dir 기준 절대경로>` URI 로 치환**한다.
  따라서 walk 에서 만나는 `img.src` 는 대개 `file:///…` 또는 원격 `http(s)://`.
- **`file:///` 인 경우:** URI → 로컬 경로 복원(`urllib.parse.urlparse` + `url2pathname`,
  또는 `urllib.request.url2pathname(urlparse(src).path)`; Windows 드라이브 문자 주의).
  파일이 존재하면 `doc.add_picture(path, width=Inches(...))` 로 임베드.
  본문 폭(A4 - 여백 ≈ 6.3in)을 넘으면 폭을 클램프.
- **원격/복원 실패/파일 없음:** 임베드하지 않고 **`alt` 텍스트**(없으면 `[이미지]`)를
  이탤릭 run 으로 대체. **네트워크 다운로드는 하지 않는다**(v1 비범위 — 오프라인·지연·
  보안 회피). 예외 없이 조용히 폴백.
- 이미지 임베드 중 python-docx 가 포맷 미지원 등으로 실패하면 catch → alt 폴백.

#### 2.6 하이퍼링크 처리

- python-docx 에 하이퍼링크 고수준 API 가 없다. 두 단계로 둔다:
  - **권장(정식):** OXML 헬퍼로 `w:hyperlink` 관계(relationship)를 추가해 클릭 가능한
    파란 밑줄 링크 run 생성. `href` 가 `file:///…page.md`(내부 링크 치환분)면 그대로 둔다.
  - **최소(허용):** 링크 텍스트를 일반 run 으로 쓰고 뒤에 ` (<href>)` 를 옅게 덧붙임.
    경계면(시그니처)에는 영향 없음 — core 내부 품질 선택. v1 은 최소 허용, 정식 권장.

#### 2.7 견고성 규칙 (필수)

- 빈/공백 `markdown_text` → `render` 가 빈 본문 → **빈 docx** 저장(예외 없음).
- 알 수 없는/매핑 없는 태그 → 자식 텍스트만 보존(건너뛰되 텍스트 유실 최소화).
- 어떤 walk 단계의 내부 예외도 **삼켜서** 변환을 계속한다(부분 결과라도 저장).
- 단, 마지막 `doc.save(out_path)` 의 OSError 만 전파(§2.1).
- `out_path.parent.mkdir(parents=True, exist_ok=True)` 로 부모 보장(write_markdown 대칭).

### 2.8 패키지 노출 (`__init__.py`)

`src/mdviewer/__init__.py` 에 추가:

```python
from mdviewer.exporter import markdown_to_docx
# __all__ 에 "markdown_to_docx" 추가
```

- PDF 내보내기는 **UI 전용**이라 core 노출 대상이 **아니다**(`__init__` 에 넣지 않음).
- core 노출은 `markdown_to_docx` **하나뿐**. 이게 ui-dev 가 import 하는 유일한 신규 심볼.

---

## 3. UI 계층 — PDF 내보내기 (★ ui-dev 구현, `main_window.py`)

PDF 는 `QWebEnginePage.printToPdf` 로 만든다. 핵심은 **화면 뷰(`self.view`)를 쓰지
않고**, `_doc_text` 를 **라이트 테마로 새로 렌더한 오프스크린 페이지**를 인쇄하는 것이다.
(다크로 보고 있어도 인쇄물은 라이트, 줌/스크롤 무관.)

### 3.1 왜 오프스크린 페이지인가

- `self.view.page().printToPdf` 를 쓰면 **현재 테마·줌·스크롤·WYSIWYG contentEditable**
  상태가 그대로 인쇄돼 결과가 비결정적이고 편집 잔재가 섞인다.
- 별도 `QWebEnginePage` 를 만들어 `wrap_document(body, dark=False)` HTML 을
  `setHtml` → `loadFinished` → `printToPdf` 하면 **깨끗한 라이트 인쇄본**이 보장된다.

### 3.2 비동기 생명주기 (printToPdf 는 비동기 — 단골 함정)

`setHtml` 도 `printToPdf` 도 비동기다. **페이지/콜백 객체의 참조를 살려두지 않으면
GC 로 사라져 인쇄가 조용히 실패**한다. 아래 단계와 멤버 보관을 지킨다.

```
[export_pdf()]                         (사용자 액션)
  1. _doc_text 최신화: _flush_pending_edit() (+ WYSIWYG면 캡처 후 진행, §5)
  2. 빈 문서면 안내 후 return (액션은 §4 에서 비활성이라 방어적)
  3. QFileDialog.getSaveFileName(*.pdf) → 취소면 return
  4. 재진입 가드: self._pdf_busy 가 True 면 "내보내는 중..." 안내 후 return
  5. _export_pdf_async(out_path) 호출
        ↓
[_export_pdf_async(out_path)]
  6. self._pdf_busy = True; 상태바 "PDF 내보내는 중..."
  7. body = render(self._doc_text, base_dir).html
     html = theme_mod.wrap_document(body, dark=False)   # ★ 라이트 강제
  8. page = QWebEnginePage(self)        # ★ self 부모 + 멤버로 보관(GC 방지)
     self._pdf_page = page
     page.settings().setAttribute(LocalContentCanAccessFileUrls, True)
     page.settings().setAttribute(LocalContentCanAccessRemoteUrls, True)
  9. page.loadFinished.connect(lambda ok: self._on_pdf_load_finished(ok, out_path))
 10. base = QUrl.fromLocalFile(str(base_dir) + "/")   # 상대 이미지/링크 해석
     page.setHtml(html, base)
        ↓ (로드 완료 시그널)
[_on_pdf_load_finished(ok, out_path)]
 11. ok 아니면: 정리(_finish_pdf) + 경고
 12. layout = QPageLayout(QPageSize(A4), Portrait, QMarginsF(12.7,12.7,12.7,12.7), Millimeter)
 13. page.printToPdf(str(out_path), layout)            # 비동기
     page.pdfPrintingFinished.connect(self._on_pdf_printing_finished)
        ↓ (인쇄 완료 시그널: filePath, success)
[_on_pdf_printing_finished(file_path, ok)]
 14. ok면 상태바 "PDF 저장됨: ..." / 아니면 QMessageBox 경고
 15. _finish_pdf(): self._pdf_page = None; self._pdf_busy = False   # 참조 해제·가드 해제
```

### 3.3 멤버 / 시그널 / 정리

- `self.__init__` 에 추가: `self._pdf_page = None`, `self._pdf_busy = False`.
- **페이지 객체는 반드시 `self._pdf_page` 로 보관**(로컬 변수만 두면 GC 로 인쇄 실패).
- 부모를 `self` 로 주어 윈도우 수명에 묶되, 완료 시 `_finish_pdf` 에서 `None` 대입해
  다음 내보내기를 위해 해제(매번 새 페이지 — 이전 상태 잔재 0).
- **재진입 가드** `_pdf_busy`: 인쇄 진행 중 재호출 차단(중복 페이지·경합 방지).
- `pdfPrintingFinished(str filePath, bool success)` 시그널로 완료 통지 → 사용자 알림.
- 모든 단계 try/except 로 감싸 실패 시 `_finish_pdf()` 로 **반드시 가드/참조 정리**
  (한 번 실패가 영구 busy 로 굳지 않게).

### 3.4 페이지 레이아웃 (고정값)

| 항목 | 값 |
|------|-----|
| 용지 | A4 (`QPageSize.PageSizeId.A4`) |
| 방향 | 세로(Portrait) |
| 여백 | 상하좌우 12.7mm(0.5in) 균일, `QPageLayout.Unit.Millimeter` |
| 배경 인쇄 | 켬(테마 배경/코드 음영 보존; printToPdf 는 기본 배경 인쇄) |

- import: `from PySide6.QtCore import QMarginsF`,
  `from PySide6.QtGui import QPageLayout, QPageSize`,
  `from PySide6.QtWebEngineCore import QWebEnginePage`.
- **라이트 강제**: `wrap_document(body, dark=False)` — 화면 `self._theme` 와 독립.

### 3.5 보안 설정 동일 적용

오프스크린 페이지에도 화면 뷰와 **동일한** WebEngine 보안 속성을 준다(안 주면 file://
출처 페이지가 로컬 이미지나 원격 배지를 차단해 PDF 에 이미지가 빠진다):

```python
s = page.settings()
s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
```

(현행 `main_window.__init__` 의 `self.view` 설정과 동일.)

---

## 4. UI 액션 / 메뉴 / 툴바 (★ ui-dev, `main_window.py`)

### 4.1 액션 (`_build_actions`)

```python
self.act_export_pdf = QAction("PDF로 내보내기...", self)
self.act_export_pdf.setToolTip("PDF로 내보내기")
self.act_export_pdf.setIcon(st.standardIcon(QStyle.StandardPixmap.SP_FileIcon))  # 신규 자산 0
self.act_export_pdf.triggered.connect(self.export_pdf)

self.act_export_docx = QAction("Word(.docx)로 내보내기...", self)
self.act_export_docx.setToolTip("Word 문서로 내보내기")
self.act_export_docx.triggered.connect(self.export_docx)
```

- **단축키 없음**(전역 충돌 회피, 메뉴/툴바 클릭 전용 — Phase 9 서식 액션과 동일 정책).
  필요 시 후보로 `Ctrl+E`(PDF)만 둘 수 있으나 v1 은 무단축.

### 4.2 메뉴 — 파일 메뉴에 "내보내기" 서브메뉴

`_build_menus` 의 파일 메뉴에서 저장 그룹 **아래** 서브메뉴로 묶는다:

```
파일(&F)
  새 문서
  열기...
  최근 파일(&R) ▸
  ───
  클립보드를 마크다운으로 붙여넣기
  저장
  다른 이름으로 저장...
  내보내기(&E) ▸          ← 신규 서브메뉴
      PDF로 내보내기...
      Word(.docx)로 내보내기...
  ───
  새로고침
  ───
  종료
```

```python
self.menu_export = m_file.addMenu("내보내기(&E)")
self.menu_export.addAction(self.act_export_pdf)
self.menu_export.addAction(self.act_export_docx)
```
(`addMenu` 위치는 `act_save_as` 다음, 첫 separator 앞.)

### 4.3 툴바 (`_build_toolbar`)

- 메인 툴바가 이미 빽빽하므로 **PDF 버튼 1개만** 추가(가장 흔한 작업), Word 는 메뉴 전용.
  [편집] 그룹 뒤 separator 다음에 `tb.addAction(self.act_export_pdf)` 권장.
- 또는 둘 다 메뉴 전용으로 두고 툴바는 미변경(ui-dev 재량 — 혼잡도 판단).

### 4.4 활성/비활성 (빈 문서 가드)

- **빈 문서**(`not self._doc_text.strip()`)면 두 액션 모두 **비활성**.
  업데이트 지점: 문서가 바뀌는 모든 곳에서 `_update_export_actions_enabled()` 호출
  — `_set_document` 직후, `_set_scratch`/`_new_scratch`/`_load_from_disk` 직후,
  `_on_editor_text_changed`/`_ingest_wysiwyg_html`(dirty 갱신과 같은 자리)에서.

```python
def _update_export_actions_enabled(self) -> None:
    has_doc = bool(self._doc_text.strip())
    self.act_export_pdf.setEnabled(has_doc)
    self.act_export_docx.setEnabled(has_doc)
```

> 편집 중 디바운스로 `_doc_text` 가 한 박자 늦을 수 있으나, 내보내기 진입 시
> `_flush_pending_edit()`(§5)로 최신화되므로 "비었는데 활성"이어도 무해
> (export 메서드가 다시 빈 문서를 방어).

### 4.5 기본 파일명 / QFileDialog 필터

```python
def _suggest_export_name(self, ext: str) -> Path:
    """내보내기 기본 파일명: title(첫 h1) → _path stem → '제목 없음'.<ext>."""
    base_dir = self._path.parent if self._path else self._scratch_base_dir()
    if self._path is not None:
        stem = self._path.stem
    else:
        # 첫 h1 을 제목으로(없으면 '제목 없음'). render 결과 title 재사용 가능.
        stem = (self._current_title() or "제목 없음")
    return base_dir / f"{stem}.{ext}"
```

- **PDF**: `QFileDialog.getSaveFileName(self, "PDF로 내보내기", str(_suggest_export_name("pdf")), "PDF 문서 (*.pdf);;모든 파일 (*.*)")`.
  확장자 없으면 `.pdf` 보정.
- **Word**: 필터 `"Word 문서 (*.docx);;모든 파일 (*.*)"`. 확장자 없으면 `.docx` 보정.
- `_current_title()`: 직전 렌더의 `RenderResult.title` 을 멤버(`self._doc_title`)로
  캐싱하거나, 내보내기 시 `render(self._doc_text, base_dir).title` 로 즉석 계산.
  (export_docx 는 어차피 core 가 다시 render 하므로 title 을 core 에 넘겨주면 일관.)

### 4.6 export_docx 흐름 (동기 — core 호출)

```python
def export_docx(self) -> None:
    self._prepare_doc_for_export()          # §5: flush (+ WYSIWYG 캡처는 §5.2 처리)
    if not self._doc_text.strip():
        self.statusBar().showMessage("내보낼 내용이 없습니다.", 3000); return
    out = QFileDialog.getSaveFileName(... "*.docx" ...)
    if not out: return
    path = Path(out); 
    if path.suffix == "": path = path.with_suffix(".docx")
    base_dir = self._path.parent if self._path else self._scratch_base_dir()
    title = self._current_title()
    try:
        markdown_to_docx(self._doc_text, path, base_dir, title=title)  # core(OSError 전파)
    except OSError as exc:
        QMessageBox.warning(self, "내보내기 실패", f"Word 문서를 저장할 수 없습니다:\n{exc}")
        return
    self.statusBar().showMessage(f"Word 문서로 내보냈습니다: {path}", 4000)
```

- core 가 없을 수 있는 **graceful import**(§9.3)를 두어 ui-dev 가 선개발 가능.

---

## 5. 내보내기 전 `_doc_text` 최신화 규칙 (★ 통합 버그 단골)

내보내기는 `_doc_text` 를 읽으므로, 편집 중 마지막 입력이 반영되지 않으면 **결과물에서
누락**된다. 두 surface 상태를 각각 처리한다.

### 5.1 Editor / Split (소스 편집기 디바운스)

- 디바운스 타이머 대기 중이면 `_flush_pending_edit()` 로 **즉시 `_doc_text` 확정**
  (기존 메서드 재사용: 타이머 stop + `_doc_text = editor.toPlainText()`).
- 이미 `_write_to`(저장)가 같은 패턴을 쓴다 — 내보내기도 동일하게 선행 호출.

### 5.2 WYSIWYG (contentEditable innerHTML 폴링)

- WYSIWYG 활성(`self._wysiwyg_active`) 중에는 최신 편집이 **다음 폴링 tick 전**이라
  `_doc_text` 에 없을 수 있다. 저장의 `_save_after_wysiwyg_capture()` 와 **동형**으로,
  내보내기도 **innerHTML 을 비동기 캡처한 뒤** 진행한다.

```python
def _prepare_doc_for_export(self) -> None:
    """편집 중 _doc_text 최신화(동기 경로). Editor/Split 의 디바운스 flush."""
    self._flush_pending_edit()

def export_pdf(self) -> None:
    if self._wysiwyg_active:
        self._export_after_wysiwyg_capture(self._do_export_pdf)  # 비동기 캡처 후
        return
    self._prepare_doc_for_export()
    self._do_export_pdf()

def _export_after_wysiwyg_capture(self, then) -> None:
    """WYSIWYG innerHTML 1회 캡처 → _ingest_wysiwyg_html → then() (저장 캡처와 동형)."""
    js_get = "(function(){var a=document.getElementById(%r);return a?a.innerHTML:null;})()" % _WYSIWYG_ROOT_ID
    def _cb(html):
        if isinstance(html, str): self._ingest_wysiwyg_html(html)
        then()
    try: self.view.page().runJavaScript(js_get, 0, _cb)
    except Exception: then()
```

- `export_docx` 도 동일하게 WYSIWYG 면 `_export_after_wysiwyg_capture(self._do_export_docx)`.
- **권장 리팩터**: 저장의 `_save_after_wysiwyg_capture` 와 이 캡처 로직을 **공용 헬퍼**
  (`_capture_wysiwyg_then(callback)`)로 묶어 중복 제거(선택, ui-dev 재량).

### 5.3 정리

| surface | 내보내기 진입 시 |
|---------|------------------|
| Preview | 추가 처리 없음(`_doc_text` 가 이미 최신) |
| Editor / Split | `_flush_pending_edit()` (디바운스 강제 commit) |
| WYSIWYG | innerHTML 비동기 캡처 → `_ingest_wysiwyg_html` → 그 콜백에서 실제 내보내기 |

---

## 6. 의존성 — `requirements.txt`

코어 계층으로 1줄 추가:

```
# --- Word(.docx) 내보내기 (코어 엔진) ---
python-docx>=1.1          # OOXML 문서 생성. lxml 의존(C 확장) — exporter.py 전용
```

### 6.1 코어 순수성에 미치는 영향 (lxml C 확장) 과 정당화

- `python-docx` 는 **lxml**(C 확장 = libxml2 바인딩)을 끌고 온다. 지금까지 코어는
  **순수 파이썬/번들안전**을 선호해 왔다(html2text 채택 사유 §06). lxml 은 그 결과
  **첫 네이티브 트랜지티브 의존**이다.
- **그래도 채택하는 이유:**
  - .docx(OOXML)는 사실상 python-docx 가 표준 생성 경로이며, 순수 파이썬 대안은
    품질/유지보수에서 열세다. 직접 OOXML zip 생성은 비용·버그 위험이 크다.
  - lxml 은 **wheel 로 광범위 배포**(Windows/macOS 휠 존재)되어 설치는 문제없다.
  - **격리:** lxml 의존은 `exporter.py` 안에 갇힌다. `renderer.py`/`file_watcher.py`/
    `theme.py` 등 **렌더 핫패스는 lxml 무관**. 내보내기를 안 쓰면 import 도 안 된다
    (지연 import 권장: `markdown_to_docx` 내부에서 `from docx import Document`).
  - PySide6(이미 Qt 네이티브 번들)에 비하면 lxml 추가 번들 표면은 작다.
- **PyInstaller 영향만 유의**(§7). 런타임 import 자체는 표준.
- 버전 정책: 코어 관례대로 **하한만**(`>=1.1`), 상한 느슨.

---

## 7. 패키징 영향 — `mdviewer.spec` (★ packager)

`python-docx` 는 **데이터 파일**(빈 문서 템플릿 `default.docx`, 기본 스타일 등)과
**lxml 네이티브 바이너리**를 가진다. `collect_all("PySide6")` 처럼 **명시 수집**이 필요하다.

### 7.1 spec 변경 (권장)

`mdviewer.spec` 의 `collect_all("PySide6")` 아래에 추가:

```python
from PyInstaller.utils.hooks import collect_all

# 기존
datas, binaries, hiddenimports = collect_all("PySide6")

# 신규: python-docx — 템플릿 default.docx 등 datas + 모듈 hiddenimports 수집.
_docx_datas, _docx_bins, _docx_hidden = collect_all("docx")  # 패키지명 'docx'
datas += _docx_datas
binaries += _docx_bins
hiddenimports += _docx_hidden

# lxml 네이티브 — PyInstaller 기본 훅이 대개 처리하나 명시로 안전.
_lxml_datas, _lxml_bins, _lxml_hidden = collect_all("lxml")
datas += _lxml_datas
binaries += _lxml_bins
hiddenimports += _lxml_hidden
```

> ⚠️ `collect_all` 의 인자는 **import 이름**이다. python-docx 의 import 이름은 **`docx`**
> (배포명 `python-docx`와 다름). `collect_all("docx")` 가 맞다.

### 7.2 검증 포인트 (packager — 빌드 후 frozen 스모크)

- frozen exe 에서 `from docx import Document; Document().save(tmp)` 가 동작하는지.
  (`default.docx` 템플릿 datas 누락 시 `PackageNotFoundError`/템플릿 못 찾음으로 실패.)
- lxml `import lxml.etree` 가 frozen 에서 import 되는지(네이티브 .pyd 번들 확인).
- 실제 `markdown_to_docx` 1회 실행 → .docx 생성 → 파일 크기 > 0 확인.
- PDF 는 신규 번들 의존 **없음**(printToPdf 는 이미 번들된 QtWebEngine 기능) — 회귀 스모크만.

---

## 8. 파일별 변경 목록 & 담당

| 파일 | 변경 | 담당 |
|------|------|------|
| `src/mdviewer/exporter.py` | **신규** — `markdown_to_docx` + HTML walk 매핑 + OXML 헬퍼 | core-engine-dev |
| `src/mdviewer/__init__.py` | `markdown_to_docx` 루트 export + `__all__` 추가 | core-engine-dev |
| `requirements.txt` | `python-docx>=1.1` 추가 | architect ✓(이 문서) → core-engine-dev 반영 |
| `src/mdviewer/main_window.py` | PDF 오프스크린 내보내기(`export_pdf`/`_export_pdf_async`/콜백/`_pdf_page`/`_pdf_busy`), `export_docx`, 내보내기 액션·"내보내기" 서브메뉴·(툴바 PDF), 빈 문서 게이팅, 기본 파일명, flush/WYSIWYG 캡처 규칙, graceful import | ui-dev |
| `mdviewer.spec` | `collect_all("docx")` + `collect_all("lxml")` 수집 추가 | packager |
| `tests/test_export.py` | **신규** — docx 생성/매핑/예외 단위 + (선택) PDF 스모크 | QA |
| `pyproject.toml` | (선택) 의존성 미러 시 `python-docx` 추가 | core-engine-dev |

---

## 9. 경계면 / 병렬 작업 (누가 무엇을, 어디서 만나는가)

### 9.1 빌드 순서

```
Step 1  architect    ──▶ 본 설계(10) 확정 → core/ui 에 §2 시그니처 통지
Step 2  (병렬)
  ├ core-engine-dev ──▶ exporter.py: markdown_to_docx (render 호출 → HTML walk → docx)
  │                     __init__ export, requirements.txt 반영
  └ ui-dev          ──▶ main_window.py: PDF 오프스크린 비동기 + export_docx 호출 흐름
                        + 액션/메뉴/툴바/게이팅/flush  (graceful import 로 선개발)
Step 3  QA           ──▶ docx 단위(매핑/예외/round-trip) + PDF 스모크 + 경계면 shape 대조
Step 4  packager     ──▶ collect_all("docx"/"lxml") 추가 → frozen import/생성 검증
```

### 9.2 경계면 (코드가 만나는 단 두 지점)

- **A. core 경계:** `exporter.markdown_to_docx(markdown_text, out_path, base_dir, *, title=None) -> None`.
  - ui-dev 는 이 **시그니처만** 의존한다. 반환 None, **OSError 만 전파**, 그 외 비전파.
  - core-engine-dev 는 내부에서 **`renderer.render`(기존 계약) 만** 의존한다. 새 렌더 API 불필요.
- **B. 렌더 경계(기존):** PDF 경로의 `render(self._doc_text, base_dir).html` 은 이미 확정된
  계약(`RenderResult.html`)을 그대로 쓴다. **신규 계약 없음** → ui-dev 가 core 완성 전에도
  실제 render 로 PDF 를 완성할 수 있다(PDF 는 core 신규 코드 0).

> 즉, **PDF 는 ui-dev 단독으로 끝까지 완성 가능**(core 의존 0). **Word 만** core 경계 A
> 에 의존한다. 두 갈래가 사실상 독립이라 병렬성이 높다.

### 9.3 graceful import (ui-dev 선개발용 — 기존 패턴 확장)

`main_window.py` 코어 import 블록에 추가:

```python
try:
    from .exporter import markdown_to_docx  # type: ignore
    _EXPORT_AVAILABLE = True
except Exception:
    _EXPORT_AVAILABLE = False
    def markdown_to_docx(markdown_text, out_path, base_dir, *, title=None):  # type: ignore
        raise OSError("Word 내보내기 모듈(exporter.py)이 아직 연결되지 않았습니다.")
```

- `_EXPORT_AVAILABLE` 가 False 면 `act_export_docx` 를 비활성(또는 안내). **PDF 는
  영향 없음**(core 무관). 이 폴백으로 ui-dev 는 exporter.py 완성 전 PDF/UI 를 전부 만든다.

---

## 10. QA 검증 포인트 (★ qa-verifier)

### 10.1 core 단위 — `markdown_to_docx` (GUI 불필요)

- **기본 생성:** 대표 마크다운(헤딩/단락/굵게·기울임·인라인코드/링크/목록(중첩)/인용/
  표/코드펜스/hr/task) → `markdown_to_docx(md, tmp/"out.docx", base_dir)` → 파일 존재,
  크기 > 0. `docx.Document(out)` 로 다시 열어 단락/표 개수·텍스트 포함 검증.
- **매핑 대조:** 열린 Document 에서 — h1 이 Heading 1 스타일, bold run 의 `.bold is True`,
  표가 `len(doc.tables)==1` 및 셀 텍스트 일치, 코드블록 텍스트(개행 포함) 보존,
  task 항목에 `☑/☐` 접두 존재.
- **이미지:** 로컬 실제 PNG(작은 더미) → `doc.add_picture` 임베드 성공(`len(inline_shapes) >= 1`);
  존재하지 않는 file:// → 예외 없이 alt 텍스트 폴백; 원격 http → 다운로드 없이 alt 폴백.
- **견고성(비전파):** 빈 문자열/`None`/깨진 HTML 유발 입력 → 예외 없이 .docx 저장.
- **I/O 전파:** 쓰기 불가 경로(읽기전용/존재하는 디렉터리명) → **`OSError` 전파** 확인.
- **title:** `title="X"` → `Document(out).core_properties.title == "X"`.

### 10.2 PDF 스모크 (UI — 오프스크린)

- 앱 실행 → 샘플 .md 열기 → `export_pdf` 프로그램 호출(또는 액션 트리거) → 임시 .pdf
  경로 지정 → `pdfPrintingFinished(success=True)` 수신 → 파일 존재, 크기 > 0, 선두 바이트
  `%PDF` 확인.
- **다크 무관:** 테마를 다크로 두고 내보내도 PDF 가 생성되는지(라이트 강제 경로 동작).
- **재진입 가드:** 진행 중 재호출이 차단되는지(`_pdf_busy`), 완료 후 `_pdf_page is None`.
- **빈 문서:** 빈 문서에서 액션 비활성(또는 export 가 조용히 방어) 확인.

### 10.3 경계면 shape 대조 (통합 버그 차단)

- `inspect.signature(markdown_to_docx)` 가 §2.1 과 **정확히 일치**
  (`(markdown_text, out_path, base_dir, *, title=None)`, 반환 None).
- core 가 OSError **만** 전파하고 변환 오류는 비전파하는지(대조표 §2.2)를 의도적 깨진
  입력 + 쓰기불가 경로 2케이스로 교차 확인.
- ui 의 `export_docx` 가 `try/except OSError` 로 감싸 QMessageBox 폴백하는지(소스 검토).
- **flush 정합:** Editor 에서 디바운스 대기 중 즉시 export → 마지막 타이핑이 결과물에
  포함되는지(누락 = §5 위반). WYSIWYG 에서 마지막 글자 입력 직후 export → 캡처 후 포함.

---

## 11. 확장 후보 (범위 외 — 표기만)

- 페이지 머리글/꼬리말·페이지 번호·표지/목차 페이지, 용지/여백 설정 UI.
- 원격 이미지 다운로드 후 임베드, 코드블록 구문 색상 docx 재현(런별 색).
- docx 하이퍼링크 정식(`w:hyperlink`) 전면 적용, 사용자 스타일 템플릿(.dotx) 선택.
- PDF 책갈피/링크 트리, PDF 암호화, "내보내기 후 자동 열기", 일괄 내보내기.
- HTML 단독 내보내기(`wrap_document` 결과를 .html 로 저장 — 거의 무료, 후보 1순위).

이상은 표준 뷰어 범위 밖이므로 후보로만 둔다.
```