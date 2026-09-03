"""Genrec track cells C-remainder + D: the ctor MIL-field rows, the bare
borrow return, and the Own[genrec] ctor-arg cascade.

- MIL: an `Own[Tree[T]]` param moves into the generic-instance field via
  the type-agnostic M3b-move arm (`t(std::move(t))`); a container literal
  takes the ru-instance spelled render (`mil.genrec_literal`).
- A bare `-> Tree[Int32]` slot is the borrow direction
  (`Tree<int32_t>&`); a field read returns `this->t` bare.
- `Holder(seed)` at an `Own[Tree[Int32]]` ctor slot rides the Own-slot
  cascade; the ru-literal decl rows now promote movability, so a movable
  last use renders the temp-free `std::move(seed)`.
"""

from .testutil import (
    _assert_rejects_at, _assert_routes_byte_identical,
                      _reject_tally)

_SRC = (
    "from tpy import Int32, Own\n"
    "type Tree[T] = T | list[Tree[T]]\n"
    "class Holder:\n"
    "    t: Tree[Int32]\n"
    "    def __init__(self, t: Own[Tree[Int32]]) -> None:\n"
    "        self.t = t\n"
    "    def get(self) -> Tree[Int32]:\n"
    "        return self.t\n"
    "class Prefilled:\n"
    "    t: Tree[Int32]\n"
    "    def __init__(self) -> None:\n"
    "        self.t = [Int32(1), Int32(2)]\n"
    "def main() -> None:\n"
    "    seed: Tree[Int32] = [1, [2, 3], 4]\n"
    "    h = Holder(seed)\n"
    "    h2 = Holder([Int32(7), Int32(8)])\n"
    "    p = Prefilled()\n"
    "    print(1)\n"
    "main()\n"
)


class TestGenrecBorrowCallAndFieldArgs:
    # The tail rows: the borrow-call decl alias (`g = h.get()` ->
    # `Tree<int32_t>& g = h.get();`), the borrow-returning call bound bare
    # at a wrapper arg slot, and the same-wrapper FIELD read bound bare
    # (self- and name-receiver flavors).
    SRC = (
        "from tpy import Int32, Own\n"
        "type Tree[T] = T | list[Tree[T]]\n"
        "def leaf_count(t: Tree[Int32]) -> Int32:\n"
        "    match t:\n"
        "        case list() as b:\n            return len(b)\n"
        "        case _:\n            return 1\n"
        "class Holder:\n"
        "    t: Tree[Int32]\n"
        "    def __init__(self, t: Own[Tree[Int32]]) -> None:\n"
        "        self.t = t\n"
        "    def get(self) -> Tree[Int32]:\n"
        "        return self.t\n"
        "    def count(self) -> Int32:\n"
        "        return leaf_count(self.t)\n"
        "def main() -> None:\n"
        "    seed: Tree[Int32] = [1, 2]\n"
        "    h = Holder(seed)\n"
        "    g = h.get()\n"
        "    print(leaf_count(g))\n"
        "    print(leaf_count(h.get()))\n"
        "    print(leaf_count(h.t))\n"
        "    print(h.count())\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC, comments=False)
        out = hpp + cpp
        assert "Tree<int32_t>& g = h.get();" in out
        assert "leaf_count(h.get())" in out
        assert "leaf_count(h.t)" in out
        assert "leaf_count(this->t)" in out


class TestGenrecMethodArgRow:
    # The method-arg ladder's wrapper NAME row, pinned directly (not just
    # via the corpus case): `h.matches(probe)` binds the wrapper name bare.
    SRC = (
        "from tpy import Int32, Own\n"
        "type Tree[T] = T | list[Tree[T]]\n"
        "class Holder:\n"
        "    t: Tree[Int32]\n"
        "    def __init__(self, t: Own[Tree[Int32]]) -> None:\n"
        "        self.t = t\n"
        "    def matches(self, other: Tree[Int32]) -> bool:\n"
        "        return True\n"
        "def main() -> None:\n"
        "    probe: Tree[Int32] = [1]\n"
        "    h = Holder([Int32(2)])\n"
        "    print(h.matches(probe))\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC, comments=False)
        assert "h.matches(probe)" in hpp + cpp


class TestGenrecFieldArgBoundaries:
    # `_ru_wrapper_field_arg`'s two reject guards: a CHAINED receiver
    # (field off a call) rides the borrow-call row instead or folds; a
    # MEMBER-typed field (not the wrapper itself) stays out of the bare
    # row. Both fold byte-identically here (the member-typed field at a
    # wrapper slot has no admitted row).
    SRC = (
        "from tpy import Int32, Own\n"
        "type Tree[T] = T | list[Tree[T]]\n"
        "def leaf_count(t: Tree[Int32]) -> Int32:\n"
        "    return 1\n"
        "class Inner:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32) -> None:\n        self.n = n\n"
        "class Holder:\n"
        "    inner: Inner\n"
        "    def __init__(self, inner: Own[Inner]) -> None:\n"
        "        self.inner = inner\n"
        "def use(h: Holder) -> None:\n"
        "    print(leaf_count(h.inner.n))\n"
        "def main() -> None:\n"
        "    use(Holder(Inner(5)))\n"
        "main()\n"
    )

    def test_member_typed_chain_field_stays_out_of_bare_row(self):
        _assert_rejects_at(_reject_tally(self.SRC),
                           "body:stmt.expr_stmt:call.arg_shape.other_recursivealiasinstancetype")


class TestGenrecFieldRows:
    def test_routes_byte_identical(self):
        hpp, cpp = _assert_routes_byte_identical(_SRC, comments=False)
        out = hpp + cpp
        # The own-param MIL move...
        assert ": t(std::move(t)) {}" in out
        # ...the literal MIL render...
        assert ": t(std::vector<Tree<int32_t>>{1, 2}) {}" in out
        # ...the bare borrow return of the field...
        assert "return this->t;" in out
        # ...the movable local's temp-free move into the Own ctor slot...
        assert "Holder(std::move(seed))" in out
        # ...and the container literal's INLINE ru-instance render at the
        # Own[genrec] ctor slot (arg.genrec_own_literal's witness).
        assert "Holder(std::vector<Tree<int32_t>>{7, 8})" in out


class TestGenericRecordFieldMilMove:
    # The generic-record MIL move arm (`mil.generic_record_move`): a field
    # whose instantiation _f1_record rejects (`Box[Tree[T]]` -- a genrec /
    # open-T type arg) still takes the type-agnostic M3b Own-param move.
    # Move sources ONLY; the ctor-ARG side of the same case stays fenced
    # (the parked _f1_record type-arg widening).
    SRC = (
        "from tpy import Int32, Own\n"
        "from tplib.box import Box\n"
        "type Tree[T] = T | list[Tree[T]]\n"
        "class Holder[T]:\n"
        "    data: Box[Tree[T]]\n"
        "    def __init__(self, data: Own[Box[Tree[T]]]) -> None:\n"
        "        self.data = data\n"
    )

    def test_ctor_routes_with_move(self):
        from ..codegen_cpp import CodeGenOptions
        from .testutil import _compile, _entry
        compiler, modules = _compile(self.SRC)
        hpp, cpp = compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False))
        assert ": data(std::move(data)) {}" in hpp + cpp
        assert compiler._thir_face_witnesses.get("mil.generic_record_move")
        compiler2, modules2 = _compile(self.SRC)
        hpp2, cpp2 = compiler2.generate_code_to_strings(
            _entry(modules2),
            options=CodeGenOptions(emit_source_comments=False))
        assert (hpp, cpp) == (hpp2, cpp2)

    def test_call_source_mil_routes(self):
        # A CALL source at the same generic-record field now routes: the
        # alias-instance type-arg slice made Box[Tree[Int32]] F1, so the
        # MIL call row admits it -- byte-identical, no fallback.
        from ..codegen_cpp import CodeGenOptions
        from .testutil import _compile, _entry
        src = (
            "from tpy import Int32, Own\n"
            "from tplib.box import Box\n"
            "type Tree[T] = T | list[Tree[T]]\n"
            "def make_box() -> Own[Box[Tree[Int32]]]:\n"
            "    seed: Tree[Int32] = [1]\n"
            "    return Box(seed)\n"
            "class Holder:\n"
            "    data: Box[Tree[Int32]]\n"
            "    def __init__(self) -> None:\n"
            "        self.data = make_box()\n"
        )
        compiler, modules = _compile(src)
        hpp, cpp = compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False))
        compiler2, modules2 = _compile(src)
        hpp2, cpp2 = compiler2.generate_code_to_strings(
            _entry(modules2),
            options=CodeGenOptions(emit_source_comments=False))
        assert (hpp, cpp) == (hpp2, cpp2)
