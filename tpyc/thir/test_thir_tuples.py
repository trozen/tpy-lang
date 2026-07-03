"""THIR F3 tuple rungs (tuple_to_pointer / tuple_to_storage) + tuple
subscript reads/writes."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from ..codegen_cpp.forms import LocalBinding
from .dump import dump_thir
from .nodes import (
    Form, THIRAssign, THIRBinOp, THIRFieldAccess, THIRFormConvert, THIRName,
    THIRReturn, THIRSubscript, THIRVarDecl,
)
from .testutil import (
    _compile, _entry, _lower, _lower_ctx, _fn, _lower_ctor, _PRELUDE,
)

# --- F3 form rung: storage->borrow tuple read (tuple_to_pointer) ---

# A record with a pointer-repr tuple field: storage form `std::tuple<int32_t,
# Leaf>`, borrow form `std::tuple<int32_t, Leaf*>`.
_F3_RECORDS = (
    "from tpy import Int32, readonly\n"
    "class Leaf:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32):\n        self.n = n\n"
    "class Holder:\n"
    "    pair: tuple[Int32, Leaf]\n"
    "    def __init__(self, b: Leaf):\n        self.pair = (1, b)\n"
)


class TestF3TupleReturn:
    def test_borrow_tuple_return_routes(self):
        # `return h.pair` lifts the storage tuple field into the borrow-form tuple
        # return via a STORAGE->BORROW convert (the tuple_to_pointer family).
        thir = _lower_ctx(
            _F3_RECORDS
            + "def ret_field(h: Holder) -> tuple[Int32, Leaf]:\n    return h.pair\n")
        fn = _fn(thir, "ret_field")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret, THIRReturn)
        assert isinstance(ret.value, THIRFormConvert) and ret.value.form is Form.BORROW
        assert ret.value.is_const is False  # mutable receiver -> mutable Leaf* elements
        assert ret.value.value.form is Form.STORAGE  # the h.pair storage read

    def test_value_tuple_return_is_ineligible(self):
        # An all-value-scalar tuple has no pointer-repr element (borrow == storage),
        # so no tuple_to_pointer lift applies -- it stays on the AST path.
        thir = _lower_ctx(
            _F3_RECORDS
            + "def ret_pair(h: Holder) -> tuple[Int32, Int32]:\n    return (1, 2)\n")
        assert _fn(thir, "ret_pair") is None

    def test_tuple_field_init_ctor_stays_on_ast_path(self):
        # Regression guard for the M3 ctor-frontier fix: `Holder.__init__` does
        # `self.pair = (1, b)` -- a leading own-field init of an F3+ tuple type the
        # AST hoists into the member-init-list but THIR cannot reproduce there. It
        # must REJECT the whole ctor (return None, AST path) rather than demote the
        # init into the body, which would diverge from the AST's MIL hoist.
        assert _lower_ctor(_F3_RECORDS, "Holder") is None

    def test_storage_tuple_alias_local_routes(self):
        # A storage-tuple alias local (`t = h.pair`) binds `auto&&` (a STORAGE-form
        # alias) and a `return t` lifts it via tuple_to_pointer like a direct field
        # source.
        thir = _lower_ctx(
            _F3_RECORDS
            + "def ret_alias(h: Holder) -> tuple[Int32, Leaf]:\n"
            + "    t = h.pair\n    return t\n")
        fn = _fn(thir, "ret_alias")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.STORAGE_TUPLE_ALIAS
        assert decl.form is Form.STORAGE
        ret = fn.body[1]
        assert isinstance(ret, THIRReturn) and isinstance(ret.value, THIRFormConvert)
        assert ret.value.form is Form.BORROW
        assert ret.value.value.form is Form.STORAGE  # the `t` alias read



class TestF3TupleReturnEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _F3_RECORDS
        + "def ret_field(h: Holder) -> tuple[Int32, Leaf]:\n    return h.pair\n"
        + "def main():\n    h = Holder(Leaf(5))\n    t = ret_field(h)\n    print(0)\n"
        + "main()\n"
    )

    def test_f3_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_tuple_to_pointer(self):
        assert ("return ::tpy::tuple_to_pointer<std::tuple<int32_t, Leaf*>>(h.pair);"
                in self._cpp(self.SRC, thir=True))

    ALIAS_SRC = (
        _F3_RECORDS
        + "def ret_alias(h: Holder) -> tuple[Int32, Leaf]:\n"
        + "    t = h.pair\n    return t\n"
        + "def main():\n    h = Holder(Leaf(5))\n    t = ret_alias(h)\n    print(0)\n"
        + "main()\n"
    )

    def test_alias_byte_identical(self):
        assert self._cpp(self.ALIAS_SRC, thir=True) == self._cpp(self.ALIAS_SRC, thir=False)

    def test_alias_emits_auto_ref(self):
        cpp = self._cpp(self.ALIAS_SRC, thir=True)
        assert "auto&& t = h.pair;" in cpp
        assert "return ::tpy::tuple_to_pointer<std::tuple<int32_t, Leaf*>>(t);" in cpp

    # A const (readonly) receiver makes the borrow tuple's element pointers const,
    # exercising the `to_cpp_return_const()` arm of the tuple_to_pointer lift -- for
    # both a direct field return and a storage-tuple alias local.
    CONST_SRC = (
        _F3_RECORDS
        + "def f(h: readonly[Holder]) -> tuple[Int32, readonly[Leaf]]:\n"
        + "    return h.pair\n"
        + "def g(h: readonly[Holder]) -> tuple[Int32, readonly[Leaf]]:\n"
        + "    t = h.pair\n    return t\n"
        + "def main():\n    h = Holder(Leaf(5))\n    a = f(h)\n    b = g(h)\n    print(0)\n"
        + "main()\n"
    )

    def test_const_byte_identical(self):
        assert self._cpp(self.CONST_SRC, thir=True) == self._cpp(self.CONST_SRC, thir=False)

    def test_const_receiver_emits_const_tuple_to_pointer(self):
        cpp = self._cpp(self.CONST_SRC, thir=True)
        # direct field return + alias local both lift with const element pointers.
        assert ("return ::tpy::tuple_to_pointer<std::tuple<int32_t, const Leaf*>>(h.pair);"
                in cpp)
        assert ("return ::tpy::tuple_to_pointer<std::tuple<int32_t, const Leaf*>>(t);"
                in cpp)

    def test_const_receiver_lowers_const_convert(self):
        thir = _lower_ctx(
            _F3_RECORDS
            + "def f(h: readonly[Holder]) -> tuple[Int32, readonly[Leaf]]:\n"
            + "    return h.pair\n")
        ret = _fn(thir, "f").body[0]
        assert isinstance(ret, THIRReturn) and isinstance(ret.value, THIRFormConvert)
        assert ret.value.is_const is True



# --- F3 form rung: borrow->storage tuple field write (tuple_to_storage) ---

# Records with a pointer-repr Optional-element tuple field: storage form
# `std::tuple<std::optional<T>, ...>`, borrow form `std::tuple<T*, ...>`.
_F3_OPT_RECORDS = (
    "from tpy import Int32\n"
    "class T:\n"
    "    x: Int32\n"
    "    def __init__(self, x: Int32) -> None:\n        self.x = x\n"
    "class Holder:\n"
    "    pair: tuple[T | None, T | None]\n"
    "    def __init__(self) -> None:\n        self.pair = (None, None)\n"
)


class TestF3TupleFieldWrite:
    def test_borrow_tuple_field_write_routes(self):
        # `self.pair = p` where p is a borrow tuple param lifts borrow->storage via
        # a STORAGE-form convert (the tuple_to_storage family); the receiver becomes
        # a written (non-const) self.
        thir = _lower_ctx(
            _F3_OPT_RECORDS
            + "class Setter:\n"
            + "    pair: tuple[T | None, T | None]\n"
            + "    def __init__(self) -> None:\n        self.pair = (None, None)\n"
            + "    def update(self, p: tuple[T | None, T | None]) -> None:\n"
            + "        self.pair = p\n")
        fn = _fn(thir, "update")
        assert fn is not None
        write = fn.body[0]
        assert isinstance(write, THIRAssign)
        assert isinstance(write.target, THIRFieldAccess) and write.target.field_cpp == "pair"
        assert isinstance(write.value, THIRFormConvert)
        assert write.value.form is Form.STORAGE and write.value.move is False
        assert write.value.value.form is Form.BORROW  # the borrow tuple param p

    def test_storage_source_field_write_is_ineligible(self):
        # A storage-form source (`other.pair`, a field read) is a direct copy with
        # no tuple_to_storage wrap -- a later F3 cell, so it stays on the AST path.
        thir = _lower_ctx(
            _F3_OPT_RECORDS
            + "def copy_from(h: Holder, other: Holder) -> None:\n"
            + "    h.pair = other.pair\n")
        assert _fn(thir, "copy_from") is None



class TestF3TupleFieldWriteEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _F3_OPT_RECORDS
        + "def upd(h: Holder, p: tuple[T | None, T | None]) -> None:\n"
        + "    h.pair = p\n"
        + "def main():\n    h = Holder()\n    t = T(1)\n    upd(h, (t, None))\n    print(0)\n"
        + "main()\n"
    )

    def test_f3_write_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_tuple_to_storage(self):
        assert ("h.pair = ::tpy::tuple_to_storage<std::tuple<std::optional<T>, "
                "std::optional<T>>>(p);" in self._cpp(self.SRC, thir=True))



# --- Statement-shape axis: value-result tuple subscript reads (std::get<N>) ---

# The first cell of the statement-shape axis. A value-scalar tuple param
# (`const std::tuple<...>&`, its signature emitted by the AST path) read by
# subscript routes its body; the value-scalar slot of an already-routed
# pointer-repr tuple reads the same way. Borrow-result (record / Optional) element
# reads stay on the AST path.
class TestTupleSubscriptRead:
    def test_value_tuple_param_subscript_routes(self):
        # `tuple[Int32, Int32]` is admitted as a param; `p[0]` / `p[1]` lower to
        # value-form THIRSubscript reads off the param name.
        thir = _lower(
            _PRELUDE
            + "def consume(p: tuple[Int32, Int32]) -> Int32:\n    return p[0] + p[1]\n")
        fn = _fn(thir, "consume")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret, THIRReturn) and isinstance(ret.value, THIRBinOp)
        left = ret.value.left
        assert isinstance(left, THIRSubscript) and left.index.value == 0
        assert left.form is Form.VALUE
        assert isinstance(left.receiver, THIRName) and left.receiver.name == "p"
        assert ret.value.right.index.value == 1

    def test_negative_index_normalized(self):
        # `p[-3]` on a 3-tuple folds to index 0; `p[-1]` to index 2 -- the AST's
        # _extract_compile_time_index normalization.
        thir = _lower(
            _PRELUDE
            + "def f(p: tuple[Int32, Int32, Int32]) -> Int32:\n    return p[-3] + p[-1]\n")
        ret = _fn(thir, "f").body[0]
        assert ret.value.left.index.value == 0
        assert ret.value.right.index.value == 2

    def test_value_scalar_slot_of_pointer_repr_tuple_routes(self):
        # The Int32 slot of a pointer-repr tuple `tuple[Int32, Leaf]` reads as a
        # plain std::get (value form), reusing the already-routed borrow receiver.
        thir = _lower_ctx(
            _F3_RECORDS
            + "def scalar_slot(t: tuple[Int32, Leaf]) -> Int32:\n    return t[0]\n")
        fn = _fn(thir, "scalar_slot")
        assert fn is not None
        sub = fn.body[0].value
        assert isinstance(sub, THIRSubscript) and sub.index.value == 0
        assert sub.form is Form.VALUE

    def test_value_scalar_tuple_local_is_ineligible(self):
        # Only value-tuple PARAMS are admitted in this cell; a value-tuple local
        # (`t = (1, 2)`) needs literal construction / init-source eligibility, a
        # later cell -- so a function building one stays on the AST path.
        thir = _lower(
            _PRELUDE
            + "def f() -> Int32:\n    t = (1, 2)\n    return t[0]\n")
        assert _fn(thir, "f") is None



class TestTupleSubscriptReadEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _PRELUDE
        + "def consume(p: tuple[Int32, Int32]) -> Int32:\n    return p[0] + p[1]\n"
        + "def main():\n    print(consume((1, 2)))\n"
        + "main()\n"
    )

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_std_get(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "std::get<0>(p)" in cpp and "std::get<1>(p)" in cpp



# The routing-heavy subscript cell: a record-element read `t[N].field`. The subscript
# yields a borrow -- `std::get<N>(t)->field` off a borrow-form tuple param, or
# `std::get<N>(t).field` off a storage `auto&&` alias. Optional-element member access
# (null-check path), standalone binds, and writes stay on the AST path.
class TestTupleSubscriptRecordRead:
    def test_record_element_via_borrow_param_routes(self):
        thir = _lower_ctx(
            _F3_RECORDS
            + "def via_param(t: tuple[Int32, Leaf]) -> Int32:\n    return t[1].n\n")
        fn = _fn(thir, "via_param")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret, THIRReturn) and isinstance(ret.value, THIRFieldAccess)
        assert ret.value.is_arrow  # borrow-tuple param -> std::get<1>(t) is a T*
        sub = ret.value.receiver
        assert isinstance(sub, THIRSubscript) and sub.index.value == 1
        assert sub.form is Form.BORROW

    def test_record_element_via_storage_alias_reads_dot(self):
        thir = _lower_ctx(
            _F3_RECORDS
            + "def via_alias(h: Holder) -> Int32:\n    a = h.pair\n    return a[1].n\n")
        fn = _fn(thir, "via_alias")
        assert fn is not None
        ret = fn.body[1]
        assert isinstance(ret.value, THIRFieldAccess)
        assert ret.value.is_arrow is False  # storage auto&& alias -> T&, dot access
        assert isinstance(ret.value.receiver, THIRSubscript)

    def test_element_form_drives_arrow_vs_dot(self):
        # Element form, not the receiver alone, decides `->` vs `.`: an Own element is
        # held by value (`std::get<0>(p).n`, dot), a bare-reference element is a borrow
        # pointer (`std::get<1>(p)->n`, arrow). Regression for the mixed owned+borrow
        # tuple (_tuple_subscript_yields_borrow_ptr mirror).
        thir = _lower_ctx(
            "from tpy import Own, Int32\n"
            "class A:\n    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n        self.n = n\n"
            "def f(p: tuple[Own[A], A]) -> Int32:\n    return p[0].n + p[1].n\n")
        fn = _fn(thir, "f")
        assert fn is not None
        add = fn.body[0].value
        assert isinstance(add.left, THIRFieldAccess) and add.left.is_arrow is False
        assert isinstance(add.right, THIRFieldAccess) and add.right.is_arrow is True

    def test_negative_index_and_multi_element_record_read(self):
        # A negative index on a record element, and a record at index 2 of a 3-tuple:
        # both normalize to std::get<2> and arrow-decide correctly.
        thir = _lower_ctx(
            _F3_RECORDS
            + "def f(t: tuple[Int32, Int32, Leaf]) -> Int32:\n"
            + "    return t[-1].n + t[2].n\n")
        fn = _fn(thir, "f")
        assert fn is not None
        add = fn.body[0].value
        assert isinstance(add.left, THIRFieldAccess) and add.left.is_arrow
        assert isinstance(add.left.receiver, THIRSubscript) and add.left.receiver.index.value == 2
        assert add.right.receiver.index.value == 2

    def test_readonly_tuple_record_read_routes(self):
        # A readonly[tuple[...]] receiver still reads a record element via `->` (the
        # const is carried in the param type, not the access) -- byte-identical.
        thir = _lower_ctx(
            _F3_RECORDS
            + "def f(t: readonly[tuple[Int32, Leaf]]) -> Int32:\n    return t[1].n\n")
        fn = _fn(thir, "f")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret.value, THIRFieldAccess) and ret.value.is_arrow
        assert isinstance(ret.value.receiver, THIRSubscript)

    def test_standalone_record_element_read_is_ineligible(self):
        # `b = t[1]` binds a record borrow local from a subscript -- the borrow-local
        # binding source path keeps its name-receiver gate, so this stays on AST.
        thir = _lower_ctx(
            _F3_RECORDS
            + "def f(t: tuple[Int32, Leaf]) -> Int32:\n    b = t[1]\n    return b.n\n")
        assert _fn(thir, "f") is None



class TestTupleSubscriptRecordReadEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _F3_RECORDS
        + "def via_param(t: tuple[Int32, Leaf]) -> Int32:\n    return t[1].n\n"
        + "def via_alias(h: Holder) -> Int32:\n    a = h.pair\n    return a[1].n\n"
        + "def main():\n    h = Holder(Leaf(5))\n"
        + "    print(via_param(h.pair) + via_alias(h))\n"
        + "main()\n"
    )

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_arrow_and_dot(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "return std::get<1>(t)->n;" in cpp   # borrow param
        assert "return std::get<1>(a).n;" in cpp     # storage alias



# The read frontier's tail: an unproven `Optional[record]`-element member access
# `t[N].field` -> `deref_check(...).field`. Off a borrow tuple the element is a nullable
# `T*`; off a storage alias it is `std::optional<T>` lifted to `T*` via optional_to_ptr.
class TestTupleSubscriptOptionalRead:
    def test_optional_element_via_borrow_param_routes(self):
        thir = _lower_ctx(
            _F3_OPT_RECORDS
            + "def f(t: tuple[T | None, T | None]) -> Int32:\n    return t[0].x\n")
        fn = _fn(thir, "f")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret.value, THIRFieldAccess) and ret.value.deref_check
        assert ret.value.is_arrow is False  # deref_check reads `.` after the checked deref
        assert isinstance(ret.value.receiver, THIRSubscript)  # already a T*, no lift

    def test_optional_element_via_storage_alias_lifts(self):
        # Off a storage auto&& alias the element is std::optional<T>, lifted to T* via a
        # STORAGE->BORROW optional_to_ptr convert before the deref_check.
        thir = _lower_ctx(
            _F3_OPT_RECORDS
            + "def f(h: Holder) -> Int32:\n    a = h.pair\n    return a[0].x\n")
        fn = _fn(thir, "f")
        assert fn is not None
        ret = fn.body[1]
        assert isinstance(ret.value, THIRFieldAccess) and ret.value.deref_check
        conv = ret.value.receiver
        assert isinstance(conv, THIRFormConvert) and conv.form is Form.BORROW
        assert isinstance(conv.value, THIRSubscript) and conv.value.form is Form.STORAGE

    def test_index_1_and_readonly_optional_route(self):
        # An Optional element at index 1, and a readonly[tuple] receiver, both route
        # (index normalization + the const deref_check/optional_to_ptr overloads are
        # shared, unmodified machinery).
        thir = _lower_ctx(
            "from tpy import readonly\n" + _F3_OPT_RECORDS
            + "def i1(t: tuple[T | None, T | None]) -> Int32:\n    return t[1].x\n"
            + "def ro(t: readonly[tuple[T | None, T | None]]) -> Int32:\n    return t[0].x\n")
        assert _fn(thir, "i1") is not None and _fn(thir, "ro") is not None
        assert _fn(thir, "i1").body[0].value.receiver.index.value == 1

    def test_optional_element_write_is_ineligible(self):
        # A write through an Optional-element subscript (`t[0].x = 5`) keeps the
        # name-receiver gate on the write path -> stays on the AST path.
        thir = _lower_ctx(
            _F3_OPT_RECORDS
            + "def f(t: tuple[T | None, T | None]) -> None:\n    t[0].x = 5\n")
        assert _fn(thir, "f") is None

    def test_optional_element_bind_is_ineligible(self):
        # A standalone bind of an Optional element (`e = t[0]`) is a borrow-local
        # binding source, which keeps the name-receiver gate -> stays on the AST path.
        thir = _lower_ctx(
            _F3_OPT_RECORDS
            + "def f(t: tuple[T | None, T | None]) -> Int32:\n    e = t[0]\n    return e.x\n")
        assert _fn(thir, "f") is None

    def test_dump_renders_deref_check(self):
        # The --dump-thir rendering of the runtime-null-checked member access (the one
        # test exercising dump.py's deref_check branch).
        thir = _lower_ctx(
            _F3_OPT_RECORDS
            + "def f(t: tuple[T | None, T | None]) -> Int32:\n    return t[0].x\n")
        assert "deref_check(%t[0] [borrow]).x" in dump_thir(thir)



class TestTupleSubscriptOptionalReadEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _F3_OPT_RECORDS
        + "def viap(t: tuple[T | None, T | None]) -> Int32:\n    return t[0].x\n"
        + "def viaa(h: Holder) -> Int32:\n    a = h.pair\n    return a[0].x\n"
        + "def main():\n    h = Holder()\n    print(0)\n"
        + "main()\n"
    )

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_deref_check(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "::tpy::deref_check(std::get<0>(t)).x" in cpp
        assert "::tpy::deref_check(::tpy::optional_to_ptr(std::get<0>(a))).x" in cpp



# The write position that closes the tuple-subscript family: a scalar field write to a
# record element, `t[N].field = <scalar>` (and `+= <scalar>`) -> `std::get<N>(t)->field
# = ...`. The target renders the same as the record-element read; only the write/aug-write
# eligibility gates are extended to the subscript target.
class TestTupleSubscriptWrite:
    def test_record_element_field_write_routes(self):
        thir = _lower_ctx(
            _F3_RECORDS
            + "def w(t: tuple[Int32, Leaf]) -> None:\n    t[1].n = 5\n")
        fn = _fn(thir, "w")
        assert fn is not None
        st = fn.body[0]
        assert isinstance(st, THIRAssign)
        assert isinstance(st.target, THIRFieldAccess) and st.target.is_arrow
        assert isinstance(st.target.receiver, THIRSubscript)

    def test_record_element_field_aug_write_routes(self):
        # `t[N].field += y` lowers to `target = (target OP value)`; the subscript target
        # renders identically on both sides.
        thir = _lower_ctx(
            _F3_RECORDS
            + "def w(t: tuple[Int32, Leaf]) -> None:\n    t[1].n += 3\n")
        st = _fn(thir, "w").body[0]
        assert isinstance(st, THIRAssign) and isinstance(st.value, THIRBinOp)
        assert isinstance(st.target, THIRFieldAccess) and st.target.is_arrow

    def test_write_via_storage_alias_dots(self):
        thir = _lower_ctx(
            _F3_RECORDS
            + "def w(h: Holder) -> None:\n    a = h.pair\n    a[1].n = 9\n")
        st = _fn(thir, "w").body[1]
        assert isinstance(st.target, THIRFieldAccess) and st.target.is_arrow is False

    def test_optional_element_write_still_ineligible(self):
        # Extending the scalar-field-write gate to subscript targets must NOT admit an
        # Optional-element write (needs a null-checked write) -- markers reject it.
        thir = _lower_ctx(
            _F3_OPT_RECORDS
            + "def w(t: tuple[T | None, T | None]) -> None:\n    t[0].x = 5\n")
        assert _fn(thir, "w") is None



class TestTupleSubscriptWriteEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    # Both target forms are byte-diffed and emit-checked: the borrow-param arrow write
    # (`w`) and the storage-alias dot write (`wa`), each with a plain and a `+=` variant.
    SRC = (
        "from tpy import Int32\n"
        "class Leaf:\n    n: Int32\n"
        "    def __init__(self, n: Int32) -> None:\n        self.n = n\n"
        "class Holder:\n    pair: tuple[Int32, Leaf]\n"
        "    def __init__(self, b: Leaf) -> None:\n        self.pair = (1, b)\n"
        "def w(t: tuple[Int32, Leaf]) -> None:\n    t[1].n = 5\n    t[1].n += 3\n"
        "def wa(h: Holder) -> None:\n    a = h.pair\n    a[1].n = 9\n    a[1].n += 2\n"
        "def main():\n    leaf = Leaf(1)\n    w((5, leaf))\n    h = Holder(leaf)\n"
        "    wa(h)\n    print(leaf.n)\n"
        "main()\n"
    )

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_writes(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "std::get<1>(t)->n = 5;" in cpp                      # borrow param -> arrow
        assert ("std::get<1>(t)->n = ::tpy::add_check<int32_t>(std::get<1>(t)->n, 3);"
                in cpp)
        assert "std::get<1>(a).n = 9;" in cpp                       # storage alias -> dot
        assert ("std::get<1>(a).n = ::tpy::add_check<int32_t>(std::get<1>(a).n, 2);"
                in cpp)
