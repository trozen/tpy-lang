"""Pins for the F4 dynamic_cast isinstance arms: Ptr / Optional-ptr /
readonly subjects on the if-init form, the tuple and root-class no-alias
forms, the value-position chain -- and the negated / condition-position
boundaries that must keep falling back."""

from __future__ import annotations

from .testutil import (
    _assert_byte_identical,
    _fn,
    _lower_ctx,
    _lower_ctx_witnessed,
)

_POLY = (
    "from typing import Optional, Protocol\n"
    "from tpy import Int32, dynamic, readonly\n"
    "@dynamic\n"
    "class Tagged(Protocol):\n"
    "    def kind(self) -> str: ...\n"
    "class Base(Tagged):\n"
    "    def __init__(self) -> None:\n"
    "        pass\n"
    "    def kind(self) -> str:\n"
    "        return \"base\"\n"
    "    def tag(self) -> Int32:\n"
    "        return 0\n"
    "class A(Base):\n"
    "    def __init__(self) -> None:\n"
    "        pass\n"
    "    def tag(self) -> Int32:\n"
    "        return 1\n"
    "class B(Base):\n"
    "    def __init__(self) -> None:\n"
    "        pass\n"
    "    def tag(self) -> Int32:\n"
    "        return 2\n"
)


class TestPolyPtrSubjects:
    def test_optional_ptr_subject_if_init_routes(self):
        src = _POLY + (
            "def f(e: Optional[Base]) -> Int32:\n"
            "    if isinstance(e, A):\n"
            "        return e.tag()\n"
            "    return -1\n"
        )
        thir, _ = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        _assert_byte_identical(src)

    def test_readonly_optional_subject_const_cast(self):
        # readonly[Optional[Base]] -> `dynamic_cast<const A*>` const
        # pointees.
        src = _POLY + (
            "def f(e: readonly[Optional[Base]]) -> Int32:\n"
            "    if isinstance(e, A):\n"
            "        return 1\n"
            "    return -1\n"
        )
        assert _fn(_lower_ctx(src), "f") is not None
        _assert_byte_identical(src)

    def test_tuple_form_or_chain_routes(self):
        src = _POLY + (
            "def f(e: Optional[Base]) -> Int32:\n"
            "    if isinstance(e, (A, B)):\n"
            "        return 1\n"
            "    return 0\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces["narrow.poly_tuple"] >= 1
        _assert_byte_identical(src)

    def test_root_class_check_routes(self):
        # `isinstance(e, Base)` on Optional[Base]: the single no-init
        # null-check, no alias (identity fact extracts nothing).
        src = _POLY + (
            "def f(e: Optional[Base]) -> Int32:\n"
            "    if isinstance(e, Base):\n"
            "        return e.tag()\n"
            "    return -1\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces["narrow.poly_tuple"] >= 1
        _assert_byte_identical(src)

    def test_value_position_chain_routes(self):
        src = _POLY + (
            "def f(e: Optional[Base]) -> bool:\n"
            "    return isinstance(e, (A, B))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces["narrow.poly_value"] >= 1
        _assert_byte_identical(src)

    def test_negated_cond_stays_ast(self):
        # `not isinstance` carries the post-if persistent alias (the AST's
        # early-return narrowing) -- unmirrored; the body falls back.
        src = _POLY + (
            "def f(e: Optional[Base]) -> Int32:\n"
            "    if not isinstance(e, A):\n"
            "        return -1\n"
            "    return e.tag()\n"
        )
        assert _fn(_lower_ctx(src), "f") is None
        _assert_byte_identical(src)

    def test_compound_cond_stays_ast(self):
        # The value-position chain must not misfire inside a compound
        # truthy condition (the CONDITION/TRUTHY exclusion): the whole
        # body falls back.
        src = _POLY + (
            "def f(e: Optional[Base], flag: bool) -> Int32:\n"
            "    if isinstance(e, (A, B)) and flag:\n"
            "        return 1\n"
            "    return 0\n"
        )
        assert _fn(_lower_ctx(src), "f") is None
        _assert_byte_identical(src)

    def test_spelled_subject_member_call_dot(self):
        # In-branch reads through the spelled `(*__e_ptr)` alias use `.`
        # member access, never the pointer `->` (the double-deref /
        # arrow seam).
        src = _POLY + (
            "def f(e: Optional[Base]) -> Int32:\n"
            "    if isinstance(e, A):\n"
            "        return e.tag()\n"
            "    elif isinstance(e, B):\n"
            "        return e.tag()\n"
            "    return -1\n"
        )
        assert _fn(_lower_ctx(src), "f") is not None
        _assert_byte_identical(src)
