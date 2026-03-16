"""Tests for codegen C++ context utilities."""

import pytest

from .context import expand_cpp_template, CodeGenError


class TestExpandCppTemplate:
    def test_self_only(self):
        assert expand_cpp_template("{self}.size()", "v") == "v.size()"

    def test_self_and_positional(self):
        result = expand_cpp_template("{self}.push_back({0})", "vec", "42")
        assert result == "vec.push_back(42)"

    def test_multiple_positional(self):
        result = expand_cpp_template("{self}[{0}] = {1}", "arr", "i", "val")
        assert result == "arr[i] = val"

    def test_no_placeholders(self):
        assert expand_cpp_template("std::sort()", "x") == "std::sort()"

    def test_repeated_placeholder(self):
        result = expand_cpp_template("{self} + {self}", "a")
        assert result == "a + a"

    def test_unreplaced_self_raises(self):
        with pytest.raises(CodeGenError, match=r"\{self\}"):
            expand_cpp_template("{self}.set({0})", "obj")

    def test_unreplaced_positional_raises(self):
        with pytest.raises(CodeGenError, match=r"\{1\}"):
            expand_cpp_template("{self}.f({0}, {1})", "obj", "a")

    def test_extra_args_ok(self):
        result = expand_cpp_template("{self}.f({0})", "obj", "a", "unused")
        assert result == "obj.f(a)"

    def test_cpp_braces_in_substituted_value(self):
        """C++ initializer braces in substituted values must not trigger false positives."""
        result = expand_cpp_template(
            "::tpy::list_concat({self}, {0})",
            "v",
            "std::vector<int>{30}",
        )
        assert result == "::tpy::list_concat(v, std::vector<int>{30})"
