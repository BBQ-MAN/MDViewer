# -*- coding: utf-8 -*-
"""renderer.py 코어 단위 테스트 (PySide6 무의존).

계약(_workspace/01_architect_blueprint.md §3)과 core notes(§02)를 검증한다:
- render(): 기본/빈/깨진/유니코드/코드하이라이트/앵커 일치/이미지 처리
- read_markdown(): 인코딩 감지/BOM 제거/없는 파일 예외
- pygments_css(): 스코프(.codehilite), 라이트/다크 구분
- slugify(): 앵커 규칙
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mdviewer.renderer import (
    RenderResult,
    TocItem,
    pygments_css,
    read_markdown,
    render,
    slugify,
)


# --------------------------------------------------------------------------- #
# 반환 shape / 기본 동작
# --------------------------------------------------------------------------- #
def test_render_returns_renderresult():
    r = render("# Hello\n\n`code`", base_dir=Path("."))
    assert isinstance(r, RenderResult)
    assert isinstance(r.html, str)
    assert isinstance(r.toc, list)


def test_render_basic_title_and_anchor():
    r = render("# Hello\n\nsome `code` here", base_dir=Path("."))
    assert "Hello" in r.html
    assert r.title == "Hello"
    assert r.toc, "TOC 가 생성돼야 한다"
    assert r.toc[0].anchor  # 앵커 생성됨
    assert isinstance(r.toc[0], TocItem)
    assert r.toc[0].level == 1
    assert r.toc[0].text == "Hello"


def test_render_empty():
    r = render("", base_dir=Path("."))
    assert r.html == ""
    assert r.toc == []
    assert r.title is None


def test_render_whitespace_only():
    r = render("   \n\t\n  ", base_dir=Path("."))
    assert r.html == ""
    assert r.toc == []


def test_render_none_is_safe():
    # 계약: 예외를 던지지 않는다. None 도 방어.
    r = render(None, base_dir=Path("."))  # type: ignore[arg-type]
    assert isinstance(r, RenderResult)


def test_render_broken_unclosed_fence_no_crash():
    r = render("```\nunclosed code fence", base_dir=Path("."))
    assert isinstance(r, RenderResult)
    assert r.html is not None


def test_render_bad_base_dir_type_no_crash():
    # base_dir 이 Path 아니어도 내부 변환(방어적, core notes §1).
    r = render("# Hi", base_dir="some/dir")  # type: ignore[arg-type]
    assert isinstance(r, RenderResult)
    assert r.title == "Hi"


def test_render_unicode_korean():
    r = render("# 한글 제목\n\n본문 내용 한글", base_dir=Path("."))
    assert "한글" in r.html
    assert r.title == "한글 제목"


def test_render_does_not_emit_raw_html():
    # html:False — 원시 HTML 은 escape (견고성/보안, core notes §5).
    r = render("<script>alert(1)</script>", base_dir=Path("."))
    assert "<script>" not in r.html
    assert "&lt;script&gt;" in r.html


# --------------------------------------------------------------------------- #
# 코드 하이라이트
# --------------------------------------------------------------------------- #
def test_code_highlight_codehilite_class():
    r = render("```python\nprint(1)\n```", base_dir=Path("."))
    # core 의 cssclass 는 'codehilite' (pygments_css 스코프와 일치해야 함).
    assert "codehilite" in r.html


def test_code_highlight_has_token_spans():
    r = render("```python\ndef foo():\n    return 1\n```", base_dir=Path("."))
    # Pygments 토큰 span 클래스가 들어가야 한다(예: keyword 'k', name.function 'nf').
    assert 'class="k"' in r.html or 'class="kn"' in r.html or 'class="nf"' in r.html


def test_code_highlight_unknown_language_no_crash():
    r = render("```nonexistlang\nfoo bar\n```", base_dir=Path("."))
    assert isinstance(r, RenderResult)
    # 미상 언어 → plain escape, 크래시 없음. 내용 보존.
    assert "foo bar" in r.html


def test_code_block_no_language_no_crash():
    r = render("```\nplain text block\n```", base_dir=Path("."))
    assert isinstance(r, RenderResult)
    assert "plain text block" in r.html


# --------------------------------------------------------------------------- #
# 앵커 / TOC 일치 (계약 §3.1 — 통합 핵심)
# --------------------------------------------------------------------------- #
def _heading_ids(html: str) -> list[str]:
    return re.findall(r'<h[1-6][^>]*id="([^"]+)"', html)


def test_anchor_matches_heading_id():
    md = "# First\n\n## Second\n\n### Third"
    r = render(md, base_dir=Path("."))
    ids = _heading_ids(r.html)
    anchors = [t.anchor for t in r.toc]
    assert anchors == ids, "TOC anchor 와 본문 헤딩 id 가 완전히 일치해야 한다"


def test_anchor_korean_matches():
    md = "# 코드 하이라이트\n\n## 테이블"
    r = render(md, base_dir=Path("."))
    ids = _heading_ids(r.html)
    assert "코드-하이라이트" in ids
    assert "테이블" in ids
    assert [t.anchor for t in r.toc] == ids


def test_duplicate_headings_uniquified():
    md = "# dup\n\n# dup\n\n# dup"
    r = render(md, base_dir=Path("."))
    anchors = [t.anchor for t in r.toc]
    assert anchors == ["dup", "dup-1", "dup-2"]
    # 본문 id 도 동일해야 한다.
    assert _heading_ids(r.html) == anchors


def test_all_heading_levels_get_ids():
    md = "\n\n".join(f"{'#' * lvl} H{lvl}" for lvl in range(1, 7))
    r = render(md, base_dir=Path("."))
    assert len(r.toc) == 6
    assert [t.level for t in r.toc] == [1, 2, 3, 4, 5, 6]
    assert all(t.anchor for t in r.toc)


def test_slugify_consistency():
    # 공개 slugify 가 본문 앵커와 동일 결과를 내야 한다(UI 내부 링크 생성용).
    assert slugify("Hello World") == "hello-world"
    assert slugify("코드 하이라이트") == "코드-하이라이트"
    r = render("# Hello World", base_dir=Path("."))
    assert r.toc[0].anchor == slugify("Hello World")


# --------------------------------------------------------------------------- #
# GFM 확장
# --------------------------------------------------------------------------- #
def test_table_rendered():
    md = "| A | B |\n|---|---|\n| 1 | 2 |"
    r = render(md, base_dir=Path("."))
    assert "<table>" in r.html
    assert "<td>1</td>" in r.html or "1" in r.html


def test_footnote_rendered():
    md = "Text with footnote[^1].\n\n[^1]: the note."
    r = render(md, base_dir=Path("."))
    assert "footnote" in r.html.lower()


def test_tasklist_checkbox_rendered():
    md = "- [x] done\n- [ ] todo"
    r = render(md, base_dir=Path("."))
    assert 'type="checkbox"' in r.html


def test_strikethrough():
    r = render("~~gone~~", base_dir=Path("."))
    assert "<s>" in r.html or "<del>" in r.html


# --------------------------------------------------------------------------- #
# 이미지 / 링크 base_dir 처리 (계약 §3.3, core notes §4)
# --------------------------------------------------------------------------- #
def test_relative_image_converted_to_file_uri(tmp_path):
    md = "![alt](img/logo.png)"
    r = render(md, base_dir=tmp_path)
    assert "file:///" in r.html
    assert "logo.png" in r.html


def test_external_image_url_preserved():
    md = "![alt](https://example.com/x.png)"
    r = render(md, base_dir=Path("."))
    assert "https://example.com/x.png" in r.html
    assert "file:///" not in r.html


def test_internal_anchor_link_preserved():
    # 내부 앵커는 file URI 로 치환되면 안 됨(#... 유지).
    md = "[go](#section)\n\n# section"
    r = render(md, base_dir=Path("."))
    assert 'href="#section"' in r.html
    assert "file:///" not in r.html


def test_missing_image_no_crash():
    # 존재하지 않는 이미지도 예외 없이 렌더(견고성).
    r = render("![x](does/not/exist.png)", base_dir=Path("."))
    assert isinstance(r, RenderResult)
    assert "exist.png" in r.html


# --------------------------------------------------------------------------- #
# pygments_css
# --------------------------------------------------------------------------- #
def test_pygments_css_scoped_to_codehilite():
    css = pygments_css(dark=False)
    assert ".codehilite" in css


def test_pygments_css_light_dark_differ():
    light = pygments_css(dark=False)
    dark = pygments_css(dark=True)
    assert light and dark
    assert light != dark


def test_pygments_css_scope_matches_render_class():
    # ★ 경계면: render() 가 부여하는 클래스와 pygments_css 의 스코프가 일치해야
    #   코드블록 색이 실제로 적용된다.
    r = render("```python\nprint(1)\n```", base_dir=Path("."))
    css = pygments_css(dark=False)
    assert "codehilite" in r.html
    assert ".codehilite" in css


# --------------------------------------------------------------------------- #
# read_markdown — 인코딩/견고성
# --------------------------------------------------------------------------- #
def test_read_markdown_utf8(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("한글 내용", encoding="utf-8")
    assert "한글" in read_markdown(p)


def test_read_markdown_utf8_bom_stripped(tmp_path):
    p = tmp_path / "bom.md"
    p.write_bytes("﻿# Title".encode("utf-8"))
    text = read_markdown(p)
    assert not text.startswith("﻿")
    assert text.startswith("# Title")


def test_read_markdown_empty_file(tmp_path):
    p = tmp_path / "empty.md"
    p.write_bytes(b"")
    assert read_markdown(p) == ""


def test_read_markdown_cp949(tmp_path):
    # 한국어 레거시 인코딩 — charset-normalizer 폴백 검증.
    # 주의: charset-normalizer 는 통계적 감지기라 매우 짧은 입력(수십 바이트)에서는
    # 인코딩을 잘못 추정할 수 있다. 실제 사용자가 여는 파일 크기에 가까운
    # 충분한 본문으로 검증한다(현실적 시나리오).
    p = tmp_path / "cp949.md"
    content = (
        "# 한글 문서 제목\n\n"
        "이 문서는 한국어 인코딩(cp949/euc-kr) 테스트를 위한 것입니다.\n"
        "마크다운 뷰어는 다양한 인코딩의 파일을 안전하게 열 수 있어야 합니다.\n\n"
        "## 두 번째 섹션\n\n"
        "충분히 긴 한글 본문이 있어야 인코딩을 제대로 감지합니다. "
        "가나다라마바사아자차카타파하 한국어 텍스트 반복.\n"
    )
    p.write_bytes(content.encode("cp949"))
    text = read_markdown(p)
    assert "한국어" in text or "한글" in text  # 복구된 한글


def test_read_markdown_short_legacy_no_crash(tmp_path):
    # 매우 짧은 레거시 인코딩 입력은 정확 복구가 보장되지 않으나(통계적 감지 한계),
    # 계약상 절대 예외를 던지지 않고 str 을 반환해야 한다.
    p = tmp_path / "short.md"
    p.write_bytes("안녕".encode("cp949"))
    text = read_markdown(p)
    assert isinstance(text, str)


def test_read_markdown_binary_no_crash(tmp_path):
    p = tmp_path / "bin.md"
    p.write_bytes(bytes(range(256)) * 4)
    text = read_markdown(p)  # 예외 없이 최선 결과 반환
    assert isinstance(text, str)
    # render 도 바이너리 디코딩 결과에 크래시하지 않아야 함.
    r = render(text, base_dir=tmp_path)
    assert isinstance(r, RenderResult)


def test_read_markdown_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_markdown(tmp_path / "nope.md")
