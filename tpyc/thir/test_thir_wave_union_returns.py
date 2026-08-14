"""The Own[union] / wrapper-union returns track (designed-queue item 2,
cells A-C).

Rows pinned here:
  * 2A -- `_own_storage_union_return` over the WIDENED member class
    (str / containers / value tuples / Span via `_ptr_union_member_wide`):
    container names and slice rvalues return bare, a value-tuple literal
    spells its own resolved type; the `decl.slot_type` variant-decl twin
    (`std::variant<...> __slot_N = call();` + `to_ptr_variant`) rides
    `_eligible_ptr_union_wide`, as does the isinstance narrowing over the
    wide binding.
  * 2B -- `Own[V]` wrapper-union returns: `None` -> monostate, scalar
    literals bare, container literals via the ru render (including the
    OUTER literal typed AS the wrapper), member-container names bare; a
    scalar-literal insert at a wrapper element slot renders the bare
    push_back; a union-typed NAME prints via the `::tpy::__str__` visitor
    and passes bare at a native same-union slot (`repr(a)`).
  * 2C -- wrapper BORROW returns (`-> Expr` -> `Expr&`): a borrow param
    returns bare; a wrapper-borrow-returning call composes bare at the
    same-wrapper arg slot (`count(passthru(tree))`).

Boundaries that must keep rejecting: a member-NAME insert at a wrapper
element slot, a wrapper NAME at the Own[V] return, and the resumable
union-name print (the STR row is fenced out of resumable bodies -- see
TestFlatAssertNarrowScoping in test_thir_resumable).
"""

from .testutil import (
    _assert_byte_identical,
    _assert_routes_byte_identical,
    _fn,
    _lower_ctx,
    _lower_ctx_witnessed,
    _top_level,
)


class TestOwnUnionWideReturns:
    SRC = (
        "from tpy import Int32, Own\n"
        "class Rec:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32) -> None:\n        self.n = n\n"
        "def get_list() -> Own[list[Int32] | Int32]:\n"
        "    xs: list[Int32] = [10, 20]\n"
        "    return xs\n"
        "def get_tuple(flag: bool) -> Own[tuple[str, Int32] | Rec]:\n"
        "    if flag:\n"
        "        return (\"hi\", 7)\n"
        "    return Rec(3)\n"
        "def main() -> None:\n"
        "    w = get_list()\n"
        "    if isinstance(w, list):\n"
        "        w.append(30)\n"
        "        print(len(w))\n"
        "    v = get_tuple(False)\n"
        "    if isinstance(v, Rec):\n"
        "        print(v.n)\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        # Container name returns bare into the storage variant.
        assert "return xs;" in cpp
        # Tuple literal spells its own resolved type.
        assert "return std::tuple<std::string, int32_t>{\"hi\", 7};" in cpp
        # The variant-decl twin: value-variant slot + to_ptr_variant lift.
        assert "= ::tpy::to_ptr_variant(__slot_1);" in cpp
        assert "= ::tpy::to_ptr_variant(__slot_2);" in cpp


class TestOwnWrapperReturns:
    SRC = (
        "from tpy import Own\n"
        "type V = None | bool | int | float | str | list[V]\n"
        "def make_int() -> Own[V]:\n"
        "    return 42\n"
        "def make_null() -> Own[V]:\n"
        "    return None\n"
        "def make_list() -> Own[V]:\n"
        "    return [1, 2.5]\n"
        "def build() -> Own[V]:\n"
        "    xs: list[V] = [1, 2]\n"
        "    return xs\n"
        "def main() -> None:\n"
        "    a = make_int()\n"
        "    print(a)\n"
        "    arr: V = build()\n"
        "    if isinstance(arr, list):\n"
        "        arr.append(4)\n"
        "        print(len(arr))\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "return 42;" in cpp
        assert "return std::monostate{};" in cpp
        assert "return std::vector<V>{1, 2.5};" in cpp
        # Member-container name returns bare.
        assert "return xs;" in cpp
        # The union print streams through the runtime visitor.
        assert "::tpy::__str__(a)" in cpp
        # The wrapper-element literal insert renders the bare push_back.
        assert "__arr.push_back(4);" in cpp


class TestWrapperBorrowReturn:
    SRC = (
        "from tpy import Int32\n"
        "type Expr = int | list[Expr]\n"
        "def passthru(e: Expr) -> Expr:\n"
        "    return e\n"
        "def count(e: Expr) -> Int32:\n"
        "    if isinstance(e, int):\n"
        "        return 1\n"
        "    n = 0\n"
        "    for sub in e:\n"
        "        n += count(sub)\n"
        "    return n\n"
        "def main() -> None:\n"
        "    tree: Expr = [1, [2, 3], 4]\n"
        "    print(count(passthru(tree)))\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "Expr& passthru(Expr& e)" in cpp
        assert "return e;" in cpp
        # The wrapper container-literal decl (typed AS the wrapper).
        assert ("Expr tree = std::vector<Expr>{1, "
                "std::vector<Expr>{2, 3}, 4};") in cpp
        # The borrow-returning call composes bare at the wrapper slot.
        assert "count(passthru(tree))" in cpp


class TestWrapperValueCallAndGlobal:
    """The union_recursive_ref_return chain: an Own[Expr]-returning call
    composes bare in a VALUE position, a direct-storage wrapper GLOBAL
    seeds read-only (reads render bare like a wrapper local's), and the
    top-level literal init takes the assign-sink ru-literal render."""

    SRC = (
        "from tpy import Int32, Own, readonly\n"
        "type Expr = int | list[Expr]\n"
        "g: Expr = [1, [2, 3], 4]\n"
        "def count(e: readonly[Expr]) -> Int32:\n"
        "    if isinstance(e, int):\n"
        "        return 1\n"
        "    n = 0\n"
        "    for sub in e:\n"
        "        n += count(sub)\n"
        "    return n\n"
        "def get_global() -> Expr:\n"
        "    return g\n"
        "def build() -> Own[Expr]:\n"
        "    return [5, 6]\n"
        "def main() -> None:\n"
        "    print(count(build()))\n"
        "    print(count(get_global()))\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        thir, wit = _lower_ctx_witnessed(self.SRC)
        assert wit.get("arg.ru_wrapper_value_call", 0) >= 1
        assert wit.get("call.wrapper_value_ret", 0) >= 1
        # The top-level init routes with the assign-sink face (the
        # function-only lowering above never reaches __tpy_init).
        top, top_wit, _fb = _top_level(self.SRC)
        assert top is not None
        assert top_wit.get("assign.ru_wrapper_literal", 0) >= 1
        # The value-returning call composes bare at the const-ref slot.
        assert "count(build())" in cpp
        # The seeded global reads bare at the Expr& return.
        assert "return g;" in cpp
        # The top-level init is the assign-sink ru-literal render.
        assert ("g = std::vector<Expr>{1, "
                "std::vector<Expr>{2, 3}, 4};") in cpp

    def test_storage_sink_routes_discard_defers(self):
        # BOUNDARY: the STORAGE decl sink rides its own pre-existing arms
        # (routes); the DISCARD statement has no rung and keeps deferring.
        src = (
            "from tpy import Int32, Own\n"
            "type Expr = int | list[Expr]\n"
            "def build() -> Own[Expr]:\n"
            "    return [5, 6]\n"
            "def storage_sink() -> None:\n"
            "    x: Expr = build()\n"
            "    print(isinstance(x, int))\n"
            "def discard_sink() -> None:\n"
            "    build()\n"
            "def main() -> None:\n"
            "    storage_sink()\n"
            "    discard_sink()\n"
            "main()\n"
        )
        _assert_byte_identical(src)
        thir = _lower_ctx(src)
        assert _fn(thir, "storage_sink") is not None
        assert _fn(thir, "discard_sink") is None

    def test_reassigned_wrapper_literal_decl_defers(self):
        # BOUNDARY: the wrapper-literal DECL arm excludes reassigned names
        # (rebind guard), so the whole body stays AST; the assign-sink arm
        # alone cannot rescue it (its decl never routes).
        src = (
            "from tpy import Int32\n"
            "type Expr = int | list[Expr]\n"
            "def f() -> None:\n"
            "    seed: Expr = [1, 2]\n"
            "    seed = [3, [4]]\n"
            "    print(isinstance(seed, int))\n"
            "f()\n"
        )
        _assert_byte_identical(src)
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None


class TestUnionReturnsBoundaries:
    def test_member_name_insert_keeps_rejecting(self):
        # A member NAME insert at a wrapper element slot is not the
        # literal row; the body falls back byte-identically.
        src = (
            "from tpy import Own, Int32\n"
            "type Json = None | bool | int | str | list[Json]\n"
            "def main() -> None:\n"
            "    xs: list[Json] = [1, 2]\n"
            "    n = 3\n"
            "    xs.append(n)\n"
            "    print(len(xs))\n"
            "main()\n"
        )
        _assert_byte_identical(src)
        thir, wit = _lower_ctx_witnessed(src)
        assert wit.get("arg.ru_wrapper_elem_literal", 0) == 0

    def test_wrapper_elem_literal_int32_edge_keeps_rejecting(self):
        # The wrapper-element literal insert reuses _ru_elem_ok's strict
        # int32 bounds: `xs.append(2**31)` is out of range and must fall
        # back (byte-identical via AST), like the ru-literal elements.
        src = (
            "from tpy import Own\n"
            "type Json = None | bool | int | str | list[Json]\n"
            "def main() -> None:\n"
            "    xs: list[Json] = [1]\n"
            "    xs.append(2147483648)\n"
            "    print(len(xs))\n"
            "main()\n"
        )
        _assert_byte_identical(src)
        thir, wit = _lower_ctx_witnessed(src)
        assert wit.get("arg.ru_wrapper_elem_literal", 0) == 0

    def test_wrapper_name_return_keeps_rejecting(self):
        # A WRAPPER NAME at the Own[V] return slot is outside the source
        # rows (container member names only); byte-identical via fallback.
        src = (
            "from tpy import Own\n"
            "type V = None | int | str\n"
            "def make() -> Own[V]:\n"
            "    return 5\n"
            "def echo() -> Own[V]:\n"
            "    v: V = make()\n"
            "    return v\n"
            "def main() -> None:\n"
            "    print(echo())\n"
            "main()\n"
        )
        _assert_byte_identical(src)
        thir, wit = _lower_ctx_witnessed(src)
        assert wit.get("ret.own_wrapper_member", 0) == 0
