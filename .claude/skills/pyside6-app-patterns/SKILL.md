---
name: pyside6-app-patterns
description: PySide6로 데스크톱 GUI를 구현하는 스킬. QMainWindow 메뉴/툴바, QWebEngineView로 HTML 렌더, QFileDialog 파일 열기, 드래그앤드롭, QSettings로 최근파일/테마 영속화, 시그널/슬롯, 다크/라이트 테마, 단축키를 다룬다. MDViewer UI(main_window.py, app.py, theme.py) 구현 시 반드시 사용. "PySide6", "Qt UI", "윈도우", "메뉴", "WebEngineView", "테마", "최근 파일" 작업에 적용.
---

# PySide6 App Patterns — MDViewer UI

데스크톱 UI 전체를 구현한다. 마크다운 변환은 **core 엔진을 계약대로 호출**하고(직접 파싱 금지), 너는 표시·상호작용·설정에 집중한다.

## 1. 앱 부트스트랩

```python
# app.py
import sys
from PySide6.QtWidgets import QApplication
from .main_window import MainWindow

def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("MDViewer")
    app.setOrganizationName("MDViewer")   # QSettings 키 경로에 사용
    win = MainWindow()
    if len(sys.argv) > 1:                  # 명령줄/연결프로그램으로 파일 받기
        win.open_path(Path(sys.argv[1]))
    win.show()
    return app.exec()

# __main__.py
from .app import main
import sys
if __name__ == "__main__":
    sys.exit(main())
```

고DPI는 Qt6에서 기본 활성. 필요 시 `Qt.HighDpiScaleFactorRoundingPolicy`만 조정.

## 2. 메인 윈도우 + 메뉴

```python
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.view = QWebEngineView()
        self.setCentralWidget(self.view)
        self._build_menus()
        self._build_recent_menu()
        self.resize(1000, 760)
```

메뉴 구성: **File**(Open `Ctrl+O`, Open Recent ▸, Reload `Ctrl+R`, Exit), **View**(Toggle Theme, Zoom In `Ctrl+=`, Zoom Out `Ctrl+-`, Reset Zoom `Ctrl+0`, Fullscreen `F11`, Toggle TOC), **Help**(About). 각 액션은 `QAction`에 `setShortcut`.

## 3. QWebEngineView로 렌더 — base URL이 핵심

엔진이 만든 본문 HTML에 테마 CSS를 입혀 완성 문서로 감싼 뒤 로드한다. **상대 경로 이미지가 동작하려면 baseUrl을 문서 폴더로 지정**해야 한다.

```python
def render_current(self):
    text = read_markdown(self._path)              # core 호출
    result = render(text, base_dir=self._path.parent)  # core 호출
    html = self._wrap_html(result.html)           # <head>에 테마 CSS 주입
    base = QUrl.fromLocalFile(str(self._path.parent) + "/")
    self.view.setHtml(html, base)                 # baseUrl 필수
```

`_wrap_html`은 `<!DOCTYPE html><html><head><style>{theme_css}{pygments_css}</style></head><body>{body}</body></html>` 형태. 테마 토글 시 CSS만 바꿔 다시 감싼다.

## 4. 파일 열기 / 드래그앤드롭

```python
def open_dialog(self):
    path, _ = QFileDialog.getOpenFileName(
        self, "마크다운 열기", "", "Markdown (*.md *.markdown *.mdown);;모든 파일 (*.*)")
    if path:
        self.open_path(Path(path))

# 드래그앤드롭: setAcceptDrops(True) + dragEnterEvent/dropEvent에서 .md 수락
def dragEnterEvent(self, e):
    if e.mimeData().hasUrls(): e.acceptProposedAction()
def dropEvent(self, e):
    self.open_path(Path(e.mimeData().urls()[0].toLocalFile()))
```

`open_path`는 파일 읽기→렌더→감시 등록→최근 파일 추가→타이틀 갱신을 한 곳에서 처리한다.

## 5. 실시간 갱신 — 스레드 안전

watchdog 콜백은 워커 스레드에서 온다. **UI를 워커 스레드에서 만지면 크래시**한다. Qt 시그널로 메인 스레드에 넘긴다.

```python
class WatchBridge(QObject):
    fileChanged = Signal()

self._bridge = WatchBridge()
self._bridge.fileChanged.connect(self._on_file_changed)   # 큐 연결(자동)
self._watcher = FileWatcher(on_changed=lambda: self._bridge.fileChanged.emit())

def _on_file_changed(self):
    pos = self.view.page().scrollPosition()   # 스크롤 보존
    self.render_current()
    # load 완료 후 pos로 복원 (loadFinished 시그널에서)
```

외부 변경 시 스크롤 위치를 유지해 사용자 경험을 해치지 않는다.

## 6. 최근 파일 / 테마 영속화 (QSettings)

```python
from PySide6.QtCore import QSettings
s = QSettings()                 # app/org 이름으로 자동 위치(레지스트리)
s.setValue("recent_files", self._recent[:10])
s.setValue("theme", self._theme)   # "light" | "dark"
```

시작 시 읽어 최근 메뉴와 테마를 복원한다. 최근 파일은 최대 10개, 중복 제거, 최신이 위.

## 7. 테마 (theme.py 연동)

라이트/다크 두 벌의 본문 CSS + 두 벌의 Pygments CSS를 보유하고 토글한다. GitHub 스타일 CSS를 기본으로 하면 친숙하다. 토글 시 즉시 다시 렌더하고 QSettings에 저장.

## 8. 단축키 / UX 체크리스트

- [ ] Ctrl+O 열기, Ctrl+R 새로고침, Ctrl+= / - / 0 줌, F11 전체화면
- [ ] 드래그앤드롭으로 .md 열기
- [ ] 명령줄 인자(`mdviewer file.md`)로 파일 열기
- [ ] 외부 변경 시 스크롤 유지하며 자동 갱신
- [ ] 최근 파일 10개 영속화, 테마 영속화
- [ ] 상대 경로 이미지가 baseUrl로 표시됨
- [ ] 코어 엔진은 계약(`02_core_engine_notes.md`)대로만 호출

## 9. 패키징 대비

CSS/아이콘 로드는 architect의 `paths.resource_dir()`를 거친다(직접 `__file__` 상대경로 금지). 그래야 PyInstaller 번들에서 깨지지 않는다. packager와 base path 처리를 공유하라.

완료 후 `_workspace/03_ui_notes.md`에 실행법(`python -m mdviewer`)과 엔진 호출 지점을 기록한다.
