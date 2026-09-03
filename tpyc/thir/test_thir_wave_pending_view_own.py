"""Wave 3c of the mechanical residue: the Pending-view Own family and the
Own[tuple] movable-name return.

Row 1 -- the F1 fence resolves a Pending view type-arg through the same
per-analyzer view_vars the resolver reads (`Box(s)` on a str local ->
`Box<std::string_view>`), and the Own[T] slot's VIEW payload takes the
brace-init copy temp (`std::string_view __tmp_1{s};` + `std::move`).
Row 2 -- an `Own[tuple[...]]` return's tuple literal admits MOVABLE NAME
members at their last use (`return (w1, w2, w3)` ->
`{std::move(w1), ...}`), beside the pre-existing rvalue members.
"""

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _reject_tally,
    _assert_routes_byte_identical,
    _compile,
    _entry,
    _lower_ctx_witnessed,
)


def _reject_tags(source, extra_lib_dirs=None):
    return _reject_tally(source, extra_lib_dirs=extra_lib_dirs)


class TestPendingViewOwnCtorArg:
    def test_box_str_lvalue_routes(self):
        src = (
            "from tplib import Box\n"
            "def main() -> None:\n"
            "    s = \"world\"\n"
            "    b = Box(s)\n"
            "    print(b.get())\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "std::string_view __tmp_1{s};" in cpp
        assert "Box<std::string_view>(std::move(__tmp_1))" in cpp.replace(
            "::tpystd::tplib::box::", "")

    def test_rc_new_str_lvalue_routes(self):
        src = (
            "from tplib import Rc\n"
            "def main() -> None:\n"
            "    s = \"world\"\n"
            "    r = Rc.new(s)\n"
            "    print(r.get())\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "std::string_view __tmp_1{s};" in cpp
        assert "new_<std::string_view>(std::move(__tmp_1))" in cpp

    def test_pending_bytes_at_own_slot_keeps_rejecting(self):
        # The view arm is StrView-gated: a Pending BYTES local at the same
        # Own slot resolves outside the admitted family and stays AST.
        src = (
            "from tplib import Box\n"
            "def main() -> None:\n"
            "    b = b\"hi\"\n"
            "    bx = Box(b)\n"
            "    print(1)\n"
            "main()\n"
        )
        fell = _reject_tags(src)
        assert "body:stmt.var_decl:decl.slot_type" in fell, fell

    def test_owned_str_name_at_own_str_slot_takes_the_owned_temp(self):
        # The OWNED-str slot's NAME source takes the OWNED copy temp
        # (`std::string __tmp_1{t};`), not the VIEW brace-init the two rows
        # above pin -- the form split the row keys on, both halves live.
        src = (
            "from tpy import Own\n"
            "def sink(x: Own[str]) -> None:\n"
            "    print(x)\n"
            "def main() -> None:\n"
            "    s = \"abc\" + \"def\"\n"
            "    t: str = s\n"
            "    sink(t)\n"
            "main()\n"
        )
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "std::string __tmp_1{t};" in cpp
        assert "sink(std::move(__tmp_1))" in cpp


class TestOwnTupleMovableNameReturn:
    SRC = (
        "from tpy import Int32, Own\n"
        "from tplib import Rc\n"
        "from tplib.rc import Weak\n"
        "class Cell:\n"
        "    v: Int32\n"
        "    def __init__(self, v: Int32) -> None:\n"
        "        self.v = v\n"
        "def make_weaks() -> Own[tuple[Weak[Cell], Weak[Cell]]]:\n"
        "    r = Rc.new(Cell(1))\n"
        "    w1 = r.downgrade()\n"
        "    w2 = r.downgrade()\n"
        "    return (w1, w2)\n"
        "def main() -> None:\n"
        "    ws = make_weaks()\n"
        "    print(1)\n"
        "main()\n"
    )

    def test_movable_name_members_move_into_storage(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "{std::move(w1), std::move(w2)}" in cpp.replace(
            "::tpystd::tplib::rc::", "").replace(
            "return std::tuple<Weak<Cell>, Weak<Cell>>", "")

    def test_non_last_use_name_member_keeps_rejecting(self):
        # A name whose use in the literal is NOT its last (the same name
        # feeds two members) is not a move source for the first member --
        # that copy render is the borrow ladder's, not this row's.
        src = (
            "from tpy import Int32, Own\n"
            "class Node:\n"
            "    v: Int32\n"
            "    def __init__(self, v: Int32) -> None:\n"
            "        self.v = v\n"
            "def dup() -> Own[tuple[Node, Node]]:\n"
            "    n = Node(1)\n"
            "    return (n, n)\n"
        )
        fell = _reject_tags(src)
        assert "body:stmt.return:return.tuple_source" in fell, fell


class TestPendingStrOwnFormSplit:
    """A str local whose binding is still a `PendingStrType` -- the form is
    only in the resolved type, which is what the AST reads. The OWNED
    resolution takes this family's copy temp, the VIEW resolution the S1
    inline convert; the pair pins the split at the same slot."""

    _PRELUDE = (
        "from tpy import Own, StrView, error_return, ReturnException\n"
        "class E(ReturnException):\n"
        "    pass\n"
    )

    OWNED_SRC = _PRELUDE + (
        "@error_return(E)\n"
        "def rd() -> str:\n"
        "    return \"ab\"\n"
        "@error_return(E)\n"
        "def go() -> Own[list[str]]:\n"
        "    xs: list[str] = []\n"
        "    e = rd()\n"
        "    xs.append(e)\n"
        "    return xs\n"
        "def main() -> None:\n"
        "    print(1)\n"
        "main()\n"
    )

    VIEW_SRC = _PRELUDE + (
        "@error_return(E)\n"
        "def rdv() -> StrView:\n"
        "    return \"ab\"\n"
        "@error_return(E)\n"
        "def go() -> Own[list[str]]:\n"
        "    xs: list[str] = []\n"
        "    e = rdv()\n"
        "    xs.append(e)\n"
        "    return xs\n"
        "def main() -> None:\n"
        "    print(1)\n"
        "main()\n"
    )

    def test_owned_resolved_pending_takes_the_copy_temp(self):
        _hpp, cpp = _assert_routes_byte_identical(self.OWNED_SRC)
        assert "std::string e;" in cpp
        assert "std::string __tmp_1{e};" in cpp
        assert "xs.push_back(std::move(__tmp_1));" in cpp

    def test_owned_resolved_pending_witnesses_the_own_str_temp(self):
        _thir, faces = _lower_ctx_witnessed(self.OWNED_SRC)
        assert faces.get("argtemp.own_str", 0) >= 1, faces

    def test_view_resolved_pending_keeps_the_inline_convert(self):
        # The boundary: same slot, same statement shape, VIEW resolution --
        # the AST spells the S1 `std::string(e)` convert with no temp, so
        # the owned row must not claim it.
        _hpp, cpp = _assert_routes_byte_identical(self.VIEW_SRC)
        assert "std::string_view e;" in cpp
        assert "xs.push_back(std::string(e));" in cpp
        assert "__tmp_1{e}" not in cpp
