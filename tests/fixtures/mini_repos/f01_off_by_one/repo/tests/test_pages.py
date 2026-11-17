import pytest

from pager.pages import page_count, paginate


def test_page_count():
    assert page_count([1, 2, 3, 4, 5], 2) == 3


def test_page_past_end_is_empty():
    assert paginate([1, 2, 3], 5, 2) == []


def test_size_must_be_positive():
    with pytest.raises(ValueError):
        paginate([1], 0, 0)
