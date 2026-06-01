"""앱 진입점 — QApplication 부트스트랩, CLI 인자로 파일 열기.

``main() -> int`` 가 핵심 진입점이다(pyproject 의 gui-scripts/scripts 가 가리킴).
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from . import __app_name__


class MDViewerApplication(QApplication):
    """QApplication 서브클래스 — macOS 의 파일 열기(Apple Event)를 처리한다.

    Windows/Linux 는 파일 경로를 ``argv`` 로 전달하지만, macOS 는 Finder 더블클릭·
    Dock 드롭 시 경로를 ``argv`` 로 주지 않고 ``QEvent.Type.FileOpen`` 이벤트로 보낸다.
    이 이벤트는 윈도우 생성 전에 도착할 수 있으므로, 윈도우가 없으면 버퍼링했다가
    ``set_main_window`` 시점에 연다. (Windows/Linux 에선 이 이벤트가 발생하지 않아 무해하다.)
    """

    def __init__(self, argv: list[str]) -> None:
        super().__init__(argv)
        self._main_window = None
        self._pending_file: Path | None = None

    def set_main_window(self, win) -> None:
        self._main_window = win
        if self._pending_file is not None:
            win.open_path(self._pending_file)
            self._pending_file = None

    def event(self, e: QEvent) -> bool:  # noqa: N802 (Qt 시그니처)
        if e.type() == QEvent.Type.FileOpen:
            path = Path(e.file())
            if path:
                if self._main_window is not None:
                    self._main_window.open_path(path)
                else:
                    self._pending_file = path
            return True
        return super().event(e)


def _configure_app(app: QApplication) -> None:
    # QSettings 가 일관된 경로를 쓰도록 app/org 이름 설정.
    # (Windows=레지스트리, macOS=~/Library/Preferences plist, Linux=~/.config — QSettings 가 자동 분기.)
    app.setApplicationName(__app_name__)
    app.setApplicationDisplayName(__app_name__)
    app.setOrganizationName(__app_name__)
    app.setOrganizationDomain("lectus.kr")


def main(argv: list[str] | None = None) -> int:
    """앱을 실행한다. 첫 CLI 인자가 있으면 해당 파일을 연다."""
    argv = list(sys.argv if argv is None else argv)

    app = QApplication.instance() or MDViewerApplication(argv)
    _configure_app(app)

    # 윈도우는 app 설정 이후에 생성(QSettings 가 올바른 경로를 쓰도록).
    from .main_window import MainWindow

    win = MainWindow()

    # macOS: 윈도우 생성 전에 도착한 FileOpen 이벤트를 연결/플러시.
    if isinstance(app, MDViewerApplication):
        app.set_main_window(win)

    # CLI 인자(또는 파일 연결 프로그램)로 받은 첫 파일 열기.
    first_file = _first_existing_path(argv[1:])
    if first_file is not None:
        win.open_path(first_file)

    win.show()
    return app.exec()


def _first_existing_path(args: list[str]) -> Path | None:
    for a in args:
        if not a or a.startswith("-"):
            continue
        p = Path(a)
        if p.exists():
            return p
    return None


if __name__ == "__main__":
    sys.exit(main())
