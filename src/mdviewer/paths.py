"""리소스 경로 해석 — 개발 환경과 PyInstaller 번들 양쪽을 지원한다.

PyInstaller로 패키징하면 리소스가 ``sys._MEIPASS`` 임시 폴더에 풀린다.
모든 CSS/아이콘/JS 로드는 반드시 이 모듈의 함수를 거쳐야 번들에서도 경로가 깨지지 않는다.

번들 규칙(packager와 공유):
    .spec 의 datas 에서 리소스를 ``mdviewer/resources`` 로 수집한다.
    예: ('src/mdviewer/resources', 'mdviewer/resources')
    따라서 frozen 상태의 리소스 루트는 ``<_MEIPASS>/mdviewer/resources`` 다.
"""

from __future__ import annotations

import sys
from pathlib import Path

# 번들 내부에서 리소스가 위치하는 상대 경로. .spec 의 datas 대상과 반드시 일치해야 한다.
_BUNDLE_RESOURCE_SUBPATH = "mdviewer/resources"


def is_frozen() -> bool:
    """PyInstaller 등으로 동결(번들)된 실행 환경인지 여부."""
    return getattr(sys, "frozen", False)


def resource_dir() -> Path:
    """리소스 디렉터리(styles/, icons/, ...)의 절대 경로를 반환한다.

    - 번들: ``<sys._MEIPASS>/mdviewer/resources``
    - 개발: ``<이 파일이 있는 mdviewer 패키지>/resources``
    """
    if is_frozen():
        base = Path(getattr(sys, "_MEIPASS"))
        return base / _BUNDLE_RESOURCE_SUBPATH
    return Path(__file__).resolve().parent / "resources"


def resource_path(*parts: str) -> Path:
    """리소스 루트 하위의 파일/폴더 절대 경로를 반환한다.

    예) ``resource_path("styles", "github.css")``
    """
    return resource_dir().joinpath(*parts)


def styles_dir() -> Path:
    """CSS 스타일 디렉터리."""
    return resource_dir() / "styles"


def icons_dir() -> Path:
    """아이콘 디렉터리."""
    return resource_dir() / "icons"
