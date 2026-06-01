---
name: python-desktop-architecture
description: Python/PySide6 데스크톱 앱(MDViewer 마크다운 뷰어)의 프로젝트 구조·모듈 분리·렌더 API 계약·의존성을 설계하는 스킬. 아키텍처 청사진 작성, 모듈 경계 정의, 패키징 가능한 구조 설계가 필요할 때 반드시 사용. "구조 설계", "청사진", "모듈 분리", "계약 정의" 요청에 적용.
---

# Python Desktop Architecture — MDViewer 설계

마크다운 뷰어를 **병렬 개발 가능하고 패키징 안전한** 구조로 설계한다. 핵심 산출물은 `_workspace/01_architect_blueprint.md`다.

## 1. 표준 프로젝트 구조

```
MDViewer/
├── src/mdviewer/
│   ├── __init__.py
│   ├── __main__.py          # python -m mdviewer 진입점
│   ├── app.py               # QApplication 부트스트랩, 고DPI
│   ├── main_window.py       # QMainWindow, 메뉴/툴바/상태바 [ui-dev]
│   ├── renderer.py          # 마크다운 -> HTML 엔진 [core-engine-dev]
│   ├── file_watcher.py      # 파일 변경 감시 [core-engine-dev]
│   ├── theme.py             # 라이트/다크 CSS [ui-dev]
│   ├── settings.py          # QSettings 래퍼(최근 파일/테마) [ui-dev]
│   ├── paths.py             # 리소스 base path (sys._MEIPASS 대응) [공유]
│   └── resources/
│       ├── styles/          # github.css, dark.css
│       └── icons/           # app.ico
├── tests/                   # 코어 단위 테스트
├── samples/                 # 검증용 .md 샘플
├── requirements.txt
├── pyproject.toml
├── mdviewer.spec            # PyInstaller [packager]
└── README.md
```

각 파일에 **담당 에이전트**를 `[...]`로 표기해 책임을 명확히 한다.

## 2. 모듈 경계 — 가장 중요한 결정

**코어(비-GUI)와 UI를 엄격히 분리한다.** `renderer.py`와 `file_watcher.py`는 PySide6 import 없이 동작 가능해야 한다(파일 감시의 Qt 시그널 어댑터만 예외). 이유:
- 코어를 GUI 없이 단위 테스트할 수 있다 → QA가 빠르고 확실하다.
- 경계면이 좁고 명시적이면 통합 버그가 줄어든다.
- 두 개발자가 계약만 지키면 서로를 기다리지 않고 병렬로 일한다.

## 3. 렌더 API 계약 (병렬 개발의 핵심)

청사진에 함수 시그니처를 **구체적으로** 못박는다. 모호하면 ui-dev가 추측하고, 추측이 통합 버그가 된다. 권장 계약:

```python
# renderer.py — core-engine-dev가 구현, ui-dev가 호출
@dataclass
class RenderResult:
    html: str              # <body>에 들어갈 본문 HTML (목차 포함)
    toc: list[TocItem]     # 목차 항목 (level, text, anchor)
    title: str | None      # 첫 h1 또는 None

def render(markdown_text: str, base_dir: Path) -> RenderResult: ...
    # base_dir: 상대 경로 이미지/링크 해석 기준
    # 깨진/빈 입력에도 예외를 던지지 않고 의미 있는 결과 반환

# file I/O
def read_markdown(path: Path) -> str: ...   # 인코딩 자동 감지, UTF-8 우선

# file_watcher.py
class FileWatcher:
    def watch(self, path: Path) -> None: ...
    changed: Signal  # 또는 콜백 등록 — UI가 연결할 인터페이스
```

계약에는 **반환 shape, 예외 정책, 스레드 안전성**을 명시한다. 이 셋이 경계면 버그의 단골 원인이다.

## 4. 의존성 선정

| 목적 | 권장 | 사유 |
|------|------|------|
| GUI | `PySide6` | Qt 공식 바인딩, LGPL, QWebEngineView 렌더 품질 |
| 마크다운 파싱 | `markdown-it-py` (+ `mdit-py-plugins`) 또는 `Markdown` | GFM 확장, 플러그인 풍부 |
| 코드 하이라이트 | `Pygments` | 광범위한 언어, CSS 테마 생성 |
| 파일 감시 | `watchdog` | 크로스플랫폼 파일시스템 이벤트 |
| 인코딩 감지 | `charset-normalizer` | 임의 파일 안전 처리 |

버전은 상한을 느슨히 두되 PySide6는 메이저 버전을 고정해 패키징 재현성을 확보한다.

## 5. 패키징 친화 설계 (처음부터 반영)

PyInstaller 번들에서 리소스가 깨지는 사고를 막으려면 설계 단계에서 base path를 추상화한다:

```python
# paths.py
import sys
from pathlib import Path

def resource_dir() -> Path:
    if getattr(sys, "frozen", False):          # PyInstaller 번들
        return Path(sys._MEIPASS) / "resources"
    return Path(__file__).parent / "resources"
```

모든 CSS/아이콘 로드는 `resource_dir()`를 거치게 한다. 이 규칙을 청사진에 명시해 ui-dev와 packager가 공유하게 하라.

## 6. 청사진 산출물 체크리스트

`_workspace/01_architect_blueprint.md`에 다음을 모두 포함했는가?
- [ ] 디렉토리 트리 + 파일별 한 줄 책임 + 담당 에이전트
- [ ] 렌더 API 계약 (시그니처/반환 shape/예외/스레드 안전성)
- [ ] 의존성 목록 + 사유
- [ ] 빌드 순서와 통합 지점
- [ ] 패키징 base path 규칙

설계가 막히거나 트레이드오프 판단이 필요하면, 표준 뷰어 범위(파일 열기·실시간 렌더·코드 하이라이트·목차·다크모드·최근 파일) 안에서 **가장 단순하고 검증 가능한** 안을 택한다.
