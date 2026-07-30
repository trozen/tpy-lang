"""Reference-select wave arms: container / F1-record and-or with Python
operand semantics (`_gen_logical_value`'s non-value slice) and the
container ternary -- the `T&` REF_ALIAS decl over lvalue operands, the
hoisted-`__logical_slot` pointer-select for an rvalue RHS, and the
all-rvalue plain-copy decl."""

from __future__ import annotations

from .testutil import (
    _lower_ctx, _lower_ctx_witnessed, _fn, _assert_byte_identical,
)

_BOX = (
    "from tpy import Int32\n"
    "class Box:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n"
    "        self.n = n\n"
)


class TestContainerSelect:
    def test_name_or_routes_len_truthy_alias(self):
        # x = a or b -> `std::vector<int32_t>& x = ((::tpy::__len__(a) != 0)
        # ? a : b);` -- the __len__-truthy ternary aliasing the chosen side.
        src = ("from tpy import Int32\n"
               "def f(a: list[Int32], b: list[Int32]) -> None:\n"
               "    x = a or b\n"
               "    x.append(9)\n"
               "    print(len(a), len(b))\n"
               "def main() -> None:\n    f([1], [2])\nmain()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("binop.container_select", 0) == 1
        cpp = _assert_byte_identical(src)
        assert ("std::vector<int32_t>& x = "
                "((::tpy::__len__(a) != 0) ? a : b);" in cpp[1])

    def test_chain_hoists_inner_select_temp(self):
        # x = a or b or c -- the inner select hoists as the AST's
        # `auto&& __tmp_N` LHS temp ahead of the alias decl.
        src = ("from tpy import Int32\n"
               "def f(a: list[Int32], b: list[Int32], c: list[Int32]) -> None:\n"
               "    x = a or b or c\n"
               "    x.append(9)\n"
               "    print(len(a), len(b), len(c))\n"
               "def main() -> None:\n    f([1], [2], [3])\nmain()\n")
        assert _fn(_lower_ctx(src), "f") is not None
        cpp = _assert_byte_identical(src)
        assert "auto&& __tmp_1 = ((::tpy::__len__(a) != 0) ? a : b);" in cpp[1]
        assert ("std::vector<int32_t>& x = "
                "((::tpy::__len__(__tmp_1) != 0) ? __tmp_1 : c);" in cpp[1])

    def test_ternary_lvalue_arms_alias(self):
        # x = a if cond else b -> `std::vector<int32_t>& x = ((cond) ? (a)
        # : (b));` (the container ternary render, REF_ALIAS-bound).
        src = ("from tpy import Int32\n"
               "def f(a: list[Int32], b: list[Int32], cond: bool) -> None:\n"
               "    x = a if cond else b\n"
               "    x.append(9)\n"
               "    print(len(a), len(b))\n"
               "def main() -> None:\n    f([1], [2], True)\nmain()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("ifexpr.container", 0) == 1
        cpp = _assert_byte_identical(src)
        assert "std::vector<int32_t>& x = ((cond) ? (a) : (b));" in cpp[1]

    def test_rvalue_rhs_takes_logical_slot(self):
        # An rvalue RHS materializes lazily into the hoisted optional slot;
        # the select stays an lvalue (`*ptr`) so the alias still binds.
        src = ("from tpy import Int32\n"
               "def f(a: list[Int32]) -> None:\n"
               "    x = a or [Int32(9)]\n"
               "    x.append(6)\n"
               "    print(len(a))\n"
               "def main() -> None:\n    f([1])\nmain()\n")
        assert _fn(_lower_ctx(src), "f") is not None
        cpp = _assert_byte_identical(src)
        assert "std::optional<std::vector<int32_t>> __logical_slot_1;" in cpp[1]
        # The literal here resolves ARRAY (never mutated itself), so the
        # emplace arg renders the bare brace-init, target-blind like the AST.
        assert ("std::vector<int32_t>& x = (*((::tpy::__len__(a) != 0) ? "
                "&(a) : (__logical_slot_1.emplace({9}), "
                "&*__logical_slot_1)));" in cpp[1])

    def test_all_rvalue_or_copies_out(self):
        # Both operands rvalue -> the select is an rvalue and the decl is
        # the plain spelled copy of the pointer-select deref.
        src = ("from tpy import Int32\n"
               "def f() -> None:\n"
               "    x = [Int32(1), Int32(2)] or [Int32(3)]\n"
               "    print(x)\n"
               "f()\n")
        assert _fn(_lower_ctx(src), "f") is not None
        cpp = _assert_byte_identical(src)
        assert "auto&& __tmp_1 = std::vector<int32_t>{1, 2};" in cpp[1]
        assert "std::vector<int32_t> x = (*((::tpy::__len__(__tmp_1)" in cpp[1]

    def test_all_rvalue_ternary_spells_list_arms(self):
        # Literal ternary arms spell their type (a bare brace-init cannot
        # deduce in ternary context); the decl is the plain copy.
        src = ("from tpy import Int32\n"
               "def f(cond: bool) -> None:\n"
               "    x = [Int32(1)] if cond else [Int32(2)]\n"
               "    print(x)\n"
               "f(True)\n")
        assert _fn(_lower_ctx(src), "f") is not None
        cpp = _assert_byte_identical(src)
        assert ("std::vector<int32_t> x = ((cond) ? "
                "(std::vector<int32_t>{1}) : (std::vector<int32_t>{2}));"
                in cpp[1])


class TestRecordSelect:
    def test_record_rvalue_rhs_short_circuits(self):
        # A plain user record LHS folds truthy to `true`; the ctor RHS is
        # constructed only when its branch is chosen (the emplace arm).
        src = (_BOX
               + "def f(a: Box) -> None:\n"
               + "    c = a or Box(9)\n"
               + "    c.n = 99\n"
               + "    print(a.n)\n"
               + "def main() -> None:\n    f(Box(3))\nmain()\n")
        assert _fn(_lower_ctx(src), "f") is not None
        cpp = _assert_byte_identical(src)
        assert "std::optional<Box> __logical_slot_1;" in cpp[1]
        assert ("Box& c = (*(true ? &(a) : "
                "(__logical_slot_1.emplace(Box(9)), &*__logical_slot_1)));"
                in cpp[1])

    def test_bool_dunder_record_stays_ast(self):
        # A record with __bool__ takes the ::tpy::__bool__ truthy render --
        # unwitnessed, the body must keep falling back.
        src = ("from tpy import Int32\n"
               "class B:\n"
               "    n: Int32\n"
               "    def __init__(self, n: Int32) -> None:\n"
               "        self.n = n\n"
               "    def __bool__(self) -> bool:\n"
               "        return self.n > 0\n"
               "def f(a: B, b: B) -> None:\n"
               "    c = a or b\n"
               "    print(c.n)\n"
               "def main() -> None:\n    f(B(1), B(2))\nmain()\n")
        assert _fn(_lower_ctx(src), "f") is None
        _assert_byte_identical(src)

    def test_call_lhs_operand_stays_ast(self):
        # A call-expression LHS is outside the Name/nested-select/literal
        # operand slice -- keeps falling back.
        src = ("from tpy import Int32\n"
               "def pick(a: list[Int32]) -> list[Int32]:\n"
               "    return a\n"
               "def f(a: list[Int32], b: list[Int32]) -> None:\n"
               "    x = pick(a) or b\n"
               "    x.append(6)\n"
               "    print(len(a), len(b))\n"
               "def main() -> None:\n    f([1], [2])\nmain()\n")
        assert _fn(_lower_ctx(src), "f") is None
        _assert_byte_identical(src)

    def test_reassigned_select_target_stays_ast(self):
        # A REASSIGNED select target is the pointer-local reseat rung the
        # slice does not carry -- keeps falling back.
        src = ("from tpy import Int32\n"
               "def f(a: list[Int32], b: list[Int32]) -> None:\n"
               "    x = a or b\n"
               "    x = b\n"
               "    x.append(5)\n"
               "    print(len(a), len(b))\n"
               "def main() -> None:\n    f([1], [2])\nmain()\n")
        assert _fn(_lower_ctx(src), "f") is None
        _assert_byte_identical(src)
