"""Call-lane gate-widening rows: the `Optional[Own[T]]` slot auto-move
at ctor/method args, the sema-filled `None` default at a value-repr
Optional marker slot, the instantiation spelling over an inherited
param-ful `__init__`, and the borrow-returning call passed bare at a
ptr-repr Optional slot."""

from __future__ import annotations

from .testutil import (
    _assert_rejects_at,
    _reject_tally,
    _lower_ctx_witnessed, _fn, _assert_byte_identical,
    _assert_routes_byte_identical, _top_level,
)

_REC = (
    "from tpy import Int32, Own\n"
    "class Point:\n"
    "    x: Int32\n"
    "    def __init__(self, x: Int32):\n"
    "        self.x = x\n"
)


class TestOptOwnRecordNameArgSlots:
    def test_ctor_slot_last_use_moves(self):
        src = (_REC +
               "class Wrapper:\n"
               "    tag: Int32\n"
               "    def __init__(self, p: Own[Point] | None, tag: Int32):\n"
               "        self.tag = tag if p is not None else -1\n"
               "def main() -> None:\n"
               "    p = Point(1)\n"
               "    w = Wrapper(p, 42)\n"
               "    print(w.tag)\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces.get("move.opt_own_last_use", 0) == 1
        cpp = _assert_routes_byte_identical(src)
        assert "Wrapper(std::move(p), 42)" in cpp[1]

    def test_method_slot_last_use_moves(self):
        src = (_REC +
               "class Sink:\n"
               "    val: Int32\n"
               "    def take(self, p: Own[Point] | None) -> None:\n"
               "        self.val = 1 if p is not None else 0\n"
               "def main() -> None:\n"
               "    s = Sink()\n"
               "    s.val = -1\n"
               "    p = Point(3)\n"
               "    s.take(p)\n"
               "    print(s.val)\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces.get("move.opt_own_last_use", 0) == 1
        cpp = _assert_routes_byte_identical(src)
        assert "s.take(std::move(p));" in cpp[1]

    def test_non_last_use_copy_shape_stays_ast(self):
        # The copy half is unwitnessed: a name read again after the call
        # must NOT take the bare move -- the lowering rejects and the body
        # falls back whole (identity is the claim).
        src = (_REC +
               "class Sink:\n"
               "    val: Int32\n"
               "    def take(self, p: Own[Point] | None) -> None:\n"
               "        self.val = 1 if p is not None else 0\n"
               "def main() -> None:\n"
               "    s = Sink()\n"
               "    p = Point(3)\n"
               "    s.take(p)\n"
               "    print(p.x)\n"
               "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:expr.call:call.opt_own_copy")


class TestMarkerNoneValueOptDefaultFill:
    def test_filled_none_default_renders_nullopt(self):
        # `followlinks=follow` arrives from sema with the omitted
        # `onerror=None` default filled positionally at its
        # Optional[Callable] slot.
        src = ("import os\n"
               "from tpy import Int32\n"
               "def count_dirs(root: str, follow: bool) -> Int32:\n"
               "    n = 0\n"
               "    for dp, dn, fn in os.walk(root, followlinks=follow):\n"
               "        n += 1\n"
               "    return n\n"
               "def main() -> None:\n"
               "    print(count_dirs(\".\", False))\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "count_dirs") is not None
        assert faces.get("call.none_value_opt", 0) >= 1
        cpp = _assert_routes_byte_identical(src)
        assert "::tpystd::os::walk(root, true, std::nullopt, follow)" in cpp[1]

    def test_ptr_repr_none_keeps_optptr_face(self):
        # A None at a POINTER-repr Optional[record] marker slot renders
        # `nullptr` via the optptr face, never the value-opt row.
        src = (_REC +
               "class Helper:\n"
               "    @staticmethod\n"
               "    def probe(p: Point | None) -> Int32:\n"
               "        return 0 if p is None else p.x\n"
               "def main() -> None:\n"
               "    print(Helper.probe(None))\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces.get("optptr.none", 0) == 1
        assert faces.get("call.none_value_opt", 0) == 0
        cpp = _assert_routes_byte_identical(src)
        assert "Helper::probe(nullptr)" in cpp[1]


class TestInheritedCtorInstantiation:
    _HDR = (
        "from tpy import Int32\n"
        "class Base[T]:\n"
        "    v: T\n"
        "    def __init__(self, v: T):\n"
        "        self.v = v\n"
        "class Sub[T](Base[T]): ...\n"
    )

    def test_instantiation_over_inherited_init_routes(self):
        src = (self._HDR +
               "def main() -> None:\n"
               "    print(Sub[Int32](7).v)\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces.get("ctor.inherited_instantiation", 0) == 1
        cpp = _assert_routes_byte_identical(src)
        assert "Sub<int32_t>(7).v" in cpp[1]

    def test_own_init_keeps_plain_path(self):
        # A subclass with its OWN __init__ carries a param-ful synthetic
        # fi: the plain arity gate admits it and the inherited face must
        # not fire.
        src = ("from tpy import Int32\n"
               "class Base[T]:\n"
               "    v: T\n"
               "    def __init__(self, v: T):\n"
               "        self.v = v\n"
               "class Sub[T](Base[T]):\n"
               "    def __init__(self, v: T):\n"
               "        self.v = v\n"
               "def main() -> None:\n"
               "    print(Sub[Int32](7).v)\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces.get("ctor.inherited_instantiation", 0) == 0
        _assert_routes_byte_identical(src)


class TestOptPtrCallPassFace:
    _HDR = (_REC +
            "class Edge:\n"
            "    target: Point | None\n"
            "    def __init__(self, p: Point | None):\n"
            "        self.target = None\n"
            "def find(items: list[Point], key: Int32) -> Point | None:\n"
            "    for p in items:\n"
            "        if p.x == key:\n"
            "            return p\n"
            "    return None\n")

    def test_borrow_opt_call_passes_bare_in_body(self):
        src = (self._HDR +
               "def main() -> None:\n"
               "    pts: list[Point] = list()\n"
               "    pts.append(Point(3))\n"
               "    e = Edge(find(pts, 3))\n"
               "    print(e.target is None)\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces.get("optptr.call_pass", 0) == 1
        cpp = _assert_routes_byte_identical(src)
        assert "Edge(find(pts, 3))" in cpp[1]

    def test_borrow_opt_call_passes_bare_at_top_level(self):
        # The flipped corpus witness is a module-init global: the same face
        # must route through the top-level seeding (pointer-slot globals).
        src = (self._HDR +
               "pts: list[Point] = list()\n"
               "pts.append(Point(3))\n"
               "e3 = Edge(find(pts, 3))\n"
               "print(e3.target is None)\n")
        top, faces, fallback = _top_level(src)
        assert top is not None
        assert faces.get("optptr.call_pass", 0) == 1
        assert not fallback
        _assert_byte_identical(src)

    def test_own_declared_opt_return_stays_out(self):
        # An Own-DECLARED optional return is storage-form
        # (`std::optional<T>`), not the `T*` the slot binds: the face must
        # not fire; the AST's optional_to_ptr lift is unmirrored, so the
        # body falls back whole.
        src = (self._HDR +
               "from tpy import Own\n"
               "def make(flag: bool) -> Own[Point] | None:\n"
               "    if flag:\n"
               "        return Point(5)\n"
               "    return None\n"
               "def main() -> None:\n"
               "    e = Edge(make(True))\n"
               "    print(e.target is None)\n"
               "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:expr.call:call.ctor_arg.optional")
