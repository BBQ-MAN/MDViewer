"""테마 — 본문 CSS + Pygments CSS 를 결합해 완전한 HTML 문서 셸을 조립한다.

경계 규칙(청사진 §8):
    renderer 는 ``<body>`` 내부 본문 HTML 만 반환한다. 완전한 문서
    (``<!DOCTYPE>``/``<head>``/CSS)는 이 모듈이 조립한다.
    테마 전환 = CSS 만 교체 후 같은 본문을 다시 감싸 setHtml.

CSS 로드는 반드시 ``mdviewer.paths`` 를 경유한다(PyInstaller 번들 대응).

Pygments CSS 출처(우선순위):
    1) ``mdviewer.renderer.pygments_css(dark: bool) -> str`` 가 있으면 사용
       (core-engine-dev 가 제공하기로 한 헬퍼).
    2) Pygments 가 설치돼 있으면 런타임에 직접 생성.
    3) 둘 다 실패하면 styles/pygments-{light,dark}.css 파일을 읽음.
    4) 그래도 없으면 빈 문자열(하이라이트만 비활성, 앱은 동작).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from . import paths

# 테마 식별자
LIGHT = "light"
DARK = "dark"
VALID_THEMES = (LIGHT, DARK)

# 본문 테마 CSS 파일명
_BODY_CSS = {
    LIGHT: "github-light.css",
    DARK: "github-dark.css",
}

# fallback 으로 읽을 사전 생성 Pygments CSS 파일명(있을 경우)
_PYGMENTS_CSS_FILE = {
    LIGHT: "pygments-light.css",
    DARK: "pygments-dark.css",
}

# Pygments 직접 생성 시 사용할 스타일 이름
_PYGMENTS_STYLE = {
    LIGHT: "default",
    DARK: "github-dark",
}


def normalize_theme(theme: str | None) -> str:
    """알 수 없는/None 테마는 LIGHT 로 보정한다."""
    return theme if theme in VALID_THEMES else LIGHT


def toggle_theme(theme: str | None) -> str:
    """light <-> dark 전환."""
    return DARK if normalize_theme(theme) == LIGHT else LIGHT


@lru_cache(maxsize=4)
def _read_body_css(theme: str) -> str:
    """본문 테마 CSS 를 paths 경유로 읽는다. 실패 시 빈 문자열."""
    fname = _BODY_CSS.get(theme, _BODY_CSS[LIGHT])
    try:
        return paths.resource_path("styles", fname).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return ""


@lru_cache(maxsize=4)
def _pygments_css(theme: str) -> str:
    """Pygments(코드 하이라이트) CSS 를 얻는다. 위 docstring 의 우선순위대로."""
    dark = theme == DARK

    # 1) core-engine-dev 가 제공하는 헬퍼 우선.
    try:
        from .renderer import pygments_css as _core_pygments_css  # type: ignore

        css = _core_pygments_css(dark)
        if css:
            return css
    except Exception:
        # renderer 미구현/시그니처 상이 — 다음 폴백으로.
        pass

    # 2) Pygments 가 있으면 런타임 생성.
    try:
        from pygments.formatters import HtmlFormatter

        style = _PYGMENTS_STYLE[DARK if dark else LIGHT]
        try:
            fmt = HtmlFormatter(style=style)
        except Exception:
            fmt = HtmlFormatter(style="default")
        # .highlight 스코프로 한정해 본문 CSS 와 충돌 방지.
        return fmt.get_style_defs(".highlight")
    except Exception:
        pass

    # 3) 사전 생성 파일.
    try:
        fname = _PYGMENTS_CSS_FILE[DARK if dark else LIGHT]
        return paths.resource_path("styles", fname).read_text(encoding="utf-8")
    except (OSError, ValueError):
        pass

    # 4) 포기 — 하이라이트만 비활성.
    return ""


def theme_css(theme: str) -> str:
    """주어진 테마의 본문 CSS + Pygments CSS 결합본."""
    theme = normalize_theme(theme)
    return _read_body_css(theme) + "\n" + _pygments_css(theme)


def wrap_document(body_html: str, dark: bool) -> str:
    """본문 HTML(RenderResult.html)을 받아 CSS 포함 완전한 HTML 문서를 반환한다.

    Args:
        body_html: ``<body>`` 안에 들어갈 본문 HTML(renderer 가 만든 것).
        dark: 다크 테마 여부.

    Returns:
        ``<!DOCTYPE html>`` 부터 시작하는 완전한 HTML 문자열. setHtml 에 바로 사용.
    """
    theme = DARK if dark else LIGHT
    css = theme_css(theme)
    body = body_html or ""
    return (
        "<!DOCTYPE html>\n"
        '<html lang="ko">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<style>\n{css}\n</style>\n"
        "</head>\n"
        f'<body class="theme-{theme}">\n'
        f'<article class="markdown-body">\n{body}\n</article>\n'
        "</body>\n"
        "</html>\n"
    )


def empty_document(dark: bool, message: str = "") -> str:
    """문서가 열리지 않은 초기 상태/안내 화면용 셸."""
    msg = message or "마크다운 파일을 열어주세요. (Ctrl+O 또는 드래그앤드롭)"
    body = (
        '<div style="text-align:center;margin-top:18vh;opacity:0.55;'
        'font-size:1.05em;">'
        f"{msg}</div>"
    )
    return wrap_document(body, dark)
