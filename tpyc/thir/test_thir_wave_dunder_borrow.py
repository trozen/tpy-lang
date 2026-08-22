"""Pins for the borrow-alias rows an operator dunder opens:

  - the DECL row -- `c = a + b` / `c = -a` off a dunder whose return is a
    reference bind the operand's lvalue (`const Acc& c = ((a) + (b));`), the
    operator flavor of the already-landed borrow-CALL decl;
  - the RESEAT row -- the same source at a HOISTED pointer local (both `if`
    arms write the name) takes the address instead: `c = &(((a) + (b)));`,
    the operator flavor of `reseat.borrow_call`;
  - the RETURN row -- an lvalue ternary at a record borrow slot
    (`return ((c) ? ((*this)) : (o));`), admitted shape-shallow so the arm
    shapes stay `ifexpr.record`'s business.

The const half is its own key: `_f1_is_const`'s dunder arms read the RAW
`fi.is_readonly`, because an arithmetic dunder's readonly-ness is normally
IMPLICIT (`IMPLICIT_READONLY_METHODS`) and so never surfaces as a
`ReadonlyType` on the init. `@readonly(False)` opts out and must bind
non-const -- the discriminating unit for that key.

The reseat row's const half is NOT that key: the branch pre-decl reads sema's
`stmt_borrow_decls`, which carries its own dunder arms, so `@readonly(False)`
discriminates there too but through a different path.

The `Own[...]`-returning dunder is the inverse pole of the same
discriminator (`is_rvalue_source`): a fresh value, never an alias, so its
decl is the plain copy and its branch-hoisted reseat stores through the
rebind slot. Boundary units hold the shapes that must keep rejecting: a
REASSIGNED local off a borrow dunder (POINTER, not REF_ALIAS), a `ValueType`
record's dunder (a by-value return -- no pointer local at all), a subclass
return at a base-annotated slot (source and pointee types differ), a ternary
at an `Own[...]` STORAGE return, and a ternary with a FIELD-access arm (no
`ifexpr.record` render)."""

from __future__ import annotations

from .testutil import (_assert_routes_byte_identical, _fn,
                       _lower_ctx_witnessed, _thir_ctx)

# A record whose dunders hand out an operand ALIAS. `__add__` is
# implicitly readonly, so its friend shim const-projects to `const Acc&`.
_ACC = (
    "from tpy import Int32\n"
    "class Acc:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n"
    "        self.n = n\n"
    "    def __add__(self, o: Acc) -> Acc:\n"
    "        return self if self.n >= o.n else o\n"
    "    def __neg__(self) -> Acc:\n"
    "        return self\n"
)


class TestDunderBorrowDecl:
    def test_binop_dunder_binds_const_alias(self):
        src = _ACC + (
            "def use() -> None:\n"
            "    a = Acc(3)\n"
            "    b = Acc(1)\n"
            "    c = a + b\n"
            "    print(c.n)\n"
            "use()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "const Acc& c = ((a) + (b));" in hpp + cpp
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert faces["decl.dunder_borrow_alias"] >= 1

    def test_unaryop_dunder_binds_const_alias(self):
        src = _ACC + (
            "def use() -> None:\n"
            "    a = Acc(6)\n"
            "    c = -a\n"
            "    print(c.n)\n"
            "use()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "const Acc& c = -(a);" in hpp + cpp
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert faces["decl.dunder_borrow_alias"] >= 1

    def test_reflected_dunder_binds_const_alias(self):
        # The reflected shim (`operator+(int32_t, const Acc&)`) is the same
        # borrow return, reached with the record on the RIGHT.
        src = _ACC.replace(
            "    def __neg__",
            "    def __radd__(self, other: Int32) -> Acc:\n"
            "        return self\n"
            "    def __neg__") + (
            "def use() -> None:\n"
            "    a = Acc(4)\n"
            "    c = 7 + a\n"
            "    print(c.n)\n"
            "use()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "const Acc& c = ((7) + (a));" in hpp + cpp

    def test_readonly_declared_return_binds_const_alias(self):
        # The DECLARED `readonly[...]` flavor: const comes from the init's
        # raw sema type (a ReadonlyType), not from the `fi.is_readonly`
        # arms -- the two const paths this row must both satisfy.
        src = (
            "from tpy import Int32, readonly\n"
            "class Acc:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "    def __add__(self, o: Acc) -> readonly[Acc]:\n"
            "        return self if self.n >= o.n else o\n"
            "def use() -> None:\n"
            "    a = Acc(3)\n"
            "    b = Acc(1)\n"
            "    c = a + b\n"
            "    print(c.n)\n"
            "use()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "const Acc& c = ((a) + (b));" in hpp + cpp

    def test_non_readonly_dunder_binds_mutable_alias(self):
        # The discriminating unit for the const KEY: `@readonly(False)` opts
        # the dunder out of IMPLICIT_READONLY_METHODS, so the shim returns a
        # MUTABLE `Acc&` and the alias must bind non-const.
        src = (
            "from tpy import Int32, readonly\n"
            "class Acc:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "    @readonly(False)\n"
            "    def __add__(self, o: Acc) -> Acc:\n"
            "        self.n = self.n + o.n\n"
            "        return self\n"
            "def use() -> None:\n"
            "    a = Acc(3)\n"
            "    b = Acc(1)\n"
            "    c = a + b\n"
            "    c.n = 42\n"
            "    print(a.n)\n"
            "use()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "Acc& c = ((a) + (b));" in hpp + cpp
        assert "const Acc& c = " not in hpp + cpp

    def test_container_returning_dunder_binds_container_alias(self):
        # The `_alias_ref_container` half of the row: a `list[...]` return is
        # a reference too, so the alias binds the vector.
        src = (
            "from tpy import Int32\n"
            "class Bag:\n"
            "    items: list[Int32]\n"
            "    def __init__(self) -> None:\n"
            "        self.items = [1, 2, 3]\n"
            "    def __neg__(self) -> list[Int32]:\n"
            "        return self.items\n"
            "def use() -> None:\n"
            "    a = Bag()\n"
            "    ys = -a\n"
            "    print(len(ys))\n"
            "use()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "const std::vector<int32_t>& ys = -(a);" in hpp + cpp

    def test_own_returning_dunder_decls_a_plain_value(self):
        # The INVERSE of the alias row: an `Own[...]` return is a FRESH
        # value, so the classifier never reaches REF_ALIAS and the decl is
        # the plain spelled copy (`_rvalue_storage_decl_op`).
        src = (
            "from tpy import Int32, Own\n"
            "class Acc:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "    def __neg__(self) -> Own[Acc]:\n"
            "        return Acc(-self.n)\n"
            "def use() -> None:\n"
            "    a = Acc(3)\n"
            "    c = -a\n"
            "    print(c.n)\n"
            "use()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "Acc c = -(a);" in hpp + cpp
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces["decl.rvalue_storage_unary"] >= 1

    def test_reassigned_dunder_local_keeps_rejecting(self):
        # BOUNDARY: a reassigned alias classifies POINTER (it must reseat),
        # and this row is REF_ALIAS-only. The reject is the DECL rung's, one
        # site ABOVE the reseat row that later widened -- a straight-line
        # reassign never reaches the `&(...)` lift, so the two stay separable.
        src = _ACC + (
            "def use() -> None:\n"
            "    a = Acc(3)\n"
            "    b = Acc(1)\n"
            "    c = a + b\n"
            "    print(c.n)\n"
            "    c = -a\n"
            "    print(c.n)\n"
            "use()\n"
        )
        _ctx, fell = _thir_ctx(src)
        assert fell == {"body:stmt.var_decl:decl.slot_type": 1}, fell


class TestRecordTernaryBorrowReturn:
    def test_self_and_param_arms_route(self):
        # The drilled shape: `self` derefs to `(*this)`, the param arm is
        # bare, and the lvalue ternary binds the `Acc&` return.
        src = _ACC + (
            "def use() -> None:\n"
            "    a = Acc(3)\n"
            "    b = Acc(1)\n"
            "    print((a + b).n)\n"
            "use()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "return (((this->n >= o.n)) ? ((*this)) : (o));" in hpp + cpp
        thir, faces = _lower_ctx_witnessed(src)
        assert faces["ret.record_ifexpr"] >= 1
        assert faces["ifexpr.record"] >= 1

    def test_borrow_call_arms_route(self):
        # The other arm shape `ifexpr.record` admits: both arms are
        # `T&`-returning calls, so the ternary stays an lvalue.
        src = (
            "from tpy import Int32\n"
            "class Acc:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "def left(a: Acc, b: Acc) -> Acc:\n"
            "    return a\n"
            "def right(a: Acc, b: Acc) -> Acc:\n"
            "    return b\n"
            "def pick(a: Acc, b: Acc, flag: bool) -> Acc:\n"
            "    return left(a, b) if flag else right(a, b)\n"
            "def use() -> None:\n"
            "    a = Acc(3)\n"
            "    b = Acc(1)\n"
            "    print(pick(a, b, True).n)\n"
            "use()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert ("return ((flag) ? (left(a, b)) : (right(a, b)));"
                in hpp + cpp)
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "pick") is not None
        assert faces["ret.record_ifexpr"] >= 1

    def test_field_arm_keeps_rejecting(self):
        # BOUNDARY: the return admission is shape-shallow on purpose -- a
        # FIELD-access arm has no `ifexpr.record` render, so the body still
        # falls back at the ternary lowering, not at the return.
        src = (
            "from tpy import Int32\n"
            "class Inner:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "class Outer:\n"
            "    inner: Inner\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.inner = Inner(n)\n"
            "    def pick(self, o: Inner) -> Inner:\n"
            "        return self.inner if self.inner.n >= o.n else o\n"
            "def use() -> None:\n"
            "    a = Outer(3)\n"
            "    b = Inner(1)\n"
            "    print(a.pick(b).n)\n"
            "use()\n"
        )
        _ctx, fell = _thir_ctx(src)
        assert fell == {"body:expr.ifexpr": 1}, fell

    def test_ternary_at_own_storage_return_keeps_rejecting(self):
        # BOUNDARY: the row is gated on `ret_record_borrow`, so the by-value
        # `Own[...]` direction keeps its own reject.
        src = (
            "from tpy import Int32, Own\n"
            "class Acc:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "def pick(flag: bool) -> Own[Acc]:\n"
            "    return Acc(1) if flag else Acc(2)\n"
            "def use() -> None:\n"
            "    print(pick(True).n)\n"
            "use()\n"
        )
        _ctx, fell = _thir_ctx(src)
        assert fell == {
            "body:stmt.return:return.record_source.TpyIfExpr.storage": 1}, fell


# The branch-hoisted flavor of `_ACC`: writing the same name in both arms
# pre-declares the pointer at the `if` head, so each arm RESEATS it.
_PICK = (
    "def pick(flag: bool) -> None:\n"
    "    a = Acc(3)\n"
    "    b = Acc(7)\n"
    "    if flag:\n"
    "        c = a + b\n"
    "    else:\n"
    "        c = -b\n"
    "    print(c.n)\n"
    "    a.n = 9\n"
    "    print(c.n)\n"
    "pick(True)\n"
)


class TestDunderBorrowReseat:
    def test_branch_hoisted_binop_and_unaryop_reseat_routes(self):
        # The drilled shape: a borrow-returning dunder's result is an operand
        # lvalue, so the hoisted pointer takes its address instead of routing
        # through the rebind slot an rvalue would need.
        src = _ACC + _PICK
        hpp, cpp = _assert_routes_byte_identical(src)
        out = hpp + cpp
        assert "const Acc* c;" in out
        assert "c = &(((a) + (b)));" in out
        assert "c = &(-(b));" in out
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "pick") is not None
        assert faces["reseat.dunder_borrow"] >= 2

    def test_reflected_dunder_reseat_routes(self):
        # The reflected shim (`operator+(int32_t, const Acc&)`) is the same
        # borrow return, reached with the record on the RIGHT.
        src = _ACC.replace(
            "    def __neg__",
            "    def __radd__(self, other: Int32) -> Acc:\n"
            "        return self\n"
            "    def __neg__") + _PICK.replace("c = a + b", "c = 7 + a")
        hpp, cpp = _assert_routes_byte_identical(src)
        out = hpp + cpp
        assert "c = &(((7) + (a)));" in out
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "pick") is not None
        assert faces["reseat.dunder_borrow"] >= 2

    def test_non_readonly_dunder_reseats_non_const_pointer(self):
        # The const KEY's discriminator, reseat side: `@readonly(False)` opts
        # the dunder out of IMPLICIT_READONLY_METHODS, so the shim hands back
        # a MUTABLE `Acc&` and the branch pre-decl must be a non-const `Acc*`.
        src = (
            "from tpy import Int32, readonly\n"
            "class Acc:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "    @readonly(False)\n"
            "    def __add__(self, o: Acc) -> Acc:\n"
            "        self.n = self.n + o.n\n"
            "        return self\n"
            "    @readonly(False)\n"
            "    def __neg__(self) -> Acc:\n"
            "        self.n = -self.n\n"
            "        return self\n"
            "def pick(flag: bool) -> None:\n"
            "    a = Acc(3)\n"
            "    b = Acc(7)\n"
            "    if flag:\n"
            "        c = a + b\n"
            "    else:\n"
            "        c = -b\n"
            "    c.n = 42\n"
            "    print(a.n)\n"
            "pick(True)\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        out = hpp + cpp
        assert "Acc* c;" in out
        assert "const Acc* c;" not in out
        assert "c = &(((a) + (b)));" in out
        thir, faces = _lower_ctx_witnessed(src)
        assert faces["reseat.dunder_borrow"] >= 2

    def test_container_returning_dunder_reseat_routes(self):
        # The `list[...]` twin: a container return is a reference too, so the
        # hoisted pointer is a `std::vector<...>*` taking the same address-of.
        src = (
            "from tpy import Int32\n"
            "class Bag:\n"
            "    items: list[Int32]\n"
            "    def __init__(self, k: Int32) -> None:\n"
            "        self.items = [k, k + 1]\n"
            "    def __neg__(self) -> list[Int32]:\n"
            "        return self.items\n"
            "    def __invert__(self) -> list[Int32]:\n"
            "        return self.items\n"
            "def pick(flag: bool) -> None:\n"
            "    a = Bag(1)\n"
            "    b = Bag(5)\n"
            "    if flag:\n"
            "        ys = -a\n"
            "    else:\n"
            "        ys = ~b\n"
            "    print(len(ys))\n"
            "pick(True)\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        out = hpp + cpp
        assert "const std::vector<int32_t>* ys;" in out
        assert "ys = &(-(a));" in out
        assert "ys = &(~(b));" in out
        thir, faces = _lower_ctx_witnessed(src)
        assert faces["reseat.dunder_borrow"] >= 2

    def test_own_returning_dunder_reseats_through_the_slot(self):
        # The INVERSE of the address-of row: an `Own[...]` return is a fresh
        # value -- addressing it would point at a temp. It is
        # `is_rvalue_source`-True, so the rebind-slot rung ABOVE this row
        # claims it and stores through the slot instead.
        src = (
            "from tpy import Int32, Own\n"
            "class Acc:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "    def __add__(self, o: Acc) -> Own[Acc]:\n"
            "        return Acc(self.n + o.n)\n"
            "    def __neg__(self) -> Own[Acc]:\n"
            "        return Acc(-self.n)\n"
        ) + _PICK.replace("    a.n = 9\n", "")
        hpp, cpp = _assert_routes_byte_identical(src)
        out = hpp + cpp
        assert "std::optional<Acc> __slot_1;" in out
        assert "c = &*(__slot_1 = ((a) + (b)));" in out
        assert "c = &*(__slot_1 = -(b));" in out
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces["reseat.rvalue_op"] >= 2

    def test_subclass_return_at_base_slot_keeps_rejecting(self):
        # BOUNDARY: the row's type guard. A dunder returning a SUBCLASS bound
        # to a base-annotated local has a source type that differs from the
        # declared pointee; the AST widens the pointer on assignment, this row
        # declines rather than mirror the implicit conversion.
        src = (
            "from tpy import Int32\n"
            "class Base:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "class Sub(Base):\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        Base.__init__(self, n)\n"
            "class Holder:\n"
            "    s: Sub\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.s = Sub(n)\n"
            "    def __neg__(self) -> Sub:\n"
            "        return self.s\n"
            "    def __invert__(self) -> Sub:\n"
            "        return self.s\n"
            "def pick(flag: bool) -> None:\n"
            "    h = Holder(3)\n"
            "    g = Holder(9)\n"
            "    if flag:\n"
            "        c: Base = -h\n"
            "    else:\n"
            "        c: Base = ~g\n"
            "    print(c.n)\n"
            "pick(True)\n"
        )
        _ctx, fell = _thir_ctx(src)
        assert fell == {"body:stmt.var_decl:decl.reseat_source": 1}, fell

    def test_value_type_record_dunder_takes_the_value_hoist(self):
        # BOUNDARY: a ValueType record's dunder returns BY VALUE
        # (`call_returns_cpp_ref` is False), so there is no pointer local at
        # all -- the branch hoist is a plain `Acc c;` and this row must not
        # fire. The whole body still routes, via the value-hoist rung.
        src = (
            "from tpy import Int32, ValueType\n"
            "class Acc(ValueType):\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "    def __add__(self, o: Acc) -> Acc:\n"
            "        return Acc(self.n + o.n)\n"
            "    def __neg__(self) -> Acc:\n"
            "        return Acc(-self.n)\n"
        ) + _PICK.replace("    a.n = 9\n", "")
        hpp, cpp = _assert_routes_byte_identical(src)
        out = hpp + cpp
        assert "Acc c;" in out
        assert "c = ((a) + (b));" in out
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("reseat.dunder_borrow", 0) == 0
