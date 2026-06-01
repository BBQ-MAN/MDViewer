# MDViewer QA 검증 리포트 (Phase 3)

> 작성: qa-verifier · 2026-06-01
> 대상: orchestrator / packager / core-engine-dev / ui-dev
> 방법: desktop-app-qa 스킬 — 경계면 교차 비교 + 코어 단위 테스트 + 앱 스모크 + 렌더 정확성
> 환경: Windows 11, Python 3.13.12, PySide6 + QtWebEngine, pytest 9.0.3

## 종합 결과: **PASS** (packager 게이트 통과)

- 코어 단위 테스트 **53/53 PASS** (`python -m pytest`, 3.45s)
- import 스모크 PASS (core + UI 모든 모듈)
- offscreen GUI 스모크 PASS (demo.md 열기, TOC 18개, 테마/줌/TOC 토글, 종료코드 0)
- `app.main()` 진입점 스모크 PASS (빈 실행 / 파일 인자 실행 모두 종료코드 0)
- 렌더 정확성 PASS (헤딩 id/코드 span/테이블/각주/작업목록/내부 앵커/상대 이미지)
- 경계면 교차 비교 PASS (불일치 없음 — 아래 상세)

미해결 blocker 없음. 비-blocker 관찰 사항 2건(아래 §5).

---

## 1. 경계면 교차 비교 (최우선) — 모두 일치

`renderer.py`/`file_watcher.py` 공개 시그니처와 `main_window.py`/`theme.py`/`app.py` 호출부를
나란히 대조한 결과:

| 항목 | core 측 (실제) | UI 호출부 (실제) | 판정 |
|---|---|---|---|
| `render(text, base_dir)` | `render(markdown_text, base_dir) -> RenderResult` | `render(text, base_dir=self._path.parent)` (main_window.py:368) | **일치** |
| `read_markdown(path)` | `read_markdown(path) -> str`, FileNotFoundError/OSError만 전파 | `read_markdown(self._path)`, `except FileNotFoundError`/`except OSError`만 잡음 (main_window.py:360-365) | **일치** |
| `RenderResult.html/toc/title` | dataclass 속성 | `getattr(result, "html"...)`, `getattr(result, "toc"...)` (속성 접근, dict 오용 없음) | **일치** |
| `TocItem.level/text/anchor` | frozen dataclass | `getattr(item,"level"/"text"/"anchor")` (main_window.py:440-442) | **일치** |
| `pygments_css(dark)` 위치/스코프 | `mdviewer.renderer.pygments_css(dark) -> str`, `.codehilite` 스코프 | theme.py:75-80 우선순위 1로 `from .renderer import pygments_css` 호출 | **일치** (아래 §3 검증) |
| `FileWatcher(on_changed)` | `__init__(on_changed: Callable[[],None])`, 콜백 인자 없음 | `FileWatcher(on_changed=self._bridge.fileChanged.emit)` — Signal() 무인자 (main_window.py:132) | **일치** |
| `watch/stop` | `watch(path)`, `stop()` 멱등 | `self._watcher.watch(path)` (300줄대), `closeEvent`에서 `stop()` | **일치** |

### 1.1 스레드 안전성 (통합 단골) — PASS
- `FileWatcher.on_changed` 는 watchdog 워커 스레드(+150ms 디바운스 타이머 스레드)에서 호출됨.
- UI: `FileWatcher(on_changed=self._bridge.fileChanged.emit)` → 워커스레드에서 **Signal.emit만** 수행.
- `_bridge.fileChanged.connect(self._on_file_changed, Qt.ConnectionType.QueuedConnection)`
  (main_window.py:122-124) → **QueuedConnection 으로 메인 스레드 전달 명시적 확인**.
- 메인 스레드 `_on_file_changed` → 120ms QTimer 디바운스 → `_reload_current`. UI 위젯 직접 조작 없음.
- 콜백 예외는 워커 스레드에서 격리(file_watcher.py:108-112) — 단위 테스트로 검증
  (`test_callback_exception_isolated`).
- **판정: PASS.** 워커→Signal→QueuedConnection→메인스레드 경로가 계약(§4.1)대로 안전.

---

## 2. 코어 단위 테스트 — 53/53 PASS

생성 파일:
- `tests/conftest.py` — src 레이아웃을 sys.path 에 주입(`pip install -e .` 불필요).
- `tests/test_renderer.py` — render/read_markdown/pygments_css/slugify (35 케이스).
- `tests/test_file_watcher.py` — 생성/watch/stop멱등/콜백/디바운스/교체/예외격리 (7 케이스).
- `tests/test_demo_render.py` — demo.md 종합 렌더 정확성 (11 케이스).

재현:
```powershell
$env:PYTHONUTF8="1"; $env:PYTHONPATH="d:/Dev/MDViewer/src"
python -m pytest tests -q
# => 53 passed in ~3.5s
```

커버한 엣지/계약 케이스:
- 빈/공백전용/None 입력 → 크래시 없음, 빈 결과.
- 안 닫힌 코드펜스 → 크래시 없음.
- 유니코드(한글) 제목/본문 → 정상.
- 원시 HTML(`<script>`) → escape (html:False, 견고성).
- 코드 하이라이트: `.codehilite` 클래스 + 토큰 span, 미상 언어/언어없음 → plain, 크래시 없음.
- 앵커: TOC anchor == 본문 헤딩 id (단순/한글/중복-유일화/전 레벨), `slugify` 일관성.
- GFM: 테이블/각주/작업목록 체크박스/취소선.
- 이미지/링크: 상대 → `file:///` URI 변환, 외부 URL 보존, 내부 `#앵커` 보존, 없는 이미지 크래시 없음.
- read_markdown: UTF-8/BOM 제거/빈 파일/cp949(현실 길이)/바이너리 크래시 없음/없는 파일 FileNotFoundError.
- FileWatcher: 디바운스(5연속 쓰기 → 콜백 1~2회), watch 교체, stop 멱등, 콜백 예외 격리.

### 2.1 테스트 작성 중 발견·수정한 테스트 결함 (코드 아님)
- 최초 `test_read_markdown_cp949` 가 15바이트 초단문(`"안녕하세요 세계"`)을 cp949로 인코딩해
  검증 → **FAIL**. 원인은 charset-normalizer 가 통계적 감지기라 수십 바이트 입력에서 인코딩을
  오판한 것(라이브러리 한계). **현실 길이(수백 바이트) 한글 cp949/euc-kr 파일은 정상 복구됨을
  별도 확인**했고, 테스트를 현실적 길이로 교정 → PASS. 추가로 초단문은 "예외 없이 str 반환"만
  보장하는 케이스(`test_read_markdown_short_legacy_no_crash`)로 분리.
  → **read_markdown 코드 결함 아님**(계약 §02: 디코딩 실패는 best-effort 복구 명시).

---

## 3. pygments_css 스코프 (ui 보고 `.codehilite` vs `.highlight`) — 실제 검증 결과: 문제 없음

ui-dev 가 03_ui_notes.md §「core-engine-dev 에게」에서 제기한 스코프 우려를 실측:
- `render()` 가 코드블록에 부여하는 래퍼 클래스 = **`codehilite`** (HtmlFormatter cssclass).
  → demo.md 렌더 HTML 에 `class="codehilite"` 존재, `class="highlight"` **없음** 확인.
- `pygments_css(dark)` 반환 CSS 의 토큰 색 규칙 스코프 = **`.codehilite`** (light/dark 둘 다 확인,
  light != dark).
- theme.py 는 우선순위 1로 core 헬퍼를 쓰므로 **렌더 클래스(`.codehilite`)와 CSS 스코프가
  실제로 일치** → 코드블록 색이 실제 적용됨. 단위 테스트 `test_pygments_css_scope_matches_render_class`로 고정.
- theme.py 폴백 경로(우선순위 2)는 `.highlight` 스코프지만, **core 헬퍼가 항상 성공하므로
  도달하지 않음**. 비-blocker(아래 §5-1).

---

## 4. 상대경로 이미지 이중처리 (renderer file:// + UI baseUrl) — 문제 없음

- renderer: 상대 `img src="img/logo.png"` → `file:///D:/Dev/MDViewer/samples/img/logo.png` 절대 URI 치환 확인.
- UI: `view.setHtml(html, QUrl.fromLocalFile(str(self._path.parent)+"/"))` 로 baseUrl 도 지정.
- 이미 절대 `file:///` URI 이므로 baseUrl 은 **무시됨(상대경로 잔여 없음)** → 중복/깨짐 없음.
- 내부 앵커 링크는 markdown-it 의 normalizeLink 로 percent-encoding 됨
  (`#mdviewer-%EB%8D%B0...`)이나, 헤딩 id 는 raw 한글. **브라우저 프래그먼트 네비게이션은
  percent-decoded 후 매칭하므로 동작**, TOC 패널 클릭은 `getElementById(raw anchor)` 사용 →
  양쪽 모두 raw id 를 타깃 → 일관. 비-blocker(아래 §5-2, QWebEngine 런타임에서 육안 확인 권장).

---

## 5. 비-blocker 관찰 사항 (배포 차단 아님 — 향후 합의/개선 후보)

### 5-1. theme.py 폴백 스코프 불일치 (담당: ui-dev, 우선순위 낮음)
- `theme.py:_pygments_css` 우선순위 2(런타임 생성)는 `.highlight` 스코프
  (theme.py:95) — renderer 클래스 `.codehilite` 와 불일치. **현재 우선순위 1(core 헬퍼)이
  항상 성공해 도달 불가**라 무해. core 헬퍼가 사라지는 회귀가 생기면 이 폴백에서 코드 색이
  안 먹는다. 방어적으로 폴백도 `.codehilite` 스코프로 통일 권장. (blocker 아님)

### 5-2. 내부 앵커 링크 percent-encoding (담당: 정보성, 조치 불요)
- `<a href="#한글앵커">` 가 `#%EB...` 로 인코딩되나 헤딩 id 는 raw. Chromium/QWebEngine 은
  프래그먼트를 decode 해 매칭하므로 정상 동작 예상. 단위 테스트로는 문자열만 확인 가능하므로,
  **실 GUI 에서 데모의 한글 내부 링크 클릭 스크롤을 한 번 육안 확인**하면 100% 확정.
  (ui-dev 스모크에서 TOC 패널 클릭 동작은 이미 확인됨 — 본문 인라인 링크 클릭만 잔여.)

---

## 6. 앱 스모크 상세 (offscreen)

- import 스모크: `import mdviewer.{app,main_window,theme,settings,paths,renderer,file_watcher}` 전부 OK.
- offscreen GUI 스모크: `QT_QPA_PLATFORM=offscreen` + WebEngine GPU 비활성 플래그로
  demo.md 열기 → TOC 18개, 윈도우 타이틀 "demo.md — MDViewer", 테마토글/줌/TOC토글 예외 없음, 종료코드 0.
- 진입점 스모크: `app.main(["mdviewer"])`(웰컴) 및 `app.main([..,"demo.md"])`(파일) 모두 종료코드 0.
- 주의(환경 한정, 코드 결함 아님): offscreen 에서 GPU 플래그 없이 실행 시 QtWebEngine 의
  Skia/GLES GPU 컨텍스트 에러로 비정상 종료(exit 9) 발생. 헤드리스 GPU 가상화 한계이며,
  실제 데스크톱 GPU 환경에서는 발생하지 않음. 헤드리스 검증 시
  `QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --in-process-gpu"` 권장.

재현:
```powershell
$env:PYTHONUTF8="1"; $env:QT_QPA_PLATFORM="offscreen"
$env:QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --disable-software-rasterizer --no-sandbox --in-process-gpu"
python -m mdviewer samples/demo.md   # 실 데스크톱은 플래그 불필요
```

---

## 7. packager 에게

- QA PASS — 패키징 진행 가능. 단, 청사진 §7 의 QtWebEngine 리소스
  (`QtWebEngineProcess`, ICU, locales, resources) 번들 누락 검증을 빌드 후 반드시 수행할 것
  (빈 화면의 주원인). 본 QA 의 GPU 컨텍스트 이슈는 *개발 헤드리스 환경 한정*이며 패키징 결함과는 무관.
