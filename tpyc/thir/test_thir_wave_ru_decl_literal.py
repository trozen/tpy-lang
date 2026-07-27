"""A list/dict literal at a recursive-union WRAPPER decl slot, in both the
non-generic (`JsonValue`) and generic alias-instance (`Tree[int]`) forms."""

from __future__ import annotations

from .testutil import (
    _lower_ctx, _lower_ctx_witnessed, _fn, _assert_byte_identical,
)

_TREE = (
    "type Tree[T] = T | list[Tree[T]] | None\n"
)


class TestRecursiveUnionDeclLiteral:
    def test_generic_instance_list_routes(self):
        # The OUTER literal types AS the wrapper (`Tree[int]`), the NESTED one
        # as the container of wrappers (`list[Tree[int]]`) -- both render the
        # spelled `std::vector<Tree<..>>{...}`.
        src = (_TREE
               + "def use() -> None:\n"
               + "    t: Tree[int] = [1, [2, 3], 4]\n"
               + "    pass\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("decl.ru_instance_literal", 0) >= 1
        _assert_byte_identical(src)

    def test_generic_instance_nested_deeper(self):
        src = (_TREE
               + "def use() -> None:\n"
               + "    t: Tree[str] = [\"a\", [\"b\", [\"c\", \"d\"]], \"e\"]\n"
               + "    pass\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)



class TestRecursiveUnionDeclBoundaries:
    def test_none_element_in_a_generic_instance_stays_ast(self):
        # The wrapper's monostate render keys on the element's result type
        # being the UnionType; an alias INSTANCE renders `nullptr` instead of
        # `std::monostate{}`. Probe-verified divergence, so the generic arm
        # excludes `None` (the NON-generic arm still admits it).
        src = (_TREE
               + "def use() -> None:\n"
               + "    t: Tree[int] = [1, None, 3]\n"
               + "    pass\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None

    def test_name_element_stays_ast(self):
        # Only literal elements render target-less on both paths; a NAME
        # element would take the wrapper's converting-ctor render.
        src = (_TREE
               + "def use(n: int) -> None:\n"
               + "    t: Tree[int] = [1, n, 3]\n"
               + "    pass\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None

    def test_rebound_local_stays_ast(self):
        # The arm carries the same block-local guards as the ordinary
        # container-literal decl path: a REBOUND name is a pointer-local on
        # the AST path and would silently copy here.
        src = (_TREE
               + "def use() -> None:\n"
               + "    t: Tree[int] = [1, 2]\n"
               + "    t = [3, 4]\n"
               + "    pass\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None
