"""The board pack renders for any close length, and never trusts the dataset name as HTML."""

import pytest

from app.board_pack import _period_title, build_board_pack_html


@pytest.mark.parametrize("period,title", [("2025-12", "December 2025"), ("2025-Q4", "Q4 2025"), ("2025", "2025")])
def test_board_pack_renders_for_months_quarters_and_years(period, title):
    # /board-pack?period=2025 used to crash: the title assumed YYYY-MM.
    assert _period_title(period) == title
    html = build_board_pack_html([], period, {"dataset_name": "FY actuals"})
    assert f"<h1>{title}<br>Board Pack</h1>" in html


def test_dataset_name_is_escaped():
    html = build_board_pack_html([], "2025-12", {"dataset_name": "<img src=x onerror=alert(1)>"})
    assert "<img src=x" not in html and "&lt;img src=x" in html


def test_dataset_name_is_escaped_once():
    html = build_board_pack_html([], "2025-12", {"dataset_name": "R&D actuals"})
    assert "R&amp;D" in html and "&amp;amp;" not in html
