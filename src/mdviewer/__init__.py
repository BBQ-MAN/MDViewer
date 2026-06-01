"""MDViewer — Python/PySide6 기반 Windows 데스크톱 마크다운 뷰어.

코어 엔진(renderer, file_watcher)은 PySide6 의존성 없이 동작한다.
UI 계층(app, main_window, theme, settings)만 PySide6에 의존한다.
"""

__version__ = "0.1.0"
__app_name__ = "MDViewer"
__author__ = "Lectus"

# 코어 공개 심볼(비-GUI). UI/QA 가 `from mdviewer import render, ...` 로 사용.
from mdviewer.file_watcher import FileWatcher
from mdviewer.renderer import (
    RenderResult,
    TocItem,
    pygments_css,
    read_markdown,
    render,
)

__all__ = [
    "__version__",
    "__app_name__",
    "__author__",
    # 코어 렌더 API
    "render",
    "read_markdown",
    "RenderResult",
    "TocItem",
    "pygments_css",
    # 파일 감시
    "FileWatcher",
]
