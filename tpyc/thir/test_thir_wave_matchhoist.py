"""Match-arm container hoists: the non-value hoist admission extends to
scalar-read containers -- the single-bind OPTIONAL_STORAGE flavor
(`std::optional<std::vector<T>> xs;`, plain arm assigns, deref reads)
and the rvalue-reassigned pointer+slot flavor (`std::vector<T>* xs;`
plus the match-head rebind slot), record tiers only."""

from __future__ import annotations

from .testutil import (
    _assert_rejects_at,
    _reject_tally,
    _lower_ctx, _lower_ctx_witnessed, _fn, _assert_byte_identical,
)

_PT = (
    "from tpy import Int32\n"
    "class Point:\n"
    "    x: Int32\n"
    "    y: Int32\n"
    "    def __init__(self, x: Int32, y: Int32) -> None:\n"
    "        self.x = x\n"
    "        self.y = y\n"
)


class TestMatchContainerHoists:
    def test_single_bind_takes_optional_storage(self):
        src = _PT + (
            "def f(p: Point) -> int:\n"
            "    match p:\n"
            "        case Point(x=0):\n"
            "            xs = [1, 2]\n"
            "        case _:\n"
            "            return -1\n"
            "    xs.append(7)\n"
            "    return xs[0] + len(xs)\n"
            "def main() -> None:\n"
            "    print(f(Point(0, 5)))\n"
            "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces["match.hoist_optional_storage"] >= 1
        cpp = _assert_byte_identical(src)
        assert "std::optional<std::vector<int32_t>> xs;" in cpp[1]
        assert "xs->push_back(7);" in cpp[1]

    def test_rvalue_reassigned_takes_ptr_slot(self):
        src = _PT + (
            "def g(p: Point) -> int:\n"
            "    match p:\n"
            "        case Point(x=0):\n"
            "            xs = [1, 2]\n"
            "        case _:\n"
            "            xs = [3]\n"
            "    return xs[0] + len(xs)\n"
            "def main() -> None:\n"
            "    print(g(Point(0, 5)))\n"
            "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "g") is not None
        assert faces["match.hoist_ptr_slot"] >= 1
        cpp = _assert_byte_identical(src)
        assert "std::vector<int32_t>* xs;" in cpp[1]
        assert "xs = &*(__slot_1 = {1, 2});" in cpp[1]

    def test_scalar_tier_container_hoist_stays_ast(self):
        # The ptr_slot flavor is threaded through the record tier only;
        # a scalar-subject match with the same hoist keeps rejecting.
        src = ("from tpy import Int32\n"
               "def f(n: Int32) -> Int32:\n"
               "    match n:\n"
               "        case 0:\n"
               "            xs = [1, 2]\n"
               "        case _:\n"
               "            xs = [3]\n"
               "    return xs[0] + len(xs)\n"
               "def main() -> None:\n"
               "    print(f(Int32(0)))\n"
               "main()\n")
        _assert_rejects_at(_reject_tally(src), "body:stmt.match")


class TestNestedMatchValueHoist:
    def test_nested_match_leaked_capture_hoists_in_branch(self):
        # A nested match's leaked VALUE capture hoists at the nested
        # match's own site (`::tpy::BigInt t;` inside the outer arm) --
        # the value flavor is position-neutral.
        src = ("class Inner:\n"
               "    n: int\n"
               "    def __init__(self, n: int) -> None:\n"
               "        self.n = n\n"
               "class Holder:\n"
               "    inner: Inner\n"
               "    tag: int\n"
               "    def __init__(self, inner: Inner, tag: int) -> None:\n"
               "        self.inner = inner\n"
               "        self.tag = tag\n"
               "def keeps_alias(h: Holder, g: Holder) -> int:\n"
               "    match h:\n"
               "        case Holder(inner=q):\n"
               "            match g:\n"
               "                case Holder(tag=t):\n"
               "                    pass\n"
               "            h.inner.n = 42\n"
               "            return q.n + t\n"
               "    return 0\n"
               "def main() -> None:\n"
               "    print(keeps_alias(Holder(Inner(1), 5), "
               "Holder(Inner(2), 7)))\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "keeps_alias") is not None
        cpp = _assert_byte_identical(src)
        assert "::tpy::BigInt t;" in cpp[1]
        assert "t = __match_subject_2.tag;" in cpp[1]


class TestValueHoistPositions:
    def test_in_loop_value_hoist_routes(self):
        # The value flavor is position-neutral in loops too: `T t;`
        # redeclares at the match site each iteration, exactly the AST.
        src = _PT + (
            "def f(ps: list[Point]) -> Int32:\n"
            "    total = 0\n"
            "    for p in ps:\n"
            "        match p:\n"
            "            case Point(x=0):\n"
            "                t = 1\n"
            "            case _:\n"
            "                t = 2\n"
            "        total = total + t\n"
            "    return total\n"
            "def main() -> None:\n"
            "    print(f([Point(0, 1), Point(3, 1)]))\n"
            "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        _assert_byte_identical(src)

    def test_nested_nonvalue_hoist_stays_fenced(self):
        # Only the VALUE flavor is position-neutral: a nested match's
        # leaked CONTAINER capture keeps the in-branch reject.
        src = ("from tpy import Int32\n"
               "class Inner:\n"
               "    n: Int32\n"
               "    def __init__(self, n: Int32) -> None:\n"
               "        self.n = n\n"
               "class Holder:\n"
               "    inner: Inner\n"
               "    def __init__(self, inner: Inner) -> None:\n"
               "        self.inner = inner\n"
               "def f(h: Holder, g: Holder) -> Int32:\n"
               "    match h:\n"
               "        case Holder(inner=q):\n"
               "            match g:\n"
               "                case Holder():\n"
               "                    xs = [1, 2]\n"
               "            return q.n + len(xs)\n"
               "    return 0\n"
               "def main() -> None:\n"
               "    print(f(Holder(Inner(1)), Holder(Inner(2))))\n"
               "main()\n")
        _assert_rejects_at(_reject_tally(src), "body:stmt.match")
