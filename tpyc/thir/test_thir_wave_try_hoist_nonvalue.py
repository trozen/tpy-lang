"""A NON-VALUE branch-first decl hoisted out of a `try`: the same
OPTIONAL_STORAGE predecl (`std::optional<T> name;`, plain engaging assigns,
deref reads) the if cascade and the with family already carry."""

from __future__ import annotations

from .testutil import (
    _lower_ctx, _lower_ctx_witnessed, _fn, _assert_byte_identical,
)

_PT = ("class Point:\n"
       "    x: int\n"
       "    def __init__(self, x: int) -> None:\n"
       "        self.x = x\n")


class TestTryHoistNonValue:
    def test_container_hoist_routes(self):
        src = ("def use() -> None:\n"
               "    try:\n"
               "        items = [1, 2, 3]\n"
               "    except Exception:\n"
               "        return\n"
               "    print(items)\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("with.hoist_optional_storage", 0) >= 1
        _assert_byte_identical(src)

    def test_record_hoist_routes(self):
        src = (_PT
               + "def use() -> None:\n"
               + "    try:\n"
               + "        p = Point(10)\n"
               + "    except Exception:\n"
               + "        return\n"
               + "    print(p.x)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)

    def test_finally_tier_hoist_routes(self):
        # The finally-only tier reaches the same predecl loop.
        src = ("from tpy import Own\n"
               "def get() -> Own[list[int]]:\n"
               "    return [1, 2]\n"
               "def use() -> None:\n"
               "    try:\n"
               "        items = get()\n"
               "    finally:\n"
               "        print(1)\n"
               "    print(len(items))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)


class TestTryHoistNonValueBoundaries:
    def test_reassigned_hoist_stays_ast(self):
        # A reassigned non-value takes the AST's POINTER-local flavor
        # (`T* name;` plus rebind slots), which this arm does not carry --
        # the optional predecl would own where the AST aliases.
        src = ("def use(f: bool) -> None:\n"
               "    try:\n"
               "        items = [1, 2]\n"
               "    except Exception:\n"
               "        return\n"
               "    items = [3, 4]\n"
               "    print(items)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None

    def test_hoist_inside_a_loop_stays_ast(self):
        # THIR scopes `declared` per block while the AST's declared_vars is
        # body-global, so a hoist under a loop could diverge on a later
        # same-named decl outside it.
        src = ("from tpy import Own\n"
               "def get() -> Own[list[int]]:\n"
               "    return [1]\n"
               "def use() -> None:\n"
               "    i = 0\n"
               "    while i < 2:\n"
               "        try:\n"
               "            items = get()\n"
               "        except Exception:\n"
               "            break\n"
               "        print(len(items))\n"
               "        i += 1\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None

    def test_resumable_body_hoist_stays_ast(self):
        # A generator frame has no function-top drain for the hoist line.
        src = ("from typing import Iterator\n"
               "from tpy import Own\n"
               "def get() -> Own[list[int]]:\n"
               "    return [1]\n"
               "def gen() -> Iterator[int]:\n"
               "    try:\n"
               "        items = get()\n"
               "    except Exception:\n"
               "        return\n"
               "    yield len(items)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "gen") is None
