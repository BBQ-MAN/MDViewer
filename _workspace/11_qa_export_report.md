# QA 리포트 — Word(.docx)/PDF 내보내기 (Phase 10)

> 작성: qa-verifier · 2026-06-25
> 대상: `src/mdviewer/exporter.py`(core), `src/mdviewer/main_window.py`(UI export 경로),
> `src/mdviewer/__init__.py`(노출), `tests/test_export.py`(신규)
> 기준: `_workspace/10_export_feature_design.md` §10 (QA 검증 포인트)
> 환경: Windows 11, Python 3.12.12, python-docx 1.2.0, lxml 5.4.0, PySide6(offscreen)

---

## 0. 요약 (게이트 판단)

| 검증 영역 | 결과 |
|-----------|------|
| A. core 단위 (markdown_to_docx) | **PASS** (25 passed, 1 xfail=BUG-01 추적) |
| B. 경계면 shape 대조 | **PASS** (시그니처·호출부·예외정책 정확히 일치) |
| C. PDF 스모크 (offscreen) | **PASS** (11/11) |
| C. 전체 회귀 (tests/) | **PASS** (144 passed, 1 skipped, 1 xfail — 회귀 없음) |
| D. flush 정합 | **PASS** (5/5) |
| 발견 결함 | **BUG-01 (중첩 목록 구조 평탄화)** — 비차단(non-blocking) |

**패키징 게이트 판단: 통과 가능 (GO).**
BUG-01 은 크래시·텍스트 유실·잘못된 .docx 가 아니라 **중첩 목록의 시각적 구조(들여쓰기/
단락 분리)만 어긋나는 렌더링 품질 결함**이다. 핵심 계약(시그니처/예외/저장/매핑 대부분/
이미지/title/PDF 생명주기)은 모두 PASS 다. BUG-01 은 packaging 을 막지 않으며, core-engine-dev
가 후속으로 고치면 된다(아래 §6 에 정확한 위치·수정안 명시). 즉, **현 상태로 packager 진행 가능.**

---

## A. core 단위 — `markdown_to_docx` (GUI 불필요)

신규 정식 테스트 `tests/test_export.py` 작성·실행. `pytest`:

```
tests/test_export.py ... 25 passed, 1 xfailed in 3.64s
```

| 항목 | 테스트 | 결과 |
|------|--------|------|
| 기본 생성(파일 존재·크기>0·재오픈·단락/표 개수·텍스트 포함) | test_basic_creation | PASS |
| 반환 None | test_returns_none | PASS |
| h1=Heading 1 / h2=Heading 2 스타일 | test_heading_style | PASS |
| bold run `.bold is True` | test_bold_run | PASS |
| italic run | test_italic_run | PASS |
| 표 `len(doc.tables)==1` + 셀 텍스트 일치(2x2) | test_table_mapping | PASS |
| 코드블록 텍스트(개행 포함) 보존 + Consolas | test_code_block_preserved | PASS |
| 인라인 코드 monospace | test_inline_code | PASS |
| task 항목 `☑`/`☐` 접두 | test_task_list_prefix | PASS |
| 중첩 목록: 크래시 없음 + 텍스트 무손실 | test_nested_list_no_loss | PASS |
| 중첩 목록: **구조 분리(정상)** | test_nested_list_structure | **xfail(BUG-01)** |
| 이미지: 유효 4x4 PNG 임베드(`inline_shapes>=1`) | test_image_embed_valid_png | PASS |
| 이미지: 없는 file:// → alt 폴백, 임베드 0 | test_image_missing_file_alt_fallback | PASS |
| 이미지: 원격 http:// → 다운로드 없이 alt 폴백 | test_image_remote_alt_fallback | PASS |
| 견고성: 빈/공백/개행/None/깨진HTML/미닫힌펜스 → 예외 없이 저장 | test_robustness_no_exception (6 케이스) | PASS |
| 부모 디렉터리 자동 생성 | test_empty_creates_parent_dir | PASS |
| I/O 전파: out_path=기존 디렉터리 → OSError 전파 | test_io_error_propagates_directory_out_path | PASS |
| title → core_properties.title | test_title_core_property | PASS |
| title 미지정 → 본문 h1 은 본문에(별도 제목 단락 아님) | test_title_omitted_no_crash | PASS |

검증 디테일(실측):
- 유효 PNG 는 stdlib `zlib`+`struct` 로 완전한 IHDR/IDAT/IEND 청크 4x4 RGB 생성(1x1 더미는 python-docx 거부 — 설계 주의대로 회피).
- I/O 전파 실측: out_path 가 디렉터리일 때 `PermissionError`(OSError 서브클래스) 전파 확인.
- task 실측: `☐ task undone` / `☑ task done` (renderer 의 `<input checked="checked">` 를 `"checked" in attrs` 로 정확히 판정).
- 코드블록 실측: `def f():` / `    return 1` 각각 별도 Consolas 단락(개행·들여쓰기 보존).

---

## B. 경계면 shape 대조 (통합 버그 차단 — 최우선) — PASS

### B.1 시그니처 (설계 §2.1)

`inspect.signature(mdviewer.markdown_to_docx)` 실측:
```
(markdown_text: 'str', out_path: 'Path', base_dir: 'Path', *, title: 'str | None' = None) -> 'None'
```
- 인자 순서·이름 정확히 일치, `title` 은 **keyword-only**(default None) 확인.
- `mdviewer.markdown_to_docx is mdviewer.exporter.markdown_to_docx` (루트 export 동일 객체).
- 테스트 `test_signature_shape` / `test_export_identity` 로 자동 고정.

### B.2 UI 호출부 (main_window.py `_do_export_docx`, line ~2195)

```python
markdown_to_docx(self._doc_text, path, base_dir, title=title)   # 정확히 그 시그니처
```
- `try/except OSError` **로만** 감쌈(line 2193–2200) → QMessageBox 폴백. 변환 예외는 core 가
  안 던지므로 OSError 한정이 정확. **일치.**

### B.3 graceful 폴백 (`_EXPORT_AVAILABLE`, line 119–134)

- `from .exporter import markdown_to_docx` 실패 시 `_EXPORT_AVAILABLE=False` + 폴백 함수가
  **`OSError`** 를 던짐 → UI 의 `except OSError` 가 잡아 QMessageBox 안내. **일치.**
- 또한 `_EXPORT_AVAILABLE` False 면 `act_export_docx` 는 항상 비활성(line 662–663, 1586)이라
  정상 경로로는 폴백에 도달하지 않음(이중 방어). 현재 환경은 `_EXPORT_AVAILABLE=True`.

### B.4 예외 정책 교차 확인 (설계 §2.2 대조표)

- 변환 비전파: 깨진 HTML/빈/None/미닫힌 펜스 → 예외 없이 .docx 저장(실측 PASS).
- I/O 전파: 쓰기 불가 경로 → OSError 전파(실측 PASS).
- 두 케이스 모두 통과 → core 가 "변환 안전 + OSError만 전파" 결합 철학을 정확히 구현.

---

## C. PDF 스모크 (UI 오프스크린) — PASS

전체 회귀 먼저: `PYTHONPATH=src python -m pytest tests/ -q` →
**144 passed, 1 skipped, 1 xfailed** (회귀 없음. xfail 은 BUG-01).

오프스크린 PDF 스모크(MainWindow 를 `QT_QPA_PLATFORM=offscreen` 으로 띄워 `_export_pdf_async`
직접 호출, 파일 대화상자 우회, Qt 이벤트 루프로 `pdfPrintingFinished` 대기):

| 검증 | 결과 |
|------|------|
| 다크 테마(`_theme=DARK`)로 두고도 PDF 생성(라이트 강제 경로) | PASS |
| `pdfPrintingFinished(success=True)` 수신 | PASS |
| 파일 존재 · 크기>0 (52,130 bytes) | PASS |
| 선두 바이트 `%PDF-` | PASS |
| 완료 후 `_pdf_page is None` | PASS |
| 완료 후 `_pdf_busy is False` | PASS |
| 재진입 가드: 진행 중 `_do_export_pdf` 재호출 차단(`_pdf_busy True`) | PASS |
| 빈 문서(`"   "`) → `act_export_pdf`/`act_export_docx` 비활성 | PASS |
| 비어있지 않은 문서 → `act_export_pdf` 활성 | PASS |

→ **11/11 PASS.** PDF 생명주기(오프스크린 페이지 GC 방지·완료 정리·재진입 가드)와
다크 무관 라이트 강제, 빈 문서 게이팅이 설계 §3/§10.2 대로 동작.

메뉴/툴바 배선(소스 확인): 파일 메뉴 "내보내기(&E)" 서브메뉴에 PDF/Word 액션이
`act_save_as` 다음·첫 separator 앞 배치(line 781–785), 툴바에 PDF 버튼(line 835). 설계 §4.2/§4.3 일치.

> **GUI 자동화 범위 한계(정직 보고):** 실제 마우스/메뉴 클릭·`QFileDialog` 상호작용은
> offscreen 환경에서 자동화하지 않았다(대화상자는 블로킹). 대신 대화상자 **이후** 메서드
> (`_export_pdf_async` / `markdown_to_docx`)를 직접 호출해 경로를 검증했다. 액션→슬롯 연결은
> 소스 검토로 확인(`triggered.connect(self.export_pdf/export_docx)`, line 656/660).

---

## D. flush 정합 (설계 §5) — PASS

offscreen 스모크로 Editor 디바운스 대기 중 export 진입 시 마지막 타이핑 포함 확인:

| 검증 | 결과 |
|------|------|
| export 진입 전 디바운스 타이머 active 모사 | PASS |
| `_prepare_doc_for_export()`→`_flush_pending_edit()` 가 `_doc_text` 를 마지막 타이핑으로 최신화 | PASS |
| flush 후 타이머 정지 | PASS |
| 내보낸 docx 에 마지막 타이핑 라인 포함 | PASS |
| 내보낸 docx 에 직전(stale) 값 미포함 | PASS |

→ **5/5 PASS.** Editor/Split 디바운스 경로는 누락 없이 최신화.

**WYSIWYG 경로(소스 검토):** `export_pdf`/`export_docx` 가 `self._wysiwyg_active` 면
`_export_after_wysiwyg_capture(then)` → `_capture_wysiwyg_then` 로 innerHTML 을 비동기 1회
캡처 → `_ingest_wysiwyg_html` 로 `_doc_text` 확정 → `then()`(실제 내보내기) 호출(line 2057–2078,
2135–2141, 2170–2171, 2206–2207). 저장 경로(`_save_after_wysiwyg_capture`)와 동일한 공용 헬퍼를
재사용해 중복 제거. 설계 §5.2 정합. (실제 contentEditable 동작은 GUI 상호작용이라 소스 수준 검증.)

---

## 발견 결함

### BUG-01 — 중첩 목록 구조 평탄화 (렌더 품질 / 비차단)

- **담당: core-engine-dev** (`src/mdviewer/exporter.py`)
- **증상:** 자식 목록을 가진 부모 `<li>` 의 텍스트가 **첫 자식 항목 단락에 병합**되고
  **자식 들여쓰기 레벨(List Bullet 2 / List Number 2)** 로 잘못 렌더된다. 텍스트는 유실되지
  않으나(부분 보존), 항목 경계·들여쓰기 구조가 어긋나고 단락 내부에 잉여 개행이 섞인다.
- **재현:**
  ```
  - Item A
  - Item B
    - Nested B1
    - Nested B2
  - Item C
  ```
  → 실제 docx 단락:
  ```
  'Item A'                 | List Bullet
  'Item B\n\nNested B1'    | List Bullet 2     ← 부모 'Item B' 가 자식과 병합·오들여쓰기
  '\nNested B2'            | List Bullet 2     ← 선두 잉여 개행
  'Item C'                 | List Bullet
  ```
  (ordered list 도 동일: `'two\n\ntwo-a'` 등.)
- **기대:** `Item B` 가 자체 단락(List Bullet, depth 1)으로, `Nested B1/B2` 가 별도 단락(List
  Bullet 2)으로 분리.
- **근본 원인:** `_DocxBuilder._start` 에서 `tag in ("ul","ol")` 진입 시 `self._list_stack.append(...)`
  만 하고, **현재 열려 있는 부모 `<li>` 의 누적 인라인(`self._inlines`)을 flush 하지 않는다.**
  그래서 부모 `<li>` 의 텍스트가 자식 목록 항목과 같은 버퍼에 남아 첫 자식 `</li>` flush 시
  함께 토해진다(이때 depth=2 라 자식 스타일로 찍힘).
- **수정 가이드(제안):** `_start` 의 `ul`/`ol` 분기에서, **리스트 컨텍스트(`self._ctx[-1]=="li"`)
  안이면 자식 목록을 열기 직전에 부모 항목을 먼저 flush** 한다. 예:
  ```python
  elif tag in ("ul", "ol"):
      # 자식 목록 진입 전, 부모 li 의 누적 텍스트를 먼저 항목으로 확정(분리).
      if self._ctx and self._ctx[-1] == "li" and (self._inlines and _has_visible(self._inlines)):
          self._flush_list_item_parent()   # 현재 list_stack/li_task 기준으로 한 항목 emit
      self._list_stack.append("ul" if tag == "ul" else "ol")
  ```
  (`_flush_list_item` 을 그대로 부르면 `li_task` 팝/컨텍스트 처리와 충돌하므로, 부모 항목만
  현재 깊이로 한 단락 emit 하는 분리 헬퍼를 두는 편이 안전. 자식 `</ul>` 후 닫히는 `</li>` 의
  `_flush_list_item` 은 잔여 인라인이 없으면 no-op 가 되도록 `_has_visible` 가드가 이미 있음.)
- **회귀 테스트:** `tests/test_export.py::test_nested_list_structure` 를 **xfail(strict)** 로
  추가해 추적 중. 수정되면 xpass 로 전환되어 즉시 감지된다. `test_nested_list_no_loss` 는
  무손실/무크래시를 계속 보증.
- **차단성:** **비차단.** 크래시·유실·잘못된 파일이 아니며 핵심 매핑은 동작. 패키징 게이트를
  막지 않는다(평탄 목록·기타 모든 매핑은 정상). 후속 패치 대상.

---

## 미해결 항목

- **BUG-01 (중첩 목록 구조)** — core-engine-dev 에 통지(SendMessage). 1회 재검증 예정.
  비차단이므로 패키징과 병행 가능.

## 재현/검증 산출물

- 정식 테스트: `d:\Dev\MDViewer\tests\test_export.py` (25 passed, 1 xfail).
- PDF 스모크 스크립트: scratchpad `pdf_smoke.py` (11/11) — 재실행:
  `QT_QPA_PLATFORM=offscreen PYTHONPATH=src python <scratchpad>/pdf_smoke.py`
- flush 스모크 스크립트: scratchpad `flush_smoke.py` (5/5).
