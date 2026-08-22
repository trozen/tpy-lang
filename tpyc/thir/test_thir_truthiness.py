"""Structured THIR truthiness wraps and their expression-position coverage."""

from __future__ import annotations

from dataclasses import fields as dataclass_fields, is_dataclass

from ..codegen_cpp.context import CodeGenOptions
from .lower import lower_module
from .nodes import TruthinessMode, THIRTruthy, THIRUnaryNot
from .testutil import (
    _assert_byte_identical, _assert_routes_byte_identical, _compile, _entry,
    _fn, _lower_ctx, _lower_ctx_witnessed,
)


_SRC = (
    "from typing import Any\n"
    "from tpy import Int32, Own\n"
    "class Flag:\n"
    "    value: bool\n"
    "    def __init__(self, value: bool):\n        self.value = value\n"
    "    def __bool__(self) -> bool:\n        return self.value\n"
    "class Counted:\n"
    "    size: Int32\n"
    "    def __init__(self, size: Int32):\n        self.size = size\n"
    "    def __len__(self) -> Int32:\n        return self.size\n"
    "class Plain:\n"
    "    value: Int32\n"
    "    def __init__(self, value: Int32):\n        self.value = value\n"
    "class Box:\n"
    "    text: str\n"
    "    def __init__(self, text: str):\n        self.text = text\n"
    "    def get(self) -> str:\n        return self.text\n"
    "class Flags:\n"
    "    first: Flag\n"
    "    second: Flag\n"
    "    def __init__(self, first: Flag, second: Flag):\n"
    "        self.first = first\n        self.second = second\n"
    "def positions(s: str, data: bytes, x: Any, flag: Flag, sized: Counted, "
    "plain: Plain) -> Int32:\n"
    "    n = 0\n"
    "    if s:\n        n += 1\n"
    "    if not data:\n        n += 2\n"
    "    assert x\n"
    "    if flag:\n        n += 4\n"
    "    while sized:\n        return n + 8\n"
    "    if plain:\n        n += 16\n"
    "    return n\n"
    "def compound(a: str, b: bytes) -> bool:\n"
    "    return True if a and b else False\n"
    "def field_method(box: Box) -> bool:\n"
    "    return True if box.text and box.get() else False\n"
    "def storage_optional(p: Own[Flag] | None) -> bool:\n"
    "    if p:\n        return True\n"
    "    return False\n"
    "def indirect(flags: Flags, choose_second: bool) -> bool:\n"
    "    selected = flags.first\n"
    "    if choose_second:\n        selected = flags.second\n"
    "    return True if selected else False\n"
    "def filtered(items: list[str]) -> Int32:\n"
    "    selected = [item for item in items if item]\n"
    "    return len(selected)\n"
    "def optional_record(f: Flag | None) -> bool:\n"
    "    if f:\n        return True\n"
    "    return False\n"
)


def _truthy_nodes(node) -> list[THIRTruthy]:
    """Every THIRTruthy under `node`, in traversal order."""
    found: list[THIRTruthy] = []
    seen: set[int] = set()

    def walk(n) -> None:
        if id(n) in seen:
            return
        seen.add(id(n))
        if isinstance(n, THIRTruthy):
            found.append(n)
        if is_dataclass(n):
            for f in dataclass_fields(n):
                walk(getattr(n, f.name, None))
        elif isinstance(n, (list, tuple)):
            for item in n:
                walk(item)

    walk(node)
    return found


class TestStructuredTruthiness:
    def test_all_modes_route(self):
        thir, witnessed = _lower_ctx_witnessed(_SRC)
        names = (
            "positions", "compound", "field_method", "storage_optional",
            "indirect", "filtered", "optional_record",
        )
        missing = [name for name in names if _fn(thir, name) is None]
        assert not missing, missing
        for mode in TruthinessMode:
            assert witnessed.get("truthy." + mode.name.lower(), 0) >= 1

    def test_nodes_carry_modes(self):
        fn = _fn(_lower_ctx(_SRC), "positions")
        first = fn.body[1].condition
        second = fn.body[2].condition
        assert isinstance(first, THIRTruthy)
        assert first.mode is TruthinessMode.NONEMPTY
        assert isinstance(second, THIRUnaryNot)
        assert isinstance(second.operand, THIRTruthy)
        assert second.operand.mode is TruthinessMode.NONEMPTY

    def test_indirect_record_dunder_dereferences_operand(self):
        fn = _fn(_lower_ctx(_SRC), "indirect")
        cond = fn.body[-1].value.cond
        assert isinstance(cond, THIRTruthy)
        assert cond.mode is TruthinessMode.RECORD_BOOL
        assert cond.deref

    def test_thir_emit_is_byte_identical(self):
        compiler, modules = _compile(_SRC)
        entry = _entry(modules)
        ast_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False))
        thir_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(
                emit_source_comments=False, thir_codegen=True))
        assert thir_out == ast_out

    _EFFECT_SRC = (
        "from tpy import Int32, Own\n"
        "class Plain:\n"
        "    value: Int32\n"
        "    def __init__(self, value: Int32):\n        self.value = value\n"
        "def make(n: Int32) -> Own[Plain]:\n    return Plain(n)\n"
        "def probe(n: Int32) -> bool:\n"
        "    if make(n):\n        return True\n"
        "    return False\n"
        "def inert(p: Plain) -> bool:\n"
        "    if p:\n        return True\n"
        "    return False\n"
    )

    def test_plain_record_call_is_kept_for_effect(self):
        # The always-true arm drops its operand, so a CALL operand must stay
        # attached -- folding it away would lose the call's side effects.
        thir = _lower_ctx(self._EFFECT_SRC)
        cond = _fn(thir, "probe").body[0].condition
        assert isinstance(cond, THIRTruthy)
        assert cond.mode is TruthinessMode.ALWAYS_TRUE
        assert cond.operand is not None

    def test_plain_record_name_still_evaluates(self):
        # Even an inert read keeps its operand: the render is what carries any
        # effect or runtime check, so the fold never drops it.
        thir = _lower_ctx(self._EFFECT_SRC)
        cond = _fn(thir, "inert").body[0].condition
        assert isinstance(cond, THIRTruthy)
        assert cond.mode is TruthinessMode.ALWAYS_TRUE
        assert cond.operand is not None

    def test_always_true_effect_emit_is_byte_identical(self):
        compiler, modules = _compile(self._EFFECT_SRC)
        entry = _entry(modules)
        ast_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False))
        thir_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(
                emit_source_comments=False, thir_codegen=True))
        assert thir_out == ast_out
        assert "static_cast<void>(make(n)), true" in "".join(
            ast_out.values() if isinstance(ast_out, dict) else ast_out)

    _POSITION_SRC = (
        "from tpy import Int32, Own\n"
        "class Plain:\n"
        "    value: Int32\n"
        "    def __init__(self, value: Int32):\n        self.value = value\n"
        "class Holder:\n"
        "    inner: Plain\n"
        "    def __init__(self, inner: Plain):\n        self.inner = inner\n"
        "class Outer:\n"
        "    holder: Holder\n"
        "    def __init__(self, holder: Holder):\n        self.holder = holder\n"
        "    @property\n"
        "    def got(self) -> Own[Holder]:\n        return Holder(Plain(1))\n"
        "def make(n: Int32) -> Own[Plain]:\n    return Plain(n)\n"
        "def yes() -> bool:\n    return True\n"
        "def positions(n: Int32) -> Int32:\n"
        "    total = 0\n"
        "    while make(n):\n        total += 1\n        break\n"
        "    assert make(n)\n"
        "    if make(n) and yes():\n        total += 2\n"
        "    if yes() or make(n):\n        total += 4\n"
        "    return total\n"
        "def mid_chain(o: Outer) -> Int32:\n"
        "    if o.got.inner:\n        return 1\n"
        "    return 0\n"
    )

    def test_effect_wrap_routes_in_every_position(self):
        # while / assert / and / or reach the same THIRTruthy node the `if`
        # position does; pin each one so a gate narrowing can't strand them.
        kept = [n for n in _truthy_nodes(_fn(_lower_ctx(self._POSITION_SRC),
                                             "positions"))
                if n.mode is TruthinessMode.ALWAYS_TRUE]
        assert len(kept) == 4, kept
        assert all(n.operand is not None for n in kept)

    def test_property_mid_chain_evaluates(self):
        # `o.got.inner` buries a getter in the MIDDLE of the chain -- the
        # render carries the call, and the fold keeps it.
        kept = _truthy_nodes(_fn(_lower_ctx(self._POSITION_SRC), "mid_chain"))
        assert len(kept) == 1, kept
        assert kept[0].mode is TruthinessMode.ALWAYS_TRUE
        assert kept[0].operand is not None

    def test_position_emit_is_byte_identical(self):
        compiler, modules = _compile(self._POSITION_SRC)
        entry = _entry(modules)
        ast_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False))
        thir_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(
                emit_source_comments=False, thir_codegen=True))
        assert thir_out == ast_out

    def test_narrowed_optional_name_dispatches_dunder(self):
        # A pointer-repr Optional[record] narrowed past None dispatches the
        # record's __bool__/__len__ (a plain record is always-truthy); THIR
        # routes it from the same narrowed occurrence type the AST uses.
        src = (
            "from tpy import Int32\n"
            "class Plain:\n"
            "    value: Int32\n"
            "    def __init__(self, value: Int32):\n        self.value = value\n"
            "class Flag:\n"
            "    value: bool\n"
            "    def __init__(self, value: bool):\n        self.value = value\n"
            "    def __bool__(self) -> bool:\n        return self.value\n"
            "def probe_plain(p: Plain | None) -> Int32:\n"
            "    if p is None:\n        return 0\n"
            "    if p:\n        return p.value\n"
            "    return -1\n"
            "def probe_dunder(f: Flag | None) -> Int32:\n"
            "    if f is None:\n        return 0\n"
            "    if f:\n        return 1\n"
            "    return 2\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "probe_plain") is not None
        assert _fn(thir, "probe_dunder") is not None
        compiler, modules = _compile(src)
        entry = _entry(modules)
        ast_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False))
        thir_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(
                emit_source_comments=False, thir_codegen=True))
        assert thir_out == ast_out
        assert any("__bool__" in part for part in ast_out)

    def test_indirect_record_len_dereferences_operand(self):
        src = (
            "from tpy import Int32\n"
            "class Counted:\n"
            "    size: Int32\n"
            "    def __init__(self, size: Int32):\n        self.size = size\n"
            "    def __len__(self) -> Int32:\n        return self.size\n"
            "class Pair:\n"
            "    first: Counted\n"
            "    second: Counted\n"
            "    def __init__(self, first: Counted, second: Counted):\n"
            "        self.first = first\n        self.second = second\n"
            "def probe(pair: Pair, choose_second: bool) -> bool:\n"
            "    selected = pair.first\n"
            "    if choose_second:\n        selected = pair.second\n"
            "    return True if selected else False\n"
        )
        fn = _fn(_lower_ctx(src), "probe")
        assert fn is not None
        cond = fn.body[-1].value.cond
        assert isinstance(cond, THIRTruthy)
        assert cond.mode is TruthinessMode.RECORD_LEN
        assert cond.deref
        compiler, modules = _compile(src)
        entry = _entry(modules)
        ast_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False))
        thir_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(
                emit_source_comments=False, thir_codegen=True))
        assert thir_out == ast_out

    def test_optional_field_truthy_routes_whole_read(self):
        # A truthy Optional FIELD condition routes: `is_truthy(box.value)`
        # over the raw declared storage; the branch's narrowed read rides
        # sema's per-occurrence retype (no THIR-side path fact -- the
        # registry premise dissolved in design round C).
        src = (
            "from tpy import Int32\n"
            "class Box:\n"
            "    value: Int32 | None\n"
            "    def __init__(self, value: Int32 | None):\n"
            "        self.value = value\n"
            "def probe(box: Box) -> Int32:\n"
            "    if box.value:\n        return box.value\n"
            "    return 0\n"
        )
        from .testutil import (_assert_routes_byte_identical,
                               _lower_ctx_witnessed)
        _hpp, cpp = _assert_routes_byte_identical(src)
        _thir, wit = _lower_ctx_witnessed(src)
        assert wit.get("truthy.optional_field_whole", 0) >= 1
        assert "::tpy::is_truthy(box.value)" in _hpp + cpp


class TestAlwaysTrueDiscardsItsOperand:
    _SRC = (
        "from tpy import Own\n"
        "class Rec:\n"
        "    v: int\n"
        "    def __init__(self, v: int):\n        self.v = v\n"
        "class Holder:\n"
        "    r: Rec\n"
        "    def __init__(self) -> None:\n        self.r = Rec(0)\n"
        "    def get_ref(self) -> Rec:\n        return self.r\n"
        "    def make(self) -> Own[Rec]:\n        return Rec(1)\n"
        "    @property\n"
        "    def made(self) -> Own[Rec]:\n        return Rec(2)\n"
        "def probe(h: Holder) -> None:\n"
        "    if h.make():\n        print(1)\n"
        "    if h.get_ref():\n        print(2)\n"
        "    if h.made:\n        print(3)\n"
        "def main():\n    probe(Holder())\nmain()\n"
    )

    def test_record_returning_method_operands_route(self):
        # `(static_cast<void>(h.make()), true)` discards the result, so the
        # method's own return gate sees the same widened set it does at
        # statement position -- an owned, a borrow and a property return.
        kept = [n for n in _truthy_nodes(_fn(_lower_ctx(self._SRC), "probe"))
                if n.mode is TruthinessMode.ALWAYS_TRUE]
        assert len(kept) == 3, kept
        assert all(n.operand is not None for n in kept)
        _assert_byte_identical(self._SRC)

    def test_discard_does_not_leak_to_value_positions(self):
        # Only the ALWAYS_TRUE wrap discards. A __bool__ record's operand is
        # CONSUMED by the dunder call, so its method-call return keeps the
        # ordinary value-position gate and the body still falls back.
        src = (
            "class Flag:\n"
            "    value: bool\n"
            "    def __init__(self, value: bool):\n        self.value = value\n"
            "    def __bool__(self) -> bool:\n        return self.value\n"
            "class Holder:\n"
            "    f: Flag\n"
            "    def __init__(self) -> None:\n        self.f = Flag(True)\n"
            "    def get(self) -> Flag:\n        return self.f\n"
            "def probe(h: Holder) -> None:\n"
            "    if h.get():\n        print(1)\n"
            "def main():\n    probe(Holder())\nmain()\n"
        )
        assert _fn(_lower_ctx(src), "probe") is None
        _assert_byte_identical(src)


class TestPtrTruthyOptionalRecord:
    """`gen_truthy_expr`'s un-narrowed pointer-repr `Optional[record]` arm and
    its storage-form complement."""

    _SRC = (
        "class Flag:\n"
        "    value: bool\n"
        "    def __init__(self, value: bool):\n        self.value = value\n"
        "    def __bool__(self) -> bool:\n        return self.value\n"
        "class Holder:\n"
        "    f: Flag | None\n"
        "    def __init__(self, f: Flag | None):\n        self.f = f\n"
        "def observe(f: Flag) -> Flag | None:\n    return f\n"
        "def name_op(c: Flag | None) -> bool:\n"
        "    if c:\n        return True\n"
        "    return False\n"
        "def not_op(c: Flag | None) -> bool:\n"
        "    return not c\n"
        "def and_op(c: Flag | None, d: Flag | None) -> bool:\n"
        "    if c and d:\n        return True\n"
        "    return False\n"
        "def call_op(f: Flag) -> bool:\n"
        "    if observe(f):\n        return True\n"
        "    return False\n"
        "def alias_reseat(c: Flag | None) -> bool:\n"
        "    x = c\n"
        "    while x:\n        x = None\n"
        "    return False\n"
        "def field_op(h: Holder) -> bool:\n"
        "    if h.f:\n        return True\n"
        "    return False\n"
        "def elem_op(xs: list[Flag | None]) -> bool:\n"
        "    if xs[0]:\n        return True\n"
        "    return False\n"
        "def main():\n    print(name_op(None))\nmain()\n"
    )

    def test_ptr_and_storage_forms_route(self):
        # The pointer sources dispatch `::tpy::ptr_truthy` (null check AND
        # the dunder, one evaluation); the storage-form field / element read
        # `std::optional<T>`, which has no pointer dispatch, so they render
        # bare (the divergence BUGS.md files, not an oracle accident).
        hpp, cpp = _assert_routes_byte_identical(self._SRC)
        _thir, wit = _lower_ctx_witnessed(self._SRC)
        assert wit.get("truthy.ptr_truthy", 0) >= 5
        assert wit.get("call.ptr_truthy_operand", 0) == 1
        assert wit.get("truthy.storage_opt_bare", 0) == 2
        assert wit.get("subscript.opt_record_truthy", 0) == 1
        out = hpp + cpp
        assert "if (::tpy::ptr_truthy(c))" in out
        assert "(!(::tpy::ptr_truthy(c)))" in out
        assert "(::tpy::ptr_truthy(c) && ::tpy::ptr_truthy(d))" in out
        assert "::tpy::ptr_truthy(observe(f))" in out
        assert "Flag* x = c;" in out
        assert "if (h.f)" in out
        assert "if (::tpy::__getitem__(xs, 0))" in out

    def test_ptr_truthy_nodes_carry_the_mode(self):
        thir = _lower_ctx(self._SRC)
        modes = [n.mode for n in _truthy_nodes(_fn(thir, "name_op"))]
        assert modes == [TruthinessMode.PTR_TRUTHY]

    def test_container_inner_keeps_the_bare_non_null_test(self):
        # `is_user_record` excludes builtin containers from the dispatch, so
        # `if xs:` on a `list | None` stays the bare pointer test -- a filed
        # divergence THIR must not "fix" by routing a different render.
        src = (
            "from tpy import Int32\n"
            "def probe(xs: list[Int32] | None) -> bool:\n"
            "    if xs:\n        return True\n"
            "    return False\n"
        )
        assert _fn(_lower_ctx(src), "probe") is None
        _assert_byte_identical(src)

    def test_dunderless_record_inner_keeps_the_bare_test(self):
        # No `__bool__`/`__len__` on the inner: the AST falls through to the
        # bare non-null test, so the dispatch must NOT fire (it would add a
        # dunder call the record does not have).
        src = (
            "from tpy import Int32\n"
            "class Plain:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32):\n        self.n = n\n"
            "def probe(p: Plain | None) -> bool:\n"
            "    if p:\n        return True\n"
            "    return False\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        _thir, wit = _lower_ctx_witnessed(src)
        assert wit.get("truthy.ptr_truthy", 0) == 0
        assert "if (p)" in hpp + cpp

    def test_non_name_non_call_ptr_sources_keep_rejecting(self):
        # A tuple ELEMENT, a method call and a ternary all reach the AST's
        # ptr_truthy gate as raw `T*` sources, but none has a verified
        # operand render here -- they must reject rather than fall into the
        # ladder, whose bare render would silently drop the dunder.
        src = (
            "from tpy import Int32\n"
            "class Flag:\n"
            "    value: bool\n"
            "    def __init__(self, value: bool):\n        self.value = value\n"
            "    def __bool__(self) -> bool:\n        return self.value\n"
            "class Box:\n"
            "    f: Flag | None\n"
            "    def __init__(self, f: Flag | None):\n        self.f = f\n"
            "    def get(self) -> Flag | None:\n        return self.f\n"
            "def tup_op(t: tuple[Flag | None, Int32]) -> bool:\n"
            "    if t[0]:\n        return True\n"
            "    return False\n"
            "def method_op(b: Box) -> bool:\n"
            "    if b.get():\n        return True\n"
            "    return False\n"
            "def tern_op(a: Flag | None, b: Flag | None, c: bool) -> bool:\n"
            "    if (a if c else b):\n        return True\n"
            "    return False\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "tup_op") is None
        assert _fn(thir, "method_op") is None
        assert _fn(thir, "tern_op") is None
        _assert_byte_identical(src)
