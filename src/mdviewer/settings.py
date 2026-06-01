"""QSettings 래퍼 — 최근 파일 목록, 테마, 창 상태를 영속화한다.

위치(Windows): 레지스트리 ``HKEY_CURRENT_USER\\Software\\MDViewer\\MDViewer``.
앱/조직 이름은 app.py 가 QApplication 에 설정하므로, 여기서는 인자 없이
``QSettings()`` 만 생성하면 동일 경로로 연결된다.
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QSettings

from . import theme as theme_mod

_KEY_RECENT = "recent_files"
_KEY_THEME = "theme"
_KEY_GEOMETRY = "window/geometry"
_KEY_STATE = "window/state"
_KEY_TOC_VISIBLE = "view/toc_visible"
_KEY_ZOOM = "view/zoom"

MAX_RECENT = 10


class Settings:
    """앱 설정 영속화 헬퍼. 얇은 QSettings 래퍼."""

    def __init__(self) -> None:
        # app/org 이름은 QApplication 에서 설정됨 → 인자 없이 생성.
        self._s = QSettings()

    # ---- 테마 -------------------------------------------------------------
    def theme(self) -> str:
        return theme_mod.normalize_theme(self._s.value(_KEY_THEME, theme_mod.LIGHT, type=str))

    def set_theme(self, value: str) -> None:
        self._s.setValue(_KEY_THEME, theme_mod.normalize_theme(value))

    # ---- 최근 파일 --------------------------------------------------------
    def recent_files(self) -> list[str]:
        """존재 여부와 무관하게 저장된 경로 목록(최신이 위)."""
        raw = self._s.value(_KEY_RECENT, [], type=list) or []
        # QSettings 가 단일 문자열을 반환하는 경우 방어.
        if isinstance(raw, str):
            raw = [raw]
        out: list[str] = []
        for item in raw:
            if item and str(item) not in out:
                out.append(str(item))
        return out[:MAX_RECENT]

    def add_recent_file(self, path: str | os.PathLike[str]) -> list[str]:
        """경로를 최근 목록 맨 위로 올리고(중복 제거, 최대 10개) 저장. 새 목록 반환."""
        p = str(Path(path).resolve())
        items = [x for x in self.recent_files() if x != p]
        items.insert(0, p)
        items = items[:MAX_RECENT]
        self._s.setValue(_KEY_RECENT, items)
        return items

    def clear_recent_files(self) -> None:
        self._s.setValue(_KEY_RECENT, [])

    def set_recent_files(self, items: list[str]) -> None:
        self._s.setValue(_KEY_RECENT, items[:MAX_RECENT])

    # ---- 창 상태 ----------------------------------------------------------
    def geometry(self):
        return self._s.value(_KEY_GEOMETRY)

    def set_geometry(self, data) -> None:
        self._s.setValue(_KEY_GEOMETRY, data)

    def window_state(self):
        return self._s.value(_KEY_STATE)

    def set_window_state(self, data) -> None:
        self._s.setValue(_KEY_STATE, data)

    # ---- 보기 옵션 --------------------------------------------------------
    def toc_visible(self) -> bool:
        return self._s.value(_KEY_TOC_VISIBLE, True, type=bool)

    def set_toc_visible(self, value: bool) -> None:
        self._s.setValue(_KEY_TOC_VISIBLE, bool(value))

    def zoom(self) -> float:
        try:
            return float(self._s.value(_KEY_ZOOM, 1.0))
        except (TypeError, ValueError):
            return 1.0

    def set_zoom(self, value: float) -> None:
        self._s.setValue(_KEY_ZOOM, float(value))

    def sync(self) -> None:
        self._s.sync()
