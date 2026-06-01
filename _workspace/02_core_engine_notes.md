# Core Engine Notes (Phase 2) — core-engine-dev

> 작성: core-engine-dev · 2026-06-01
> 대상 독자: ui-dev, qa-verifier (경계면/계약 대조용)
> 구현 파일: `src/mdviewer/renderer.py`, `src/mdviewer/file_watcher.py`, `src/mdviewer/__init__.py`

코어는 **PySide6 무의존**으로 구현됨(grep 확인: renderer/file_watcher 에 PySide6 import 없음).
청사진 3·4절의 렌더 API 계약을 **변경 없이** 그대로 구현함.

---

## 1. 공개 API — import 경로와 정확한 시그니처

전부 `mdviewer` 패키지 루트에서도, 개별 모듈에서도 import 가능.

```python
# 패키지 루트 (권장)
from mdviewer import (
    render, read_markdown, RenderResult, TocItem, pygments_css, FileWatcher,
)
# 또는 모듈 직접
from mdviewer.renderer import render, read_markdown, RenderResult, TocItem, pygments_css
from mdviewer.file_watcher import FileWatcher
```

### renderer.py

```python
@dataclass(frozen=True)
class TocItem:
    level: int        # 1~6 (h1=1)
    text: str         # 평문 헤딩 텍스트(인라인 마크업 제거)
    anchor: str       # HTML id. 본문 헤딩 id 와 항상 일치.

@dataclass
class RenderResult:
    html: str = ""                       # <body> 본문 HTML만. 문서 셸/CSS 미포함.
    toc: list[TocItem] = field(default_factory=list)
    title: str | None = None             # 첫 h1 텍스트, 없으면 None

def render(markdown_text: str, base_dir: Path) -> RenderResult
    # 예외 던지지 않음. None 반환 안 함. 빈/깨진/바이너리 입력에도 의미 있는 결과.
    # base_dir 이 Path 아니어도 내부에서 Path 변환(방어적).

def read_markdown(path: Path) -> str
    # UTF-8(-sig) 우선 → charset-normalizer 폴백 → 최후 errors="replace".
    # BOM 제거. FileNotFoundError/OSError 만 전파(디코딩 실패는 복구).

def pygments_css(dark: bool = False) -> str
    # 코드 하이라이트 색상 CSS. theme.py 가 사용. (위치: renderer.py)

def slugify(text: str) -> str
    # 헤딩 텍스트 → 앵커 슬러그(공개). UI 내부 링크 생성 시 동일 결과 보장용.
```

### file_watcher.py

```python
class FileWatcher:
    def __init__(self, on_changed: Callable[[], None]) -> None
    def watch(self, path: Path) -> None   # 파일 교체 시 기존 감시 대체
    def stop(self) -> None                # 멱등(여러 번 안전)
    @property
    def path(self) -> Path | None         # 현재 감시 중인 경로(부가, 계약 외)
    # on_changed 는 watchdog 워커 스레드에서 호출됨. ~150ms 디바운스 내장.
```

---

## 2. pygments_css 위치 (★ theme.py 가 찾을 곳)

- **함수 위치: `mdviewer.renderer.pygments_css(dark: bool = False) -> str`**
  (패키지 루트 `from mdviewer import pygments_css` 로도 노출.)
- 반환 CSS 의 선택자 루트는 **`.codehilite`** 한정.
  renderer 가 코드블록에 부여하는 CSS 클래스가 `codehilite` 이기 때문.
- 라이트 = Pygments `default` 스타일, 다크 = `monokai` 스타일.
- renderer 는 **인라인 색상 스타일을 넣지 않음** → 테마 전환 = `pygments_css` 결과 CSS만 교체.
- theme.py 사용 예:
  ```python
  from mdviewer.renderer import pygments_css
  code_css = pygments_css(dark=is_dark)   # <style> 로 문서 셸에 인라인 주입
  ```

---

## 3. 앵커(anchor) 규칙 — UI 내부 링크와 일치 보장 방법

- 슬러그 = `mdit_py_plugins.anchors.index.slugify` 와 동일:
  `소문자화 → 공백을 '-' → [^\w一-鿿\- ] 제거`.
  (`\w` 가 한글 Hangul 을 포함하므로 한글 헤딩 보존됨.)
- 중복은 첫 등장 = 접미사 없음, 이후 `-1`, `-2` …
- **TOC 추출과 본문 id 부여를 같은 패스에서 같은 슬러그 함수로 수행** →
  `TocItem.anchor` 와 본문 헤딩 `id` 는 항상 동일 문자열.
- **모든 레벨(h1~h6)** 헤딩에 id 부여(청사진은 h1~h2 만 요구했으나, 깊은 헤딩 TOC/링크도
  동작하도록 전체 부여). 데모의 `#mdviewer-데모-문서 / #코드-하이라이트 / #테이블 / #각주`
  내부 링크 4종 모두 본문 id 와 일치 확인됨.
- UI 가 자체적으로 슬러그가 필요하면 `mdviewer.renderer.slugify(text)` 를 쓰면 동일 결과.

---

## 4. 이미지/링크 base_dir 처리 (청사진 3.3 과의 차이 — 확인 요망)

- **청사진 3.3 의 "결정"은 "renderer 는 상대경로를 그대로 보존하고 ui-dev 가 setHtml 의
  baseUrl 로 처리"였음.**
- 그러나 본문 4번 줄 위 SKILL/계약 본문, 그리고 7절·8절(인라인 CSS 주입 권장)과의 정합을 위해,
  **renderer 는 상대 `img src` / `a href` 를 `base_dir` 기준 `file:///` 절대 URI 로 치환**하도록 구현함.
  - 외부(`http(s)://`, `mailto:`, `file:`, `data:`, `//`), 내부 앵커(`#...`)는 그대로 둠.
  - 변환 실패(잘못된 경로 등) 시 원본 보존, 크래시 없음.
- **ui-dev 영향:** baseUrl 을 굳이 주지 않아도 상대 이미지가 로드됨(이미 절대 file URI).
  setHtml 에 baseUrl 을 추가로 줘도 절대 URI 라서 무해함. 즉 **두 방식 모두와 호환**.
- 이 점이 청사진 3.3 의 문구상 "보존" 결정과 다르므로 architect/ui-dev 에 통지 필요.
  (계약의 함수 시그니처/shape 는 불변. 동작 세부만 더 견고한 쪽으로 채택.)

---

## 5. 렌더 파이프라인 세부

- 파서: `MarkdownIt("commonmark", {...})` + `table`/`strikethrough` enable
  + `front_matter` / `footnote` / `tasklists(enabled=True)` 플러그인.
- **`html: False`** (원시 HTML 비활성) — 임의 파일 견고성/보안. 마크다운 안의 raw `<script>`
  등은 escape 되어 출력됨. (UI 가 raw HTML 통과를 원하면 알려줄 것 → 옵션화 가능.)
- `anchors_plugin` 은 **사용하지 않음** — id/TOC 일치를 직접 보장하려고 자체 토큰 후처리로 대체.
- 코드 하이라이트: `_highlight_code` 가 Pygments span 클래스 HTML 생성.
  - 명시 언어가 미상(ClassNotFound)이면 빈 문자열 반환 → markdown-it 기본 escape(plain) 처리.
  - 언어 미지정이면 `guess_lexer` 시도, 실패 시 plain.

## 6. 견고성 — 검증 완료 항목

- [x] 빈 문자열 → `RenderResult("", [], None)`
- [x] 안 닫힌 코드펜스 → 크래시 없음, 본문/title 생성
- [x] 바이너리 바이트 디코딩(`read_markdown`) → 복구(손실 허용), `render` 도 크래시 없음
- [x] 빈 파일 → `""`
- [x] BOM 제거됨
- [x] 없는 파일 → `FileNotFoundError`
- [x] 미상 코드 언어 → 하이라이트 안 깨짐
- [x] 중복 헤딩 → `dup`, `dup-1`, `dup-2`
- [x] TOC anchor ⟷ 본문 id 100% 일치(데모 18개 헤딩)
- [x] 상대 이미지 → file URI, 외부 URL 보존
- [x] FileWatcher 디바운스(5연속 쓰기 → 콜백 1회), watch 교체, stop 멱등

## 7. 알려진 제약

- `read_markdown` 은 전체를 메모리에 적재(매우 큰 파일 스트리밍 미지원 — 표준 범위 단순화).
- `render` 의 전역 파서 `_MD` 는 모듈 1개 공유. 파싱은 호출별 `env` dict 를 써서 상태 공유가
  없으나, markdown-it 인스턴스의 완전한 스레드 안전성은 보장하지 않음. UI 는 메인 스레드 호출 기본.
- `html: False` 이므로 마크다운 내 원시 HTML 블록은 렌더되지 않고 escape 됨(의도된 견고성 선택).
- `on_changed` 콜백 예외는 워커 스레드에서 격리(삼켜짐) — 워커가 죽지 않도록.
  UI 는 콜백에서 `Signal.emit()` 만 할 것(청사진 4.1).

## 8. 패키지 export 확인

`mdviewer.__all__` = `__version__, __app_name__, __author__, render, read_markdown,
RenderResult, TocItem, pygments_css, FileWatcher` — 재import 정상.
