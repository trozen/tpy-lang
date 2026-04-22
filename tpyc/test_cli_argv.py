"""Unit tests for tpyc argv pre-split helper."""

import pytest

from .cli import _split_tpyc_argv


def test_no_separator_returns_empty_script_args():
    assert _split_tpyc_argv(["foo.py", "-o", "out/"]) == (["foo.py", "-o", "out/"], [])


def test_empty_argv():
    assert _split_tpyc_argv([]) == ([], [])


def test_separator_at_end_yields_empty_script_args():
    assert _split_tpyc_argv(["foo.py", "-x", "--"]) == (["foo.py", "-x"], [])


def test_separator_splits_compiler_and_program_args():
    before, after = _split_tpyc_argv(["foo.py", "-x", "--", "a", "b"])
    assert before == ["foo.py", "-x"]
    assert after == ["a", "b"]


def test_tpyc_flags_preserved_before_separator():
    before, after = _split_tpyc_argv(["foo.py", "-o", "out/", "--pcre2=none", "-x", "--", "arg"])
    assert before == ["foo.py", "-o", "out/", "--pcre2=none", "-x"]
    assert after == ["arg"]


def test_multiple_dash_dash_only_first_is_separator():
    before, after = _split_tpyc_argv(["foo.py", "-x", "--", "a", "--", "b"])
    assert before == ["foo.py", "-x"]
    assert after == ["a", "--", "b"]


def test_leading_separator_with_trailing_tokens_rejected():
    with pytest.raises(ValueError, match="input file must appear before"):
        _split_tpyc_argv(["--", "foo.py"])


def test_only_separator_passes_through():
    # `tpyc --` alone: no trailing tokens, so nothing to reject. argparse will
    # then fail with "required: input" which is the right error for this shape.
    assert _split_tpyc_argv(["--"]) == ([], [])


def test_flag_like_program_args_preserved():
    before, after = _split_tpyc_argv(["foo.py", "-x", "--", "-O", "--help", "-"])
    assert before == ["foo.py", "-x"]
    assert after == ["-O", "--help", "-"]
