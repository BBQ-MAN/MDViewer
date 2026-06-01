"""마크다운 → HTML 렌더링 엔진 (비-GUI 코어).

이 모듈은 **PySide6에 의존하지 않는다.** 순수 Python + markdown-it-py /
mdit-py-plugins / Pygments / charset-normalizer 만 사용한다.

청사진(_workspace/01_architect_blueprint.md)의 렌더 API 계약을 그대로 구현한다:
    - render(markdown_text, base_dir) -> RenderResult  (예외 던지지 않음)
    - read_markdown(path) -> str                       (인코딩 자동 감지)
    - RenderResult / TocItem dataclass
    - pygments_css(dark) -> str                        (테마 CSS 헬퍼; theme.py 가 사용)

출력 HTML은 **<body> 본문만** 포함한다(헤딩 id, Pygments span 클래스 적용).
완전한 문서 셸/CSS 는 UI(theme.py)가 조립한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.anchors.index import slugify as _default_slugify
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.front_matter import front_matter_plugin
from mdit_py_plugins.tasklists import tasklists_plugin
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name, guess_lexer
from pygments.util import ClassNotFound

__all__ = [
    "TocItem",
    "RenderResult",
    "render",
    "read_markdown",
    "pygments_css",
    "slugify",
]

# 코드블록에 적용되는 CSS 클래스. pygments_css() 가 생성하는 선택자와 일치해야 한다.
_PYGMENTS_CSS_CLASS = "codehilite"

# 라이트/다크 Pygments 스타일 이름.
_PYGMENTS_LIGHT_STYLE = "default"
_PYGMENTS_DARK_STYLE = "monokai"


# ---------------------------------------------------------------------------
# 공개 데이터 모델 (계약 고정)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TocItem:
    """목차 한 항목."""

    level: int  # 헤딩 깊이 1~6 (h1=1)
    text: str  # 헤딩의 표시 텍스트(인라인 마크업 제거된 평문)
    anchor: str  # HTML id. 본문 헤딩의 id 와 항상 일치.


@dataclass
class RenderResult:
    """render() 의 반환 shape. 절대 None 을 반환하지 않는다."""

    html: str = ""  # <body> 안에 들어갈 본문 HTML
    toc: list[TocItem] = field(default_factory=list)
    title: str | None = None  # 첫 h1 텍스트. 없으면 None.


# ---------------------------------------------------------------------------
# 슬러그 (앵커) — 청사진 3.1 규칙
# ---------------------------------------------------------------------------
def slugify(text: str) -> str:
    """헤딩 텍스트 → HTML id 슬러그.

    규칙: 소문자화 → 공백을 ``-`` 로 → 영숫자/한글/``-`` 외 문자 제거.
    mdit-py-plugins 의 anchors 기본 슬러그와 동일하게 맞춘다(본문 id 일치 보장).
    """
    return _default_slugify(text)


def _unique(slug: str, used: dict[str, int]) -> str:
    """중복 슬러그를 ``-1``, ``-2`` 접미사로 유일화한다.

    mdit_py_plugins.anchors 의 unique_slug 와 동일한 정책(첫 등장은 접미사 없음,
    이후 ``-1`` 부터)을 따른다.
    """
    if slug not in used:
        used[slug] = 0
        return slug
    i = used[slug] + 1
    candidate = f"{slug}-{i}"
    while candidate in used:
        i += 1
        candidate = f"{slug}-{i}"
    used[slug] = i
    used[candidate] = 0
    return candidate


# ---------------------------------------------------------------------------
# Pygments 코드 하이라이트
# ---------------------------------------------------------------------------
def _highlight_code(code: str, lang: str, _attrs: object = None) -> str:
    """코드 펜스를 Pygments span 클래스가 적용된 HTML 로 변환한다.

    인라인 색상 스타일은 넣지 않는다(테마는 외부 CSS 로 분리).
    알 수 없는 언어/실패 시 빈 문자열을 반환해 markdown-it 의 기본 escape 처리에 위임한다.
    """
    if not code:
        return ""
    lexer = None
    if lang:
        try:
            lexer = get_lexer_by_name(lang)
        except ClassNotFound:
            lexer = None
    if lexer is None:
        # 언어 미지정/미상이면 추측을 시도하되, 실패하면 기본 escape 로 위임.
        try:
            if lang:
                # 명시했으나 미상인 언어 → 추측 대신 plain 으로 둔다.
                return ""
            lexer = guess_lexer(code)
        except (ClassNotFound, ValueError):
            return ""
    try:
        formatter = HtmlFormatter(nowrap=False, cssclass=_PYGMENTS_CSS_CLASS)
        return highlight(code, lexer, formatter)
    except Exception:
        # 어떤 경우에도 렌더 전체를 깨지 않는다.
        return ""


def pygments_css(dark: bool = False) -> str:
    """Pygments 토큰 색상 CSS 를 반환한다(라이트/다크 두 벌).

    theme.py 가 HTML 문서 셸에 인라인 주입하거나 styles/ 에 기록하는 데 사용한다.
    선택자는 ``.codehilite`` (renderer 가 코드블록에 부여하는 클래스)에 한정된다.

    Args:
        dark: True 면 다크 테마(monokai), False 면 라이트 테마(default).

    Returns:
        ``.codehilite ...`` 규칙들로 이루어진 CSS 문자열.
    """
    style = _PYGMENTS_DARK_STYLE if dark else _PYGMENTS_LIGHT_STYLE
    formatter = HtmlFormatter(style=style, cssclass=_PYGMENTS_CSS_CLASS)
    return formatter.get_style_defs(f".{_PYGMENTS_CSS_CLASS}")


# ---------------------------------------------------------------------------
# 마크다운 파서 구성
# ---------------------------------------------------------------------------
def _build_md() -> MarkdownIt:
    """렌더 파서를 구성한다.

    anchors_plugin 은 쓰지 않고(본문 id 와 TOC anchor 의 일치를 직접 보장하기 위해),
    헤딩 id 부여와 TOC 추출을 자체 core rule + 토큰 후처리로 수행한다.
    """
    md = MarkdownIt(
        "commonmark",
        {
            "html": False,  # 원시 HTML 비활성(임의 파일 보안/견고성).
            "linkify": True,
            "typographer": True,
            "highlight": _highlight_code,
        },
    )
    # GFM 핵심: 테이블/취소선은 commonmark 프리셋에 없으므로 활성화.
    md.enable("table")
    md.enable("strikethrough")
    md = (
        md.use(front_matter_plugin)
        .use(footnote_plugin)
        .use(tasklists_plugin, enabled=True)
    )
    return md


# 모듈 전역 파서(스레드 안전: 파싱은 상태를 공유하지 않는 호출별 env 사용).
_MD = _build_md()


def _heading_text(inline_token: Token) -> str:
    """헤딩 inline 토큰에서 평문 텍스트를 추출한다(마크업 제거)."""
    if inline_token.children is None:
        return inline_token.content.strip()
    parts: list[str] = []
    for child in inline_token.children:
        if child.type in ("text", "code_inline"):
            parts.append(child.content)
        elif child.type == "image":
            # 이미지 alt 텍스트는 헤딩 텍스트로 포함.
            parts.append(child.content)
    text = "".join(parts).strip()
    return text or inline_token.content.strip()


def _is_external(url: str) -> bool:
    """절대 URL/프로토콜/프래그먼트/메일 등 — base_dir 로 치환하지 않을 대상인가."""
    if not url:
        return True
    if url.startswith("#"):
        return True  # 내부 앵커.
    parts = urlsplit(url)
    if parts.scheme:  # http, https, file, data, mailto, ...
        return True
    if url.startswith("//"):
        return True
    return False


def _to_file_uri(rel: str, base_dir: Path) -> str:
    """상대 경로를 base_dir 기준 ``file:///`` 절대 URI 로 변환한다.

    프래그먼트/쿼리는 보존한다. 변환 실패 시 원본을 그대로 둔다.
    """
    parts = urlsplit(rel)
    path_part = parts.path
    if not path_part:
        return rel
    try:
        target = (base_dir / path_part).resolve()
        uri = target.as_uri()
    except (OSError, ValueError):
        return rel
    if parts.query:
        uri += "?" + parts.query
    if parts.fragment:
        uri += "#" + parts.fragment
    return uri


def _process_tokens(
    tokens: list[Token], base_dir: Path
) -> tuple[list[TocItem], str | None]:
    """토큰 트리를 후처리한다.

    1) 헤딩에 결정적 id(슬러그) 부여 + TOC 추출
    2) 상대 이미지 src / 링크 href 를 base_dir 기준 file URI 로 치환

    Returns:
        (toc, title)
    """
    toc: list[TocItem] = []
    title: str | None = None
    used_slugs: dict[str, int] = {}

    def walk(toklist: list[Token]) -> None:
        nonlocal title
        i = 0
        while i < len(toklist):
            tok = toklist[i]

            if tok.type == "heading_open":
                level = int(tok.tag[1])
                inline = toklist[i + 1] if i + 1 < len(toklist) else None
                text = _heading_text(inline) if inline is not None else ""
                anchor = _unique(slugify(text), used_slugs)
                tok.attrSet("id", anchor)
                toc.append(TocItem(level=level, text=text, anchor=anchor))
                if title is None and level == 1:
                    title = text

            elif tok.type == "image":
                src = tok.attrGet("src")
                if isinstance(src, str) and not _is_external(src):
                    tok.attrSet("src", _to_file_uri(src, base_dir))

            elif tok.type == "link_open":
                href = tok.attrGet("href")
                if isinstance(href, str) and not _is_external(href):
                    tok.attrSet("href", _to_file_uri(href, base_dir))

            # 인라인 자식 토큰(이미지/링크)도 처리.
            if tok.children:
                walk(tok.children)
            i += 1

    walk(tokens)
    return toc, title


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------
def render(markdown_text: str, base_dir: Path) -> RenderResult:
    """마크다운 텍스트를 HTML 본문으로 변환한다.

    **예외를 던지지 않는다.** 빈 입력/깨진 마크다운/없는 이미지에도 의미 있는 결과를 반환한다.

    Args:
        markdown_text: 원본 마크다운 문자열.
        base_dir: 상대경로 이미지/링크 해석 기준 디렉터리(보통 열린 파일의 부모).

    Returns:
        RenderResult(html, toc, title). 절대 None 아님.
    """
    if markdown_text is None:  # type: ignore[redundant-expr]
        markdown_text = ""
    if not isinstance(base_dir, Path):
        try:
            base_dir = Path(base_dir)
        except (TypeError, ValueError):
            base_dir = Path.cwd()

    if not markdown_text.strip():
        return RenderResult(html="", toc=[], title=None)

    try:
        env: dict = {}
        tokens = _MD.parse(markdown_text, env)
        toc, title = _process_tokens(tokens, base_dir)
        html = _MD.renderer.render(tokens, _MD.options, env)
        return RenderResult(html=html, toc=toc, title=title)
    except Exception as exc:  # pragma: no cover - 최후의 안전망.
        # 어떤 입력에도 크래시하지 않는다. 의미 있는 안내 본문을 반환.
        from html import escape

        msg = escape(str(exc))
        return RenderResult(
            html=(
                "<p><em>이 문서를 렌더링하는 중 문제가 발생했습니다.</em></p>"
                f"<pre class=\"render-error\">{msg}</pre>"
            ),
            toc=[],
            title=None,
        )


def read_markdown(path: Path) -> str:
    """파일을 읽어 마크다운 텍스트(str)로 반환한다. 인코딩 자동 감지.

    UTF-8 우선 시도 → 실패 시 charset-normalizer 폴백. BOM 은 제거한다.

    Raises:
        FileNotFoundError: 경로가 없을 때.
        OSError: 읽기 실패(권한 등) 시.
        (디코딩 실패는 예외 대신 charset-normalizer 최선 결과로 복구한다.)
    """
    path = Path(path)
    raw = path.read_bytes()  # FileNotFoundError / OSError 는 그대로 전파.

    if raw == b"":
        return ""

    # 1) UTF-8 우선(가장 흔함). BOM 은 utf-8-sig 로 제거.
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue

    # 2) charset-normalizer 폴백.
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(raw).best()
        if best is not None:
            return _strip_bom(str(best))
    except Exception:
        pass

    # 3) 최후: 손실 허용 디코딩(절대 예외 던지지 않음 — 계약상 디코딩 실패는 복구).
    return _strip_bom(raw.decode("utf-8", errors="replace"))


def _strip_bom(text: str) -> str:
    """선두 BOM(U+FEFF)을 제거한다."""
    if text and text[0] == "﻿":
        return text[1:]
    return text
