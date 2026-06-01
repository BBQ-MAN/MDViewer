---
name: markdown-rendering
description: Python에서 마크다운 텍스트를 HTML로 변환하는 렌더링 엔진 구현 스킬. 코드 구문 강조(Pygments), 목차/앵커 생성, GFM 확장(테이블·각주·작업목록), 상대경로 해석, 파일 인코딩 감지, 파일 변경 감시(watchdog)를 다룬다. MDViewer 코어 엔진(renderer.py, file_watcher.py) 구현 시 반드시 사용. "렌더링 엔진", "마크다운 변환", "코드 하이라이트", "파일 감시" 작업에 적용.
---

# Markdown Rendering — MDViewer 코어 엔진

비-GUI 코어를 구현한다. **PySide6에 의존하지 않고** 마크다운→HTML 변환, 파일 I/O, 변경 감시를 제공한다. 청사진의 렌더 API 계약을 정확히 따른다.

## 1. 렌더링 파이프라인 (markdown-it-py 권장)

```python
from markdown_it import MarkdownIt
from mdit_py_plugins.front_matter import front_matter_plugin
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.tasklists import tasklists_plugin
from mdit_py_plugins.anchors import anchors_plugin

def _build_md() -> MarkdownIt:
    md = (
        MarkdownIt("gfm-like", {"html": True, "linkify": True, "typographer": True})
        .use(front_matter_plugin)
        .use(footnote_plugin)
        .use(tasklists_plugin, enabled=True)
        .use(anchors_plugin, max_level=3, permalink=False)  # 헤딩 id 부여 -> 목차 앵커
    )
    md.options["highlight"] = _highlight_code   # 아래 Pygments 연동
    return md
```

대안: `Markdown`(python-markdown) + `extensions=['fenced_code','codehilite','tables','toc','footnotes']`. 둘 중 하나로 일관되게 간다.

## 2. 코드 구문 강조 (Pygments)

```python
from pygments import highlight
from pygments.lexers import get_lexer_by_name, guess_lexer
from pygments.formatters import HtmlFormatter
from pygments.util import ClassNotFound

def _highlight_code(code: str, lang: str, _attrs) -> str:
    try:
        lexer = get_lexer_by_name(lang) if lang else guess_lexer(code)
    except ClassNotFound:
        return ""  # markdown-it가 기본 escape 처리하도록 빈 문자열 반환
    formatter = HtmlFormatter(nowrap=False, cssclass="codehilite")
    return highlight(code, lexer, formatter)
```

CSS는 `HtmlFormatter(style="...").get_style_defs(".codehilite")`로 생성해 라이트/다크용 두 벌을 ui-dev에 제공하거나 resources에 둔다. 테마별 style 이름(예: `default`, `monokai`)을 분리한다.

## 3. 목차(TOC)와 앵커

헤딩에 안정적 id(슬러그)를 부여하고, 목차 항목을 구조화해 반환한다. UI가 목차 패널을 그릴 수 있도록 `RenderResult.toc`로 노출한다.

```python
@dataclass
class TocItem:
    level: int      # 1~6
    text: str
    anchor: str     # 헤딩 id와 일치 -> 내부 링크 #anchor

@dataclass
class RenderResult:
    html: str
    toc: list[TocItem]
    title: str | None
```

슬러그는 결정적으로 생성하고 중복 시 `-1`, `-2` 접미사로 유일화한다. 내부 링크 `[x](#anchor)`가 동작하려면 앵커 id와 슬러그가 정확히 일치해야 한다.

## 4. 상대 경로 해석

문서 안의 `![img](./pic.png)`, `[doc](other.md)`는 문서 위치(`base_dir`) 기준이다. 렌더 시 상대 경로를 `base_dir` 기준 절대 경로(또는 `file://` URL)로 치환해 UI의 QWebEngineView가 로드할 수 있게 한다. 외부 URL(`http(s)://`)은 그대로 둔다.

## 5. 파일 I/O — 임의 파일 견고성

사용자는 무엇이든 연다. 크래시는 금물이다.

```python
from charset_normalizer import from_path

def read_markdown(path: Path) -> str:
    result = from_path(path).best()
    if result is None:
        raise ValueError(f"텍스트로 디코딩할 수 없는 파일: {path}")
    return str(result)
```

- 빈 파일 → 빈 문자열 정상 처리.
- 바이너리/디코딩 실패 → 명확한 예외 또는 안내 HTML 반환(계약에 맞춰 일관되게).
- 매우 큰 파일 → 필요 시 크기 경고, 하지만 표준 범위에선 단순 처리 우선.

## 6. 파일 변경 감시 (watchdog)

외부 편집기에서 파일이 바뀌면 UI에 알려 다시 렌더하게 한다.

```python
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class FileWatcher:
    def __init__(self, on_changed):     # on_changed: 콜백 (스레드 주의)
        self._observer = Observer()
        self._on_changed = on_changed
        self._path = None

    def watch(self, path: Path) -> None:
        self._stop()
        self._path = path
        handler = _Handler(path, self._on_changed)
        self._observer.schedule(handler, str(path.parent), recursive=False)
        if not self._observer.is_alive():
            self._observer.start()
```

watchdog 콜백은 **워커 스레드**에서 실행된다. UI 스레드로 안전하게 넘기는 것은 ui-dev의 책임이지만, 계약(콜백 시그니처 또는 Qt Signal)을 명확히 노출하라. 저장 중 중복 이벤트(디바운스)는 짧은 타이머로 합치는 것을 권장한다.

## 7. 견고성 체크리스트

- [ ] 빈 파일, 깨진 마크다운, 바이너리 파일에도 크래시 없음
- [ ] 알 수 없는 코드 언어에도 하이라이트가 깨지지 않음
- [ ] 목차 앵커와 헤딩 id가 정확히 일치 (내부 링크 동작)
- [ ] 상대 경로 이미지가 base_dir 기준으로 해석됨
- [ ] watchdog 디바운스로 중복 갱신 억제
- [ ] 코어가 PySide6 import 없이 단위 테스트 가능

완료 후 실제 노출한 공개 시그니처를 `_workspace/02_core_engine_notes.md`에 기록해 ui-dev·qa가 경계면을 맞추게 한다.
