"""Tests for the util seam: the shared pure primitives (`now_ts` / `iso` / `_num`)."""
import util


def test_num_rounds_and_tolerates_garbage():
    assert util._num("12.345") == 12.35
    assert util._num(None) is None
    assert util._num("not-a-number") is None


def test_iso_formats_epoch_and_passes_through_falsy():
    assert util.iso(0) is None                      # falsy ts -> None (no epoch-0 timestamp)
    assert util.iso(None) is None
    assert util.iso(1_700_000_000).startswith("2023-11-14T")   # UTC ISO-8601


def test_now_ts_returns_monotonic_wallclock():
    a = util.now_ts()
    b = util.now_ts()
    assert isinstance(a, float) and b >= a
