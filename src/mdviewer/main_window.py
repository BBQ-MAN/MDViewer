"""MainWindow — MDViewer 의 QMainWindow.

메뉴/툴바/상태바, QWebEngineView 중앙 렌더 패널, 드래그앤드롭,
외부 변경 자동 새로고침(스크롤 보존), 최근 파일, 테마 토글, TOC 사이드 패널.

코어 호출 지점:
    - read_markdown(path)            : 파일 읽기(인코딩 자동 감지)
    - render(text, base_dir=...)     : 마크다운 → 본문 HTML + TOC
    - html_to_markdown(html)         : 클립보드 HTML → 마크다운 소스(항상 str, 예외 비전파)
    - write_markdown(path, text)     : 마크다운 소스 → 파일 저장(OSError 전파)
    - FileWatcher(on_changed=...)    : 외부 변경 감시(워커 스레드 콜백)
코어가 아직 없을 수 있으므로 import 는 graceful 하게 처리한다(병렬 개발).

문서 모델(Phase 6):
    - _doc_text : 현재 표시 중인 마크다운 소스(항상 최신). 렌더 입력원.
    - _path     : 연결된 파일(None = scratch, 미저장 임시 문서).
    - _dirty    : 마지막 저장 이후 변경 여부(타이틀 미저장 표시).
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
    from .renderer import (  # type: ignore
        html_to_markdown,
        read_markdown,
        render,
        write_markdown,
    )
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

    def html_to_markdown(html: str) -> str:  # type: ignore
        """코어 미존재 시 폴백: 태그 제거 평문(크래시 금지, 항상 str)."""
        import re
        from html import unescape

        return unescape(re.sub(r"<[^>]+>", "", html or "")).strip()

    def write_markdown(path: Path, text: str) -> None:  # type: ignore
        """코어 미존재 시 폴백: UTF-8(BOM 없음)·개행 보존 쓰기(OSError 전파)."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(text or "")

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
        # ---- 문서 모델(Phase 6) ----
        self._doc_text: str = ""          # 현재 표시 중인 마크다운 소스(렌더 입력원)
        self._path: Path | None = None    # 연결된 파일(None = scratch 임시 문서)
        self._dirty: bool = False         # 마지막 저장 이후 변경 여부
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
        # 외부 변경(watch)은 디스크 파일이 있을 때만 발생 → 디스크 재독, 스크롤 보존.
        self._reload_timer.timeout.connect(
            lambda: self._load_from_disk(preserve_scroll=True)
        )
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
        self.act_reload.triggered.connect(self.reload_current)

        self.act_paste = QAction("클립보드를 마크다운으로 붙여넣기", self)
        self.act_paste.setShortcut(QKeySequence("Ctrl+Shift+V"))
        self.act_paste.triggered.connect(self.paste_clipboard)

        self.act_save = QAction("저장", self)
        self.act_save.setShortcut(QKeySequence.StandardKey.Save)  # Ctrl+S
        self.act_save.triggered.connect(self.save)

        self.act_save_as = QAction("다른 이름으로 저장...", self)
        self.act_save_as.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self.act_save_as.triggered.connect(self.save_as)

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
        m_file.addSeparator()
        m_file.addAction(self.act_paste)
        m_file.addAction(self.act_save)
        m_file.addAction(self.act_save_as)
        m_file.addSeparator()
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
        tb.addAction(self.act_paste)
        tb.addAction(self.act_save)
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

        # 미저장 변경 가드(scratch/편집 후 다른 파일 열기 시 데이터 유실 방지).
        if not self._maybe_discard():
            return

        self._path = path
        self._pending_scroll = None  # 새 문서는 상단부터
        ok = self._load_from_disk(preserve_scroll=False)
        if not ok:
            return

        # 디스크 파일에 연결: 감시 등록 + 최근 추가 + 타이틀(_dirty=False).
        self._attach_path(path)

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
    # 문서 모델 — dirty/타이틀, scratch ↔ 파일 전환, watch 생명주기
    # ------------------------------------------------------------------ #
    def _set_dirty(self, dirty: bool) -> None:
        self._dirty = dirty
        self._update_title()

    def _update_title(self) -> None:
        name = self._path.name if self._path is not None else "제목 없음"
        mark = "•" if self._dirty else ""
        self.setWindowTitle(f"{mark}{name} — MDViewer")

    def _scratch_base_dir(self) -> Path:
        """scratch 문서의 상대경로 해석 기준 폴더.

        최근 파일의 부모가 있으면 사용, 없으면 현재 작업 디렉터리.
        """
        recents = self.settings.recent_files()
        if recents:
            try:
                parent = Path(recents[0]).parent
                if parent.exists():
                    return parent
            except Exception:
                pass
        return Path.cwd()

    def _suggest_name(self) -> Path:
        """scratch 저장 시 제안할 기본 파일 경로(_scratch_base_dir/제목 없음.md)."""
        return self._scratch_base_dir() / "제목 없음.md"

    def _set_scratch(self, text: str) -> None:
        """현재 문서를 경로 없는 scratch(미저장 임시 문서)로 전환.

        watch 중지(디스크 파일 없음) → _path=None → _doc_text 설정 → 즉시 렌더
        → _dirty=True(저장 유도). watch 생명주기 규칙: scratch 전환 = stop.
        """
        if self._watcher is not None:
            try:
                self._watcher.stop()
            except Exception:
                pass
        self._path = None
        self._doc_text = text
        self._pending_scroll = None  # 새 문서는 상단부터
        self._render_doc(preserve_scroll=False)
        self._set_dirty(True)
        self.statusBar().showMessage("임시 문서(붙여넣기) — 저장하려면 Ctrl+S", 5000)

    def _attach_path(self, path: Path) -> None:
        """문서를 디스크 파일에 연결(열기/저장 성공 후).

        watch 시작(교체) + 최근 파일 추가 + 타이틀 갱신 + _dirty=False.
        watch 생명주기 규칙: 열기/저장성공 = watch(new_path).
        """
        self._path = Path(path)
        if self._watcher is not None:
            try:
                self._watcher.watch(self._path)
            except Exception:
                pass
        self.settings.add_recent_file(str(self._path))
        self._refresh_recent_menu()
        self._set_dirty(False)
        self.statusBar().showMessage(str(self._path))

    # ------------------------------------------------------------------ #
    # 붙여넣기 — 클립보드(HTML 우선 / plain 폴백) → scratch 문서
    # ------------------------------------------------------------------ #
    def paste_clipboard(self) -> None:
        """Ctrl+Shift+V: 클립보드 내용을 마크다운으로 변환해 scratch 문서로 표시.

        HTML 이 있으면 html_to_markdown(core, 항상 str). 변환 결과가 비면 plain
        text 폴백. 둘 다 없으면 현재 문서를 유지하고 안내만 한다.
        """
        cb = QGuiApplication.clipboard()
        md = cb.mimeData()
        text = ""
        if md.hasHtml():
            text = html_to_markdown(md.html())  # core 호출(예외 비전파, str)
            if not text.strip() and md.hasText():
                text = md.text()  # HTML 변환이 비면 plain 폴백
        elif md.hasText():
            text = md.text()  # plain text 를 그대로 마크다운으로 취급
        else:
            self.statusBar().showMessage("클립보드에 붙여넣을 텍스트가 없습니다.", 3000)
            return

        if not text.strip():
            self.statusBar().showMessage("클립보드 내용이 비어 있습니다.", 3000)
            return

        # 미저장 변경 가드(붙여넣기로 현재 문서를 덮어쓰기 전).
        if not self._maybe_discard():
            return
        self._set_scratch(text)

    # ------------------------------------------------------------------ #
    # 저장 — Ctrl+S(저장) / Ctrl+Shift+S(다른 이름으로 저장)
    # ------------------------------------------------------------------ #
    def save(self) -> bool:
        """Ctrl+S: scratch 면 Save As, 파일연결이면 같은 경로에 바로 저장."""
        if self._path is None:
            return self.save_as()
        return self._write_to(self._path)

    def save_as(self) -> bool:
        """Ctrl+Shift+S: 항상 파일 대화상자. *.md 기본, 확장자 없으면 .md 보정."""
        start = str(self._path) if self._path else str(self._suggest_name())
        path_str, _ = QFileDialog.getSaveFileName(
            self, "다른 이름으로 저장", start, "Markdown (*.md *.markdown);;모든 파일 (*.*)"
        )
        if not path_str:
            return False  # 사용자 취소
        path = Path(path_str)
        if path.suffix == "":
            path = path.with_suffix(".md")
        return self._write_to(path)

    def _write_to(self, path: Path) -> bool:
        """write_markdown(core) 으로 _doc_text 저장. OSError 는 잡아 경고."""
        path = Path(path)
        try:
            write_markdown(path, self._doc_text)  # core 호출(OSError 전파)
        except OSError as exc:
            QMessageBox.warning(self, "저장 실패", f"파일을 저장할 수 없습니다:\n{exc}")
            return False  # dirty 유지, watch/연결 변경 없음
        was_scratch = self._path is None
        if self._path != path:
            # 새 경로(또는 scratch→파일): watch 교체 + recent + 연결 + dirty off.
            self._attach_path(path)
        else:
            self._set_dirty(False)  # 같은 경로 재저장
        suffix = "  (임시 문서를 파일로 저장)" if was_scratch else ""
        self.statusBar().showMessage(f"저장됨: {path}{suffix}", 3000)
        return True

    def _maybe_discard(self) -> bool:
        """미저장 변경이 있으면 저장/버림/취소 묻기. True=계속 진행 가능."""
        if not self._dirty:
            return True
        btn = QMessageBox.question(
            self,
            "저장하지 않은 변경",
            "변경 내용을 저장하시겠습니까?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if btn == QMessageBox.StandardButton.Save:
            return self.save()  # 저장 성공 시에만 진행
        if btn == QMessageBox.StandardButton.Discard:
            return True
        return False  # Cancel

    # ------------------------------------------------------------------ #
    # 렌더링 — 디스크 재독(_load_from_disk) vs 소스 렌더(_render_doc) 분리
    # ------------------------------------------------------------------ #
    def _load_from_disk(self, *, preserve_scroll: bool) -> bool:
        """디스크에서 현재 _path 를 다시 읽어 _doc_text 갱신 후 렌더.

        열기/외부변경/Ctrl+R 전용. scratch(_path is None) 면 디스크 소스가
        없으므로 아무 것도 하지 않고 False 반환. 성공 시 _dirty=False.
        """
        if self._path is None:
            return False
        try:
            self._doc_text = read_markdown(self._path)
        except FileNotFoundError:
            self.statusBar().showMessage(f"파일이 사라졌습니다: {self._path}")
            return False
        except OSError as exc:
            QMessageBox.warning(self, "읽기 오류", f"파일을 읽을 수 없습니다:\n{exc}")
            return False

        self._render_doc(preserve_scroll=preserve_scroll)
        self._set_dirty(False)  # 디스크와 동기화됨
        return True

    def _render_doc(self, *, preserve_scroll: bool) -> None:
        """_doc_text 를 렌더해 화면에 반영(파일 재독 없음).

        base_dir 은 _path 가 있으면 그 부모, scratch 면 _scratch_base_dir().
        테마 전환·붙여넣기 직후처럼 디스크 재독이 불필요한 경로에서 사용.
        """
        base_dir = self._path.parent if self._path else self._scratch_base_dir()
        result = render(self._doc_text, base_dir=base_dir)  # 예외 안 던짐(계약)

        if preserve_scroll:
            # loadFinished 후 복원할 스크롤 위치를 비동기로 캡처.
            self._capture_scroll_then_render(result)
        else:
            self._set_document(result, restore_scroll=False)

    def reload_current(self) -> bool:
        """Ctrl+R: 현재 파일을 디스크에서 다시 읽어 렌더(스크롤 보존).

        scratch 는 디스크 소스가 없어 새로고침 대상이 없다(no-op + 안내).
        """
        if self._path is None:
            self.statusBar().showMessage("임시 문서는 새로고침 대상이 없습니다.", 3000)
            return False
        return self._load_from_disk(preserve_scroll=True)

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
        # scratch 도 base_dir 을 부여해 상대 링크/이미지 해석 기준을 제공.
        base_dir = self._path.parent if self._path else self._scratch_base_dir()
        base = QUrl.fromLocalFile(str(base_dir) + "/")
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
        # 문서가 있으면(파일/scratch 무관) CSS 만 교체해 다시 감싸 렌더(디스크 재독 없음).
        # 문서가 없으면(초기 상태) 환영 화면만 테마 반영.
        if self._path is not None or self._doc_text:
            self._render_doc(preserve_scroll=True)
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
            "단축키: Ctrl+O 열기 · Ctrl+Shift+V 붙여넣기 · "
            "Ctrl+S 저장 · Ctrl+Shift+S 다른 이름으로 저장 · "
            "Ctrl+R 새로고침 · Ctrl+T 테마 · "
            "Ctrl+= / Ctrl+- / Ctrl+0 줌 · F11 전체화면 · Ctrl+\\ 목차</p>",
        )

    # ------------------------------------------------------------------ #
    # 종료 정리
    # ------------------------------------------------------------------ #
    def closeEvent(self, event) -> None:  # noqa: N802
        # 미저장 변경 가드(데이터 유실 방지) — 취소 시 종료 중단.
        if not self._maybe_discard():
            event.ignore()
            return
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
