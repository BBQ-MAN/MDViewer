"""클립보드 변환/저장 코어 함수 단위 테스트 (Phase 6, 설계 06 §2).

대상(모두 PySide6 무의존 코어):
    - mdviewer.renderer.html_to_markdown(html: str) -> str   (예외 비전파, 항상 str)
    - mdviewer.renderer.write_markdown(path, text) -> None    (OSError 전파, 개행 보존)
    - write_markdown ↔ read_markdown round-trip (UTF-8 한글/개행 보존)

설계 문서: _workspace/06_clipboard_feature_design.md
계약 위치: html_to_markdown/write_markdown 은 renderer.py 에 동거(__init__ export).
(프롬프트가 언급한 convert.py 는 존재하지 않으며, 설계 §2/§5 와 __init__ 모두
 renderer.py 를 계약 위치로 명시한다.)
"""

from __future__ import annotations

import os
import sys

import pytest

# 패키지 루트 export 와 모듈 직접 import 가 모두 같은 객체를 가리키는지도 확인한다.
from mdviewer import html_to_markdown as pkg_html_to_markdown
from mdviewer import write_markdown as pkg_write_markdown
from mdviewer.renderer import html_to_markdown, read_markdown, write_markdown


# --------------------------------------------------------------------------- #
# 0. export 경계: __init__ 과 renderer 가 같은 함수
# --------------------------------------------------------------------------- #
def test_export_identity():
    """`from mdviewer import ...` 와 `from mdviewer.renderer import ...` 동일 객체."""
    assert pkg_html_to_markdown is html_to_markdown
    assert pkg_write_markdown is write_markdown


# --------------------------------------------------------------------------- #
# 1. html_to_markdown — 기본 변환
# --------------------------------------------------------------------------- #
def test_html_heading():
    md = html_to_markdown("<h1>제목</h1>")
    assert isinstance(md, str)
    assert md.startswith("#")          # h1 → '# '
    assert "제목" in md


def test_html_bold():
    md = html_to_markdown("<p><b>굵게</b></p>")
    assert "**굵게**" in md


def test_html_link():
    md = html_to_markdown('<p><a href="https://x.com">링크</a></p>')
    assert "[링크](https://x.com)" in md


def test_html_list():
    md = html_to_markdown("<ul><li>하나</li><li>둘</li></ul>")
    # html2text 는 '  * ' 형식. 불릿 마커와 항목 텍스트 존재 확인.
    assert "하나" in md and "둘" in md
    assert "*" in md or "-" in md


def test_html_codeblock():
    md = html_to_markdown("<pre><code>print(1)</code></pre>")
    assert "print(1)" in md
    # 펜스 또는 들여쓰기 코드블록으로 보존되어야 한다.
    assert "```" in md or "    print(1)" in md


def test_html_combined_doc_and_fragment():
    """완전 문서·조각 모두 허용(설계 §2.1 Args). 제목/볼드/링크 동시 보존."""
    html = "<h1>제목</h1><p><b>굵게</b> <a href='x'>링크</a></p>"
    md = html_to_markdown(html)
    assert "# 제목" in md
    assert "**굵게**" in md
    assert "[링크](x)" in md


def test_html_image_preserved():
    md = html_to_markdown('<p><img src="a.png" alt="그림"></p>')
    assert "![그림](a.png)" in md


# --------------------------------------------------------------------------- #
# 2. html_to_markdown — 엣지/견고성 (예외 비전파, 항상 str, 빈입력→"")
# --------------------------------------------------------------------------- #
def test_html_empty_string():
    assert html_to_markdown("") == ""


def test_html_none():
    # 설계 §2.1: None 허용 → "" 반환, 절대 예외 없음.
    assert html_to_markdown(None) == ""  # type: ignore[arg-type]


def test_html_whitespace_only():
    assert html_to_markdown("   \n\t  ") == ""


def test_html_broken_unclosed_tags():
    """깨진/미닫힘 태그에도 예외 없이 str 반환."""
    md = html_to_markdown("<h1>제목<b>굵게<p>문단</h1></b")
    assert isinstance(md, str)
    assert "제목" in md or md == "" or "굵게" in md


def test_html_office_wrapper_noise():
    """워드/브라우저 wrapper(StartFragment/Office NS)에도 크래시 없음(설계 §3.4)."""
    html = (
        "<html><body><!--StartFragment-->"
        "<p class='MsoNormal'><b>본문</b></p>"
        "<!--EndFragment--></body></html>"
    )
    md = html_to_markdown(html)
    assert isinstance(md, str)
    assert "본문" in md


def test_html_always_str_never_none():
    for sample in ["", None, "<p>x</p>", "<<>>", "한글<br>줄"]:
        out = html_to_markdown(sample)  # type: ignore[arg-type]
        assert isinstance(out, str), f"{sample!r} → {out!r}"


def test_html_no_trailing_blank_lines():
    """결과 끝 잉여 개행은 가볍게 트림(설계 §2.1)."""
    md = html_to_markdown("<p>끝줄</p>")
    assert md == md.strip("\n")
    assert not md.endswith("\n")


# --------------------------------------------------------------------------- #
# 3. write_markdown — 부모 디렉터리 자동 생성 / 개행·인코딩 보존
# --------------------------------------------------------------------------- #
def test_write_creates_parent_dirs(tmp_path):
    target = tmp_path / "a" / "b" / "c.md"
    assert not target.parent.exists()
    write_markdown(target, "# hi")
    assert target.exists()
    assert target.read_text(encoding="utf-8") == "# hi"


def test_write_returns_none(tmp_path):
    assert write_markdown(tmp_path / "x.md", "x") is None


def test_write_utf8_no_bom(tmp_path):
    p = tmp_path / "ko.md"
    write_markdown(p, "한글 제목\n본문")
    raw = p.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")   # BOM 없음
    assert raw.decode("utf-8") == "한글 제목\n본문"


def test_write_preserves_crlf_bytes(tmp_path):
    """newline='' 로 universal-newline 변환을 끄고 CRLF 를 바이트 그대로 보존."""
    p = tmp_path / "crlf.md"
    write_markdown(p, "x\r\ny")
    assert p.read_bytes() == b"x\r\ny"


def test_write_none_text_becomes_empty(tmp_path):
    p = tmp_path / "none.md"
    write_markdown(p, None)  # type: ignore[arg-type]
    assert p.read_bytes() == b""


def test_write_overwrites_existing(tmp_path):
    p = tmp_path / "ow.md"
    write_markdown(p, "first content longer")
    write_markdown(p, "2nd")
    assert p.read_text(encoding="utf-8") == "2nd"   # truncate 확인


# --------------------------------------------------------------------------- #
# 4. round-trip: write_markdown → read_markdown (한글/개행 보존)
# --------------------------------------------------------------------------- #
def test_roundtrip_korean_lf(tmp_path):
    src = "# 한글 제목\n\n본문 첫째 줄\n둘째 줄\n"
    p = tmp_path / "rt.md"
    write_markdown(p, src)
    assert read_markdown(p) == src


def test_roundtrip_crlf_preserved(tmp_path):
    """CRLF 가 write→read 왕복에서 보존(read_markdown 은 universal-newline 변환 안 함)."""
    src = "line1\r\nline2\r\n한글\r\n"
    p = tmp_path / "rt_crlf.md"
    write_markdown(p, src)
    assert read_markdown(p) == src


def test_roundtrip_html_to_md_to_file(tmp_path):
    """클립보드 흐름 통합: HTML → md → 파일 저장 → 재독 동일."""
    md = html_to_markdown("<h1>리포트</h1><p><b>중요</b></p>")
    p = tmp_path / "report.md"
    write_markdown(p, md)
    assert read_markdown(p) == md
    assert "# 리포트" in read_markdown(p)


# --------------------------------------------------------------------------- #
# 5. write_markdown — I/O 오류 전파(설계 §2.2: OSError 비삼킴)
# --------------------------------------------------------------------------- #
def test_write_oserror_propagates_dir_as_file(tmp_path):
    """대상 경로가 디렉터리이면 쓰기 시 OSError 가 그대로 전파되어야 한다."""
    d = tmp_path / "iam_a_dir"
    d.mkdir()
    with pytest.raises(OSError):
        write_markdown(d, "x")


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX 읽기전용 디렉터리 권한 모델(Windows 와 다름)",
)
def test_write_oserror_propagates_readonly_dir(tmp_path):
    ro = tmp_path / "ro"
    ro.mkdir()
    os.chmod(ro, 0o500)  # r-x: 쓰기 불가
    try:
        with pytest.raises(OSError):
            write_markdown(ro / "x.md", "x")
    finally:
        os.chmod(ro, 0o700)
