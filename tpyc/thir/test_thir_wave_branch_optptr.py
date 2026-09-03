"""Pins for a BRANCH-FIRST pointer-repr `Optional[T]` local seeded null.

A branch-first decl has nowhere to place a hoist line, so only the flavor that
needs none lowers in position: `T* sel = nullptr;`, emitted exactly where it
is written. The boundary units hold the two flavors that do need one -- an
rvalue RESEAT, whose rebind slot the AST pre-declares at function top, and an
rvalue INIT, which brings its own `__slot_N` storage."""

from __future__ import annotations

from .testutil import (_assert_byte_identical, _assert_rejects_at,
                       _assert_routes_byte_identical,
                       _lower_ctx_witnessed, _thir_ctx)

_NODE = (
    "from tpy import Int32\n"
    "class Node:\n"
    "    x: Int32\n"
    "    def __init__(self, x: Int32) -> None:\n"
    "        self.x = x\n"
)


class TestBranchFirstNullOptionalPointer:
    def test_branch_first_null_pointer_local_routes(self):
        src = _NODE + (
            "def pick(flag: bool) -> Int32:\n"
            "    if flag:\n"
            "        sel: Node | None = None\n"
            "        if sel is not None:\n"
            "            return sel.x\n"
            "        return -1\n"
            "    return 0\n"
            "def main() -> None:\n"
            "    print(pick(True))\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "Node* sel = nullptr;" in hpp + cpp
        assert "__slot_" not in hpp + cpp
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces["decl.opt_slot_none"] >= 1

    def test_branch_first_rvalue_reseat_stays_ast(self):
        # BOUNDARY: the rebind slot the reseat writes is pre-declared at
        # FUNCTION top, which a decl lowering in position cannot place.
        src = _NODE + (
            "def pick(flag: bool, k: Int32) -> Int32:\n"
            "    if flag:\n"
            "        sel: Node | None = None\n"
            "        if k > 0:\n"
            "            sel = Node(k)\n"
            "        if sel is not None:\n"
            "            return sel.x\n"
            "        return -1\n"
            "    return 0\n"
            "def main() -> None:\n"
            "    print(pick(True, 3))\n"
            "main()\n"
        )
        _ctx, fell = _thir_ctx(src)
        _assert_rejects_at(fell, "body:stmt.var_decl",
                           "decl.branch_slot_type")

    def test_branch_first_rvalue_init_stays_ast(self):
        # BOUNDARY: an F1-record rvalue init carries its own storage line.
        src = _NODE + (
            "def pick(flag: bool, k: Int32) -> Int32:\n"
            "    if flag:\n"
            "        sel: Node | None = Node(k)\n"
            "        if sel is not None:\n"
            "            return sel.x\n"
            "        return -1\n"
            "    return 0\n"
            "def main() -> None:\n"
            "    print(pick(True, 3))\n"
            "main()\n"
        )
        _ctx, fell = _thir_ctx(src)
        _assert_rejects_at(fell, "body:stmt.var_decl",
                           "decl.branch_slot_type")
