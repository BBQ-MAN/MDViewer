"""MainWindow — MDViewer 의 QMainWindow.

메뉴/툴바/상태바, QWebEngineView 중앙 렌더 패널, 드래그앤드롭,
외부 변경 자동 새로고침(스크롤 보존), 최근 파일, 테마 토글, TOC 사이드 패널.

코어 호출 지점:
    - read_markdown(path)            : 파일 읽기(인코딩 자동 감지)
    - render(text, base_dir=...)     : 마크다운 → 본문 HTML + TOC
    - FileWatcher(on_changed=...)    : 외부 변경 감시(워커 스레드 콜백)
코어가 아직 없을 수 있으므로 import 는 graceful 하게 처리한다(병렬 개발).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QWidget,
)
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

from . import paths, theme as theme_mod
from .settings import Settings

# ---- 코어 엔진(병렬 개발) — 없으면 graceful degradation --------------------
try:  # pragma: no cover - 통합 시점에 따라 분기
    from .renderer import read_markdown, render  # type: ignore
    _CORE_AVAILABLE = True
    _CORE_IMPORT_ERROR = ""
except Exception as exc:  # ImportError 또는 의존성 누락
    _CORE_AVAILABLE = False
    _CORE_IMPORT_ERROR = str(exc)

    def read_markdown(path: Path) -> str:  # type: ignore
        return Path(path).read_text(encoding="utf-8", errors="replace")

    def render(markdown_text: str, base_dir: Path):  # type: ignore
        """코어 미존재 시 임시 폴백: 원문을 그대로 보여준다(개발용)."""
        import html as _html

        class _Toc(list):
            pass

        class _Result:
            def __init__(self, body: str) -> None:
                self.html = body
                self.toc = _Toc()
                self.title = None

        safe = _html.escape(markdown_text)
        body = (
            "<p style='color:#b35900'><strong>[코어 엔진 미연결]</strong> "
            "renderer.py 가 아직 없어 원문을 그대로 표시합니다.</p>"
            f"<pre><code>{safe}</code></pre>"
        )
        return _Result(body)

try:  # FileWatcher 도 별도 graceful 처리.
    from .file_watcher import FileWatcher  # type: ignore
    _WATCHER_AVAILABLE = True
except Exception:
    _WATCHER_AVAILABLE = False
    FileWatcher = None  # type: ignore

_MD_FILTER = "Markdown (*.md *.markdown *.mdown *.mkd);;모든 파일 (*.*)"
_RELOAD_DEBOUNCE_MS = 120
_ZOOM_MIN = 0.4
_ZOOM_MAX = 3.0
_ZOOM_STEP = 0.1


class _WatchBridge(QObject):
    """watchdog 워커 스레드 → 메인 스레드로 시그널을 넘기는 어댑터."""

    fileChanged = Signal()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = Settings()
        self._path: Path | None = None
        self._theme = self.settings.theme()
        self._zoom = self.settings.zoom()
        self._pending_scroll: tuple[float, float] | None = None
        self._recent_actions: list[QAction] = []

        self.setWindowTitle("MDViewer")
        self._apply_window_icon()
        self.resize(1100, 800)

        # ---- 중앙: TOC 패널 + 웹뷰 (스플리터) ----
        self.view = QWebEngineView(self)
        self.view.setAcceptDrops(False)  # 드롭은 메인 윈도우가 처리
        self.view.loadFinished.connect(self._on_load_finished)

        # file:// baseUrl 로 setHtml 하면 페이지 출처가 "로컬"로 취급되어
        # 기본값에선 원격(http/https) 이미지·리소스 로드가 차단된다.
        # 마크다운의 외부 이미지(예: 배지 shields.io)를 보여주려면 명시적으로 허용한다.
        _ws = self.view.settings()
        _ws.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        _ws.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)

        self.toc_list = QListWidget(self)
        self.toc_list.setObjectName("tocList")
        self.toc_list.setMaximumWidth(360)
        self.toc_list.setMinimumWidth(140)
        self.toc_list.itemClicked.connect(self._on_toc_clicked)

        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.addWidget(self.toc_list)
        self.splitter.addWidget(self.view)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([240, 860])
        self.setCentralWidget(self.splitter)

        # ---- 파일 감시 브리지 ----
        self._bridge = _WatchBridge()
        self._bridge.fileChanged.connect(
            self._on_file_changed, Qt.ConnectionType.QueuedConnection
        )
        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.setInterval(_RELOAD_DEBOUNCE_MS)
        self._reload_timer.timeout.connect(self._reload_current)
        self._watcher = None
        if _WATCHER_AVAILABLE:
            try:
                self._watcher = FileWatcher(on_changed=self._bridge.fileChanged.emit)
            except Exception:
                self._watcher = None

        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self.statusBar().showMessage("준비됨")

        self.setAcceptDrops(True)
        self._restore_window_state()
        self._refresh_recent_menu()
        self._apply_zoom()
        self._show_welcome()

    # ------------------------------------------------------------------ #
    # 아이콘 / 윈도우 상태
    # ------------------------------------------------------------------ #
    def _apply_window_icon(self) -> None:
        try:
            ico = paths.resource_path("icons", "app.ico")
            if ico.exists():
                self.setWindowIcon(QIcon(str(ico)))
        except Exception:
            pass  # 아이콘 없으면 무시(packager 가 처리)

    def _restore_window_state(self) -> None:
        geo = self.settings.geometry()
        if geo is not None:
            try:
                self.restoreGeometry(geo)
            except Exception:
                pass
        state = self.settings.window_state()
        if state is not None:
            try:
                self.restoreState(state)
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # 액션 / 메뉴 / 툴바
    # ------------------------------------------------------------------ #
    def _build_actions(self) -> None:
        self.act_open = QAction("열기...", self)
        self.act_open.setShortcut(QKeySequence.StandardKey.Open)  # Ctrl+O
        self.act_open.triggered.connect(self.open_dialog)

        self.act_reload = QAction("새로고침", self)
        self.act_reload.setShortcut(QKeySequence("Ctrl+R"))
        self.act_reload.triggered.connect(self._reload_current)

        self.act_exit = QAction("종료", self)
        self.act_exit.setShortcut(QKeySequence.StandardKey.Quit)
        self.act_exit.triggered.connect(self.close)

        self.act_toggle_theme = QAction("테마 전환", self)
        self.act_toggle_theme.setShortcut(QKeySequence("Ctrl+T"))
        self.act_toggle_theme.triggered.connect(self.toggle_theme)

        self.act_zoom_in = QAction("확대", self)
        # Ctrl+= 와 Ctrl++ 양쪽 수용
        self.act_zoom_in.setShortcuts(
            [QKeySequence("Ctrl+="), QKeySequence("Ctrl++"), QKeySequence.StandardKey.ZoomIn]
        )
        self.act_zoom_in.triggered.connect(self.zoom_in)

        self.act_zoom_out = QAction("축소", self)
        self.act_zoom_out.setShortcuts(
            [QKeySequence("Ctrl+-"), QKeySequence.StandardKey.ZoomOut]
        )
        self.act_zoom_out.triggered.connect(self.zoom_out)

        self.act_zoom_reset = QAction("줌 초기화", self)
        self.act_zoom_reset.setShortcut(QKeySequence("Ctrl+0"))
        self.act_zoom_reset.triggered.connect(self.zoom_reset)

        self.act_fullscreen = QAction("전체화면", self)
        self.act_fullscreen.setShortcut(QKeySequence("F11"))
        self.act_fullscreen.setCheckable(True)
        self.act_fullscreen.triggered.connect(self.toggle_fullscreen)

        self.act_toggle_toc = QAction("목차 표시", self)
        self.act_toggle_toc.setShortcut(QKeySequence("Ctrl+\\"))
        self.act_toggle_toc.setCheckable(True)
        self.act_toggle_toc.setChecked(self.settings.toc_visible())
        self.act_toggle_toc.triggered.connect(self.toggle_toc)
        self.toc_list.setVisible(self.settings.toc_visible())

        self.act_about = QAction("MDViewer 정보", self)
        self.act_about.triggered.connect(self.show_about)

        self.act_clear_recent = QAction("최근 파일 지우기", self)
        self.act_clear_recent.triggered.connect(self._clear_recent)

    def _build_menus(self) -> None:
        mb = self.menuBar()

        m_file = mb.addMenu("파일(&F)")
        m_file.addAction(self.act_open)
        self.menu_recent = m_file.addMenu("최근 파일(&R)")
        m_file.addAction(self.act_reload)
        m_file.addSeparator()
        m_file.addAction(self.act_exit)

        m_view = mb.addMenu("보기(&V)")
        m_view.addAction(self.act_toggle_theme)
        m_view.addSeparator()
        m_view.addAction(self.act_zoom_in)
        m_view.addAction(self.act_zoom_out)
        m_view.addAction(self.act_zoom_reset)
        m_view.addSeparator()
        m_view.addAction(self.act_fullscreen)
        m_view.addAction(self.act_toggle_toc)

        m_help = mb.addMenu("도움말(&H)")
        m_help.addAction(self.act_about)

    def _build_toolbar(self) -> None:
        tb = self.addToolBar("메인")
        tb.setObjectName("mainToolbar")
        tb.setMovable(False)
        tb.addAction(self.act_open)
        tb.addAction(self.act_reload)
        tb.addSeparator()
        tb.addAction(self.act_zoom_out)
        tb.addAction(self.act_zoom_reset)
        tb.addAction(self.act_zoom_in)
        tb.addSeparator()
        tb.addAction(self.act_toggle_theme)
        tb.addAction(self.act_toggle_toc)

    # ------------------------------------------------------------------ #
    # 최근 파일 메뉴
    # ------------------------------------------------------------------ #
    def _refresh_recent_menu(self) -> None:
        self.menu_recent.clear()
        self._recent_actions.clear()
        recents = self.settings.recent_files()
        if not recents:
            empty = QAction("(없음)", self)
            empty.setEnabled(False)
            self.menu_recent.addAction(empty)
            return
        for i, path_str in enumerate(recents, start=1):
            name = Path(path_str).name
            act = QAction(f"&{i}  {name}", self)
            act.setData(path_str)
            act.setStatusTip(path_str)
            act.triggered.connect(lambda checked=False, p=path_str: self.open_path(Path(p)))
            self.menu_recent.addAction(act)
            self._recent_actions.append(act)
        self.menu_recent.addSeparator()
        self.menu_recent.addAction(self.act_clear_recent)

    def _clear_recent(self) -> None:
        self.settings.clear_recent_files()
        self._refresh_recent_menu()

    # ------------------------------------------------------------------ #
    # 파일 열기 / 드래그앤드롭
    # ------------------------------------------------------------------ #
    def open_dialog(self) -> None:
        start_dir = str(self._path.parent) if self._path else ""
        path, _ = QFileDialog.getOpenFileName(self, "마크다운 열기", start_dir, _MD_FILTER)
        if path:
            self.open_path(Path(path))

    def open_path(self, path: Path) -> None:
        """파일 읽기→렌더→감시 등록→최근 추가→타이틀 갱신을 한 곳에서 처리."""
        path = Path(path)
        if not path.exists():
            QMessageBox.warning(self, "파일 없음", f"파일을 찾을 수 없습니다:\n{path}")
            self._drop_from_recent(path)
            return
        if not path.is_file():
            QMessageBox.warning(self, "열 수 없음", f"파일이 아닙니다:\n{path}")
            return

        self._path = path
        self._pending_scroll = None  # 새 문서는 상단부터
        ok = self._reload_current(preserve_scroll=False)
        if not ok:
            return

        # 감시 등록
        if self._watcher is not None:
            try:
                self._watcher.watch(path)
            except Exception:
                pass

        # 최근 파일 + 타이틀
        self.settings.add_recent_file(str(path))
        self._refresh_recent_menu()
        self.setWindowTitle(f"{path.name} — MDViewer")
        self.statusBar().showMessage(str(path))

    def _drop_from_recent(self, path: Path) -> None:
        p = str(Path(path).resolve())
        items = [x for x in self.settings.recent_files() if x != p]
        self.settings.set_recent_files(items)
        self._refresh_recent_menu()

    # Qt 드래그앤드롭
    def dragEnterEvent(self, event) -> None:  # noqa: N802
        md = event.mimeData()
        if md.hasUrls() and any(u.isLocalFile() for u in md.urls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self.open_path(Path(url.toLocalFile()))
                event.acceptProposedAction()
                return
        event.ignore()

    # ------------------------------------------------------------------ #
    # 렌더링
    # ------------------------------------------------------------------ #
    def _reload_current(self, preserve_scroll: bool = True) -> bool:
        """현재 파일을 다시 읽어 렌더한다. 성공 시 True."""
        if self._path is None:
            return False
        try:
            text = read_markdown(self._path)
        except FileNotFoundError:
            self.statusBar().showMessage(f"파일이 사라졌습니다: {self._path}")
            return False
        except OSError as exc:
            QMessageBox.warning(self, "읽기 오류", f"파일을 읽을 수 없습니다:\n{exc}")
            return False

        result = render(text, base_dir=self._path.parent)  # 예외 안 던짐(계약)

        if preserve_scroll:
            # loadFinished 후 복원할 스크롤 위치를 비동기로 캡처.
            self._capture_scroll_then_render(result)
        else:
            self._set_document(result, restore_scroll=False)
        return True

    def _capture_scroll_then_render(self, result) -> None:
        """현재 스크롤을 JS 로 읽은 뒤 렌더(외부 변경 시 위치 보존)."""
        def _cb(value) -> None:
            try:
                x, y = value
                self._pending_scroll = (float(x), float(y))
            except Exception:
                self._pending_scroll = None
            self._set_document(result, restore_scroll=True)

        try:
            self.view.page().runJavaScript(
                "[window.scrollX, window.scrollY]", 0, _cb
            )
        except Exception:
            self._set_document(result, restore_scroll=False)

    def _set_document(self, result, restore_scroll: bool) -> None:
        body = getattr(result, "html", "") or ""
        dark = self._theme == theme_mod.DARK
        html = theme_mod.wrap_document(body, dark)
        base = QUrl.fromLocalFile(str(self._path.parent) + "/") if self._path else QUrl()
        if not restore_scroll:
            self._pending_scroll = None
        self.view.setHtml(html, base)
        self._populate_toc(getattr(result, "toc", []) or [])

    def _show_welcome(self) -> None:
        dark = self._theme == theme_mod.DARK
        if not _CORE_AVAILABLE:
            msg = (
                "마크다운 파일을 열어주세요. (Ctrl+O 또는 드래그앤드롭)<br>"
                "<span style='font-size:0.8em;color:#b35900'>"
                "[안내] 코어 렌더 엔진(renderer.py)이 아직 연결되지 않았습니다 — "
                "원문 미리보기 모드로 동작합니다.</span>"
            )
            self.view.setHtml(theme_mod.empty_document(dark, msg))
        else:
            self.view.setHtml(theme_mod.empty_document(dark))

    def _on_load_finished(self, ok: bool) -> None:
        if ok and self._pending_scroll is not None:
            x, y = self._pending_scroll
            self._pending_scroll = None
            try:
                self.view.page().runJavaScript(f"window.scrollTo({x}, {y});")
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # 파일 변경 콜백 (메인 스레드)
    # ------------------------------------------------------------------ #
    def _on_file_changed(self) -> None:
        # 디바운스(콜백 폭주 방어) — 최종 1회만 reload.
        self._reload_timer.start()

    # ------------------------------------------------------------------ #
    # TOC
    # ------------------------------------------------------------------ #
    def _populate_toc(self, toc) -> None:
        self.toc_list.clear()
        for item in toc:
            try:
                level = int(getattr(item, "level", 1))
                text = str(getattr(item, "text", ""))
                anchor = str(getattr(item, "anchor", ""))
            except Exception:
                continue
            indent = "    " * max(0, level - 1)
            li = QListWidgetItem(f"{indent}{text}")
            li.setData(Qt.ItemDataRole.UserRole, anchor)
            self.toc_list.addItem(li)

    def _on_toc_clicked(self, item: QListWidgetItem) -> None:
        anchor = item.data(Qt.ItemDataRole.UserRole)
        if not anchor:
            return
        # 본문 헤딩 id 로 스크롤(앵커 == 헤딩 id, 계약 §3.1).
        js = (
            "var el = document.getElementById(%r);"
            "if (el) { el.scrollIntoView({behavior:'smooth', block:'start'}); }"
            % str(anchor)
        )
        try:
            self.view.page().runJavaScript(js)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # 테마 / 줌 / 전체화면 / TOC 토글
    # ------------------------------------------------------------------ #
    def toggle_theme(self) -> None:
        self._theme = theme_mod.toggle_theme(self._theme)
        self.settings.set_theme(self._theme)
        if self._path is not None:
            # CSS 만 교체 — 스크롤 보존하며 다시 감싸 렌더.
            self._reload_current(preserve_scroll=True)
        else:
            self._show_welcome()
        self.statusBar().showMessage(
            f"테마: {'다크' if self._theme == theme_mod.DARK else '라이트'}", 2000
        )

    def _apply_zoom(self) -> None:
        self._zoom = max(_ZOOM_MIN, min(_ZOOM_MAX, self._zoom))
        self.view.setZoomFactor(self._zoom)
        self.settings.set_zoom(self._zoom)

    def zoom_in(self) -> None:
        self._zoom += _ZOOM_STEP
        self._apply_zoom()
        self.statusBar().showMessage(f"확대: {int(self._zoom * 100)}%", 1500)

    def zoom_out(self) -> None:
        self._zoom -= _ZOOM_STEP
        self._apply_zoom()
        self.statusBar().showMessage(f"축소: {int(self._zoom * 100)}%", 1500)

    def zoom_reset(self) -> None:
        self._zoom = 1.0
        self._apply_zoom()
        self.statusBar().showMessage("줌 100%", 1500)

    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
            self.act_fullscreen.setChecked(False)
        else:
            self.showFullScreen()
            self.act_fullscreen.setChecked(True)

    def toggle_toc(self) -> None:
        visible = not self.toc_list.isVisible()
        self.toc_list.setVisible(visible)
        self.act_toggle_toc.setChecked(visible)
        self.settings.set_toc_visible(visible)

    # ------------------------------------------------------------------ #
    # 도움말
    # ------------------------------------------------------------------ #
    def show_about(self) -> None:
        from . import __version__

        core = "연결됨" if _CORE_AVAILABLE else "미연결(원문 미리보기)"
        QMessageBox.about(
            self,
            "MDViewer 정보",
            f"<h3>MDViewer {__version__}</h3>"
            "<p>Python/PySide6 기반 마크다운 뷰어.</p>"
            "<p>만든이 : One &amp; Wise for YONI</p>"
            f"<p style='color:gray;font-size:0.9em'>코어 엔진: {core}</p>"
            "<p style='color:gray;font-size:0.85em'>"
            "단축키: Ctrl+O 열기 · Ctrl+R 새로고침 · Ctrl+T 테마 · "
            "Ctrl+= / Ctrl+- / Ctrl+0 줌 · F11 전체화면 · Ctrl+\\ 목차</p>",
        )

    # ------------------------------------------------------------------ #
    # 종료 정리
    # ------------------------------------------------------------------ #
    def closeEvent(self, event) -> None:  # noqa: N802
        try:
            self.settings.set_geometry(self.saveGeometry())
            self.settings.set_window_state(self.saveState())
            self.settings.sync()
        except Exception:
            pass
        if self._watcher is not None:
            try:
                self._watcher.stop()
            except Exception:
                pass
        super().closeEvent(event)
