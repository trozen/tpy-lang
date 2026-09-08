"""Unit tests for tpyc argv pre-split helper and the build-variant selection."""

from argparse import Namespace

import pytest

from .cli import _build_variant, _opt_flags, _split_tpyc_argv


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
    before, after = _split_tpyc_argv(["foo.py", "-x", "--", "--debug", "--help", "-"])
    assert before == ["foo.py", "-x"]
    assert after == ["--debug", "--help", "-"]


def test_default_build_is_optimized_without_ndebug():
    # The NDEBUG-gated runtime checks stay in the default build by design.
    args = Namespace(debug=False)
    assert _build_variant(args) == "release"
    assert _opt_flags(args) == ["-O3"]


def test_debug_build_is_unoptimized_with_debug_info():
    args = Namespace(debug=True)
    assert _build_variant(args) == "debug"
    assert _opt_flags(args) == ["-g", "-O0"]
