"""Tests for codegen C++ context utilities."""

import pytest

from .context import expand_cpp_template, CodeGenError, resumable_struct_name


@pytest.mark.parametrize("prefix", ["__coro_", "__gen_"])
@pytest.mark.parametrize(("name", "owner", "suffix"), [
    ("compute", None, "compute"),
    ("delete", None, "delete_"),
    ("delete_", None, "delete_"),
    ("compute", "Worker", "Worker_compute"),
    ("delete", "Worker", "Worker_delete_"),
    ("delete_", "Worker", "Worker_delete_"),
    ("compute", "delete", "delete__compute"),
    ("compute", "delete_", "delete__compute"),
])
def test_resumable_flat_names_preserve_existing_spelling(
        prefix: str, name: str, owner: str | None, suffix: str) -> None:
    assert resumable_struct_name(name, owner, prefix) == prefix + suffix


@pytest.mark.parametrize("prefix", ["__coro_", "__gen_"])
def test_resumable_nested_names_are_unambiguous(prefix: str) -> None:
    subjects = [
        ("compute", "Outer.Inner"),
        ("compute", "Outer_Inner.More"),
        ("compute", "Outer.Inner_More"),
        ("compute", "Outer.Inner.More"),
        ("Inner_compute", "Outer.More"),
        ("compute", "Outer.More_Inner"),
        ("delete", "Outer.Inner"),
        ("delete_", "Outer.Inner"),
        ("compute", "delete.Inner"),
        ("compute", "delete_.Inner"),
    ]
    names = {resumable_struct_name(name, owner, prefix) for name, owner in subjects}
    assert len(names) == len(subjects)
    assert all(name.isascii() and name.isidentifier() for name in names)
    assert prefix + "2_5_Outer_5_Inner_7_compute" in names
    # These legacy free/method identities already alias; nested names must alias neither.
    assert resumable_struct_name("Outer_Inner_compute", prefix=prefix) not in names
    assert resumable_struct_name("compute", "Outer_Inner", prefix) not in names


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

    def test_escaped_braces_in_template(self):
        """`{{`/`}}` collapse to literal braces, so a template can spell C++
        aggregate-init / lambda / scope braces."""
        result = expand_cpp_template("MyType{{}}", None)
        assert result == "MyType{}"
        result = expand_cpp_template("::tpy::BasicSlice{{{0}, {1}}}", None, "a", "b")
        assert result == "::tpy::BasicSlice{a, b}"
        result = expand_cpp_template("[]() {{ return {0}; }}()", None, "x")
        assert result == "[]() { return x; }()"

    def test_free_function_no_receiver(self):
        """A free-function template (self_val=None) substitutes positionals."""
        assert expand_cpp_template("std::max<int>({0}, {1})", None, "a", "b") \
            == "std::max<int>(a, b)"

    def test_self_in_free_function_raises(self):
        with pytest.raises(CodeGenError, match=r"\{self\}"):
            expand_cpp_template("{self}.size()", None)

    def test_lone_open_brace_raises(self):
        with pytest.raises(CodeGenError, match=r"Invalid placeholder"):
            expand_cpp_template("::Foo{static_cast<int>({0})}", None, "x")

    def test_unmatched_open_brace_raises(self):
        with pytest.raises(CodeGenError, match=r"Unmatched '\{'"):
            expand_cpp_template("::Foo{ no close", None, "x")

    def test_unmatched_close_brace_raises(self):
        with pytest.raises(CodeGenError, match=r"Unmatched '\}'"):
            expand_cpp_template("foo({0}) }", None, "x")
