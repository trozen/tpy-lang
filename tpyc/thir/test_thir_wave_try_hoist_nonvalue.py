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
    def test_reassigned_hoist_routes_pointer_flavor(self):
        # A reassigned non-value rides the POINTER-local flavor (`T*
        # name;`); rvalue reseats fill the lazily-allocated function-top
        # `__slot_N` (BRANCH_RVALUE) -- the flavor the fence once said was
        # missing landed with the try-site classifier threading.
        src = ("def use(f: bool) -> None:\n"
               "    try:\n"
               "        items = [1, 2]\n"
               "    except Exception:\n"
               "        return\n"
               "    items = [3, 4]\n"
               "    print(items)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)

    def test_hoist_inside_a_loop_routes(self):
        # `_gen_try` emits the predecl at the try, so the loop body IS the
        # C++ scope on both paths. The per-block-vs-body-global `declared`
        # question is pinned directly in test_thir_try.py.
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
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)

    def test_nonvalue_hoist_with_a_later_same_name_decl_stays_ast(self):
        # The non-value flavor rejects at the OPTIONAL_STORAGE flavor gate,
        # which is what holds this shape -- not the enclosing block's scope.
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
               "        i += 1\n"
               "    items = get()\n"
               "    print(len(items))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None
        _assert_byte_identical(src)

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


class TestIfHoistConstPointer:
    """The if-cascade's CONST borrow-decl hoist rung (`const Reg* v;` --
    sema's stmt-borrow const bit; the with/try const_pointer flavor
    mirrored into `_lower_if_hoist_predecls`)."""

    _SRC = (
        "from tpy import Int32, readonly\n"
        "class Reg:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32) -> None:\n"
        "        self.n = n\n"
        "    @readonly\n"
        "    def view(self) -> Reg:\n"
        "        return self\n"
        "def probe(flag: bool) -> Int32:\n"
        "    r = Reg(1)\n"
        "    if flag:\n"
        "        v = r.view()\n"
        "    else:\n"
        "        v = r.view()\n"
        "    return v.n\n"
        "def main() -> None:\n"
        "    print(probe(True))\n"
        "main()\n")

    def test_const_ptr_hoist_routes(self):
        from .testutil import (_assert_routes_byte_identical,
                               _lower_ctx_witnessed)
        _hpp, cpp = _assert_routes_byte_identical(self._SRC)
        _thir, wit = _lower_ctx_witnessed(self._SRC)
        assert wit.get("if.hoist_const_ptr", 0) >= 1
        assert "const Reg* v;" in cpp

    def test_optional_readonly_inner_still_defers(self):
        # The Optional[readonly[T]]-inner hoist is the const-INDIRECT
        # Optional rung, still outside the slice.
        from .testutil import _fn, _lower_ctx
        src = (
            "from tpy import Int32, readonly\n"
            "from typing import Optional\n"
            "class Reg:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "def find(r: readonly[Reg], want: bool) -> Optional[readonly[Reg]]:\n"
            "    if want:\n"
            "        return r\n"
            "    return None\n"
            "def pick(r: readonly[Reg], flag: bool) -> Int32:\n"
            "    if flag:\n"
            "        v = find(r, True)\n"
            "    else:\n"
            "        v = find(r, False)\n"
            "    if v is not None:\n"
            "        return v.n\n"
            "    return 0\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "pick") is None
