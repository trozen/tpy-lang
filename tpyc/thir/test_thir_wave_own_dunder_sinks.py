"""Pins for the sinks an Own-RETURNING operator dunder reaches.

The binop/unary EXPRESSION already routed everywhere it was witnessed; what
these units cover is the statement-level admissions that consume its result:

  - the STORAGE return (`return p + q` / `return -p` at an `Own[Vec]` slot);
  - the F1-record FIELD operand (`return self.a + self.b`), which reads the
    bare member into the operator parens;
  - a POINTER-bound record operand, which derefs (`((*v)) + (inc)`) because
    the AST renders both arithmetic operands through `gen_expr_deref`;
  - the in-place dunder's record RVALUE value (`v += Vec(9)` ->
    `v.__iadd__(Vec(9));`);
  - the aug FALLBACK for a record with NO `__iadd__`, where sema resolves the
    Own-returning `__add__` and the AST emits `a = (a) + (b);`.

`is_rvalue_source` -- the dunder's own return convention -- is the whole
discriminator at the return sink: a BORROW-returning dunder aliases an
operand and keeps rejecting there (the decl/reseat poles of the same split
live in test_thir_wave_dunder_borrow.py).
"""

from __future__ import annotations

from .testutil import (
    _assert_rejects_at,
    _reject_tally, _assert_routes_byte_identical, _lower_ctx_witnessed,
                       _thir_ctx)

# Own-returning dunders: every result is a FRESH record.
_VEC = (
    "from tpy import Int32, Own\n"
    "class Vec:\n"
    "    x: Int32\n"
    "    def __init__(self, x: Int32) -> None:\n"
    "        self.x = x\n"
    "    def __add__(self, o: Vec) -> Own[Vec]:\n"
    "        return Vec(self.x + o.x)\n"
    "    def __neg__(self) -> Own[Vec]:\n"
    "        return Vec(-self.x)\n"
    "    def __iadd__(self, o: Vec) -> Vec:\n"
    "        self.x += o.x\n"
    "        return self\n"
)


class TestOwnDunderStorageReturn:
    def test_binop_and_unary_return_bare(self):
        src = _VEC + (
            "def add2(p: Vec, q: Vec) -> Own[Vec]:\n"
            "    return p + q\n"
            "def neg1(p: Vec) -> Own[Vec]:\n"
            "    return -p\n"
            "def use() -> None:\n"
            "    print(add2(Vec(3), Vec(4)).x)\n"
            "    print(neg1(Vec(5)).x)\n"
            "use()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        out = hpp + cpp
        assert "return ((p) + (q));" in out
        assert "return -(p);" in out
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces["ret.record_op_storage"] >= 2

    def test_record_field_operands_read_bare(self):
        # The operand half: an F1-record FIELD renders the plain member read
        # into the operator parens (the compare arm's field row, one operator
        # family over).
        src = _VEC + (
            "class Seg:\n"
            "    a: Vec\n"
            "    b: Vec\n"
            "    def __init__(self, a: Vec, b: Vec) -> None:\n"
            "        self.a = a\n"
            "        self.b = b\n"
            "    def total(self) -> Own[Vec]:\n"
            "        return self.a + self.b\n"
            "def use() -> None:\n"
            "    print(Seg(Vec(1), Vec(2)).total().x)\n"
            "use()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "return ((this->a) + (this->b));" in hpp + cpp

    def test_pointer_bound_record_operand_derefs(self):
        # A branch-hoisted record local is a `Vec*`; the AST reads both
        # operands through gen_expr_deref, so the operand spells `(*v)` --
        # not the bare arrow-receiver form a record pointer takes elsewhere.
        src = _VEC + (
            "def use(flag: bool) -> None:\n"
            "    v = Vec(1)\n"
            "    inc = Vec(2)\n"
            "    if flag:\n"
            "        v = v + inc\n"
            "    else:\n"
            "        v = inc + v\n"
            "    print(v.x)\n"
            "use(True)\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        out = hpp + cpp
        assert "v = &*(__slot_2 = (((*v)) + (inc)));" in out
        assert "v = &*(__slot_2 = ((inc) + ((*v))));" in out

    def test_borrow_returning_dunder_return_keeps_rejecting(self):
        # BOUNDARY: the discriminator. A dunder returning the operand ALIAS
        # is not an rvalue source, so the Own[...] slot's copy-from-reference
        # render stays on the AST path.
        src = (
            "from tpy import Int32, Own\n"
            "class Acc:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "    def __add__(self, o: Acc) -> Acc:\n"
            "        return self if self.n >= o.n else o\n"
            "def bigger(a: Acc, b: Acc) -> Own[Acc]:\n"
            "    return a + b\n"
            "def use() -> None:\n"
            "    print(bigger(Acc(3), Acc(1)).n)\n"
            "use()\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.return:return.record_source.TpyBinOp.storage")


class TestInplaceDunderRecordValue:
    def test_ctor_rvalue_value_routes(self):
        src = _VEC + (
            "def use() -> None:\n"
            "    v = Vec(1)\n"
            "    v += Vec(9)\n"
            "    print(v.x)\n"
            "use()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "v.__iadd__(Vec(9));" in hpp + cpp
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces["aug.inplace_dunder"] >= 1

    def test_subscript_lvalue_value_keeps_rejecting(self):
        # BOUNDARY: the leg is keyed on the record RVALUE shape. A container
        # element read is an lvalue no admitted row covers.
        src = _VEC + (
            "def use(xs: list[Vec]) -> None:\n"
            "    v = Vec(1)\n"
            "    v += xs[0]\n"
            "    print(v.x)\n"
            "use([Vec(2)])\n"
        )
        _assert_rejects_at(_reject_tally(src), "body:stmt.aug_assign")


class TestRecordAugBinopFallback:
    # `Fresh` has no `__iadd__`, so `+=` falls back to the Own-returning
    # `__add__`. (A BORROW-returning fallback is a sema ERROR -- "the in-place
    # update would copy where CPython rebinds" -- so that pole is not
    # constructible and the row's return-convention guard is defensive.)
    _FRESH = (
        "from tpy import Int32, Own\n"
        "class Fresh:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32) -> None:\n"
        "        self.n = n\n"
        "    def __add__(self, o: Fresh) -> Own[Fresh]:\n"
        "        return Fresh(self.n + o.n)\n"
    )

    def test_name_target_routes(self):
        src = self._FRESH + (
            "def use() -> None:\n"
            "    a = Fresh(1)\n"
            "    b = Fresh(2)\n"
            "    a += b\n"
            "    print(a.n)\n"
            "use()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        # ONE paren layer fewer than the decl render: the tail substitutes
        # the target into both slots of the synthetic assign.
        assert "a = (a) + (b);" in hpp + cpp
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces["aug.record_binop"] >= 1

    def test_record_field_target_keeps_rejecting(self):
        # BOUNDARY: the row admits the bare local only -- a field lvalue has
        # to render identically on both sides of the substitution, which is
        # the scalar row's own (separately gated) business.
        src = self._FRESH + (
            "class Box:\n"
            "    acc: Fresh\n"
            "    def __init__(self, acc: Fresh) -> None:\n"
            "        self.acc = acc\n"
            "def use() -> None:\n"
            "    bx = Box(Fresh(1))\n"
            "    b = Fresh(2)\n"
            "    bx.acc += b\n"
            "    print(bx.acc.n)\n"
            "use()\n"
        )
        _assert_rejects_at(_reject_tally(src), "body:stmt.aug_assign")
