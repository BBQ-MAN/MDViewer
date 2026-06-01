# MDViewer 아키텍처 청사진 (Phase 1)

> 작성: architect · 초기 실행 · 2026-06-01
> 범위: 표준 마크다운 뷰어 — 파일 열기, 실시간 렌더링, 코드 하이라이트,
> 목차/내부 링크, 다크/라이트 모드, 최근 파일, 드래그앤드롭, 명령줄 인자 파일 열기.

이 문서는 **계약(contract)**이다. core-engine-dev 와 ui-dev 는 아래 렌더 API
시그니처를 못박힌 경계로 삼아 병렬로 구현한다. 계약 변경은 architect 를 통해
양쪽 모두에게 통지한다.

---

## 1. 디렉토리 트리 (파일별 책임 + 담당 에이전트)

```
MDViewer/
├── src/mdviewer/
│   ├── __init__.py          # 패키지 메타(__version__, __app_name__)          [architect ✓]
│   ├── __main__.py          # `python -m mdviewer` 진입 → app.main()           [ui-dev]
│   ├── app.py               # QApplication 부트스트랩, HiDPI, CLI 인자 파싱,    [ui-dev]
│   │                        #   main() entry point, 첫 파일 열기
│   ├── main_window.py       # QMainWindow: 메뉴/툴바/상태바, QWebEngineView,    [ui-dev]
│   │                        #   드래그앤드롭, 최근파일 메뉴, 테마 토글,
│   │                        #   renderer/file_watcher 호출 및 시그널 연결
│   ├── renderer.py          # 마크다운 → HTML 엔진. render()/read_markdown().   [core-engine-dev]
│   │                        #   RenderResult/TocItem dataclass. PySide6 무의존.
│   ├── file_watcher.py      # watchdog 기반 파일 변경 감시. 콜백 인터페이스.     [core-engine-dev]
│   │                        #   PySide6 무의존(콜백만 노출, Qt 어댑터는 UI측).
│   ├── theme.py             # 라이트/다크 CSS 조립, HTML 문서 셸 생성.          [ui-dev]
│   ├── settings.py          # QSettings 래퍼: 최근파일 목록·테마·창 상태 영속화. [ui-dev]
│   ├── paths.py             # 리소스 base path(sys._MEIPASS 대응).             [architect ✓ / 공유]
│   └── resources/
│       ├── styles/          # github.css, dark.css, pygments-*.css            [ui-dev]
│       │   └── .gitkeep                                                       [architect ✓]
│       └── icons/           # app.ico, 툴바 아이콘                            [ui-dev]
├── tests/                   # 코어 단위 테스트(renderer/file_watcher/paths)    [QA]
├── samples/
│   ├── demo.md              # 종합 검증 샘플(헤딩/코드/표/각주/작업목록/        [architect ✓]
│   │                        #   상대이미지/앵커)
│   └── img/                 # (선택) 자리표시 이미지. logo.png 없어도 무방.    [-]
├── requirements.txt         # 런타임 의존성                                    [architect ✓]
├── pyproject.toml           # 프로젝트 메타, src 레이아웃, entry point          [architect ✓]
├── mdviewer.spec            # PyInstaller 스펙                                 [packager]
├── README.md                # 사용법                                          [ui-dev/packager]
└── _workspace/
    └── 01_architect_blueprint.md   # 본 문서                                  [architect ✓]
```

`✓` = Phase 1 에서 architect 가 이미 생성한 골격 파일.

---

## 2. 모듈 경계 — 코어/UI 엄격 분리

**원칙: `renderer.py` 와 `file_watcher.py` 는 PySide6 를 import 하지 않는다.**

- 코어는 순수 Python(+markdown-it-py/Pygments/watchdog/charset-normalizer)만 사용한다.
- 이유: (1) GUI 없이 단위 테스트 가능 → QA 가 빠르고 확실, (2) 경계면이 좁아 통합 버그
  감소, (3) 두 개발자가 계약만 지키면 병렬 작업 가능.
- Qt 시그널 어댑터(파일 변경을 메인 스레드로 전달)는 **UI 측(main_window.py)** 책임이다.
  file_watcher 는 watchdog 워커 스레드에서 콜백을 호출할 뿐이며, UI 가 그 콜백 안에서
  Qt Signal 을 emit 하여 메인 스레드로 안전하게 넘긴다. (아래 4.3 참조)

---

## 3. 렌더 API 계약 (★ 병렬 개발의 핵심 — 이대로 확정)

`renderer.py` 가 노출하는 공개 인터페이스. core-engine-dev 가 구현, ui-dev 가 호출.

```python
# renderer.py
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class TocItem:
    """목차 한 항목."""
    level: int          # 헤딩 깊이 1~6 (h1=1)
    text: str           # 헤딩의 표시 텍스트(인라인 마크업 제거된 평문)
    anchor: str         # HTML id (예: "코드-하이라이트"). 본문 헤딩의 id 와 일치.


@dataclass
class RenderResult:
    """render() 의 반환 shape. 절대 None 을 반환하지 않는다."""
    html: str                              # <body> 안에 들어갈 본문 HTML.
                                           #   - 헤딩에는 id(anchor) 가 부여됨
                                           #   - 코드블록은 Pygments span 클래스 적용
                                           #   - 목차 자체는 toc 리스트로 별도 제공(본문에 미포함)
    toc: list[TocItem] = field(default_factory=list)
    title: str | None = None               # 첫 h1 텍스트. 없으면 None.


def render(markdown_text: str, base_dir: Path) -> RenderResult:
    """마크다운 텍스트를 HTML 본문으로 변환한다.

    Args:
        markdown_text: 원본 마크다운 문자열.
        base_dir: 상대경로 이미지/링크 해석 기준 디렉터리(보통 열린 파일의 부모).
                  렌더러는 상대 src/href 를 base_dir 기준 절대 file URI 로
                  변환하거나, <base> 처리를 위해 base_dir 을 결과에 반영한다.

    Returns:
        RenderResult. **예외를 던지지 않는다.** 빈 입력/깨진 마크다운/없는 이미지
        링크에도 의미 있는 결과(빈 toc, 빈 본문 등)를 반환한다.

    Thread-safety:
        순수 함수에 가까움(전역 가변 상태 없음). 워커 스레드에서 호출 가능하나,
        UI 는 메인 스레드에서 호출하는 것을 기본으로 한다.
    """
    ...


def read_markdown(path: Path) -> str:
    """파일을 읽어 마크다운 텍스트(str)로 반환한다. 인코딩 자동 감지.

    Args:
        path: 마크다운 파일 경로.

    Returns:
        디코딩된 텍스트. UTF-8 우선 시도 후 charset-normalizer 로 폴백.
        BOM 은 제거한다.

    Raises:
        FileNotFoundError: 경로가 없을 때.
        OSError: 읽기 실패(권한 등) 시.
        (디코딩 실패는 예외 대신 charset-normalizer 의 최선 결과로 복구한다.)
    """
    ...
```

### 3.1 앵커(anchor) 규칙 — UI 내부 링크와 반드시 일치

- 헤딩 → id 슬러그화 규칙: **소문자화 → 공백을 `-` 로 → 영숫자/한글/`-` 외 문자 제거**.
  (markdown-it-py 의 `anchors_plugin`/`mdit_py_plugins.anchors` 기본 슬러그와 동일하게 맞춘다.)
- 중복 헤딩은 `-1`, `-2` 접미사로 유일화한다.
- `TocItem.anchor` 와 본문 헤딩 `id` 는 **항상 동일 문자열**이어야 한다.
  ui-dev 는 `<a href="#{anchor}">` 클릭 시 QWebEngineView 의 기본 프래그먼트
  스크롤에 의존한다. (이 일치가 깨지면 내부 링크 버그가 된다.)

### 3.2 코드 하이라이트 규칙

- Pygments 로 토큰에 `class="..."` 부여, 색상은 외부 CSS(`pygments-light/dark.css`)로 분리.
- renderer 는 인라인 스타일을 넣지 않는다(테마 전환을 CSS 교체만으로 가능하게).
- ui-dev 는 테마별 Pygments CSS 를 빌드 시점(또는 최초 실행 시) 생성해 styles/ 에 둔다.

### 3.3 이미지/링크 base_dir 처리

- 상대 이미지 `src` 와 상대 링크 `href` 는 `base_dir` 기준으로 해석.
- 권장: renderer 가 상대 경로를 `base_dir` 기준 `file:///` 절대 URI 로 치환.
  (QWebEngineView 에 `setHtml(html, baseUrl)` 로 baseUrl 을 주는 방식도 가능 —
   이 경우 renderer 는 상대경로를 그대로 두고 ui-dev 가 baseUrl 을 지정한다.
   **결정: renderer 는 상대경로를 그대로 보존하고, ui-dev 가 setHtml 의 baseUrl
   에 `QUrl.fromLocalFile(str(base_dir) + os.sep)` 을 넘긴다.** 단순하고 견고함.)

---

## 4. FileWatcher 계약

```python
# file_watcher.py
from __future__ import annotations
from pathlib import Path
from typing import Callable


class FileWatcher:
    """단일 파일의 변경을 감시한다. watchdog 워커 스레드에서 콜백을 호출한다.

    PySide6 무의존. UI 가 콜백 안에서 Qt Signal 을 emit 해 메인 스레드로 넘긴다.
    """

    def __init__(self, on_changed: Callable[[], None]) -> None:
        """on_changed: 감시 대상 파일이 수정되면 호출되는 콜백(인자 없음).
        ⚠️ watchdog 워커 스레드에서 호출되므로, UI 는 이 콜백에서 직접
           위젯을 건드리지 말고 Qt Signal.emit() 만 해야 한다(스레드 안전)."""
        ...

    def watch(self, path: Path) -> None:
        """대상 파일을 감시 시작한다. 다른 파일이 이미 감시 중이면 교체한다.
        구현은 파일의 부모 디렉터리를 watchdog Observer 로 감시하고
        해당 파일명 이벤트만 필터링한다(에디터의 atomic-save 대응)."""
        ...

    def stop(self) -> None:
        """감시를 중지하고 watchdog Observer 스레드를 정리한다.
        앱 종료/파일 닫기 시 반드시 호출. 멱등(여러 번 호출 안전)."""
        ...
```

### 4.1 스레드 안전성 (★ 통합 버그 단골)

- `on_changed` 는 **watchdog 워커 스레드**에서 실행된다.
- ui-dev 패턴(권장):
  ```python
  class MainWindow(QMainWindow):
      file_changed = Signal()           # 메인 스레드로 넘기는 큐드 시그널
      def __init__(...):
          self._watcher = FileWatcher(on_changed=self.file_changed.emit)
          self.file_changed.connect(self._reload_current)  # Qt.QueuedConnection
  ```
- `_reload_current` 안에서 `read_markdown` → `render` → `setHtml` 을 수행한다.

### 4.2 디바운스

- 에디터는 저장 시 다중 이벤트를 낼 수 있다. file_watcher 또는 UI 측에서
  ~150ms 디바운스를 둔다. **결정: file_watcher 가 내부적으로 짧은 디바운스를
  적용해 콜백 폭주를 막는다.** (UI 는 단일 reload 만 받게 됨.)

---

## 5. 의존성 목록 + 사유

| 라이브러리 | 버전 | 계층 | 사유 |
|-----------|------|------|------|
| PySide6 | `>=6.8,<6.10` | UI | Qt 공식 바인딩(LGPL). QWebEngineView 로 고품질 HTML 렌더. 메이저/마이너 상한 고정 → 패키징 재현성. |
| markdown-it-py | `>=3.0` | 코어 | CommonMark 준수, 플러그인 확장 풍부, 앵커 슬러그 제어 용이. |
| mdit-py-plugins | `>=0.4` | 코어 | GFM 확장: 테이블/각주/작업목록/anchors. demo.md 의 기능 전부 커버. |
| Pygments | `>=2.17` | 코어 | 광범위 언어 하이라이트, CSS 테마 분리 생성. |
| watchdog | `>=4.0` | 코어 | 크로스플랫폼 파일시스템 이벤트(실시간 렌더링). |
| charset-normalizer | `>=3.3` | 코어 | 임의 인코딩 파일 안전 디코딩(read_markdown). |
| pytest (dev) | `>=8.0` | 테스트 | 코어 단위 테스트. |
| pyinstaller (dev) | `>=6.6` | 패키징 | exe 번들. QWebEngine 핸들링 성숙. |

코어 4종(markdown-it-py, mdit-py-plugins, Pygments, watchdog, charset-normalizer)은
모두 PySide6 와 무관 → 코어 단위 테스트가 GUI 없이 돌아간다.

---

## 6. 빌드 순서 & 통합 지점

```
Phase 1  architect   ──▶ 청사진 + 골격(paths/__init__/requirements/pyproject/demo.md) ✓
                          └ 렌더 API 계약 확정 → core/ui 에 통지

Phase 2  (병렬)
  ├ core-engine-dev ──▶ renderer.py (render/read_markdown/RenderResult/TocItem)
  │                     file_watcher.py (FileWatcher)
  └ ui-dev          ──▶ app.py / __main__.py / main_window.py / theme.py / settings.py
                        resources/styles/*.css, resources/icons/*

       통합 지점 A: ui-dev 는 renderer 계약(시그니처/shape)만 보고 mock 으로 선개발 가능.
       통합 지점 B: 앵커 슬러그 규칙(3.1) — core 가 부여한 id 와 ui 의 #링크가 일치해야 함.
       통합 지점 C: FileWatcher 콜백 스레드 → Qt Signal 어댑터(4.1).

Phase 3  QA          ──▶ tests/ 코어 단위테스트 + 스모크 + 경계면 shape 대조 + 견고성
                          (빈/깨진 입력, 없는 이미지, 인코딩 문제)

Phase 4  packager    ──▶ mdviewer.spec, QWebEngine 리소스 번들, sys._MEIPASS 검증
```

---

## 7. 패키징 base path 규칙 (packager·ui-dev 공유 — 확정)

- **모든 리소스 로드는 `mdviewer.paths` 를 거친다.** 직접 `__file__` 상대경로 금지.
  - `resource_dir()` → 리소스 루트
  - `styles_dir()`, `icons_dir()`, `resource_path("styles","github.css")`
- 번들 경로 규칙: `.spec` 의 `datas` 에서 리소스를
  **`('src/mdviewer/resources', 'mdviewer/resources')`** 로 수집한다.
  → frozen 시 리소스 루트 = `sys._MEIPASS / "mdviewer/resources"`.
  paths.py 의 `_BUNDLE_RESOURCE_SUBPATH = "mdviewer/resources"` 와 **반드시 일치**.
- QWebEngineView 는 PyInstaller 번들 시 Qt WebEngine 프로세스/리소스 누락이 잦다.
  packager 는 PySide6 hook 이 `QtWebEngineProcess`, ICU, locales, resources 를
  포함하는지 빌드 후 검증한다(빈 화면이면 이 누락이 주원인).
- CSS 는 `setHtml` 에 인라인 `<style>` 로 주입하거나 `file://` 로 링크. 인라인 주입이
  번들 경로 의존을 줄여 더 견고함(권장). 단 Pygments CSS 등 큰 스타일은 styles/ 에서
  읽어 인라인으로 삽입.

---

## 8. HTML 문서 셸 (theme.py 책임 — 경계 명시)

renderer 는 **본문 HTML(<body> 내용)만** 만든다. 완전한 HTML 문서(`<html><head>`
+ CSS + 본문)는 theme.py 가 조립한다. 이렇게 나누면:
- renderer 는 테마/CSS 를 몰라도 됨(순수 변환).
- 테마 전환 = theme.py 가 다른 CSS 로 같은 본문을 다시 감싸 setHtml.

```python
# theme.py (개략)
def build_document(body_html: str, theme: str, base_dir: Path | None) -> str:
    """RenderResult.html 을 받아 CSS 포함 완전한 HTML 문서를 반환."""
```

---

## 9. 확장 후보 (범위 외 — 구현하지 않음, 표기만)

- 마크다운 편집/저장, 다중 탭, 인쇄/PDF 내보내기, 검색(Ctrl+F) 하이라이트,
  Mermaid/수식(KaTeX), 사용자 정의 CSS 테마. 표준 뷰어 범위 밖이므로 후보로만 둔다.
