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

import re
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QFontDatabase,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QInputDialog,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QSplitter,
    QStyle,
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

# ---- 뷰 모드(Phase 7/8) — settings._VALID_VIEW_MODES 리터럴과 반드시 동일 ----
MODE_EDITOR = "editor"    # 편집기 전용 (view 숨김)
MODE_PREVIEW = "preview"  # 프리뷰 전용 (editor 숨김) — 기존 동작
MODE_SPLIT = "split"      # 동시 표시 (editor + view 둘 다)
MODE_WYSIWYG = "wysiwyg"  # 라이브 편집 (프리뷰가 편집 surface — Phase 8)
VALID_MODES = (MODE_EDITOR, MODE_PREVIEW, MODE_SPLIT, MODE_WYSIWYG)

# 라이브 프리뷰 디바운스(입력 멈춤 후 렌더까지 대기).
_LIVE_PREVIEW_DEBOUNCE_MS = 300
# 자기 저장(write_markdown)이 유발한 watcher 이벤트를 무시할 시간 창.
_SELF_WRITE_SUPPRESS_MS = 700

# ---- WYSIWYG(Phase 8) ----
# 편집 캡처 폴링 주기(디바운스 효과 — 변화 없으면 비용 0, runJavaScript 는 논블로킹).
_WYSIWYG_POLL_MS = 400
# contentEditable 컨테이너 element id(진입 JS 가 article.markdown-body 에 런타임 부여).
_WYSIWYG_ROOT_ID = "md-editable"

# ---- 통합 서식(Phase 9) — 소스 편집기 마크다운 마커/접두 ----
# 인라인 마커(선택을 감싸 토글). 백틱은 코드.
_INLINE_MARK = {"bold": "**", "italic": "*", "strike": "~~", "code": "`"}
# 줄 머리 접두(목록/인용 — 토글). 번호 목록은 v1 에서 모든 줄에 "1. "(렌더러가 순번화).
_LINE_PREFIX = {"ul": "- ", "ol": "1. ", "quote": "> "}
# 헤딩 접두(exclusive — 기존 # 접두 제거 후 부여). 0(본문)은 모든 # 제거.
_HEADING_PREFIX = {1: "# ", 2: "## ", 3: "### "}
# 기존 헤딩 접두 매칭(서식 토글/제거용).
_HEADING_RE = re.compile(r"^(#{1,6}\s+)")


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
        # ---- 편집기/뷰 모드 상태(Phase 7) ----
        self._view_mode: str = MODE_PREVIEW       # 현재 뷰 모드(아래 복원으로 덮어씀)
        self._suppress_editor_signal = False      # 프로그램적 채움 시 textChanged 억제
        self._suppress_watch_until: float = 0.0   # self-write 억제 시간 창(monotonic)
        self._external_changed = False            # 외부 변경 미해결(배너) 플래그
        # ---- WYSIWYG 라이브 편집 상태(Phase 8) ----
        self._wysiwyg_active: bool = False            # WYSIWYG 진입 중인가(역렌더 게이트)
        self._wysiwyg_last_html: str | None = None    # 마지막 편집 컨테이너 innerHTML(변화 감지)
        self._wysiwyg_pending_setup: bool = False     # loadFinished 에서 editable 셋업 트리거

        self.setWindowTitle("MDViewer")
        self._apply_window_icon()
        self.resize(1100, 800)

        # ---- 중앙: TOC 패널 + (편집기 | 웹뷰) 중첩 스플리터 ----
        self.view = QWebEngineView(self)
        self.view.setAcceptDrops(False)  # 드롭은 메인 윈도우가 처리
        self.view.loadFinished.connect(self._on_load_finished)

        # file:// baseUrl 로 setHtml 하면 페이지 출처가 "로컬"로 취급되어
        # 기본값에선 원격(http/https) 이미지·리소스 로드가 차단된다.
        # 마크다운의 외부 이미지(예: 배지 shields.io)를 보여주려면 명시적으로 허용한다.
        _ws = self.view.settings()
        _ws.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        _ws.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)

        # 편집기(마크다운 소스 전용) — 고정폭 글꼴, 줄바꿈 끔, 드롭은 메인 윈도우.
        self.editor = QPlainTextEdit(self)
        self.editor.setObjectName("sourceEditor")
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.editor.setTabChangesFocus(False)
        self.editor.setAcceptDrops(False)
        self.editor.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.editor.setVisible(False)  # 초기 가시성은 _apply_view_mode 가 결정
        self.editor.textChanged.connect(self._on_editor_text_changed)

        self.toc_list = QListWidget(self)
        self.toc_list.setObjectName("tocList")
        self.toc_list.setMaximumWidth(360)
        self.toc_list.setMinimumWidth(140)
        self.toc_list.itemClicked.connect(self._on_toc_clicked)

        # 중첩: editor_preview_split[editor, view]
        self.editor_preview_split = QSplitter(Qt.Orientation.Horizontal, self)
        self.editor_preview_split.setObjectName("editorPreviewSplit")
        self.editor_preview_split.addWidget(self.editor)
        self.editor_preview_split.addWidget(self.view)
        self.editor_preview_split.setStretchFactor(0, 1)
        self.editor_preview_split.setStretchFactor(1, 1)
        self.editor_preview_split.setSizes([550, 550])

        # 바깥: splitter[toc_list, editor_preview_split]
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.addWidget(self.toc_list)
        self.splitter.addWidget(self.editor_preview_split)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([240, 860])
        self.setCentralWidget(self.splitter)

        # 라이브 프리뷰 디바운스 타이머(편집 → _doc_text → 재렌더).
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(_LIVE_PREVIEW_DEBOUNCE_MS)
        self._render_timer.timeout.connect(self._commit_editor_to_preview)

        # WYSIWYG innerHTML 폴링 타이머(WYSIWYG 동안에만 active — 편집 캡처).
        self._wysiwyg_poll = QTimer(self)
        self._wysiwyg_poll.setInterval(_WYSIWYG_POLL_MS)
        self._wysiwyg_poll.timeout.connect(self._wysiwyg_poll_tick)

        # ---- 파일 감시 브리지 ----
        self._bridge = _WatchBridge()
        self._bridge.fileChanged.connect(
            self._on_file_changed, Qt.ConnectionType.QueuedConnection
        )
        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.setInterval(_RELOAD_DEBOUNCE_MS)
        # 외부 변경(watch) 디바운스 만료 → 충돌 정책 적용(편집 중이면 자동 reload 금지).
        self._reload_timer.timeout.connect(self._on_external_change_settled)
        self._watcher = None
        if _WATCHER_AVAILABLE:
            try:
                self._watcher = FileWatcher(on_changed=self._bridge.fileChanged.emit)
            except Exception:
                self._watcher = None

        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._build_format_toolbar()  # WYSIWYG 전용 서식 툴바(초기 숨김)
        self.statusBar().showMessage("준비됨")

        self.setAcceptDrops(True)
        self._restore_window_state()
        self._refresh_recent_menu()
        self._apply_zoom()
        # 마지막 뷰 모드 복원(복원은 다시 저장하지 않음). 초기 _doc_text=="" 라
        # EDITOR/SPLIT 복원돼도 편집기 동기화는 no-op 로 안전.
        # ★ WYSIWYG 복원 + 빈 문서면 Preview 로 강등(빈 편집 surface 어색함 방지, §5/§9).
        restored = self.settings.view_mode()
        if restored == MODE_WYSIWYG and not self._doc_text:
            restored = MODE_PREVIEW
        self._show_welcome()
        self._apply_view_mode(restored, persist=False)

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
        st = self.style()  # QStyle 표준 아이콘(신규 자산 0)

        # ---- 파일 ----
        self.act_new = QAction("새 문서", self)
        self.act_new.setShortcut(QKeySequence.StandardKey.New)  # Ctrl+N
        self.act_new.setToolTip("새 문서 (Ctrl+N)")
        self.act_new.setIcon(st.standardIcon(QStyle.StandardPixmap.SP_FileIcon))
        self.act_new.triggered.connect(self.new_document)

        self.act_open = QAction("열기...", self)
        self.act_open.setShortcut(QKeySequence.StandardKey.Open)  # Ctrl+O
        self.act_open.setToolTip("열기 (Ctrl+O)")
        self.act_open.setIcon(st.standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton))
        self.act_open.triggered.connect(self.open_dialog)

        self.act_reload = QAction("새로고침", self)
        self.act_reload.setShortcut(QKeySequence("Ctrl+R"))
        self.act_reload.setToolTip("새로고침 (Ctrl+R)")
        self.act_reload.triggered.connect(self.reload_current)

        self.act_paste = QAction("클립보드를 마크다운으로 붙여넣기", self)
        self.act_paste.setShortcut(QKeySequence("Ctrl+Shift+V"))
        self.act_paste.setToolTip("클립보드를 마크다운으로 붙여넣기 (Ctrl+Shift+V)")
        self.act_paste.triggered.connect(self.paste_clipboard)

        self.act_save = QAction("저장", self)
        self.act_save.setShortcut(QKeySequence.StandardKey.Save)  # Ctrl+S
        self.act_save.setToolTip("저장 (Ctrl+S)")
        self.act_save.setIcon(st.standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        self.act_save.triggered.connect(self.save)

        self.act_save_as = QAction("다른 이름으로 저장...", self)
        self.act_save_as.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self.act_save_as.setToolTip("다른 이름으로 저장 (Ctrl+Shift+S)")
        self.act_save_as.triggered.connect(self.save_as)

        self.act_exit = QAction("종료", self)
        self.act_exit.setShortcut(QKeySequence.StandardKey.Quit)
        self.act_exit.triggered.connect(self.close)

        # ---- 편집(실행취소/다시실행 — 활성 surface 라우팅) ----
        self.act_undo = QAction("실행취소", self)
        self.act_undo.setShortcut(QKeySequence.StandardKey.Undo)  # Ctrl+Z
        self.act_undo.setToolTip("실행취소 (Ctrl+Z)")
        self.act_undo.setIcon(st.standardIcon(QStyle.StandardPixmap.SP_ArrowBack))
        self.act_undo.triggered.connect(self.do_undo)

        self.act_redo = QAction("다시실행", self)
        # 표준 Redo(Windows=Ctrl+Y) + 명시적 Ctrl+Shift+Z + Ctrl+Y 모두 수용.
        # (StandardKey.Redo 가 플랫폼별로 다르므로 두 관습을 모두 명시 — 중복은 무해.)
        self.act_redo.setShortcuts(
            [
                QKeySequence.StandardKey.Redo,
                QKeySequence("Ctrl+Shift+Z"),
                QKeySequence("Ctrl+Y"),
            ]
        )
        self.act_redo.setToolTip("다시실행 (Ctrl+Shift+Z / Ctrl+Y)")
        self.act_redo.setIcon(st.standardIcon(QStyle.StandardPixmap.SP_ArrowForward))
        self.act_redo.triggered.connect(self.do_redo)

        self.act_toggle_theme = QAction("테마 전환", self)
        self.act_toggle_theme.setShortcut(QKeySequence("Ctrl+T"))
        self.act_toggle_theme.setToolTip("테마 전환 (Ctrl+T)")
        self.act_toggle_theme.triggered.connect(self.toggle_theme)

        self.act_zoom_in = QAction("확대", self)
        # Ctrl+= 와 Ctrl++ 양쪽 수용
        self.act_zoom_in.setShortcuts(
            [QKeySequence("Ctrl+="), QKeySequence("Ctrl++"), QKeySequence.StandardKey.ZoomIn]
        )
        self.act_zoom_in.setToolTip("확대 (Ctrl+=)")
        self.act_zoom_in.triggered.connect(self.zoom_in)

        self.act_zoom_out = QAction("축소", self)
        self.act_zoom_out.setShortcuts(
            [QKeySequence("Ctrl+-"), QKeySequence.StandardKey.ZoomOut]
        )
        self.act_zoom_out.setToolTip("축소 (Ctrl+-)")
        self.act_zoom_out.triggered.connect(self.zoom_out)

        self.act_zoom_reset = QAction("줌 초기화", self)
        self.act_zoom_reset.setShortcut(QKeySequence("Ctrl+0"))
        self.act_zoom_reset.setToolTip("줌 초기화 (Ctrl+0)")
        self.act_zoom_reset.triggered.connect(self.zoom_reset)

        self.act_fullscreen = QAction("전체화면", self)
        self.act_fullscreen.setShortcut(QKeySequence("F11"))
        self.act_fullscreen.setCheckable(True)
        self.act_fullscreen.setToolTip("전체화면 (F11)")
        self.act_fullscreen.triggered.connect(self.toggle_fullscreen)

        self.act_toggle_toc = QAction("목차 표시", self)
        self.act_toggle_toc.setShortcut(QKeySequence("Ctrl+\\"))
        self.act_toggle_toc.setCheckable(True)
        self.act_toggle_toc.setChecked(self.settings.toc_visible())
        self.act_toggle_toc.setToolTip("목차 표시 (Ctrl+\\)")
        self.act_toggle_toc.triggered.connect(self.toggle_toc)
        self.toc_list.setVisible(self.settings.toc_visible())

        # ---- 뷰 모드(상호배타 라디오, Ctrl+1/2/3/4) ----
        self.act_mode_editor = QAction("편집기", self)
        self.act_mode_editor.setShortcut(QKeySequence("Ctrl+1"))
        self.act_mode_editor.setCheckable(True)
        self.act_mode_editor.setToolTip("편집기 전용 (Ctrl+1)")
        self.act_mode_editor.triggered.connect(self.set_mode_editor)

        self.act_mode_preview = QAction("미리보기", self)
        self.act_mode_preview.setShortcut(QKeySequence("Ctrl+2"))
        self.act_mode_preview.setCheckable(True)
        self.act_mode_preview.setToolTip("미리보기 전용 (Ctrl+2)")
        self.act_mode_preview.triggered.connect(self.set_mode_preview)

        self.act_mode_split = QAction("분할(편집+미리보기)", self)
        self.act_mode_split.setShortcut(QKeySequence("Ctrl+3"))
        self.act_mode_split.setCheckable(True)
        self.act_mode_split.setToolTip("분할 — 편집기+미리보기 (Ctrl+3)")
        self.act_mode_split.triggered.connect(self.set_mode_split)

        self.act_mode_wysiwyg = QAction("라이브 편집", self)
        self.act_mode_wysiwyg.setShortcut(QKeySequence("Ctrl+4"))
        self.act_mode_wysiwyg.setCheckable(True)
        self.act_mode_wysiwyg.setToolTip("라이브 편집 — WYSIWYG (Ctrl+4)")
        self.act_mode_wysiwyg.triggered.connect(self.set_mode_wysiwyg)

        self._mode_group = QActionGroup(self)
        self._mode_group.setExclusive(True)
        for a in (
            self.act_mode_editor,
            self.act_mode_preview,
            self.act_mode_split,
            self.act_mode_wysiwyg,
        ):
            self._mode_group.addAction(a)

        self.act_about = QAction("MDViewer 정보", self)
        self.act_about.triggered.connect(self.show_about)

        self.act_clear_recent = QAction("최근 파일 지우기", self)
        self.act_clear_recent.triggered.connect(self._clear_recent)

    def _build_menus(self) -> None:
        mb = self.menuBar()

        m_file = mb.addMenu("파일(&F)")
        m_file.addAction(self.act_new)
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

        m_edit = mb.addMenu("편집(&E)")
        m_edit.addAction(self.act_undo)
        m_edit.addAction(self.act_redo)

        m_view = mb.addMenu("보기(&V)")
        m_view.addAction(self.act_mode_editor)
        m_view.addAction(self.act_mode_preview)
        m_view.addAction(self.act_mode_split)
        m_view.addAction(self.act_mode_wysiwyg)
        m_view.addSeparator()
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
        """워드프로세서식 단축 버튼 툴바(그룹+구분선+툴팁+표준아이콘).

        [파일] 새 문서·열기·저장 | [편집] 실행취소·다시실행 |
        [보기] 모드4종 · 줌3종 · 테마·목차.
        다른이름저장/새로고침/붙여넣기는 툴바에서 제외(메뉴 유지).
        """
        tb = self.addToolBar("메인")
        tb.setObjectName("mainToolbar")
        tb.setMovable(False)
        # 아이콘 옆 텍스트(워드프로세서 느낌 — 한글 라벨 병기).
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)

        # [파일] (다른이름저장은 메뉴 전용)
        tb.addAction(self.act_new)
        tb.addAction(self.act_open)
        tb.addAction(self.act_save)
        tb.addSeparator()
        # [편집]
        tb.addAction(self.act_undo)
        tb.addAction(self.act_redo)
        tb.addSeparator()
        # [보기] 모드 4종(QActionGroup 라디오)
        tb.addAction(self.act_mode_editor)
        tb.addAction(self.act_mode_preview)
        tb.addAction(self.act_mode_split)
        tb.addAction(self.act_mode_wysiwyg)
        tb.addSeparator()
        # 줌
        tb.addAction(self.act_zoom_out)
        tb.addAction(self.act_zoom_reset)
        tb.addAction(self.act_zoom_in)
        tb.addSeparator()
        # 테마/목차
        tb.addAction(self.act_toggle_theme)
        tb.addAction(self.act_toggle_toc)

    # ------------------------------------------------------------------ #
    # 서식 툴바(편집 surface 공통: Editor/Split=마크다운, WYSIWYG=execCommand)
    # ------------------------------------------------------------------ #
    def _build_format_toolbar(self) -> None:
        """통합 서식 툴바(Phase 9). 편집 surface(Editor/Split/WYSIWYG)에서만 표시.

        동일 QAction 이 활성 surface 에 따라 분기한다:
            - Editor/Split → QTextCursor 마크다운 삽입/토글(`_editor_*`)
            - WYSIWYG      → execCommand(`_wysiwyg_*`, 기존 동작 무변경)

        포맷 액션에는 단축키를 부여하지 않는다(전역 Ctrl+B 등 충돌·모드의존 복잡도
        회피, v1 툴바 클릭 전용). heading 은 QComboBox(본문/제목1~3).
        """
        tb = self.addToolBar("서식")
        tb.setObjectName("formatToolbar")
        tb.setMovable(False)
        self.format_toolbar = tb

        # heading 드롭다운(본문=0, 제목1~3) — 에디터=#접두, WYSIWYG=formatBlock.
        self.cmb_heading = QComboBox(self)
        self.cmb_heading.addItems(["본문", "제목 1", "제목 2", "제목 3"])
        self.cmb_heading.setToolTip("문단 스타일(본문/제목1~3)")
        self.cmb_heading.activated.connect(self.fmt_heading)  # idx 0=본문,1~3=Hn
        tb.addWidget(self.cmb_heading)
        tb.addSeparator()

        # 인라인.
        self.act_fmt_bold = self._mk_fmt("굵게", self.fmt_bold, "굵게 (**)")
        self.act_fmt_italic = self._mk_fmt("기울임", self.fmt_italic, "기울임 (*)")
        self.act_fmt_strike = self._mk_fmt("취소선", self.fmt_strike, "취소선 (~~)")
        self.act_fmt_code = self._mk_fmt("코드", self.fmt_code, "인라인 코드 (`)")
        tb.addAction(self.act_fmt_bold)
        tb.addAction(self.act_fmt_italic)
        tb.addAction(self.act_fmt_strike)
        tb.addAction(self.act_fmt_code)
        tb.addSeparator()
        # 블록.
        self.act_fmt_ul = self._mk_fmt("• 목록", self.fmt_ul, "불릿 목록 (- )")
        self.act_fmt_ol = self._mk_fmt("1. 목록", self.fmt_ol, "번호 목록 (1. )")
        self.act_fmt_quote = self._mk_fmt("인용", self.fmt_quote, "인용 (> )")
        tb.addAction(self.act_fmt_ul)
        tb.addAction(self.act_fmt_ol)
        tb.addAction(self.act_fmt_quote)
        tb.addSeparator()
        # 링크 / 서식 지우기.
        self.act_fmt_link = self._mk_fmt("링크", self.fmt_link, "링크 삽입")
        self.act_fmt_clear = self._mk_fmt(
            "서식 지우기", self.fmt_clear, "서식 지우기(WYSIWYG 전용)"
        )
        tb.addAction(self.act_fmt_link)
        tb.addAction(self.act_fmt_clear)

        # B/I 강조(툴바 위젯에 스타일 — 외부 자산 0, ui-dev 재량).
        try:
            bw = tb.widgetForAction(self.act_fmt_bold)
            if bw is not None:
                bw.setStyleSheet("font-weight:bold;")
            iw = tb.widgetForAction(self.act_fmt_italic)
            if iw is not None:
                iw.setStyleSheet("font-style:italic;")
        except Exception:
            pass

        tb.setVisible(False)  # _apply_view_mode 가 편집 surface 에서만 표시.

    def _mk_fmt(self, label: str, slot, tip: str = "") -> QAction:
        a = QAction(label, self)
        if tip:
            a.setToolTip(tip)
        a.triggered.connect(slot)
        return a

    # ---- surface 판별 헬퍼(서식/undo-redo 공유) ---------------------------
    def _is_source_editor_surface(self) -> bool:
        """활성 편집 대상이 소스 편집기인가(Editor/Split)."""
        return self._view_mode in (MODE_EDITOR, MODE_SPLIT)

    def _is_wysiwyg_surface(self) -> bool:
        """활성 편집 대상이 WYSIWYG webview 인가."""
        return self._view_mode == MODE_WYSIWYG and self._wysiwyg_active

    def _is_edit_surface(self) -> bool:
        """편집 가능한 surface 가 활성인가(Editor/Split/WYSIWYG)."""
        return self._is_source_editor_surface() or self._view_mode == MODE_WYSIWYG

    # ---- 의미 단위 서식 슬롯(툴바 액션이 연결) — surface 분기 진입 ---------
    def fmt_bold(self) -> None:
        self._dispatch_inline("bold")

    def fmt_italic(self) -> None:
        self._dispatch_inline("italic")

    def fmt_strike(self) -> None:
        self._dispatch_inline("strike")

    def fmt_code(self) -> None:
        self._dispatch_inline("code")

    def fmt_ul(self) -> None:
        self._dispatch_block("ul")

    def fmt_ol(self) -> None:
        self._dispatch_block("ol")

    def fmt_quote(self) -> None:
        self._dispatch_block("quote")

    def fmt_link(self) -> None:
        self._dispatch_link()

    def fmt_clear(self) -> None:
        self._dispatch_clear()

    def fmt_heading(self, level: int) -> None:
        """heading 콤보 선택(0=본문, 1~3=제목). idx 가 그대로 level."""
        self._dispatch_heading(int(level))

    # ---- 디스패처: 활성 surface 로 분기 ----------------------------------
    def _dispatch_inline(self, kind: str) -> None:
        if self._is_wysiwyg_surface():
            self._wysiwyg_inline(kind)
        elif self._is_source_editor_surface():
            self._editor_inline(kind)
        # Preview 전용 → no-op(툴바 숨김, 방어적).

    def _dispatch_block(self, kind: str) -> None:
        if self._is_wysiwyg_surface():
            self._wysiwyg_block(kind)
        elif self._is_source_editor_surface():
            self._editor_block(kind)

    def _dispatch_heading(self, level: int) -> None:
        if self._is_wysiwyg_surface():
            self._wysiwyg_heading(level)
        elif self._is_source_editor_surface():
            self._editor_heading(level)

    def _dispatch_link(self) -> None:
        if not self._is_edit_surface():
            return
        url, ok = QInputDialog.getText(self, "링크 삽입", "URL:")
        if not ok or not url.strip():
            return
        url = url.strip()
        if self._is_wysiwyg_surface():
            self._exec_format("createLink", url)  # 기존 execCommand 경로
        elif self._is_source_editor_surface():
            self._editor_link(url)

    def _dispatch_clear(self) -> None:
        if self._is_wysiwyg_surface():
            self._wysiwyg_clear()  # removeFormat + formatBlock P(기존)
        elif self._is_source_editor_surface():
            self._editor_clear()  # v1: 안내(설계 §5.5 — 게이팅으로 보통 비활성)

    # ------------------------------------------------------------------ #
    # (a) WYSIWYG 경로 — 기존 execCommand 동작(래퍼만, 동작 무변경)
    # ------------------------------------------------------------------ #
    def _exec_format(self, command: str, value: str | None = None) -> None:
        """WYSIWYG 편집 루트에 execCommand 적용 후 즉시 1회 캡처(폴링 보조).

        WYSIWYG 활성이 아니면 no-op(디스패치가 게이트하나 방어적).
        """
        if not self._wysiwyg_active:
            return
        if value is None:
            js = "document.execCommand(%r, false, null);" % command
        else:
            js = "document.execCommand(%r, false, %r);" % (command, value)
        try:
            self.view.page().runJavaScript(js)
        except Exception:
            pass
        # 서식 적용 직후 한 번 더 캡처(폴링 대기 없이 빠른 반영). DOM 반영이 한 틱
        # 늦으면 다음 폴링 tick 이 백업한다(best-effort).
        self._capture_wysiwyg_once(final=False)

    def _wysiwyg_inline(self, kind: str) -> None:
        cmd = {"bold": "bold", "italic": "italic", "strike": "strikeThrough"}.get(kind)
        if cmd:
            self._exec_format(cmd)
        elif kind == "code":
            self._wysiwyg_inline_code()

    def _wysiwyg_block(self, kind: str) -> None:
        if kind == "ul":
            self._exec_format("insertUnorderedList")
        elif kind == "ol":
            self._exec_format("insertOrderedList")
        elif kind == "quote":
            self._exec_format("formatBlock", "BLOCKQUOTE")

    def _wysiwyg_heading(self, level: int) -> None:
        tag = {0: "P", 1: "H1", 2: "H2", 3: "H3"}.get(level, "P")
        self._exec_format("formatBlock", tag)

    def _wysiwyg_inline_code(self) -> None:
        """선택 영역을 <code> 로 감싼다(execCommand 미지원 → 커스텀 JS).

        선택이 없으면 no-op(빈 코드 삽입 방지). html_to_markdown 이 <code>→`code`
        로 변환하므로 라운드트립 성립.
        """
        if not self._wysiwyg_active:
            return
        js = (
            "(function(){"
            " var sel=window.getSelection();"
            " if(!sel || sel.rangeCount===0 || sel.isCollapsed) return;"
            " var r=sel.getRangeAt(0);"
            " var code=document.createElement('code');"
            " try{ code.appendChild(r.extractContents()); r.insertNode(code); }"
            " catch(e){}"
            "})()"
        )
        try:
            self.view.page().runJavaScript(js)
        except Exception:
            pass
        self._capture_wysiwyg_once(final=False)

    def _wysiwyg_clear(self) -> None:
        """인라인 서식 제거 + 블록을 본문(P)으로 환원(WYSIWYG 전용)."""
        if not self._wysiwyg_active:
            return
        self._exec_format("removeFormat")
        self._exec_format("formatBlock", "P")

    # ------------------------------------------------------------------ #
    # (b) 에디터(QTextCursor) 경로 — 마크다운 마커 삽입/토글
    # ------------------------------------------------------------------ #
    def _editor_inline(self, kind: str) -> None:
        """선택을 마크다운 인라인 마커로 토글(없으면 빈 쌍 + 커서 가운데).

        QPlainTextEdit 선택은 단락 구분자로 U+2029 를 쓰므로 \\n 으로 환원한다.
        insertText 가 textChanged 를 발화 → dirty+디바운스 렌더 자동(별도 호출 불필요).
        """
        mark = _INLINE_MARK.get(kind)
        if mark is None:
            return
        cur = self.editor.textCursor()
        sel = cur.selectedText()
        if sel:
            text = sel.replace(" ", "\n")
            if (
                text.startswith(mark)
                and text.endswith(mark)
                and len(text) >= 2 * len(mark)
            ):
                new = text[len(mark): len(text) - len(mark)]  # 토글 해제
            else:
                new = f"{mark}{text}{mark}"  # 토글 적용
            cur.insertText(new)  # 선택 치환(undo 1스텝)
        else:
            cur.insertText(mark + mark)  # 빈 마커 쌍
            pos = cur.position() - len(mark)  # 가운데로 커서 이동
            cur.setPosition(pos)
            self.editor.setTextCursor(cur)
        self.editor.setFocus()

    def _editor_block(self, kind: str) -> None:
        """불릿/번호/인용: 선택된 각 줄 머리에 접두 토글."""
        prefix = _LINE_PREFIX.get(kind)
        if prefix is None:
            return
        self._editor_apply_line_prefix(prefix, exclusive=False)

    def _editor_heading(self, level: int) -> None:
        """제목1~3 = 줄 머리에 #/##/### (기존 헤딩 접두 교체). 0 = 본문(# 제거)."""
        if level == 0:
            self._editor_strip_heading()
        else:
            prefix = _HEADING_PREFIX.get(level)
            if prefix is None:
                return
            self._editor_apply_line_prefix(prefix, exclusive=True, is_heading=True)

    def _editor_apply_line_prefix(
        self, prefix: str, *, exclusive: bool, is_heading: bool = False
    ) -> None:
        """선택(또는 현재 줄)의 각 줄 머리에 prefix 를 토글한다(undo 1스텝).

        is_heading=True: 기존 헤딩 접두(#,##,###)를 먼저 제거 후 새 접두 부여
            (첫 줄이 이미 동일 접두면 제거 = 토글). 목록/인용(exclusive=False)은 단순 토글.
        번호 목록('1. ')은 v1 에서 모든 줄에 부여(마크다운 렌더러가 순번화).
        """
        doc = self.editor.document()
        cur = self.editor.textCursor()
        cur.beginEditBlock()
        try:
            start_block = doc.findBlock(cur.selectionStart()).blockNumber()
            end_block = doc.findBlock(cur.selectionEnd()).blockNumber()
            # 토글 판단: 선택 첫 줄이 이미 prefix 면 제거 모드.
            first_text = doc.findBlockByNumber(start_block).text()
            remove = first_text.startswith(prefix)
            for n in range(start_block, end_block + 1):
                blk = doc.findBlockByNumber(n)
                line = blk.text()
                edit = QTextCursor(blk)
                edit.movePosition(QTextCursor.MoveOperation.StartOfBlock)
                if is_heading:
                    m = _HEADING_RE.match(line)
                    if m:
                        edit.movePosition(
                            QTextCursor.MoveOperation.Right,
                            QTextCursor.MoveMode.KeepAnchor,
                            len(m.group(1)),
                        )
                        edit.removeSelectedText()
                    if not remove:
                        edit.insertText(prefix)  # 새 헤딩 레벨
                else:
                    if remove and line.startswith(prefix):
                        edit.movePosition(
                            QTextCursor.MoveOperation.Right,
                            QTextCursor.MoveMode.KeepAnchor,
                            len(prefix),
                        )
                        edit.removeSelectedText()
                    elif not remove and not line.startswith(prefix):
                        edit.insertText(prefix)
        finally:
            cur.endEditBlock()
        self.editor.setFocus()

    def _editor_strip_heading(self) -> None:
        """현재 줄/선택의 헤딩 접두(#,##,…)를 제거(본문 전환)."""
        doc = self.editor.document()
        cur = self.editor.textCursor()
        cur.beginEditBlock()
        try:
            start_block = doc.findBlock(cur.selectionStart()).blockNumber()
            end_block = doc.findBlock(cur.selectionEnd()).blockNumber()
            for n in range(start_block, end_block + 1):
                blk = doc.findBlockByNumber(n)
                m = _HEADING_RE.match(blk.text())
                if m:
                    edit = QTextCursor(blk)
                    edit.movePosition(QTextCursor.MoveOperation.StartOfBlock)
                    edit.movePosition(
                        QTextCursor.MoveOperation.Right,
                        QTextCursor.MoveMode.KeepAnchor,
                        len(m.group(1)),
                    )
                    edit.removeSelectedText()
        finally:
            cur.endEditBlock()
        self.editor.setFocus()

    def _editor_link(self, url: str) -> None:
        """[선택텍스트](url) 삽입. 선택이 없으면 [링크 텍스트](url) + placeholder 선택."""
        cur = self.editor.textCursor()
        sel = cur.selectedText().replace(" ", "\n")
        if sel:
            cur.insertText(f"[{sel}]({url})")
        else:
            placeholder = "링크 텍스트"
            cur.insertText(f"[{placeholder}]({url})")
            # 커서를 placeholder 선택 상태로(바로 덮어쓰기 가능): "]({url})" 길이만큼 보정.
            tail = len(url) + 3  # ']' '(' ... ')' → "](" + ")" = 3
            pos = cur.position() - (tail + len(placeholder))
            cur.setPosition(pos)
            cur.setPosition(pos + len(placeholder), QTextCursor.MoveMode.KeepAnchor)
            self.editor.setTextCursor(cur)
        self.editor.setFocus()

    def _editor_clear(self) -> None:
        """에디터 서식 지우기 — v1: 안내(소스에서 마커 자동 제거는 모호).

        보통 _apply_view_mode 가 에디터 surface 에서 act_fmt_clear 를 비활성화하므로
        도달이 드물다(방어적 안내).
        """
        self.statusBar().showMessage(
            "서식 지우기는 라이브 편집(WYSIWYG)에서 동작합니다.", 3000
        )

    # ------------------------------------------------------------------ #
    # Undo / Redo — 활성 surface 라우팅(Editor/Split=editor, WYSIWYG=execCommand)
    # ------------------------------------------------------------------ #
    def do_undo(self) -> None:
        """활성 편집 surface 로 undo 라우팅(Preview 전용이면 no-op)."""
        if self._is_wysiwyg_surface():
            self._wysiwyg_exec_simple("undo")
        elif self._is_source_editor_surface():
            self.editor.undo()
        # Preview 전용 → no-op(액션 자체가 _apply_view_mode 에서 비활성).

    def do_redo(self) -> None:
        if self._is_wysiwyg_surface():
            self._wysiwyg_exec_simple("redo")
        elif self._is_source_editor_surface():
            self.editor.redo()

    def _wysiwyg_exec_simple(self, command: str) -> None:
        """WYSIWYG 에서 execCommand 실행 후 즉시 캡처(undo/redo 공용)."""
        if not self._wysiwyg_active:
            return
        try:
            self.view.page().runJavaScript(
                "document.execCommand(%r,false,null);" % command
            )
        except Exception:
            pass
        self._capture_wysiwyg_once(final=False)  # _doc_text 동기화(폴링 보조)

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

        # 문서 교체 → WYSIWYG 라이브 편집을 빠져나온다(새 문서는 일반 렌더, §4.6).
        self._leave_wysiwyg_for_document_change()
        self._clear_external_change_banner()  # 새 문서 진입 → 이전 외부변경 안내 클리어
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
        self._sync_editor_from_doc()  # 편집기를 붙여넣기 내용과 동기화(신호 억제)
        self._set_dirty(True)
        self.statusBar().showMessage("임시 문서(붙여넣기) — 저장하려면 Ctrl+S", 5000)

    def new_document(self) -> None:
        """Ctrl+N: 빈 임시(scratch) 문서로 교체한다(미저장 가드 후).

        빈 새 문서는 dirty=False(아직 변경 없음). 편집 불가 모드(Preview)면 Split 로
        전환해 즉시 타이핑할 수 있게 하고 편집기에 포커스한다.
        """
        if not self._maybe_discard():
            return  # 사용자 취소 → 중단(데이터 보호)
        # 문서 교체 → WYSIWYG 라이브 편집을 빠져나온다(새 문서는 일반 렌더, §4.6 정합).
        self._leave_wysiwyg_for_document_change()
        self._clear_external_change_banner()
        self._new_scratch()
        # 편집 가능 모드 보장: Preview 전용이면 Split 로(편집기+프리뷰).
        if self._view_mode == MODE_PREVIEW:
            self._apply_view_mode(MODE_SPLIT)  # 내부에서 _sync_editor_from_doc 호출
        self.editor.setFocus()
        self.statusBar().showMessage("새 문서 — 입력 후 Ctrl+S 로 저장", 4000)

    def _new_scratch(self) -> None:
        """현재 문서를 '빈' scratch(미저장 임시 문서)로 전환(dirty=False).

        _set_scratch(paste, dirty=True)와 달리 빈 새 문서는 깨끗하다. watch 중지
        (디스크 파일 없음) → _path=None → _doc_text="" → 빈 렌더 → 편집기 비움.
        """
        if self._watcher is not None:
            try:
                self._watcher.stop()  # watch 생명주기: scratch=stop
            except Exception:
                pass
        self._path = None
        self._doc_text = ""
        self._pending_scroll = None
        self._render_doc(preserve_scroll=False)  # 빈 본문 렌더(프리뷰 비움)
        self._sync_editor_from_doc()  # 편집기 비움(신호 억제)
        self._set_dirty(False)  # ★ 빈 새 문서는 깨끗(타이틀 "제목 없음")

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
    # 뷰 모드 상태기계(Editor / Preview / Split)
    # ------------------------------------------------------------------ #
    def _apply_view_mode(self, mode: str, *, persist: bool = True) -> None:
        """뷰 모드를 적용한다(가시성·동기화·포커스·액션·저장·WYSIWYG 전이).

        데이터(_doc_text/_dirty)를 변경하지 않는다(WYSIWYG flush 는 _doc_text 를 확정만).
        _maybe_discard 를 호출하지 않는다(모드 전환은 데이터 변경이 아님). 일반 모드
        간 전환은 프리뷰를 재렌더하지 않는다(스크롤 보존).

        ★ WYSIWYG 전이(§1.3):
          - 이전이 WYSIWYG 이고 새 모드가 다르면 _view_mode 갱신 전에 _exit_wysiwyg()
            (flush + 폴링 stop + editable 해제).
          - 새 모드가 WYSIWYG 이고 이전이 다르면 가시성 setVisible 후 _enter_wysiwyg()
            (render→editable setHtml + 폴링 시작).
          - prev==mode 가드로 동일 모드 재클릭 시 enter/exit 둘 다 건너뜀(폴링 유지).
        """
        if mode not in VALID_MODES:
            mode = MODE_PREVIEW

        prev_mode = self._view_mode

        # ── (A) WYSIWYG 이탈: _view_mode 갱신 전에 flush + 정리(_exit 가드와 정합). ──
        if prev_mode == MODE_WYSIWYG and mode != MODE_WYSIWYG:
            self._exit_wysiwyg()

        self._view_mode = mode

        show_editor = mode in (MODE_EDITOR, MODE_SPLIT)
        show_preview = mode in (MODE_PREVIEW, MODE_SPLIT, MODE_WYSIWYG)
        # ★ 서식 툴바: 편집 surface 공통(Editor/Split/WYSIWYG)에서 표시(Phase 9).
        edit_surface = mode in (MODE_EDITOR, MODE_SPLIT, MODE_WYSIWYG)
        show_format_toolbar = edit_surface

        # EDITOR/SPLIT 진입 시 편집기를 _doc_text 와 동기화(신호 억제로 dirty 오염 방지).
        if show_editor:
            self._sync_editor_from_doc()

        # 가시성(위젯 단위 — 스플리터는 항상 표시, 자식 hide 시 핸들 자동 접힘).
        self.editor.setVisible(show_editor)
        self.view.setVisible(show_preview)

        # 서식 툴바: 편집 surface 에서 표시(Preview 전용 숨김).
        self.format_toolbar.setVisible(show_format_toolbar)
        # Undo/Redo: 편집 surface 에서만 활성(Preview 전용 비활성).
        self.act_undo.setEnabled(edit_surface)
        self.act_redo.setEnabled(edit_surface)
        # 서식 지우기: 에디터 surface 에선 모호 → WYSIWYG 에서만 활성(설계 §5.5 권장).
        self.act_fmt_clear.setEnabled(mode == MODE_WYSIWYG)

        # TOC: 프리뷰류(WYSIWYG 포함)가 보일 때만 의미. 사용자 토글값 게이팅(덮지 않음).
        toc_allowed = show_preview
        self.toc_list.setVisible(toc_allowed and self.settings.toc_visible())
        self.act_toggle_toc.setEnabled(toc_allowed)

        # 상호배타 액션 체크.
        self.act_mode_editor.setChecked(mode == MODE_EDITOR)
        self.act_mode_preview.setChecked(mode == MODE_PREVIEW)
        self.act_mode_split.setChecked(mode == MODE_SPLIT)
        self.act_mode_wysiwyg.setChecked(mode == MODE_WYSIWYG)

        # ── (B) WYSIWYG 진입: editable setHtml + 폴링 시작(이전이 WYSIWYG 가 아닐 때만). ──
        if mode == MODE_WYSIWYG and prev_mode != MODE_WYSIWYG:
            if not self._enter_wysiwyg():
                # 진입 취소(front-matter 거부) → 내부에서 이미 _apply_view_mode(PREVIEW)
                # 재귀로 Preview 상태/가시성/액션/persist 를 확정했다. 이 바깥 호출은
                # 남은 focus/persist/status(WYSIWYG)를 적용하면 안 되므로 즉시 중단.
                return

        # 포커스: 편집기가 보이면 편집기에, 아니면 프리뷰/편집 surface 에.
        if show_editor:
            self.editor.setFocus()
        else:
            self.view.setFocus()

        if persist:
            self.settings.set_view_mode(mode)

        label = {
            MODE_EDITOR: "편집기",
            MODE_PREVIEW: "미리보기",
            MODE_SPLIT: "분할",
            MODE_WYSIWYG: "라이브 편집",
        }[mode]
        self.statusBar().showMessage(f"보기: {label}", 1500)

    def set_mode_editor(self) -> None:
        self._apply_view_mode(MODE_EDITOR)

    def set_mode_preview(self) -> None:
        self._apply_view_mode(MODE_PREVIEW)

    def set_mode_split(self) -> None:
        self._apply_view_mode(MODE_SPLIT)

    def set_mode_wysiwyg(self) -> None:
        self._apply_view_mode(MODE_WYSIWYG)

    # ------------------------------------------------------------------ #
    # WYSIWYG 라이브 편집(Phase 8) — 진입/이탈/캡처/동기화
    # ------------------------------------------------------------------ #
    @staticmethod
    def _has_front_matter(text: str) -> bool:
        """문서가 front-matter(--- 로 시작하는 머리말)로 시작하는가(§8.1)."""
        import re

        return bool(re.match(r"^\s*---\r?\n", text or ""))

    def _enter_wysiwyg(self) -> bool:
        """WYSIWYG 진입: _doc_text 를 렌더해 editable surface 로 띄우고 폴링 시작.

        ★ 이 setHtml 은 '프로그램적' 렌더다(_render_doc 미경유). 이후 사용자 편집은
           폴링으로만 캡처하며 webview 를 다시 그리지 않는다(역렌더 금지 — 커서 보존).

        front-matter 문서는 라운드트립에서 머리말이 유실되므로 진입 전에 경고/차단한다
        (§8.1 A안). 사용자가 취소하면 Preview 로 강등하고 진입하지 않는다.

        Returns:
            True = 진입 완료, False = 진입 취소(front-matter 거부 → Preview 강등).
            False 면 호출 측(_apply_view_mode)이 남은 처리를 중단해야 한다.
        """
        self._flush_pending_edit()  # 소스 편집기 잔여 디바운스 반영(Phase 7)

        # front-matter 유실 가드(데이터 안전 — 필수).
        if self._has_front_matter(self._doc_text):
            btn = QMessageBox.question(
                self,
                "라이브 편집 경고",
                "이 문서는 머리말(front-matter, --- 블록)을 포함합니다.\n"
                "라이브 편집(WYSIWYG)으로 편집하면 머리말이 유실될 수 있습니다.\n\n"
                "계속하시겠습니까?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if btn != QMessageBox.StandardButton.Yes:
                # 진입 취소 → Preview 로 강등(_apply_view_mode 가 가시성/액션 정합).
                self._apply_view_mode(MODE_PREVIEW)
                return False

        base_dir = self._path.parent if self._path else self._scratch_base_dir()
        result = render(self._doc_text, base_dir=base_dir)  # 계약상 예외 없음
        body = getattr(result, "html", "") or ""
        dark = self._theme == theme_mod.DARK
        html = theme_mod.wrap_document(body, dark)
        base = QUrl.fromLocalFile(str(base_dir) + "/")

        self._wysiwyg_active = True       # ★ 역렌더 게이트 ON(이후 _render_doc no-op)
        self._wysiwyg_last_html = None    # 베이스라인 미설정 표시
        self._pending_scroll = None
        self._wysiwyg_pending_setup = True  # loadFinished 에서 editable 셋업 트리거
        self.view.setHtml(html, base)
        self._populate_toc(getattr(result, "toc", []) or [])  # 진입 시 1회 TOC 갱신
        self.statusBar().showMessage(
            "라이브 편집(WYSIWYG) — 툴바로 서식 적용, 변경은 자동 저장 대상. "
            "마크다운 소스는 정규화될 수 있습니다.",
            5000,
        )
        return True

    def _activate_editable_then_baseline(self) -> None:
        """article 을 contentEditable 로 만들고 초기 innerHTML 을 베이스라인으로 캡처.

        theme.py 무변경(런타임 querySelector 권장안 §2.1). 페이지 로드 완료 후에만
        DOM 조작 가능 → _on_load_finished 에서 호출.
        """
        js_enable = (
            "(function(){"
            " var a = document.querySelector('article.markdown-body');"
            " if(!a) return null;"
            " a.id = %r;"
            " a.setAttribute('contenteditable','true');"
            " a.focus();"
            " return a.innerHTML;"
            "})()" % _WYSIWYG_ROOT_ID
        )

        def _cb(initial_html) -> None:
            # 베이스라인 = 진입 시점 innerHTML. 이후 폴링이 이 값과 다르면 사용자 편집.
            self._wysiwyg_last_html = initial_html if isinstance(initial_html, str) else ""
            if self._wysiwyg_active:
                self._wysiwyg_poll.start()  # 폴링 시작(이탈/모드전환 시 stop).

        try:
            self.view.page().runJavaScript(js_enable, 0, _cb)
        except Exception:
            # editable 부여 실패 → WYSIWYG 무력화(크래시 금지). 폴링 미시작.
            self._wysiwyg_active = False

    def _exit_wysiwyg(self) -> None:
        """WYSIWYG 이탈: 폴링 중지 + 마지막 1회 flush(최신 편집 반영) + editable 해제.

        ★ flush(_capture_wysiwyg_once)는 runJavaScript 콜백이라 비동기다. 화면(webview)은
           건드리지 않고 _doc_text 만 확정한다(편집 결과가 그대로 화면에 남아 있으므로
           모드 전환 시 재렌더 불필요 — §2.3). 다음 모드가 Editor/Split 이면 콜백 안에서
           _sync_editor_from_doc 로 편집기를 보정한다(§2.4 비동기 경합 처리).
        """
        self._wysiwyg_poll.stop()
        self._wysiwyg_active = False   # 역렌더 게이트 OFF(이제 _render_doc 허용).
        self._wysiwyg_pending_setup = False
        self._capture_wysiwyg_once(final=True)  # 마지막 동기화(비동기 콜백).
        # editable 해제(다음 일반 렌더가 setHtml 로 덮으나 명시적 정리).
        try:
            self.view.page().runJavaScript(
                "(function(){var a=document.getElementById(%r);"
                "if(a) a.removeAttribute('contenteditable');})()" % _WYSIWYG_ROOT_ID
            )
        except Exception:
            pass

    def _leave_wysiwyg_for_document_change(self) -> None:
        """문서 교체(open/paste/reload) 전 WYSIWYG 정리 → Preview 강등(§4.6).

        데이터(_doc_text)는 _exit_wysiwyg 의 flush 로 보존된다. 새 문서는 Preview 에서
        뜬다(WYSIWYG 유지하려면 사용자가 다시 Ctrl+4). 설계 단순화: 문서 교체는
        항상 WYSIWYG 를 빠져나온다(진입은 오직 Ctrl+4/복원으로만).
        """
        if self._wysiwyg_active or self._view_mode == MODE_WYSIWYG:
            self._apply_view_mode(MODE_PREVIEW)  # _exit_wysiwyg() 자동 호출(§1.3 A).

    def _wysiwyg_poll_tick(self) -> None:
        """폴링 주기마다 편집 컨테이너 innerHTML 을 비동기 취득해 변화 시 동기화."""
        if not self._wysiwyg_active:
            self._wysiwyg_poll.stop()
            return
        self._capture_wysiwyg_once(final=False)

    def _capture_wysiwyg_once(self, *, final: bool) -> None:
        """편집 컨테이너 innerHTML 1회 취득 → html_to_markdown → _doc_text 갱신.

        final=True(이탈)면 콜백에서 편집기 동기화까지(다음 모드가 편집기류일 때) 보정해
        "마지막에 친 글자가 소스 편집기/저장에 누락" 버그를 막는다(§2.4).
        """
        js_get = (
            "(function(){var a=document.getElementById(%r);"
            "return a ? a.innerHTML : null;})()" % _WYSIWYG_ROOT_ID
        )

        def _cb(html) -> None:
            if not isinstance(html, str):
                return  # 페이지 미준비/실패 → 무시.
            self._ingest_wysiwyg_html(html)  # 변화 시에만 _doc_text/dirty.
            if final and self._view_mode in (MODE_EDITOR, MODE_SPLIT):
                self._sync_editor_from_doc()  # 편집기에 최신 _doc_text 반영(보정).

        try:
            self.view.page().runJavaScript(js_get, 0, _cb)
        except Exception:
            pass

    def _ingest_wysiwyg_html(self, html: str) -> None:
        """편집 컨테이너 innerHTML 을 받아 '변화가 있을 때만' _doc_text/dirty 갱신.

        ★ 베이스라인(_wysiwyg_last_html)과 비교해 프로그램적 setHtml(진입) 직후의
           무변화를 사용자 편집으로 오인하지 않는다(dirty 오염·무한 동기화 방지).
        ★ WYSIWYG 는 webview 를 재렌더하지 않는다 → _doc_text 만 갱신(커서 보존).
           절대 _render_doc/_set_document/setHtml/_populate_toc 를 호출하지 않는다.
        """
        if self._wysiwyg_last_html is None:
            # 아직 베이스라인 미설정(진입 셋업 콜백 전) → 이번 값을 베이스라인으로만.
            self._wysiwyg_last_html = html
            return
        if html == self._wysiwyg_last_html:
            return  # 변화 없음 → no-op(루프/비용 차단).
        self._wysiwyg_last_html = html  # 새 베이스라인.
        md = html_to_markdown(html)  # core 호출(항상 str, 예외 비전파).
        self._doc_text = md
        self._set_dirty(True)  # 사용자 편집 = 미저장 표시.

    # ------------------------------------------------------------------ #
    # 편집기 ↔ _doc_text 동기화 + 라이브 프리뷰 디바운스
    # ------------------------------------------------------------------ #
    def _sync_editor_from_doc(self) -> None:
        """_doc_text 를 편집기에 반영한다(프로그램적 채움 — textChanged 억제).

        이미 동일하면 setPlainText 를 건너뛰어 커서/스크롤/undo 스택을 보존한다.
        이중 가드(억제 플래그 + blockSignals)로 textChanged 부작용을 막는다.

        ★ 개행 주의: QPlainTextEdit.toPlainText() 는 CRLF/CR 을 LF 로 정규화해 돌려준다.
        따라서 _doc_text 가 CRLF(예: read_markdown 의 바이트 충실 반환)여도 표시상
        동일하면 재채움(setPlainText)을 건너뛰어 커서/undo 를 보존한다. 정규화 비교로
        불필요한 재채움을 막는다(실제 데이터 _doc_text 자체는 변경하지 않음).
        """
        if self.editor.toPlainText() == self._normalize_newlines(self._doc_text):
            return  # 표시상 동일 → no-op(커서/undo 보존)
        self._suppress_editor_signal = True
        self.editor.blockSignals(True)
        try:
            self.editor.setPlainText(self._doc_text)
        finally:
            self.editor.blockSignals(False)
            self._suppress_editor_signal = False

    @staticmethod
    def _normalize_newlines(text: str) -> str:
        """CRLF/CR 을 LF 로 정규화(편집기 표시 비교 전용)."""
        return text.replace("\r\n", "\n").replace("\r", "\n")

    def _on_editor_text_changed(self) -> None:
        """편집기 textChanged 슬롯.

        프로그램적 채움이면 무시, 사용자 입력이면 즉시 dirty + 디바운스 시작.
        """
        if self._suppress_editor_signal:
            return
        self._set_dirty(True)        # 사용자 편집 = 즉시 미저장 표시
        self._render_timer.start()   # 연속 입력은 마지막 1회로 합쳐 렌더

    def _commit_editor_to_preview(self) -> None:
        """디바운스 만료 시: 편집기 내용을 _doc_text 에 반영하고 프리뷰 재렌더."""
        if self._wysiwyg_active:
            return  # WYSIWYG 에선 소스 편집기 숨김(방어적 — textChanged 안 옴).
        self._doc_text = self.editor.toPlainText()
        self._render_doc(preserve_scroll=True)

    def _flush_pending_edit(self) -> None:
        """대기 중인 디바운스를 즉시 반영(저장/reload 등 _doc_text 의존 작업 전 호출).

        디바운스 만료 전 사용자가 저장하면 _doc_text 가 한 박자 오래된 상태일 수 있다.
        저장은 _doc_text 를 쓰므로, 타이머가 대기 중이면 강제 commit(렌더는 생략).
        """
        if self._render_timer.isActive():
            self._render_timer.stop()
            self._doc_text = self.editor.toPlainText()

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
        # 문서 교체 → WYSIWYG 라이브 편집을 빠져나온다(scratch 는 일반 렌더, §4.6).
        self._leave_wysiwyg_for_document_change()
        self._set_scratch(text)

    # ------------------------------------------------------------------ #
    # 저장 — Ctrl+S(저장) / Ctrl+Shift+S(다른 이름으로 저장)
    # ------------------------------------------------------------------ #
    def save(self) -> bool:
        """Ctrl+S: scratch 면 Save As, 파일연결이면 같은 경로에 바로 저장.

        WYSIWYG 활성 중에는 최신 innerHTML 을 먼저 캡처(비동기)한 뒤 저장한다 —
        마지막 타이핑 직후 저장해도 폴링 tick 전 유실되지 않도록(§4.4).
        """
        if self._wysiwyg_active:
            self._save_after_wysiwyg_capture()
            return True  # 비동기 — 반환값은 best-effort.
        if self._path is None:
            return self.save_as()
        return self._write_to(self._path)

    def _save_after_wysiwyg_capture(self) -> None:
        """WYSIWYG 최신 편집을 캡처해 _doc_text 확정 후 저장(비동기, §4.4)."""
        js_get = (
            "(function(){var a=document.getElementById(%r);"
            "return a ? a.innerHTML : null;})()" % _WYSIWYG_ROOT_ID
        )

        def _do_write() -> None:
            if self._path is None:
                self.save_as()
            else:
                self._write_to(self._path)

        def _cb(html) -> None:
            if isinstance(html, str):
                self._ingest_wysiwyg_html(html)  # _doc_text 최신화(변화 시 dirty).
            _do_write()

        try:
            self.view.page().runJavaScript(js_get, 0, _cb)
        except Exception:
            # 캡처 실패 시에도 현재 _doc_text 로 저장(폴링이 잡아둔 최신).
            _do_write()

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
        # 디바운스 대기 중인 마지막 타이핑을 _doc_text 에 반영(데이터 유실 방지).
        self._flush_pending_edit()
        # self-write 억제: 저장이 유발할 watcher 이벤트를 무시할 시간 창을 연다.
        self._suppress_watch_until = time.monotonic() + (_SELF_WRITE_SUPPRESS_MS / 1000.0)
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
        # 디스크가 _doc_text 와 동기화됨 → 미해결 외부 변경 안내(배너)를 내린다.
        self._clear_external_change_banner()
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
            # ★ WYSIWYG 활성 시 save() 는 비동기(캡처 콜백 안에서 write). 이 가드는
            #   호출 직후 문서 교체(open/paste/reload)나 종료가 이어질 수 있으므로,
            #   비동기 write 가 옛 _path/_doc_text 로 끝나도록 짧게 이벤트 루프를 돌려
            #   캡처+쓰기를 안착시킨 뒤 진행한다(데이터 안전).
            if self._wysiwyg_active:
                self.save()  # 캡처+쓰기 예약(비동기).
                try:
                    from PySide6.QtCore import QCoreApplication, QEventLoop

                    for _ in range(20):  # 최대 ~200ms.
                        QCoreApplication.processEvents(
                            QEventLoop.ProcessEventsFlag.AllEvents, 10
                        )
                except Exception:
                    pass
                # 쓰기 성공 시 _dirty=False 가 되어 있어야 진행 허용.
                return not self._dirty
            return self.save()  # 저장 성공 시에만 진행(동기 경로)
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
        self._sync_editor_from_doc()  # 편집기를 디스크 내용과 동기화(신호 억제)
        self._set_dirty(False)  # 디스크와 동기화됨
        return True

    def _render_doc(self, *, preserve_scroll: bool) -> None:
        """_doc_text 를 렌더해 화면에 반영(파일 재독 없음).

        base_dir 은 _path 가 있으면 그 부모, scratch 면 _scratch_base_dir().
        테마 전환·붙여넣기 직후처럼 디스크 재독이 불필요한 경로에서 사용.

        ★ WYSIWYG 라이브 편집 중엔 역렌더 금지(커서/선택/스크롤 보존). 진입 시의
          1회 setHtml 은 _enter_wysiwyg 가 직접 수행하므로 이 게이트를 거치지 않는다.
        """
        if self._wysiwyg_active:
            return
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
        편집 중(dirty)이면 데이터 유실 가드(_maybe_discard) 후 진행 — 충돌 정책의 탈출구.
        """
        if self._path is None:
            self.statusBar().showMessage("임시 문서는 새로고침 대상이 없습니다.", 3000)
            return False
        if self._dirty and not self._maybe_discard():
            return False  # 사용자가 취소 → reload 중단(편집 내용 보호)
        # 문서 재독 → WYSIWYG 라이브 편집을 빠져나온다(일반 렌더로 복귀, §4.6).
        self._leave_wysiwyg_for_document_change()
        self._clear_external_change_banner()
        # _load_from_disk 내부에서 _sync_editor_from_doc 호출됨(편집기 동기화).
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
        # WYSIWYG 진입 셋업(페이지 로드 완료 후에만 DOM 조작 가능 — §2.2).
        if ok and self._wysiwyg_pending_setup:
            self._wysiwyg_pending_setup = False
            self._activate_editable_then_baseline()

    # ------------------------------------------------------------------ #
    # 파일 변경 콜백 (메인 스레드) — 편집↔감시 충돌 정책
    # ------------------------------------------------------------------ #
    def _on_file_changed(self) -> None:
        """watcher 콜백(메인 스레드). self-write 시간 창 억제 후 디바운스."""
        # 가드 A: 자기 저장(write_markdown) 직후의 이벤트는 무시(시간 창).
        if time.monotonic() < self._suppress_watch_until:
            return
        # 디바운스(콜백 폭주 방어) — 최종 1회만 정책 적용.
        self._reload_timer.start()

    def _on_external_change_settled(self) -> None:
        """디바운스 만료 후 외부 변경 처리(충돌 정책 적용).

        - scratch(_path None): watch 대상 아님 → 무시.
        - 디스크 내용 == _doc_text: self-write 잔향/무변경 → 무시(가드 B).
        - dirty(편집 중): 자동 reload 금지 → 배너/상태바 안내(편집 내용 보호).
        - not dirty: 안전하게 자동 reload(순수 뷰어 동작 보존, 스크롤 보존).
        """
        if self._path is None:
            return
        try:
            disk_text = read_markdown(self._path)  # core 호출(작은 파일 — 블로킹 무시)
        except OSError:
            self.statusBar().showMessage("외부에서 파일이 변경/삭제되었습니다.", 5000)
            return
        # 가드 B: 디스크 내용이 이미 _doc_text 와 같으면 reload 불필요(self-write 잔향).
        if disk_text == self._doc_text:
            return

        # 가드 C: WYSIWYG 라이브 편집 중이면 무조건 배너만(편집 surface 덮어쓰기 금지).
        if self._wysiwyg_active:
            self._show_external_change_banner()
            return

        if self._dirty:
            # ★ 편집 중 미저장 변경 → 자동 reload 금지(편집 내용 보호). 비차단 안내만.
            self._show_external_change_banner()
            return

        # dirty 아님(순수 뷰어) → 안전하게 자동 reload.
        self._doc_text = disk_text
        self._render_doc(preserve_scroll=True)
        self._sync_editor_from_doc()
        self._set_dirty(False)
        self.statusBar().showMessage("외부 변경을 반영했습니다.", 2000)

    def _show_external_change_banner(self) -> None:
        """비차단 안내: 상태바 영구 메시지(모달 지양). 다음 액션까지 유지."""
        self.statusBar().showMessage(
            "⚠ 외부에서 파일이 변경되었습니다 — Ctrl+R 로 새로고침"
            "(편집 내용은 덮어쓰여집니다)"
        )
        self._external_changed = True

    def _clear_external_change_banner(self) -> None:
        if self._external_changed:
            self._external_changed = False
            self.statusBar().clearMessage()

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
        # WYSIWYG 중 테마 전환: 최신 편집을 _doc_text 로 확정한 뒤 새 테마로 재진입
        # (CSS 교체엔 setHtml 재호출이 필요하나 편집 DOM 을 덮으므로 flush→재진입).
        # 캡처가 비동기라 콜백 안에서 _enter_wysiwyg 를 호출해 옛 _doc_text 경합 제거(§4.5).
        if self._wysiwyg_active:
            self._wysiwyg_poll.stop()
            js_get = (
                "(function(){var a=document.getElementById(%r);"
                "return a ? a.innerHTML : null;})()" % _WYSIWYG_ROOT_ID
            )

            def _cb(html) -> None:
                if isinstance(html, str):
                    self._ingest_wysiwyg_html(html)  # _doc_text 최신화(변화 시 dirty).
                self._wysiwyg_active = False  # 게이트 잠시 내려 재진입 허용.
                self._enter_wysiwyg()  # render(_doc_text)→editable(새 테마).

            try:
                self.view.page().runJavaScript(js_get, 0, _cb)
            except Exception:
                self._wysiwyg_active = False
                self._enter_wysiwyg()
            self.statusBar().showMessage(
                f"테마: {'다크' if self._theme == theme_mod.DARK else '라이트'}"
                " (라이브 편집 — 커서가 문서 처음으로 이동할 수 있음)",
                2500,
            )
            return
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
            "단축키: Ctrl+N 새 문서 · Ctrl+O 열기 · Ctrl+Shift+V 붙여넣기 · "
            "Ctrl+S 저장 · Ctrl+Shift+S 다른 이름으로 저장 · "
            "Ctrl+Z 실행취소 · Ctrl+Shift+Z / Ctrl+Y 다시실행 · "
            "Ctrl+1 / Ctrl+2 / Ctrl+3 / Ctrl+4 편집기 / 미리보기 / 분할 / 라이브 편집 · "
            "Ctrl+R 새로고침 · Ctrl+T 테마 · "
            "Ctrl+= / Ctrl+- / Ctrl+0 줌 · F11 전체화면 · Ctrl+\\ 목차</p>"
            "<p style='color:gray;font-size:0.82em'>"
            "서식 툴바: 편집기/분할/라이브 편집에서 표시됩니다. 편집기/분할에선 "
            "마크다운 마커(**, *, ~~, `, 목록/인용/제목)를 삽입하고, "
            "라이브 편집(WYSIWYG)에선 미리보기에서 직접 서식을 적용합니다. "
            "마크다운 소스가 정규화될 수 있습니다(내용은 보존).</p>",
        )

    # ------------------------------------------------------------------ #
    # 종료 정리
    # ------------------------------------------------------------------ #
    def closeEvent(self, event) -> None:  # noqa: N802
        # WYSIWYG 활성 시: 마지막 편집을 _doc_text 로 확정(폴링 캡처는 비동기라 종료
        # 직전 누락 위험). 이탈 캡처를 쏘고 짧게 이벤트 루프를 돌려 콜백을 안착시킨다
        # → 이후 _maybe_discard 의 Save 가 최신 _doc_text 를 쓴다(데이터 안전).
        if self._wysiwyg_active:
            self._exit_wysiwyg()  # 폴링 stop + final 캡처(비동기 콜백 예약).
            try:
                from PySide6.QtCore import QCoreApplication
                from PySide6.QtCore import QEventLoop as _QEL

                for _ in range(20):  # 최대 ~200ms, runJavaScript 콜백 안착 대기.
                    QCoreApplication.processEvents(_QEL.ProcessEventsFlag.AllEvents, 10)
            except Exception:
                pass
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
