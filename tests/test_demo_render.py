# -*- coding: utf-8 -*-
"""samples/demo.md 종합 렌더 정확성 테스트.

demo.md 가 헤딩 계층/코드/표/각주/작업목록/상대이미지/내부앵커를 모두 담고
있으므로, 실제 렌더 결과 문자열에서 각 기능 산출물을 확인한다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mdviewer.renderer import render

_DEMO = Path(__file__).resolve().parent.parent / "samples" / "demo.md"


@pytest.fixture(scope="module")
def demo_result():
    text = _DEMO.read_text(encoding="utf-8")
    return render(text, base_dir=_DEMO.parent)


def test_demo_exists():
    assert _DEMO.exists(), "samples/demo.md 가 있어야 한다"


def test_demo_title(demo_result):
    assert demo_result.title == "MDViewer 데모 문서"


def test_demo_toc_count(demo_result):
    assert len(demo_result.toc) == 18


def test_demo_internal_link_targets_have_matching_ids(demo_result):
    ids = set(re.findall(r'<h[1-6][^>]*id="([^"]+)"', demo_result.html))
    # demo.md 내부 링크 타깃 4종이 본문 헤딩 id 로 존재해야 한다(계약 §3.1).
    for target in ("mdviewer-데모-문서", "코드-하이라이트", "테이블", "각주"):
        assert target in ids, f"내부 링크 타깃 {target!r} 에 해당하는 헤딩 id 없음"


def test_demo_anchor_id_full_match(demo_result):
    ids = re.findall(r'<h[1-6][^>]*id="([^"]+)"', demo_result.html)
    anchors = [t.anchor for t in demo_result.toc]
    assert anchors == ids


def test_demo_code_highlight(demo_result):
    assert "codehilite" in demo_result.html
    # python keyword span 존재.
    assert 'class="k"' in demo_result.html or 'class="kn"' in demo_result.html


def test_demo_table(demo_result):
    assert "<table>" in demo_result.html


def test_demo_footnotes(demo_result):
    html = demo_result.html.lower()
    assert "footnote" in html


def test_demo_tasklist(demo_result):
    assert 'type="checkbox"' in demo_result.html


def test_demo_relative_image_file_uri(demo_result):
    assert "logo.png" in demo_result.html
    assert "file:///" in demo_result.html


def test_demo_blockquote_and_hr(demo_result):
    assert "<blockquote>" in demo_result.html
    assert "<hr" in demo_result.html
