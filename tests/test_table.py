"""표(table) 삽입 + 행/열 편집 순수 로직 단위 테스트 (Phase 10).

main_window.py 모듈 수준 순수 함수(GUI 비의존)를 검증한다:
  - _split_table_cells / _is_separator_row : 셀 분해 / 구분행 식별
  - parse_table_lines                      : 표 라인 → TableBlock (구분행 없으면 None)
  - render_table_block                     : TableBlock → GFM 텍스트 (round-trip 안정)
  - apply_table_op                         : 행/열 추가·삭제 + 거부 규칙
  - build_gfm_table_skeleton               : 삽입 골격(양끝 파이프·구분행·열수)
  - cursor_col_index                       : 커서 위치 → 열 인덱스 경계

GUI(QTextCursor/JS)는 offscreen 스모크에서 별도 검증한다. 여기선 순수 로직만.
"""

from __future__ import annotations

import pytest

from mdviewer.main_window import (
    TableBlock,
    apply_table_op,
    build_gfm_table_skeleton,
    cursor_col_index,
    parse_table_lines,
    render_table_block,
    _is_separator_row,
    _split_table_cells,
)


# --------------------------------------------------------------------- #
# _split_table_cells
# --------------------------------------------------------------------- #
def test_split_cells_basic():
    assert _split_table_cells("| a | b | c |") == ["a", "b", "c"]


def test_split_cells_no_outer_pipes():
    # 양끝 파이프 생략(느슨한 식별) — 여전히 분해.
    assert _split_table_cells("a | b | c") == ["a", "b", "c"]


def test_split_cells_strips_whitespace():
    assert _split_table_cells("|  x  |   y |") == ["x", "y"]


def test_split_cells_empty_line():
    assert _split_table_cells("") == []
    assert _split_table_cells("   ") == []


def test_split_cells_empty_body_cells():
    assert _split_table_cells("|   |   |") == ["", ""]


# --------------------------------------------------------------------- #
# _is_separator_row
# --------------------------------------------------------------------- #
def test_is_separator_plain():
    assert _is_separator_row(["---", "---"]) is True


def test_is_separator_alignment_colons():
    assert _is_separator_row([":--", "--:", ":-:"]) is True


def test_is_separator_single_dash():
    assert _is_separator_row(["-"]) is True


def test_is_separator_rejects_text():
    assert _is_separator_row(["a", "---"]) is False


def test_is_separator_rejects_empty():
    assert _is_separator_row([]) is False
    assert _is_separator_row([""]) is False


# --------------------------------------------------------------------- #
# parse_table_lines
# --------------------------------------------------------------------- #
def test_parse_valid_table():
    lines = [
        "| 열1 | 열2 |",
        "| --- | --- |",
        "| a   | b   |",
        "| c   | d   |",
    ]
    tb = parse_table_lines(0, 3, lines)
    assert tb is not None
    assert tb.ncols == 2
    assert tb.header == ["열1", "열2"]
    assert tb.body == [["a", "b"], ["c", "d"]]
    assert tb.top == 0 and tb.bottom == 3


def test_parse_no_separator_returns_none():
    # 구분행 없음 → 유효 GFM 표 아님 → None.
    lines = ["| a | b |", "| c | d |"]
    assert parse_table_lines(0, 1, lines) is None


def test_parse_single_line_returns_none():
    assert parse_table_lines(0, 0, ["| a | b |"]) is None


def test_parse_empty_returns_none():
    assert parse_table_lines(0, 0, []) is None


def test_parse_separator_not_second_line_returns_none():
    # 구분행이 헤더 바로 다음(index 1)이 아니면 None.
    lines = ["| h1 | h2 |", "| a | b |", "| --- | --- |"]
    assert parse_table_lines(0, 2, lines) is None


def test_parse_rectangularizes_body():
    # 본문 셀 개수가 헤더와 다르면 ncols 로 사각형화(부족=빈셀, 초과=절단).
    lines = [
        "| h1 | h2 | h3 |",
        "| --- | --- | --- |",
        "| a |",            # 부족 → 빈셀 채움
        "| x | y | z | w |",  # 초과 → 절단
    ]
    tb = parse_table_lines(0, 3, lines)
    assert tb.ncols == 3
    assert tb.body[0] == ["a", "", ""]
    assert tb.body[1] == ["x", "y", "z"]


def test_parse_preserves_alignment():
    lines = ["| h1 | h2 | h3 |", "| :-- | :-: | --: |", "| a | b | c |"]
    tb = parse_table_lines(0, 2, lines)
    assert tb.aligns == [":--", ":-:", "--:"]


# --------------------------------------------------------------------- #
# build_gfm_table_skeleton
# --------------------------------------------------------------------- #
def test_skeleton_shape():
    sk = build_gfm_table_skeleton(2, 3)
    lines = sk.split("\n")
    # 헤더 + 구분 + 본문2 = 4줄.
    assert len(lines) == 4
    # 모든 줄 양끝 파이프.
    for ln in lines:
        assert ln.startswith("|") and ln.endswith("|")
    # 구분행은 두 번째 줄, --- 포함.
    assert "---" in lines[1]
    # 열 수 = 3 (각 줄 셀 분해).
    assert len(_split_table_cells(lines[0])) == 3
    assert _split_table_cells(lines[0]) == ["열1", "열2", "열3"]


def test_skeleton_separator_is_separator_row():
    sk = build_gfm_table_skeleton(1, 4)
    lines = sk.split("\n")
    assert _is_separator_row(_split_table_cells(lines[1]))


def test_skeleton_min_clamp():
    # rows/cols 최소 1 강제(빈 표 방지).
    sk = build_gfm_table_skeleton(0, 0)
    lines = sk.split("\n")
    assert len(lines) == 3  # 헤더 + 구분 + 본문1
    assert len(_split_table_cells(lines[0])) == 1


def test_skeleton_parses_back():
    # 골격은 곧바로 유효 표로 파싱돼야 한다.
    sk = build_gfm_table_skeleton(2, 3)
    lines = sk.split("\n")
    tb = parse_table_lines(0, len(lines) - 1, lines)
    assert tb is not None
    assert tb.ncols == 3
    assert len(tb.body) == 2


# --------------------------------------------------------------------- #
# apply_table_op — 행 추가/삭제
# --------------------------------------------------------------------- #
def _tb_2x2():
    return TableBlock(
        top=0, bottom=3,
        header=["h1", "h2"], aligns=["---", "---"],
        body=[["a", "b"], ["c", "d"]], ncols=2,
    )


def test_row_add_increases_body():
    tb = _tb_2x2()
    # 커서가 본문 첫 행(line_idx=2 → body_idx 0)일 때 아래 삽입.
    assert apply_table_op(tb, "row_add", line_idx=2, col_idx=0) is True
    assert len(tb.body) == 3
    assert tb.body[1] == ["", ""]  # 첫 행 아래 빈 행.


def test_row_add_on_header_inserts_at_top():
    tb = _tb_2x2()
    # 커서가 헤더(line_idx=0)면 본문 맨 위 삽입.
    assert apply_table_op(tb, "row_add", line_idx=0, col_idx=0) is True
    assert tb.body[0] == ["", ""]
    assert len(tb.body) == 3


def test_row_del_decreases_body():
    tb = _tb_2x2()
    assert apply_table_op(tb, "row_del", line_idx=2, col_idx=0) is True
    assert len(tb.body) == 1


def test_row_del_single_body_rejected():
    tb = TableBlock(
        top=0, bottom=2, header=["h1", "h2"], aligns=["---", "---"],
        body=[["a", "b"]], ncols=2,
    )
    # 본문 1행뿐 → 거부(빈 표 방지).
    assert apply_table_op(tb, "row_del", line_idx=2, col_idx=0) is False
    assert len(tb.body) == 1


def test_row_del_on_header_rejected():
    tb = _tb_2x2()
    # 헤더/구분행 커서에서 행 삭제 거부.
    assert apply_table_op(tb, "row_del", line_idx=0, col_idx=0) is False
    assert apply_table_op(tb, "row_del", line_idx=1, col_idx=0) is False
    assert len(tb.body) == 2


# --------------------------------------------------------------------- #
# apply_table_op — 열 추가/삭제
# --------------------------------------------------------------------- #
def test_col_add_increases_ncols():
    tb = _tb_2x2()
    assert apply_table_op(tb, "col_add", line_idx=2, col_idx=0) is True
    assert tb.ncols == 3
    assert len(tb.header) == 3
    assert len(tb.aligns) == 3
    for r in tb.body:
        assert len(r) == 3


def test_col_add_inserts_right_of_cursor():
    tb = _tb_2x2()
    # 커서 col 0 → 오른쪽(인덱스 1)에 삽입.
    apply_table_op(tb, "col_add", line_idx=2, col_idx=0)
    assert tb.body[0] == ["a", "", "b"]


def test_col_del_decreases_ncols():
    tb = _tb_2x2()
    assert apply_table_op(tb, "col_del", line_idx=2, col_idx=0) is True
    assert tb.ncols == 1
    assert len(tb.header) == 1
    for r in tb.body:
        assert len(r) == 1


def test_col_del_single_col_rejected():
    tb = TableBlock(
        top=0, bottom=2, header=["h1"], aligns=["---"],
        body=[["a"], ["b"]], ncols=1,
    )
    assert apply_table_op(tb, "col_del", line_idx=2, col_idx=0) is False
    assert tb.ncols == 1


def test_unknown_op_returns_false():
    tb = _tb_2x2()
    assert apply_table_op(tb, "bogus_op", line_idx=2, col_idx=0) is False


# --------------------------------------------------------------------- #
# render_table_block — round-trip / 정렬 보존
# --------------------------------------------------------------------- #
def test_render_outputs_valid_gfm():
    tb = _tb_2x2()
    text = render_table_block(tb)
    lines = text.split("\n")
    for ln in lines:
        assert ln.startswith("|") and ln.endswith("|")
    assert _is_separator_row(_split_table_cells(lines[1]))


def test_render_preserves_alignment_colons():
    tb = TableBlock(
        top=0, bottom=2, header=["h1", "h2", "h3"],
        aligns=[":--", ":-:", "--:"],
        body=[["a", "b", "c"]], ncols=3,
    )
    sep = render_table_block(tb).split("\n")[1]
    cells = _split_table_cells(sep)
    assert cells[0].startswith(":") and not cells[0].endswith(":")   # left
    assert cells[1].startswith(":") and cells[1].endswith(":")       # center
    assert cells[2].endswith(":") and not cells[2].startswith(":")   # right


def test_round_trip_idempotent():
    # parse → render → parse 가 안정(내용 보존).
    lines = [
        "| 이름 | 값 |",
        "| --- | --- |",
        "| 사과 | 100 |",
        "| 배   | 200 |",
    ]
    tb1 = parse_table_lines(0, 3, lines)
    rendered = render_table_block(tb1)
    out_lines = rendered.split("\n")
    tb2 = parse_table_lines(0, len(out_lines) - 1, out_lines)
    assert tb2 is not None
    assert tb2.header == tb1.header
    assert tb2.body == tb1.body
    assert tb2.ncols == tb1.ncols
    # 두 번째 렌더가 첫 번째와 동일(완전 idempotent).
    assert render_table_block(tb2) == rendered


def test_round_trip_after_col_add():
    lines = ["| a | b |", "| --- | --- |", "| 1 | 2 |", "| 3 | 4 |"]
    tb = parse_table_lines(0, 3, lines)
    apply_table_op(tb, "col_add", line_idx=2, col_idx=1)  # 마지막 열 오른쪽
    rendered = render_table_block(tb)
    re_lines = rendered.split("\n")
    tb2 = parse_table_lines(0, len(re_lines) - 1, re_lines)
    assert tb2 is not None
    assert tb2.ncols == 3
    assert len(tb2.body) == 2


# --------------------------------------------------------------------- #
# cursor_col_index — 경계
# --------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "pos,expected",
    [
        (2, 0),    # "| " 직후 → 셀 0
        (8, 1),    # 두 번째 셀 영역 → 셀 1
        (14, 2),   # 세 번째 셀 영역 → 셀 2
    ],
)
def test_cursor_col_index_segments(pos, expected):
    line = "| a | b | c |"
    #       0123456789...
    assert cursor_col_index(line, pos, 3) == expected


def test_cursor_col_index_no_pipes():
    assert cursor_col_index("plain text", 5, 3) == 0


def test_cursor_col_index_clamps_to_ncols():
    line = "| a | b | c |"
    # 끝 너머 위치 → ncols-1 로 클램프.
    assert cursor_col_index(line, len(line) + 10, 3) == 2


def test_cursor_col_index_start_clamps_zero():
    line = "| a | b |"
    assert cursor_col_index(line, 0, 2) == 0
