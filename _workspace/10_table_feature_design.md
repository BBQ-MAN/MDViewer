# MDViewer 표(table) 삽입 + 행/열 편집 설계 (Phase 10)

> 작성: architect · 2026-06-25
> 범위: 워드프로세서식 **표 삽입 + 행/열 편집** UI 추가. GFM 파이프 표는 이미
> 렌더링/역변환됨(`renderer.render` GFM table, `html_to_markdown` html2text 파이프표).
> 이 문서는 **계약(contract)** 이다. **ui-dev** 가 이 계약대로 `main_window.py`
> 단일 파일을 확장한다. **core(renderer/file_watcher) 변경은 불필요**(§g, §9).
> 기준 문서: `09_wordprocessor_ui_design.md`(통합 서식 디스패치·서식 툴바·게이팅),
> `08_wysiwyg_feature_design.md`(WYSIWYG 진입/폴링/캡처/역렌더 게이트),
> `07_editor_feature_design.md`(뷰모드·디바운스·편집↔감시), 현행 `main_window.py`/`renderer.py`.

빌드 인터프리터: `C:\Users\BBQMAN\miniconda3\python.exe` (PySide6 6.11.1).

---

## 0. 핵심 설계 결정 요약 (못박음)

| 항목 | 결정 |
|------|------|
| 진입 패턴 | **기존 통합 서식 디스패치 그대로 확장**. 같은 액션이 surface 에 따라 분기(`_dispatch_*` → `_editor_*` / `_wysiwyg_*`). 신규 패턴 도입 0 |
| 표 삽입 | 서식 툴바 "표" 버튼 → 행×열 다이얼로그(`QInputDialog.getInt` 2회, §a.1) → Editor=GFM 골격(QTextCursor), WYSIWYG=`<table>` insertHTML(JS) |
| 행/열 편집 | 4개 액션(행 위/아래 추가는 단순화하여 "행 추가"=아래, "열 추가"=오른쪽). Editor=표블록 파싱·재구성, WYSIWYG=DOM 조작 JS |
| 표 그룹 위치 | 서식 툴바 끝에 `addSeparator()` 후 [표 삽입 \| 행 추가·행 삭제·열 추가·열 삭제] |
| 행/열 버튼 게이팅 | **편집 surface(Editor/Split/WYSIWYG)에서만 활성**(Preview 숨김은 서식 툴바 전체 게이팅으로 이미 처리). 표 밖이면 동작 시 상태바 안내(비활성 토글은 §d 선택) |
| Editor 표 골격 | 양끝 파이프 포함, 헤더행 + 구분행(`---`) + 빈 본문행. `beginEditBlock`으로 undo 1스텝. 삽입 후 첫 셀(헤더 첫 칸)에 커서 |
| Editor 행/열 편집 | 커서가 속한 **연속 파이프 라인 블록**을 파싱(헤더/구분/본문 식별)→행/열 조작→재구성→블록 치환(undo 1스텝). 표 밖이면 안내 |
| WYSIWYG 표 삽입 | `document.execCommand('insertHTML', false, tableHtml)`. thead/tbody, 빈 td(`&nbsp;`로 클릭영역 확보). 삽입 후 `_capture_wysiwyg_once` |
| WYSIWYG 행/열 편집 | selection anchor → `closest('td,th')` → 행/열 인덱스 계산 → DOM insertRow/deleteRow/cell 삽입·삭제. 삽입 후 캡처 |
| core 변경 | **불필요**. render(GFM table)·html_to_markdown(파이프표 역변환) 이미 검증 |
| 신규 자산 | **0.** JS 는 파이썬 문자열 리터럴 `runJavaScript`. 아이콘은 텍스트 라벨(또는 유니코드) |
| 영향 파일 | **`main_window.py` 만**. settings/theme/renderer/file_watcher/requirements/spec 무변경 |
| 라운드트립 한계 | WYSIWYG 표→html2text→마크다운: 정렬·병합셀 한계 문서화(§g). Editor 삽입은 단정한 GFM |

---

## (a) 표 삽입 — 양 surface 동작 · 다이얼로그

### a.1 행×열 다이얼로그 (공통 진입)

표 크기 입력은 surface 무관 공통이므로 **디스패치 진입점에서 1회** 받는다.
`QInputDialog.getInt` 2회(작은 다이얼로그, 신규 위젯 0). 취소 시 전체 중단.

```python
def fmt_table(self) -> None:
    """서식 툴바 "표" 버튼 → 행×열 입력 후 활성 surface 로 삽입 분기."""
    self._dispatch_table_insert()

def _dispatch_table_insert(self) -> None:
    if not self._is_edit_surface():
        return                                   # Preview → no-op(툴바 숨김, 방어적)
    rows, ok = QInputDialog.getInt(self, "표 삽입", "행 수(본문):", 2, 1, 50, 1)
    if not ok:
        return
    cols, ok = QInputDialog.getInt(self, "표 삽입", "열 수:", 2, 1, 20, 1)
    if not ok:
        return
    if self._is_wysiwyg_surface():
        self._wysiwyg_insert_table(rows, cols)   # JS insertHTML(§a.3)
    elif self._is_source_editor_surface():
        self._editor_insert_table(rows, cols)    # GFM 골격(§a.2)
```

> **rows 의미:** "본문 행 수"(헤더는 별도). 사용자가 2 입력 → 헤더 1 + 본문 2 + 구분 1.
> 직관 보정: 라벨에 "(본문)" 명시. 최소 본문행 1 강제(빈 표 방지, §f).
> 단일 다이얼로그(QDialog 서브클래스 + 두 스핀박스)도 가능하나 v1 은 getInt 2회로
> 신규 위젯 0 유지(ui-dev 재량 — 권장은 getInt 2회).

### a.2 소스 편집기(Editor/Split) — GFM 표 골격 삽입

커서 위치에 **깔끔한 GFM 표**를 삽입한다. 양끝 파이프, 균일 폭(헤더 라벨/구분선
길이 일치), 헤더행 + 구분행 + 빈 본문행. 삽입 후 **헤더 첫 셀**에 커서.

골격 생성 규칙(결정적):
- 헤더 셀: `열1`, `열2`, … (1-based). 셀 폭 = `max(len("열N"), 3)` → 최소 3(구분선 `---` 폭).
- 구분행: 각 열에 `---`(폭 맞춤, 기본 좌측정렬 — 정렬 표기 없음). v1 정렬 미지정.
- 본문행: 각 셀 공백(폭 맞춤). rows 개수만큼.
- **앞뒤 개행 보정:** 커서가 줄 중간/비빈 줄이면 표 앞에 `\n` 삽입(표는 블록). 표 뒤 `\n`.
  → 인접 텍스트와 붙어 GFM 표 파싱이 깨지지 않게(표 위아래 빈 줄 권장).

생성 예(rows=2, cols=3):

```
| 열1 | 열2 | 열3 |
| --- | --- | --- |
|     |     |     |
|     |     |     |
```

```python
def _editor_insert_table(self, rows: int, cols: int) -> None:
    """커서 위치에 GFM 표 골격 삽입(undo 1스텝). 삽입 후 헤더 첫 셀에 커서."""
    cols = max(1, cols)
    rows = max(1, rows)                           # 본문행 ≥ 1(빈 표 방지)
    headers = [f"열{i+1}" for i in range(cols)]
    widths = [max(len(h), 3) for h in headers]    # 최소 3(--- 폭)
    def _row(cells: list[str]) -> str:
        padded = [c.ljust(widths[i]) for i, c in enumerate(cells)]
        return "| " + " | ".join(padded) + " |"
    header_line = _row(headers)
    sep_line = "| " + " | ".join("-" * widths[i] for i in range(cols)) + " |"
    body_lines = [_row([""] * cols) for _ in range(rows)]
    table_lines = [header_line, sep_line] + body_lines
    table = "\n".join(table_lines)

    cur = self.editor.textCursor()
    cur.beginEditBlock()
    try:
        # 블록 시작이 아니거나 현재 줄에 내용이 있으면 표 앞에 개행(표는 블록).
        block_text = cur.block().text()
        at_block_start = cur.positionInBlock() == 0
        prefix = "" if (at_block_start and not block_text.strip()) else "\n"
        cur.insertText(prefix + table + "\n")
    finally:
        cur.endEditBlock()
    # 커서를 헤더 첫 셀("열1"의 1)에 배치: 삽입 시작 + prefix + "| " 다음.
    self._place_cursor_in_first_cell(cur, len(prefix), header_line)
    self.editor.setFocus()
```

> **커서 배치(`_place_cursor_in_first_cell`):** `cur.position()`은 삽입 직후 표 끝.
> 헤더 첫 셀로 되돌리려면 (삽입 전 위치 + prefix길이 + 2["| "])로 setPosition,
> 그리고 헤더 라벨 길이만큼 KeepAnchor 로 선택(바로 덮어쓰기 가능 — 링크 placeholder
> 패턴 §5.5 재사용). 구현 단순화: 삽입 전 `start=cur.position()` 저장 후
> `cur.setPosition(start + len(prefix) + 2); cur.setPosition(+len("열1"), KeepAnchor)`.
> dirty/렌더는 `insertText`→`textChanged`→디바운스로 **자동**(별도 호출 불필요, §5.3 원리).

### a.3 WYSIWYG — 편집 가능한 `<table>` insertHTML

커서 위치에 빈 `<table>`(thead/tbody)을 `insertHTML` 로 삽입. 셀은 article 의
`contenteditable` 을 상속하므로 클릭 후 바로 타이핑. 빈 td 는 클릭영역 확보 위해
`&nbsp;` 1개(또는 빈 채로 — 빈 td 도 클릭 가능하나 높이 0 회피 위해 nbsp 권장).

```python
def _wysiwyg_insert_table(self, rows: int, cols: int) -> None:
    """WYSIWYG editable 에 빈 <table> 삽입(execCommand insertHTML) 후 캡처."""
    if not self._wysiwyg_active:
        return
    cols = max(1, cols); rows = max(1, rows)
    th = "".join("<th>&nbsp;</th>" for _ in range(cols))
    td = "".join("<td>&nbsp;</td>" for _ in range(cols))
    body = "".join(f"<tr>{td}</tr>" for _ in range(rows))
    html = (
        "<table class='md-table'><thead><tr>" + th + "</tr></thead>"
        "<tbody>" + body + "</tbody></table><p>&nbsp;</p>"
    )
    self._exec_format("insertHTML", html)        # 기존 래퍼 재사용(캡처 포함)
```

> `_exec_format("insertHTML", html)` 은 기존 execCommand 래퍼이므로 **삽입 직후
> `_capture_wysiwyg_once(final=False)` 가 자동 호출**(폴링 보조). 라운드트립:
> `html_to_markdown`(html2text) 이 `<table>`→파이프표로 변환 → `_doc_text` 갱신 →
> 저장 시 GFM. 표 뒤 `<p>` 는 표 다음에 이어 쓸 단락 확보(execCommand insertHTML 의
> 커서가 표 뒤 단락에 놓이도록).
> ⚠️ `insertHTML` 은 execCommand 표준 명령이나 일부 Chromium 버전에서 sanitize 차이가
> 있을 수 있다. 만약 표가 안 들어가면 §a.4 커스텀 JS(range.insertNode)로 폴백.

### a.4 WYSIWYG insertHTML 폴백(선택 — execCommand 실패 대비)

`insertHTML` 이 환경에서 불안정하면 `_wysiwyg_inline_code` 와 동일한 커스텀 JS 패턴
(selection range 에 createElement→insertNode)으로 표를 삽입한다. v1 은 execCommand
우선, QA 에서 실삽입 확인 후 필요 시 폴백 채택(ui-dev/QA 협의).

---

## (b) 소스 편집기 표블록 파서 · 행/열 알고리즘

### b.1 표블록 식별 규칙 (파서 계약)

커서가 속한 줄에서 위·아래로 **연속된 "표 라인"** 을 모아 표블록 경계를 정한다.

- **표 라인 정의:** `strip()` 후 `|` 를 1개 이상 포함하는 비어있지 않은 줄.
  (GFM 표는 보통 양끝 파이프이나, 느슨하게 "파이프 포함 라인"으로 식별 — 사용자가
  양끝 파이프를 생략했어도 잡되, 재구성 시 양끝 파이프로 정규화.)
- **표블록 = 커서 줄 포함, 위로/아래로 표 라인이 끊길 때까지** 확장한 연속 줄 범위
  `[top_block, bottom_block]`(blockNumber).
- **구분행 식별:** 표블록 내에서 셀이 모두 `^:?-{1,}:?$`(공백 트림 후) 인 줄.
  보통 2번째 줄. 구분행이 없으면 "유효 GFM 표 아님" → §f 처리(첫 줄=헤더 간주 폴백
  또는 안내). v1: 구분행이 있어야 행/열 편집 활성, 없으면 상태바 안내.
- **표 밖 판정:** 커서 줄이 표 라인이 아니면(파이프 없음) → 표블록 없음 → 안내.

```python
_PIPE_LINE_RE = re.compile(r"\|")                          # 파이프 포함 = 표 라인 후보
_SEP_CELL_RE = re.compile(r"^\s*:?-{1,}:?\s*$")            # 구분행 셀(정렬 콜론 허용)

def _find_table_block(self) -> "TableBlock | None":
    """커서가 속한 표블록을 찾아 파싱한다. 표 밖이면 None."""
    doc = self.editor.document()
    cur = self.editor.textCursor()
    n = cur.blockNumber()
    if "|" not in doc.findBlockByNumber(n).text():
        return None                                        # 커서 줄이 표 라인 아님
    top = n
    while top - 1 >= 0 and "|" in doc.findBlockByNumber(top - 1).text() \
            and doc.findBlockByNumber(top - 1).text().strip():
        top -= 1
    last = doc.blockCount() - 1
    bottom = n
    while bottom + 1 <= last and "|" in doc.findBlockByNumber(bottom + 1).text() \
            and doc.findBlockByNumber(bottom + 1).text().strip():
        bottom += 1
    lines = [doc.findBlockByNumber(i).text() for i in range(top, bottom + 1)]
    return self._parse_table_lines(top, bottom, lines)
```

### b.2 셀 분해 / 재구성

- **셀 분해(`_split_cells`):** 줄을 `strip()` → 양끝 파이프 제거 → `|` 로 split →
  각 셀 `strip()`. 빈 양끝(양끝 파이프로 생긴 빈 토큰)은 제거.
  주의: 이스케이프된 `\|`(셀 내 리터럴 파이프)는 v1 미지원(분리됨) — §f/§g 한계.
- **표 모델(`TableBlock`):** `top:int`, `bottom:int`, `header:list[str]`,
  `aligns:list[str]`(구분행 원형 보존: `---`/`:--`/`--:`/`:-:`), `body:list[list[str]]`,
  `ncols:int`. 구분행 인덱스(보통 1) 기억.
- **재구성(`_render_table`):** 각 열 폭 = `max(셀 표시폭)`(유니코드 폭 §f 고려 → v1 은
  `len()` 기반, 폭 정밀맞춤은 비목표) → 헤더/구분/본문 줄을 양끝 파이프로 직렬화.
  열 추가/삭제로 ncols 가 바뀌면 모든 행 셀 개수를 ncols 로 맞춤(부족=빈셀, 초과=절단).

```python
@dataclass
class TableBlock:
    top: int                 # 표 시작 blockNumber
    bottom: int              # 표 끝 blockNumber
    header: list[str]
    aligns: list[str]        # 구분행 셀 원형(정렬 보존)
    body: list[list[str]]    # 본문 행들(각 행 = 셀 리스트)
    ncols: int
```

> 파서/재구성은 **순수 함수**로 분리(테스트 용이 — QA 가 GUI 없이 검증). ui-dev 가
> `main_window.py` 안 정적 메서드/모듈 함수로 둔다(core 아님, UI 보조 로직).

### b.3 행/열 조작 알고리즘 (Editor)

커서가 속한 **본문 행/열 인덱스**를 계산해 조작한다. 커서가 헤더/구분행에 있으면
행 조작은 본문 끝/시작 기준으로 보정(아래 규칙).

- **행 추가:** 커서가 속한 본문 행 **아래**에 빈 행 삽입. 커서가 헤더/구분행이면
  본문 맨 위에 삽입. 본문이 비어 있으면 1행 추가.
- **행 삭제:** 커서가 속한 본문 행 삭제. **본문이 1행뿐이면 삭제 거부**(빈 표 방지,
  안내) — 헤더+구분만 남는 표는 허용하되 v1 은 최소 본문 1행 유지. 커서가 헤더/구분이면
  안내("본문 행에 커서를 두세요").
- **열 추가:** 커서가 속한 셀 **오른쪽**에 열 삽입(헤더=`열N`, 본문/구분=빈/`---`).
  커서 열 판정 불가(헤더 밖 등)면 맨 오른쪽에 추가.
- **열 삭제:** 커서가 속한 셀의 열 삭제. **열이 1개뿐이면 거부**(빈 표 방지, 안내).

커서 행/열 인덱스 계산:
- 행: `cur.blockNumber() - table.top` → 표 내 라인 인덱스. 구분행 인덱스 제외하고
  본문 인덱스로 환산(헤더=라인0, 구분=라인1, 본문=라인2.. → body_idx=line-2).
- 열: 커서 `positionInBlock` 을 줄 텍스트의 파이프 위치들과 비교해 몇 번째 셀 구간인지
  계산(`_cursor_col_index(line_text, pos)`). 파이프 경계 사이 구간 인덱스.

```python
def _editor_table_op(self, op: str) -> None:
    """op ∈ {'row_add','row_del','col_add','col_del'}. 표 밖이면 안내."""
    tb = self._find_table_block()
    if tb is None:
        self.statusBar().showMessage("표 안에 커서를 두고 사용하세요.", 3000)
        return
    cur = self.editor.textCursor()
    line_idx = cur.blockNumber() - tb.top
    col_idx = self._cursor_col_index(
        self.editor.document().findBlockByNumber(cur.blockNumber()).text(),
        cur.positionInBlock(), tb.ncols)
    changed = self._apply_table_op(tb, op, line_idx, col_idx)   # 순수 함수(모델 변형)
    if changed is None:
        return                                                  # 거부(안내는 내부에서)
    self._replace_table_block(tb, self._render_table(tb))       # 블록 치환(undo 1스텝)
    self.editor.setFocus()

def _replace_table_block(self, tb: TableBlock, new_text: str) -> None:
    """[tb.top, tb.bottom] 블록 범위를 new_text 로 치환(undo 1스텝)."""
    doc = self.editor.document()
    start = QTextCursor(doc.findBlockByNumber(tb.top))
    start.movePosition(QTextCursor.MoveOperation.StartOfBlock)
    end = QTextCursor(doc.findBlockByNumber(tb.bottom))
    end.movePosition(QTextCursor.MoveOperation.EndOfBlock)
    cur = self.editor.textCursor()
    cur.beginEditBlock()
    try:
        cur.setPosition(start.position())
        cur.setPosition(end.position(), QTextCursor.MoveMode.KeepAnchor)
        cur.insertText(new_text)                                # 선택 치환
    finally:
        cur.endEditBlock()
```

> `_apply_table_op` 는 **TableBlock 모델을 변형하는 순수 로직**(거부 시 None +
> 상태바 안내 신호). `insertText` 가 `textChanged` → dirty + 디바운스 렌더 자동.
> 치환 후 커서 위치 보정은 best-effort(v1: 표 시작 부근 유지 — 정밀 셀 추적은 §확장).

---

## (c) WYSIWYG DOM 행/열 JS 설계

WYSIWYG 는 contenteditable DOM 직접 조작이 가장 자연스럽다. selection anchor →
`closest('td,th')` → 소속 `<table>`/행/열 인덱스 계산 → DOM API 로 조작. 조작 후
`_capture_wysiwyg_once(final=False)` 로 `_doc_text` 동기화.

진입(파이썬):

```python
def _wysiwyg_table_op(self, op: str) -> None:
    """op ∈ {'row_add','row_del','col_add','col_del'}. JS 로 DOM 조작 후 캡처."""
    if not self._wysiwyg_active:
        return
    js = _WYSIWYG_TABLE_JS % op                  # 단일 JS 함수에 op 전달(아래)
    try:
        self.view.page().runJavaScript(js)
    except Exception:
        pass
    self._capture_wysiwyg_once(final=False)      # _doc_text 동기화(폴링 보조)
```

JS 개요(파이썬 문자열 리터럴 — 신규 자산 0). 단일 IIFE 가 op 분기:

```javascript
(function(){
  var sel = window.getSelection();
  if(!sel || sel.rangeCount===0) return;
  var node = sel.anchorNode;
  var cell = node && (node.nodeType===1 ? node : node.parentElement);
  cell = cell && cell.closest('td,th');
  if(!cell) return;                              // 표 밖 → no-op
  var row = cell.parentElement;                  // <tr>
  var table = cell.closest('table');
  if(!table) return;
  var colIndex = Array.prototype.indexOf.call(row.cells, cell);
  var op = '%s';
  function newCell(tag){ var c=document.createElement(tag); c.innerHTML='&nbsp;'; return c; }
  if(op==='row_add'){
    var clone = document.createElement('tr');
    for(var i=0;i<row.cells.length;i++) clone.appendChild(newCell('td'));
    row.parentElement.insertBefore(clone, row.nextSibling);   // 아래 삽입
  } else if(op==='row_del'){
    // 헤더행(부모가 thead)이면 거부. tbody 행이 1개뿐이면 거부.
    var inThead = row.parentElement.tagName==='THEAD';
    var tbody = table.tBodies[0];
    if(inThead) return;
    if(tbody && tbody.rows.length<=1) return;     // 빈 표 방지
    row.parentElement.removeChild(row);
  } else if(op==='col_add'){
    // 모든 행의 colIndex 다음에 셀 삽입(thead=th, tbody=td).
    var allRows = table.rows;
    for(var r=0;r<allRows.length;r++){
      var rr=allRows[r];
      var tag = (rr.parentElement.tagName==='THEAD') ? 'th':'td';
      var ref = rr.cells[colIndex] ? rr.cells[colIndex].nextSibling : null;
      rr.insertBefore(newCell(tag), ref);
    }
  } else if(op==='col_del'){
    if(table.rows[0] && table.rows[0].cells.length<=1) return; // 빈 표 방지
    var allRows2 = table.rows;
    for(var r2=0;r2<allRows2.length;r2++){
      var c=allRows2[r2].cells[colIndex];
      if(c) allRows2[r2].removeChild(c);
    }
  }
})()
```

> **인덱스 정합:** `row.cells` 는 해당 행의 td/th 컬렉션이라 colIndex 가 행마다 일치
> (rowspan/colspan 없을 때). v1 은 **병합셀 미지원**(§g) → 단순 사각 표만 안전.
> 조작 후 캡처가 `html_to_markdown` 으로 파이프표 재생성 → `_doc_text`/dirty 갱신.
> 거부(빈 표/표 밖)는 JS 가 조용히 no-op → 파이썬에서 별도 안내가 어려우므로(비동기),
> v1 은 캡처 후 무변화면 그대로 둔다(상태바 안내는 Editor 경로에만, §d 참조).

---

## (d) 툴바 / 게이팅

### d.1 서식 툴바 표 그룹 (배치)

`_build_format_toolbar` 끝(서식 지우기 다음)에 `addSeparator()` 후 표 그룹 추가.

```
… [링크] [서식 지우기]  ──(separator)──  [표]  [행+] [행−] [열+] [열−]
```

```python
# _build_format_toolbar() 끝부분에 추가:
tb.addSeparator()
self.act_fmt_table     = self._mk_fmt("표",   self.fmt_table,        "표 삽입(행×열)")
self.act_fmt_row_add   = self._mk_fmt("행+",  self.fmt_table_row_add,  "행 추가(아래)")
self.act_fmt_row_del   = self._mk_fmt("행−",  self.fmt_table_row_del,  "행 삭제(커서 행)")
self.act_fmt_col_add   = self._mk_fmt("열+",  self.fmt_table_col_add,  "열 추가(오른쪽)")
self.act_fmt_col_del   = self._mk_fmt("열−",  self.fmt_table_col_del,  "열 삭제(커서 열)")
for a in (self.act_fmt_table, self.act_fmt_row_add, self.act_fmt_row_del,
          self.act_fmt_col_add, self.act_fmt_col_del):
    tb.addAction(a)
```

라벨은 텍스트(신규 자산 0). 유니코드 선호 시 행+=`⊞`, 열±=화살표 등 가능하나 가독성
위해 한글/기호 텍스트 권장. 표 삽입은 "표"(또는 `▦`).

### d.2 게이팅

- **서식 툴바 전체:** 이미 `_apply_view_mode` 의 `edit_surface`(Editor/Split/WYSIWYG)
  에서만 표시. 표 그룹도 같은 툴바이므로 **추가 게이팅 불필요**(Preview 숨김 자동).
- **행/열 4버튼:** 편집 surface 에선 항상 활성(클릭 시 표 밖이면 Editor=상태바 안내,
  WYSIWYG=조용한 no-op). **표 삽입("표")** 도 편집 surface 에서 항상 활성.
- **표 컨텍스트 동적 토글(선택 — 확장):** 커서가 표 안일 때만 행/열 4버튼을 활성화하는
  것은 v1 범위 외(에디터 cursorPositionChanged 연동 비용). v1 은 **항상 활성 + 동작 시
  안내**. ui-dev 재량으로 §확장 채택 가능.

> `_apply_view_mode` 에서 별도 코드 추가 **불필요**(표 액션은 서식 툴바에 포함되어
> 툴바 단위 가시성으로 자동 게이팅). `act_fmt_clear` 처럼 surface 별 enable 분기가
> **필요 없다**(행/열은 양 편집 surface 모두 동작).

---

## (e) 통합 디스패치 확장 지점 (기존 패턴에 얹음)

기존 `09` 의 디스패치 패턴을 **그대로 따른다**. 신규 의미 슬롯 `fmt_table*` →
디스패처 → surface 분기. 추가/변경 지점만 명시:

| 추가 위치 | 내용 |
|----------|------|
| `_build_actions` 또는 `_build_format_toolbar` | 표 5개 `QAction`(`_mk_fmt`) 생성·연결(§d.1) |
| 의미 슬롯(신규) | `fmt_table`/`fmt_table_row_add`/`fmt_table_row_del`/`fmt_table_col_add`/`fmt_table_col_del` |
| 디스패처(신규) | `_dispatch_table_insert`(§a.1), `_dispatch_table_op(op)`(아래) |
| WYSIWYG 경로(신규) | `_wysiwyg_insert_table(rows,cols)`(§a.3), `_wysiwyg_table_op(op)`(§c) |
| Editor 경로(신규) | `_editor_insert_table(rows,cols)`(§a.2), `_editor_table_op(op)`(§b.3) + 파서/재구성 헬퍼 |
| 상수(신규) | (없음 필수) — 정규식 `_PIPE_LINE_RE`/`_SEP_CELL_RE` 모듈 상수 추가 |

행/열 디스패처(삽입 디스패처와 대칭):

```python
def fmt_table_row_add(self) -> None: self._dispatch_table_op("row_add")
def fmt_table_row_del(self) -> None: self._dispatch_table_op("row_del")
def fmt_table_col_add(self) -> None: self._dispatch_table_op("col_add")
def fmt_table_col_del(self) -> None: self._dispatch_table_op("col_del")

def _dispatch_table_op(self, op: str) -> None:
    if self._is_wysiwyg_surface():
        self._wysiwyg_table_op(op)               # DOM JS(§c)
    elif self._is_source_editor_surface():
        self._editor_table_op(op)                # 표블록 파싱·재구성(§b)
    # Preview → no-op(툴바 숨김, 방어적)
```

> **기존 코드와의 정합:** surface 판별은 `_is_wysiwyg_surface`/`_is_source_editor_surface`
> /`_is_edit_surface` 를 그대로 재사용(신규 판별 0). dirty/타이틀/렌더/캡처/외부변경/
> self-write 정책은 기존 경로(Editor=`textChanged`→디바운스, WYSIWYG=`_exec_format`/
> `_capture_wysiwyg_once`)를 **그대로 통과**하므로 신규 정책 코드 0.

---

## (f) 엣지 케이스

| 케이스 | 처리 |
|--------|------|
| **빈 표(0행/0열)** | 다이얼로그에서 행/열 최소 1 강제(`getInt` min=1). 본문행 ≥ 1 |
| **본문 1행에서 행 삭제** | Editor=거부+안내, WYSIWYG=JS no-op(tbody.rows≤1). 헤더+구분만 남는 표 방지 |
| **열 1개에서 열 삭제** | Editor=거부+안내, WYSIWYG=JS no-op(cells≤1) |
| **커서가 표 밖**(행/열 버튼) | Editor=`_find_table_block`→None→상태바 안내. WYSIWYG=`closest(td,th)`→null→no-op |
| **커서가 헤더/구분행**(행 삭제) | Editor=본문 행 아님→안내("본문 행에 커서"). WYSIWYG=thead 행 삭제 거부 |
| **표 삽입 시 커서가 줄 중간** | 표 앞에 `\n` 보정(표는 블록, GFM 파싱 보호). §a.2 |
| **구분행 없는 파이프 라인 뭉치** | "유효 GFM 표 아님" → 행/열 편집 안내(v1: 구분행 필요). 또는 첫 줄=헤더 폴백(ui-dev 재량) |
| **셀 내 리터럴 파이프(`\|`)** | v1 미지원(분리됨) — 한계 문서화(§g). 재구성 시 손상 가능 |
| **유니코드 폭(한글/CJK·이모지)** | 폭 정렬은 `len()` 기반(시각 정렬 불완전하나 **GFM 파싱은 정상**). 정확한 폭맞춤(wcwidth)은 비목표 — 렌더 결과는 동일 |
| **불규칙 셀 개수(행마다 다름)** | 파서가 ncols=헤더 기준으로 정규화(부족=빈셀, 초과=절단). 재구성 시 사각형화 |
| **표 + 인접 텍스트 붙음** | 삽입은 앞뒤 개행 보정. 기존 표 편집은 표블록 경계(빈 줄/파이프 없는 줄)로 안전 분리 |
| **WYSIWYG insertHTML 미지원 환경** | §a.4 커스텀 JS 폴백(range.insertNode). QA 실삽입 확인 |
| **빈 문서에서 표 삽입(WYSIWYG)** | `_wysiwyg_active` 가드. 빈 문서는 보통 Preview→WYSIWYG 진입 시 빈 article — insertHTML 정상. (새 문서 후 Ctrl+4 흐름 QA) |

---

## (g) 라운드트립 한계 (인지 · 문서화)

WYSIWYG 표 → `html_to_markdown`(html2text) → 마크다운 변환의 알려진 한계. **About
다이얼로그/상태바에 1줄 안내** 추가 권장.

1. **정렬(alignment):** WYSIWYG `<table>` 에 정렬 정보(`text-align`)를 주지 않으므로
   라운드트립 시 정렬 콜론(`:--`/`--:`/`:-:`)이 생기지 않음(기본 좌측). Editor 삽입도
   v1 은 정렬 미지정. → 정렬 지정은 §확장.
2. **병합 셀(rowspan/colspan):** GFM 파이프표는 병합셀 미지원. WYSIWYG 에서 병합셀이
   생기면(붙여넣기 등) html2text 변환이 깨지거나 셀 정렬이 어긋남. v1 표 도구는
   **병합셀을 만들지 않음**(단순 사각 표만 생성/편집). DOM 행/열 조작도 병합 가정 안 함.
3. **양끝 파이프/공백 정규화:** html2text 출력 표는 양끝 파이프·셀 공백·구분선 폭이
   **정규화**될 수 있음(사용자가 손으로 맞춘 폭이 변형). 내용은 보존(파싱 동일).
4. **셀 내 복합 콘텐츠:** 셀 안 줄바꿈·블록 요소는 GFM 표가 표현 못 함(인라인만).
   WYSIWYG 에서 셀에 Enter 로 줄바꿈 시 변환이 부정확할 수 있음 → 셀은 단순 텍스트 권장.
5. **Editor 경로는 더 단정:** 소스 편집기 삽입/편집은 **결정적 GFM** 출력(양끝 파이프,
   균일 구분선)이라 라운드트립 손실이 없음(파서가 자체 재구성). WYSIWYG 는 html2text
   의존이라 정규화 폭이 큼 — **정밀 표 작업은 Editor/Split 권장**을 상태바/About 에 안내.

> **안내 문구(About 보강 권장):** "표: 편집기/분할에선 GFM 파이프표로 정확히 편집되고,
> 라이브 편집(WYSIWYG)에선 정렬/병합셀이 단순화될 수 있습니다."

---

## 9. core 변경 여부 (확인)

**모두 불필요.** 근거:
- 표 렌더: `renderer.render` 가 `md.enable("table")` 로 **GFM 표 이미 렌더**(검증됨).
- 표 역변환: `html_to_markdown` 가 html2text 로 `<table>`→**파이프표 이미 변환**(검증됨).
- 표 삽입/편집 = `QTextCursor` 문자열 조작(Editor) + `runJavaScript` DOM(WYSIWYG) = **UI 전용**.
- 파서/재구성 = `main_window.py` 내 순수 보조 로직(core 아님).
- 신규 의존성·자산 0 → `requirements.txt`/`mdviewer.spec` 무변경.

> ui-dev 가 구현 중 core 변경이 꼭 필요하다 판단하면(예: 표 정렬을 렌더에 반영하려
> renderer 옵션 조정) architect 에게 통지해 계약을 갱신한다. **현 설계 목표:
> core/settings/theme 변경 0, `main_window.py` 단일 파일.**

---

## 10. 영향 파일 & 담당

| 파일 | 변경 | 담당 |
|------|------|------|
| `src/mdviewer/main_window.py` | 표 5개 `QAction` + `fmt_table*` 슬롯 + `_dispatch_table_insert`/`_dispatch_table_op`; WYSIWYG `_wysiwyg_insert_table`/`_wysiwyg_table_op`(+JS 리터럴); Editor `_editor_insert_table`/`_editor_table_op` + 표블록 파서(`_find_table_block`/`_parse_table_lines`/`_split_cells`/`_render_table`/`_apply_table_op`/`_cursor_col_index`/`_replace_table_block`); `_build_format_toolbar` 표 그룹 추가; 정규식 상수; About 표 안내 1줄 | **ui-dev** |
| `src/mdviewer/renderer.py` | **변경 없음**(GFM table·html2text 표 역변환 검증됨) | — |
| `settings.py`/`theme.py`/`file_watcher.py` | **변경 없음** | — |
| `requirements.txt`/`mdviewer.spec` | **변경 없음**(신규 의존성·자산 0) | — / packager(검증만) |
| `tests/` | 표블록 파서(식별/셀분해/구분행), 행/열 조작 순수 로직(추가·삭제·거부), GFM 골격 생성(폭/양끝 파이프), 라운드트립(GFM→render→html_to_markdown→GFM 안정), 게이팅(Preview 숨김), 엣지(표밖/1행/1열) | QA |

---

## 11. 빌드 순서 & 통합 지점

```
Step 1  architect ──▶ 본 설계(10) 확정 → ui-dev 에 계약 통지(core 불변, 디스패치 확장)
Step 2  ui-dev    ──▶ main_window.py 단일 파일: 표 삽입(양 surface) + 행/열 편집
                       (Editor 표블록 파서·재구성 / WYSIWYG DOM JS) + 툴바 표 그룹
                     (core 변경 없음 → core-engine-dev 대기 불필요)
Step 3  QA        ──▶ 파서/조작 단위 테스트 + 스모크(실앱): 삽입(Editor/WYSIWYG),
                       행/열 추가·삭제, 표밖/엣지, 라운드트립, 게이팅
Step 4  packager  ──▶ 회귀 스모크(신규 자산/의존성 0 → spec 변경 없음 확인만)
```

**통합 지점(경계면 버그 단골 — QA 집중 검증):**
- **A. 표 삽입 surface 분기:** "표" 버튼이 Editor 면 GFM 골격(양끝 파이프·구분행),
  WYSIWYG 면 `<table>` insertHTML. `_is_*_surface` 정합(§a, §e).
- **B. Editor 표블록 파싱 정확성:** 커서 표 안→블록 경계 식별·셀 분해·구분행 인식·
  재구성 후 유효 GFM(render 가 표로 렌더되는지). 표밖→안내(§b, §f).
- **C. WYSIWYG DOM 조작→캡처→_doc_text:** 행/열 DOM 변경 후 `_capture_wysiwyg_once`
  →`html_to_markdown`→`_doc_text`/dirty. 빈 표 방지·표밖 no-op(§c, §f).
- **D. dirty/렌더/undo 자동화:** Editor=`insertText`/`beginEditBlock`→`textChanged`→
  dirty+디바운스+undo 1스텝. WYSIWYG=캡처 경로 dirty. 신규 정책 코드 없이 기존 경로
  통과(§e). 라운드트립 안정(§g).

---

## 12. 확장 후보 (범위 외 — 표기만)

- 표 정렬 지정(좌/중/우 — Editor 구분행 콜론, WYSIWYG `text-align`+렌더 반영).
- 행 위/아래·열 좌/우 방향 선택(현 v1: 행=아래, 열=오른쪽 고정).
- 표 컨텍스트 동적 버튼 토글(커서 표 안일 때만 행/열 버튼 활성 — cursorPositionChanged).
- 병합 셀(rowspan/colspan) — GFM 한계로 별도 표현(HTML 표 보존 모드) 필요.
- 셀 내 리터럴 파이프(`\|`) 이스케이프 지원.
- 유니코드 폭 정밀 정렬(wcwidth) — 시각 정렬 개선(파싱엔 무영향).
- 표 단축키, 표 삭제(전체) 버튼, 표 셀 탐색(Tab 이동).
- 표준 워드프로세서 범위 밖이므로 후보로만 둔다.
```
