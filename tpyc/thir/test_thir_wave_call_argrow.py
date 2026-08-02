"""Pins for the free-call ARG-ROW frontier: the shapes the plain / native arg
disjunctions used to reject outright, plus the neighbours that must keep
falling back."""

from __future__ import annotations

from .testutil import (
    _assert_byte_identical,
    _assert_routes_byte_identical,
    _fn,
    _lower_ctx,
    _lower_ctx_witnessed,
)


def _fallback_reasons(src: str) -> set:
    """The body-component fallback reasons, sans the `body:` prefix and any
    statement-position qualifier. Lets a pin assert WHERE a body still
    rejects -- the only honest claim for a widening that moves the reject
    down a layer rather than routing the body."""
    from ..codegen_cpp.context import CodeGenOptions
    from .testutil import _compile, _entry
    compiler, modules = _compile(src)
    compiler.generate_code_to_strings(
        _entry(modules),
        options=CodeGenOptions(emit_source_comments=False, thir_codegen=True))
    return {k.split(":")[-1] for k in compiler._thir_fallback
            if k.startswith("body:")}


_PRELUDE = "from tpy import Int32\n"


class TestNativeIntLiteralArg:
    SRC = _PRELUDE + (
        "def main() -> None:\n"
        "    ba = bytearray(16)\n"
        "    ba[0] = 1\n"
        "    print(len(ba))\n"
    )

    def test_routes_and_witnesses(self):
        # The bare literal rides the general resolved-scalar row now (the
        # dedicated arg.native_int_literal row was shadowed and deleted);
        # byte-identity pins the slot-threaded render.
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "main") is not None

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)

    def test_literal_binop_arg_routes(self):
        # A constant-valued BINOP keeps IntLiteralType through sema; the
        # resolved-scalar row admits it and the ordinary binop render is
        # byte-identical on both paths (dualgen-verified).
        src = _PRELUDE + (
            "def main() -> None:\n"
            "    ba = bytearray(4000000000 // 1000000000)\n"
            "    print(len(ba))\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None
        _assert_byte_identical(src)


class TestBytearrayNamePassThrough:
    SRC = _PRELUDE + (
        "def mutate(buf: bytearray) -> None:\n"
        "    buf[0] = 9\n"
        "def take_bytes(b: bytes) -> Int32:\n"
        "    return Int32(len(b))\n"
        "def main() -> None:\n"
        "    ba = bytearray(4)\n"
        "    mutate(ba)\n"
        "    print(take_bytes(bytes(ba)))\n"
    )

    def test_plain_and_native_slots_route(self):
        # `mutate(ba)` is the plain loop's `std::vector<uint8_t>&` bind;
        # `bytes(ba)` is the native `::tpy::bytes_copy(ba)` one. Both are the
        # bare name, so one row serves both ladders.
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "main") is not None

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)

    def test_bytearray_into_a_bytes_slot_is_the_coerce_not_this_row(self):
        # The boundary: this row pairs bytearray WITH bytearray. Feeding a
        # `bytes` slot is the BytesView coerce, which arrives as its own node
        # -- so the shape does not fall back AT the container row, it rejects
        # one layer out at the coerce. Asserting the reason (not merely
        # "stays AST") is what makes this non-vacuous.
        src = _PRELUDE + (
            "def take_bytes(b: bytes) -> Int32:\n"
            "    return Int32(len(b))\n"
            "def main() -> None:\n"
            "    ba = bytearray(4)\n"
            "    print(take_bytes(ba))\n"
        )
        assert _fallback_reasons(src) == {"expr.coerce"}

    def test_both_slots_render_the_bare_name(self):
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _compile, _entry
        compiler, modules = _compile(self.SRC)
        hpp, cpp = compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   thir_codegen=True))
        assert "mutate(ba)" in cpp
        assert "::tpy::bytes_copy(ba)" in cpp


class TestBorrowTupleNameArg:
    SRC = _PRELUDE + (
        "class T:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n"
        "        self.x = x\n"
        "def inner(p: tuple[T | None, T | None]) -> None:\n"
        "    a, b = p\n"
        "    if a is not None:\n"
        "        a.x = a.x + 100\n"
        "def outer(p: tuple[T | None, T | None]) -> None:\n"
        "    inner(p)\n"
        "def main() -> None:\n"
        "    t1 = T(1)\n"
        "    t2 = T(2)\n"
        "    outer((t1, t2))\n"
        "    print(t1.x)\n"
    )

    def test_routes_and_witnesses(self):
        thir, faces = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "outer") is not None
        assert faces["arg.btuple_name"] >= 1

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)

    def test_forwarded_bare_keeps_the_mutation_visible(self):
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _compile, _entry
        compiler, modules = _compile(self.SRC)
        _hpp, cpp = compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   thir_codegen=True))
        # Bare, NOT `tuple_to_pointer(p)` -- the param is already borrow form.
        assert "inner(p)" in cpp
        assert "tuple_to_pointer" not in cpp.split("void outer")[1][:200]

    def test_storage_loop_var_does_not_take_the_bare_pass(self):
        # THE boundary, and a regression guard: a for-each loop var over a
        # storage container is NOT registered in `lc.storage_tuple_locals`,
        # so keying the bare pass on absence from that set rendered
        # `peek(it)` where the AST renders the `tuple_to_pointer` lift -- a
        # wrong-value divergence. The row keys on positive param evidence
        # instead, so this shape must not witness it.
        src = _PRELUDE + (
            "from tpy import readonly\n"
            "class T:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32) -> None:\n"
            "        self.x = x\n"
            "def peek(p: tuple[T | None, T | None]) -> None:\n"
            "    a, b = p\n"
            "    if a is not None:\n"
            "        print(a.x)\n"
            "def run(items: readonly[list[tuple[T | None, T | None]]]) -> None:\n"
            "    for it in items:\n"
            "        peek(it)\n"
            "def main() -> None:\n"
            "    t1 = T(1)\n"
            "    xs: list[tuple[T | None, T | None]] = [(t1, None)]\n"
            "    run(xs)\n"
        )
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("arg.btuple_name", 0) == 0
        _assert_byte_identical(src)


class TestNativeCallableValueArg:
    SRC = _PRELUDE + (
        "from typing import Callable\n"
        "def double(x: Int32) -> Int32:\n"
        "    return x * 2\n"
        "def main() -> None:\n"
        "    h: Callable[[Int32], Int32] = double\n"
        "    result = list(map(h, [1, 2, 3]))\n"
        "    print(result)\n"
    )

    def test_routes(self):
        assert _fn(_lower_ctx(self.SRC), "main") is not None

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)

    def test_func_ref_name_keeps_its_own_row(self):
        # Not a fallback boundary -- a func-ref name at the same template
        # slot ALREADY routed via `_func_ref_routable`. What this pins is
        # that adding the Callable-VALUE row did not disturb it: both
        # spellings route and stay byte-identical.
        src = _PRELUDE + (
            "def double(x: Int32) -> Int32:\n"
            "    return x * 2\n"
            "def main() -> None:\n"
            "    print(list(map(double, [1, 2, 3])))\n"
        )
        assert _fn(_lower_ctx(src), "main") is not None
        _assert_byte_identical(src)


class TestNativeOwnMoveArg:
    SRC = (
        "from tpy import Ptr, Int32, Array\n"
        "from tpy.unsafe import unsafe_ptr, unsafe_store\n"
        "class Point:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n"
        "        self.x = x\n"
        "def main() -> None:\n"
        "    arr: Array[Point, 2] = [Point(1), Point(3)]\n"
        "    p: Ptr[Point] = unsafe_ptr(arr)\n"
        "    pt: Point = Point(10)\n"
        "    unsafe_store(p, 0, pt)\n"
        "    print(arr[0].x)\n"
    )

    def test_routes_and_witnesses(self):
        thir, faces = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "main") is not None
        assert faces["move.own_last_use"] >= 1

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)

    def test_non_last_use_copy_stays_ast(self):
        # The boundary: only the temp-FREE move half rides the native ladder.
        # A source used again afterwards needs the `__tmp_N` copy, which the
        # native arg loop has no flush point for.
        src = (
            "from tpy import Ptr, Int32, Array\n"
            "from tpy.unsafe import unsafe_ptr, unsafe_store\n"
            "class Point:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32) -> None:\n"
            "        self.x = x\n"
            "def main() -> None:\n"
            "    arr: Array[Point, 2] = [Point(1), Point(3)]\n"
            "    p: Ptr[Point] = unsafe_ptr(arr)\n"
            "    pt: Point = Point(10)\n"
            "    unsafe_store(p, 0, pt)\n"
            "    print(pt.x)\n"
        )
        assert _fn(_lower_ctx(src), "main") is None


class TestSimpleGenSelfCapturingLambda:
    SRC = _PRELUDE + (
        "from typing import Callable, Iterator\n"
        "def apply(f: Callable[[Int32], Int32], v: Int32) -> Int32:\n"
        "    return f(v)\n"
        "class C:\n"
        "    n: Int32\n"
        "    def __init__(self) -> None:\n"
        "        self.n = 10\n"
        "    def emit(self, k: Int32) -> Iterator[Int32]:\n"
        "        for i in range(k):\n"
        "            yield apply(lambda x: x + self.n, i)\n"
        "def main() -> None:\n"
        "    c = C()\n"
        "    for v in c.emit(3):\n"
        "        print(v)\n"
    )

    def test_routes(self):
        # A simple generator emits through the lambda peephole, not a
        # THIRFunction -- the empty fallback tally IS the routing witness.
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _compile, _entry
        compiler, modules = _compile(self.SRC)
        compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   thir_codegen=True))
        assert not dict(compiler._thir_fallback)

    def test_capture_spells_this_while_the_body_reads_star_this(self):
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _compile, _entry
        compiler, modules = _compile(self.SRC)
        hpp, _cpp = compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   thir_codegen=True))
        assert "[this](int32_t x)" in hpp
        assert "(*this).n" in hpp

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)


class TestPropertyLenArg:
    SRC = _PRELUDE + (
        "class Foo:\n"
        "    _name: str\n"
        "    def __init__(self) -> None:\n"
        "        self._name = \"hello\"\n"
        "    @property\n"
        "    def name(self) -> str:\n"
        "        return self._name\n"
        "def get_count(f: Foo) -> Int32:\n"
        "    return Int32(len(f.name))\n"
        "def main() -> None:\n"
        "    print(get_count(Foo()))\n"
    )

    def test_routes(self):
        assert _fn(_lower_ctx(self.SRC), "get_count") is not None

    def test_renders_the_getter_call(self):
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _compile, _entry
        compiler, modules = _compile(self.SRC)
        _hpp, cpp = compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   thir_codegen=True))
        assert "::tpy::__len__(f.name())" in cpp

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)


class TestBorrowTupleParamInResumable:
    def test_resumable_param_routes_and_stays_identical(self):
        # The row is LANE-BLIND: a resumable's params are frame fields, but
        # they reach it through `lc.params` like any other and render
        # identically. The absence-keyed predecessor needed a lane guard;
        # the positive key subsumes it, and the guard was suppressing a body
        # that routes correctly. This pin is what keeps it deleted -- the
        # fence it replaces asserted only that SOME resumable reason was
        # present, which is true of any generator body.
        src = _PRELUDE + (
            "from typing import Iterator\n"
            "class T:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32) -> None:\n"
            "        self.x = x\n"
            "def peek(p: tuple[T | None, T | None]) -> Int32:\n"
            "    a, b = p\n"
            "    return a.x if a is not None else 0\n"
            "def gen(p: tuple[T | None, T | None]) -> Iterator[Int32]:\n"
            "    yield peek(p)\n"
            "    yield 0\n"
            "def main() -> None:\n"
            "    t1 = T(1)\n"
            "    for n in gen((t1, None)):\n"
            "        print(n)\n"
        )
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _compile, _entry
        compiler, modules = _compile(src)
        compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   thir_codegen=True))
        assert not dict(compiler._thir_fallback)
        _assert_byte_identical(src)


class TestRequiredProtocolUnionArg:
    SRC = (
        "from typing import Sized, Sequence\n"
        "def describe(items: Sized | Sequence[int]) -> None:\n"
        "    if isinstance(items, Sequence):\n"
        "        print(items[0])\n"
        "    elif isinstance(items, Sized):\n"
        "        print(len(items))\n"
        "def main() -> None:\n"
        "    nums: list[int] = [10, 20, 30]\n"
        "    describe(nums)\n"
    )

    def test_routes_and_witnesses(self):
        thir, faces = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "main") is not None
        assert faces["arg.required_protocol_union"] >= 1
        assert faces["arg.protocol_union_plain"] >= 1

    def test_renders_bare_not_address_of(self):
        # The whole point: the slot monomorphizes to one template param, so
        # `_gen_protocol_arg`'s required-union branch hands back the plain
        # value. A `&(nums)` here means the ptr-variant / optional-ptr arm
        # claimed the slot -- the divergence this arm exists to prevent.
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _compile, _entry
        compiler, modules = _compile(self.SRC)
        _hpp, cpp = compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   thir_codegen=True))
        assert "describe(nums)" in cpp
        assert "describe(&(nums))" not in cpp

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)

    def test_nullable_protocol_union_keeps_the_address_of_lift(self):
        # The boundary the AST splits on is has_none, NOT the call kind: add
        # a None member and the same slot takes the address-of lift again.
        src = (
            "from typing import Sized, Sequence\n"
            "def describe(items: Sized | Sequence[int] | None) -> None:\n"
            "    if items is None:\n"
            "        print(0)\n"
            "def main() -> None:\n"
            "    nums: list[int] = [10, 20, 30]\n"
            "    describe(nums)\n"
        )
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _compile, _entry
        compiler, modules = _compile(src)
        _hpp, cpp = compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   thir_codegen=False))
        assert "describe(&(nums))" in cpp
        _assert_byte_identical(src)


class TestNativeComprehensionArg:
    SRC = (
        "def main() -> None:\n"
        "    src = [\"b\", \"a\", \"c\"]\n"
        "    names = sorted([n for n in src if n != \"c\"])\n"
        "    print(names)\n"
    )

    def test_routes_and_witnesses(self):
        thir, faces = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "main") is not None
        assert faces["arg.native_comprehension"] >= 1

    def test_renders_inline_not_through_a_temp(self):
        # The template slot takes the stmt-expr directly; a `__tmp_N` here
        # would be the plain ladder's ArgTemp row leaking onto this path.
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _compile, _entry
        compiler, modules = _compile(self.SRC)
        _hpp, cpp = compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   thir_codegen=True))
        assert "::tpy::builtin_sorted<std::string>(({" in cpp

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)

    def test_concrete_container_slot_still_takes_the_named_temp(self):
        # The boundary: a PLAIN callee's concrete container slot keeps the
        # slot-typed `__tmp_N`, which the inline row must not replace.
        src = _PRELUDE + (
            "def total(xs: list[Int32]) -> Int32:\n"
            "    s = 0\n"
            "    for v in xs:\n"
            "        s = s + v\n"
            "    return s\n"
            "def main() -> None:\n"
            "    print(total([i for i in range(4)]))\n"
        )
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _compile, _entry
        compiler, modules = _compile(src)
        _hpp, cpp = compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   thir_codegen=True))
        assert "std::vector<int32_t> __tmp_" in cpp
        _assert_byte_identical(src)


class TestModuleVarLenArg:
    SRC = (
        "import os\n"
        "def main() -> None:\n"
        "    print(len(os.environ) >= 0)\n"
    )

    def test_the_len_arg_routes(self):
        # The module var at a native len slot is a pinned consumer
        # (`::tpy::__len__((*environ))`), so the body routes whole.
        assert _fallback_reasons(self.SRC) == set()

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)

    def test_record_local_keeps_its_bare_read(self):
        # The boundary: only a registered MODULE variable renders its own
        # deref. A record bound to an ordinary local reads bare, so a
        # `_module_var_len_arg` that matched any dotted receiver would show
        # up here as a spurious `(*b)`.
        src = _PRELUDE + (
            "class Bag:\n"
            "    n: Int32\n"
            "    def __init__(self) -> None:\n"
            "        self.n = 3\n"
            "    def __len__(self) -> Int32:\n"
            "        return self.n\n"
            "def main() -> None:\n"
            "    b = Bag()\n"
            "    print(len(b))\n"
        )
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _compile, _entry
        compiler, modules = _compile(src)
        _hpp, cpp = compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   thir_codegen=True))
        assert "::tpy::__len__(b)" in cpp
        _assert_byte_identical(src)


class TestSpannableCoerceArg:
    SRC = (
        "from tpy import Int32, Span, Spannable, readonly, auto_readonly\n"
        "class Buffer:\n"
        "    _data: list[Int32]\n"
        "    def __init__(self) -> None:\n"
        "        self._data = [1, 2, 3]\n"
        "    @auto_readonly\n"
        "    def __span__(self) -> Span[auto_readonly[Int32]]:\n"
        "        return self._data\n"
        "def accept_ro(s: Span[readonly[Int32]]) -> Int32:\n"
        "    total: Int32 = 0\n"
        "    for x in s:\n"
        "        total += x\n"
        "    return total\n"
        "def pass_through(c: Spannable[Int32]) -> Int32:\n"
        "    return accept_ro(c)\n"
        "def main() -> None:\n"
        "    print(pass_through(Buffer()))\n"
    )

    def test_routes(self):
        assert _fn(_lower_ctx(self.SRC), "pass_through") is not None

    def test_renders_as_span_not_the_span_method(self):
        # `_gen_span_coercion` checks the Spannable arm BEFORE the user-record
        # `.__span__()` one, so a protocol actual takes the helper.
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _compile, _entry
        compiler, modules = _compile(self.SRC)
        hpp, cpp = compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   thir_codegen=True))
        both = hpp + cpp
        assert "accept_ro(::tpy::as_span(c))" in both

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)


class TestTernaryIntoOwnSlot:
    SRC = (
        "from tpy import Own\n"
        "class Box:\n"
        "    val: int\n"
        "    def __init__(self, v: int) -> None:\n"
        "        self.val = v\n"
        "def take(b: Own[Box]) -> Own[Box]:\n"
        "    b.val += 1\n"
        "    return b\n"
        "def main() -> None:\n"
        "    a = Box(10)\n"
        "    other = Box(20)\n"
        "    flag = True\n"
        "    r = take(a if flag else other)\n"
        "    print(r.val)\n"
    )

    def test_routes_via_record_ifexpr_arm(self):
        # The ifexpr record arm (Form-threading wave) now lowers the
        # both-lvalue ternary, so the body routes whole. The Own-slot COPY
        # half captures the lvalue into its `auto __tmp_N`;
        # `_own_move_source_slice` requires a TpyName, so the ternary can
        # only ever reach the copy-temp half, never the temp-free move --
        # that exclusion is what the arg-gate widening relies on.
        assert _fallback_reasons(self.SRC) == set()

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)


class TestViewInstantiation:
    SRC = _PRELUDE + (
        "from tpy import Span, readonly\n"
        "def main() -> None:\n"
        "    lst: list[Int32] = [10, 20, 30]\n"
        "    ros: Span[readonly[Int32]] = Span[readonly[Int32]](lst)\n"
        "    print(len(ros))\n"
    )

    def test_routes_and_witnesses(self):
        thir, faces = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "main") is not None
        assert faces["call.view_instantiation"] >= 1

    def test_renders_the_spelled_direct_init(self):
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _compile, _entry
        compiler, modules = _compile(self.SRC)
        _hpp, cpp = compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   thir_codegen=True))
        assert "std::span<const int32_t>(lst)" in cpp

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)

    def test_container_instantiation_keeps_the_storage_path(self):
        # The boundary: a `list(...)` instantiation is a STORAGE container,
        # not a value view -- it goes through the make_vector machinery this
        # row deliberately bypasses, so it must not be swallowed here.
        src = _PRELUDE + (
            "def main() -> None:\n"
            "    src: list[Int32] = [1, 2]\n"
            "    out = list(src)\n"
            "    print(len(out))\n"
        )
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("call.view_instantiation", 0) == 0
        _assert_byte_identical(src)


_TUPLE_PRELUDE = _PRELUDE + (
    "class T:\n"
    "    x: Int32\n"
    "    def __init__(self, x: Int32) -> None:\n"
    "        self.x = x\n"
    "def consume(p: tuple[T | None, T | None]) -> None:\n"
    "    a, b = p\n"
    "    if a is not None:\n"
    "        print(a.x)\n"
)


class TestBorrowTupleNameArgRows:
    """The two NAME rows at a borrow-tuple param slot: a STORAGE-form tuple
    local takes the `tuple_to_pointer` lift, a BORROW-form one binds bare.
    Both key on positive evidence, so the split cannot drift into passing a
    storage source bare (a wrong-value render)."""

    STORAGE_SRC = _TUPLE_PRELUDE + (
        "def main() -> None:\n"
        "    t1 = T(1)\n"
        "    items: list[tuple[T | None, T | None]] = [(t1, None)]\n"
        "    for it in items:\n"
        "        consume(it)\n"
        "    snap = items[0]\n"
        "    consume(snap)\n"
        "main()\n"
    )

    BARE_SRC = _TUPLE_PRELUDE + (
        "def main() -> None:\n"
        "    t1 = T(1)\n"
        "    items: list[tuple[T | None, T | None]] = [(t1, None)]\n"
        "    last = items[0]\n"
        "    for last in items:\n"
        "        pass\n"
        "    consume(last)\n"
        "main()\n"
    )

    def test_storage_name_routes_and_witnesses(self):
        thir, faces = _lower_ctx_witnessed(self.STORAGE_SRC)
        assert _fn(thir, "main") is not None
        assert faces["arg.btuple_storage_name"] >= 2

    def test_storage_name_renders_the_lift(self):
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _compile, _entry
        compiler, modules = _compile(self.STORAGE_SRC)
        _hpp, cpp = compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   thir_codegen=True))
        wrap = "::tpy::tuple_to_pointer<std::tuple<const T*, const T*>>"
        assert f"consume({wrap}(it))" in cpp
        assert f"consume({wrap}(snap))" in cpp

    def test_storage_name_byte_identical(self):
        _assert_byte_identical(self.STORAGE_SRC)

    def test_reassigned_borrow_local_binds_bare(self):
        # A REASSIGNED pointer-repr tuple local is borrow form for all its
        # bindings (the AST's `borrow_form_tuple_locals`), so it must NOT
        # pick up the lift the storage rows take.
        thir, faces = _lower_ctx_witnessed(self.BARE_SRC)
        assert _fn(thir, "main") is not None
        assert faces.get("arg.btuple_storage_name", 0) == 0
        assert faces["arg.btuple_name"] >= 1

    def test_reassigned_borrow_local_byte_identical(self):
        _assert_byte_identical(self.BARE_SRC)

    def test_const_iterated_source_takes_the_const_lift(self):
        # A loop var over a `readonly[list[..]]` is a CONST storage source:
        # its element pointers come out `const T*`, and the readonly peel in
        # the for-each registration is what makes it storage at all.
        src = _TUPLE_PRELUDE + (
            "from tpy import readonly\n"
            "def show(rows: readonly[list[tuple[T | None, T | None]]]) -> None:\n"
            "    for cit in rows:\n"
            "        consume(cit)\n"
            "def main() -> None:\n"
            "    show([])\n"
            "main()\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "show") is not None
        assert faces["arg.btuple_storage_name"] >= 1
        _assert_byte_identical(src)

    def test_mixed_and_owned_tuple_slots_still_reject(self):
        # The boundary: an Own-element param slot is either the MIXED render
        # (kept verbatim) or fully-owned STORAGE form -- neither takes the
        # plain storage->borrow lift, and `_f1_tuple` is what keeps them out.
        src = _PRELUDE + (
            "from tpy import Own\n"
            "class R:\n"
            "    v: Int32\n"
            "    def __init__(self, v: Int32) -> None:\n"
            "        self.v = v\n"
            "class C:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "def take_mixed(p: tuple[Own[R], C]) -> Int32:\n"
            "    return 0\n"
            "def take_owned(p: tuple[Own[R], Own[C]]) -> Int32:\n"
            "    return 0\n"
            "def feed(mixed: list[tuple[R, C]], owned: list[tuple[R, C]]) -> None:\n"
            "    for m in mixed:\n"
            "        print(take_mixed(m))\n"
            "    for o in owned:\n"
            "        print(take_owned(o))\n"
            "def main() -> None:\n"
            "    feed([], [])\n"
            "main()\n"
        )
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("arg.btuple_storage_name", 0) == 0
        assert "call.arg_shape.tuple" in _fallback_reasons(src)
        _assert_byte_identical(src)


class TestRecursiveUnionBorrowCallArg:
    """A wrapper-returning accessor at a `const Value&` slot binds inline --
    the recursive-union twin of the record borrow-call row. `Box.get()` reads
    as an RVALUE (`call_returns_cpp_ref` gives every union return value
    semantics), which is exactly why this row asks the slot instead."""

    SRC = (
        "from tplib import Box\n"
        "type Value = int | str | Neg\n"
        "class Neg:\n"
        "    inner: Box[Value]\n"
        "def show(v: Value) -> str:\n"
        "    return \"v\"\n"
        "def unwrap(b: Box[Value]) -> str:\n"
        "    return show(b.get())\n"
        "def main() -> None:\n"
        "    print(show(42))\n"
        "main()\n"
    )

    def test_the_arg_gate_admits_and_witnesses(self):
        # The body still rejects downstream (the method-call RESULT gate has
        # no recursive-union row yet), so the honest claim is that the ARG
        # row no longer blocks -- `expr.call` is gone from the reasons.
        _thir, faces = _lower_ctx_witnessed(self.SRC)
        assert faces["arg.recursive_union_borrow_call"] >= 1
        assert "expr.call" not in _fallback_reasons(self.SRC)

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)

    def test_plain_union_slot_is_not_claimed(self):
        # The boundary: a NON-recursive value union is a bare std::variant
        # slot with its own lift rows -- `recursive_union_alternatives`
        # returning None is what keeps it out.
        src = _PRELUDE + (
            "class A:\n"
            "    v: Int32\n"
            "    def __init__(self, v: Int32) -> None:\n"
            "        self.v = v\n"
            "def take(u: Int32 | A) -> Int32:\n"
            "    return 0\n"
            "def hand(src: list[Int32 | A]) -> Int32:\n"
            "    return take(src[0])\n"
            "def main() -> None:\n"
            "    print(hand([]))\n"
            "main()\n"
        )
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("arg.recursive_union_borrow_call", 0) == 0
        _assert_byte_identical(src)


class TestOwnOptionalRecordSlotArgs:
    """`Own[Optional[record]]` forces the OWNING value form (`std::optional<
    T>&&`) where a bare Optional would use pointer repr. The ctor-rvalue half
    was already covered by `_own_optional_record_rvalue_arg` at the ctor gate
    and is now wired into the free-call gate too; the NAME half is the new
    row -- a pointer-repr Optional binding rebuilt null-safely before the
    Own-slot move."""

    _PRE = _PRELUDE + (
        "from tpy import Own\n"
        "class Rec:\n"
        "    v: Int32\n"
        "    def __init__(self, v: Int32) -> None:\n"
        "        self.v = v\n"
        "class Holder:\n"
        "    def __init__(self, r: Own[Rec | None] = None) -> None:\n"
        "        self.slot = r\n"
        "    def value(self) -> Int32:\n"
        "        if self.slot is None:\n"
        "            return -1\n"
        "        return self.slot.v\n"
    )

    SRC = _PRE + (
        "def store(r: Own[Rec | None] = None) -> Own[Holder]:\n"
        "    return Holder(r)\n"
        "def main() -> None:\n"
        "    print(store(Rec(4)).value())\n"
        "    print(store().value())\n"
        "main()\n"
    )

    def test_routes_and_witnesses_the_name_row(self):
        thir, faces = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "store") is not None
        assert _fn(thir, "main") is not None
        assert faces["own.opt_ptr_name_rebuild"] >= 1

    def test_renders_the_null_safe_rebuild_under_the_move(self):
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _compile, _entry
        compiler, modules = _compile(self.SRC)
        _hpp, cpp = compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   thir_codegen=True))
        assert ("Holder(std::move(r ? std::optional<Rec>(std::move(*r))"
                " : std::nullopt))") in cpp
        assert "store(Rec(4))" in cpp
        assert "store(std::nullopt)" in cpp

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)

    def test_narrowed_occurrence_is_not_claimed(self):
        # The boundary: a narrowed name reads as the extracted value, a
        # different render -- only the ctor-rvalue row may fire here.
        src = self._PRE + (
            "def take(r: Own[Rec | None]) -> Int32:\n"
            "    if r is not None:\n"
            "        return Holder(r).value()\n"
            "    return 0\n"
            "def main() -> None:\n"
            "    print(take(Rec(3)))\n"
            "main()\n"
        )
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("own.opt_ptr_name_rebuild", 0) == 0
        _assert_byte_identical(src)

    def test_non_last_use_name_renders_without_the_outer_move(self):
        # The outer `std::move` is the AST's `_maybe_move`, so it is gated on
        # the same last-use fact: a name read again afterwards renders the
        # bare rebuild.
        src = self._PRE + (
            "def twice(r: Own[Rec | None]) -> Int32:\n"
            "    a = Holder(r)\n"
            "    b = Holder(r)\n"
            "    return a.value() + b.value()\n"
            "def main() -> None:\n"
            "    print(twice(Rec(6)))\n"
            "main()\n"
        )
        _assert_byte_identical(src)

    def test_plain_own_record_slot_keeps_its_own_row(self):
        # The other boundary: a non-Optional `Own[record]` slot is the plain
        # value form and rides `own.record_rvalue`, not these rows.
        src = self._PRE + (
            "def keep(r: Own[Rec]) -> Int32:\n"
            "    return r.v\n"
            "def main() -> None:\n"
            "    print(keep(Rec(2)))\n"
            "main()\n"
        )
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("own.opt_ptr_name_rebuild", 0) == 0
        _assert_byte_identical(src)


class TestBorrowTupleBareNamesFrameCarveOut:
    """`_borrow_tuple_bare_names` excludes every resumable frame name. The
    lane's owning tuple slots reach neither `storage_tuple_locals` nor a
    borrow render the AST agrees on, so admitting one would pass a storage
    source bare and drop its `tuple_to_pointer` lift."""

    SRC = _TUPLE_PRELUDE + (
        "from typing import Iterator\n"
        "def gen(items: list[tuple[T | None, T | None]]) -> Iterator[Int32]:\n"
        "    cur = items[0]\n"
        "    for cur in items:\n"
        "        yield 1\n"
        "    consume(cur)\n"
        "    yield 0\n"
        "def main() -> None:\n"
        "    t1 = T(1)\n"
        "    items: list[tuple[T | None, T | None]] = [(t1, None)]\n"
        "    for n in gen(items):\n"
        "        print(n)\n"
        "main()\n"
    )

    def test_the_frame_local_is_not_bare_bound(self):
        # A reassigned pointer-repr tuple local in a RESUMABLE body: the
        # bare-bind row must not claim it (the body rejects elsewhere today;
        # what this pins is that the widening is not the reason it routes).
        _thir, faces = _lower_ctx_witnessed(self.SRC)
        assert faces.get("arg.btuple_name", 0) == 0

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)


class TestBorrowTupleStorageNameAtTemplateSlot:
    """The lift is kind-blind: a native/template callee's tuple slot is the
    same borrow form, and `tuple_to_pointer` wraps the read rather than
    hoisting, so it applies in any position."""

    SRC = _PRELUDE + (
        "class P:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n"
        "        self.x = x\n"
        "    def __repr__(self) -> str:\n"
        "        return \"P\"\n"
        "def main() -> None:\n"
        "    p = P(1)\n"
        "    items: list[tuple[P, Int32]] = [(p, 1)]\n"
        "    for it in items:\n"
        "        print(str(it))\n"
        "    t = (p, 2)\n"
        "    print(str(t))\n"
        "main()\n"
    )

    def test_routes_and_witnesses(self):
        thir, faces = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "main") is not None
        assert faces["arg.btuple_storage_name"] >= 2

    def test_storage_names_take_the_lift(self):
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _compile, _entry
        compiler, modules = _compile(self.SRC)
        _hpp, cpp = compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   thir_codegen=True))
        # Both sources are storage form -- the loop var over a storage
        # container and the VALUE-capture literal local -- so both lift.
        wrap = "::tpy::tuple_to_pointer<std::tuple<P*, int32_t>>"
        assert f"::tpy::tuple_to_str({wrap}(it))" in cpp
        assert f"::tpy::tuple_to_str({wrap}(t))" in cpp

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)

    def test_borrow_form_name_at_a_template_slot_binds_bare(self):
        # The boundary for dropping `plain_kind`: a BORROW-form tuple name
        # (a ptr-repr tuple PARAM) must keep the bare bind at a
        # native/template slot too -- the widening must not hand the lift
        # to the sibling bare-bind row's shapes.
        src = _PRELUDE + (
            "class P:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32) -> None:\n"
            "        self.x = x\n"
            "    def __repr__(self) -> str:\n"
            "        return \"P\"\n"
            "def show(p: tuple[P, Int32]) -> None:\n"
            "    print(str(p))\n"
            "def main() -> None:\n"
            "    show((P(1), 2))\n"
            "main()\n"
        )
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("arg.btuple_storage_name", 0) == 0
        _assert_byte_identical(src)


class TestTemplateArgDropped:
    """A `@cpp_template` body that never spells `{i}` DISCARDS that arg's
    render, so no shape check on it can matter -- the expansion cannot
    contain it."""

    SRC = _PRELUDE + (
        "def main() -> None:\n"
        "    print(list(filter(None, [0, 1, 2, 0, 3])))\n"
        "main()\n"
    )

    def test_routes_and_witnesses(self):
        thir, faces = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "main") is not None
        assert faces["call.template_arg_dropped"] >= 1

    def test_the_dropped_arg_is_absent_from_the_expansion(self):
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _compile, _entry
        compiler, modules = _compile(self.SRC)
        _hpp, cpp = compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   thir_codegen=True))
        assert "::tpy::builtin_filter_truthy<int32_t>(" in cpp
        assert "nullptr" not in cpp

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)

    def test_a_referenced_template_arg_keeps_its_gate(self):
        # The boundary: `{0}` IS spelled, so the arg reaches the expansion
        # and its shape still has to be checked.
        src = _PRELUDE + (
            "def main() -> None:\n"
            "    print(repr(\"hi\"))\n"
            "main()\n"
        )
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("call.template_arg_dropped", 0) == 0
        _assert_byte_identical(src)


class TestPendingStrSlotArg:
    """A generic callee's param slot can still carry the parser's unresolved
    view var (`PendingStrType`). It renders the same `std::string_view` once
    resolved, so the str row asks the resolver rather than the nominal
    spelling."""

    SRC = (
        "from tpy import Int32, make_default\n"
        "class Rec:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32) -> None:\n"
        "        self.n = n\n"
        "def main() -> None:\n"
        "    b: str = make_default()\n"
        "    print(repr(b))\n"
        "    print(repr(\"hi\"))\n"
        "    r: Rec = Rec(1)\n"
        "    print(repr(r))\n"
        "main()\n"
    )

    def test_routes_and_witnesses(self):
        thir, faces = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "main") is not None
        # EXACTLY once: only `b`'s slot is the unresolved view var. A count
        # is what makes the boundary detectable -- the record arg's bare
        # render is identical whichever row admits it, so a string check
        # cannot tell a correct route from the peel swallowing it.
        assert faces["arg.pending_str_slot"] == 1

    def test_renders_the_bare_arg(self):
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _compile, _entry
        compiler, modules = _compile(self.SRC)
        _hpp, cpp = compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   thir_codegen=True))
        assert "::tpy::repr_of(b)" in cpp
        # The boundary rides the same program: a RECORD slot is not a str
        # slot, so the peel must not sweep it into the str row -- it keeps
        # its own bare-name render.
        assert "::tpy::repr_of(r)" in cpp

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)


class TestVarargPackAtNativeCallee:
    """`gen_call_arg` renders a vararg pack (`std::array` temp +
    `::tpy::varargs<T>(__tmp_N)`) the same way whatever the callee kind, and
    the temp hoists at the same enclosing flush point -- so the row is
    kind-blind."""

    SRC = _PRELUDE + (
        "from tpy.extern import native\n"
        "@native(\"__user_sum_ints\")\n"
        "def user_sum_ints(*xs: Int32) -> Int32: ...\n"
        "@native(\"__user_join\")\n"
        "def user_join(*parts: str) -> Int32: ...\n"
        "def plain_sum(*xs: Int32) -> Int32:\n"
        "    total = 0\n"
        "    for x in xs:\n"
        "        total += x\n"
        "    return total\n"
        "def main() -> None:\n"
        "    print(user_sum_ints(1, 2, 3))\n"
        "    print(user_join(\"a\", \"b\"))\n"
        "    print(plain_sum(4, 5))\n"
        "main()\n"
    )

    def test_routes(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "main") is not None

    def test_native_and_plain_callees_render_the_same_pack(self):
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _compile, _entry
        compiler, modules = _compile(self.SRC)
        _hpp, cpp = compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   thir_codegen=True))
        assert "::__user_sum_ints(::tpy::varargs<int32_t>(" in cpp
        # The plain callee's inferred-readonly slot spells `const int32_t`;
        # the @native one keeps its declared signature (mutated_params is
        # None for a bodyless binding). Same pack shape, different element
        # const-ness -- which is exactly the fact this case exists for.
        assert "plain_sum(::tpy::varargs<const int32_t>(" in cpp
        # A str-element pack at a native callee takes the same shape.
        assert "::__user_join(::tpy::varargs<std::string>(" in cpp

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)

    def test_view_source_element_still_rejects(self):
        # The boundary the widening must not have loosened: a str VIEW
        # source element needs the AST's owned-copy wrap, so the pack still
        # raises `call.vararg_view_elem` -- at a NATIVE callee too, now that
        # the kind check is gone.
        src = _PRELUDE + (
            "from tpy.extern import native\n"
            "@native(\"__user_join\")\n"
            "def user_join(*parts: str) -> Int32: ...\n"
            "def go(s: str) -> None:\n"
            "    print(user_join(s, \"b\"))\n"
            "def main() -> None:\n"
            "    go(\"a\")\n"
            "main()\n"
        )
        assert _fn(_lower_ctx(src), "go") is None
        assert "call.vararg_view_elem" in _fallback_reasons(src)
        _assert_byte_identical(src)


class TestOpenTypeParamProtocolFieldArg:
    """An OPEN type-param field at a still-unsubstituted protocol slot
    (`len(self.value)` inside a generic body): the member read is
    form-neutral for an open T, so the bare render is whatever the
    monomorphization resolves it to."""

    # `Sizeable.__len__` is a PROTOCOL stub with no body, so the only two
    # `len(field)` args in the program are the two under test -- one open-T,
    # one concrete. That keeps the face count unambiguous.
    SRC = _PRELUDE + (
        "from typing import Protocol\n"
        "class Sizeable(Protocol):\n"
        "    def __len__(self) -> Int32: ...\n"
        "class Msg:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32) -> None:\n"
        "        self.n = n\n"
        "    def __len__(self) -> Int32:\n"
        "        return self.n\n"
        "class Container[T: Sizeable]:\n"
        "    value: T\n"
        "    def __init__(self, value: T) -> None:\n"
        "        self.value = value\n"
        "    def describe(self) -> None:\n"
        "        print(len(self.value))\n"
        "class Holder:\n"
        "    items: list[Int32]\n"
        "    def __init__(self, items: list[Int32]) -> None:\n"
        "        self.items = items\n"
        "    def show(self) -> None:\n"
        "        print(len(self.items))\n"
        "def main() -> None:\n"
        "    Container(Msg(2)).describe()\n"
        "    Holder([1, 2, 3]).show()\n"
        "main()\n"
    )

    def test_routes_and_witnesses(self):
        _thir, faces = _lower_ctx_witnessed(self.SRC)
        # The open-T field witnesses TWICE (the predicate runs at the gate
        # and again at the lowering arm) and the CONCRETE field lands on the
        # exact-match row instead. Pinning both COUNTS is what makes the
        # boundary detectable: the two renders are textually identical, so
        # dropping the `TypeParamRef` restriction would move `self.items`
        # across without changing a single character of C++ -- it would show
        # up here as 3-and-0 rather than 2-and-1.
        assert faces["arg.native_protocol_open_field"] == 2
        assert faces["arg.native_protocol_field"] == 1

    def test_both_field_kinds_render_the_bare_member_read(self):
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _compile, _entry
        compiler, modules = _compile(self.SRC)
        hpp, cpp = compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   thir_codegen=True))
        both = hpp + cpp
        assert "::tpy::__len__(this->value)" in both
        assert "::tpy::__len__(this->items)" in both

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)


class TestNativeProtocolTupleLiteralArg:
    """A tuple LITERAL at a native callee's protocol slot: the monomorphized
    slot threads no target, so the literal spells its own sema type with
    every element captured BY VALUE -- the storage form."""

    SRC = _PRELUDE + (
        "from tpy import UInt64\n"
        "class Box:\n"
        "    val: Int32\n"
        "    def __init__(self, v: Int32) -> None:\n"
        "        self.val = v\n"
        "    def __hash__(self) -> UInt64:\n"
        "        return UInt64(self.val)\n"
        "def main() -> None:\n"
        "    b = Box(5)\n"
        "    t = (1, b)\n"
        "    print(hash(t) == hash((1, Box(5))))\n"
        "    print(hash((1, 2)) == hash((1, 2)))\n"
        "    print(hash(7) == hash(7))\n"
        "main()\n"
    )

    def test_routes_and_witnesses(self):
        _thir, faces = _lower_ctx_witnessed(self.SRC)
        # EXACTLY six: three tuple literals reach the slot (one
        # record-element, two value-only), each witnessing at the gate and
        # again at the lowering arm. The count is the boundary -- the two
        # bare `hash(7)` scalars ride the shared resolved-scalar row, and
        # nothing in the emitted C++ would change if this row swallowed
        # them, so only a count catches that.
        assert faces["arg.native_protocol_tuple_literal"] == 6

    def test_spells_the_resolved_storage_type(self):
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _compile, _entry
        compiler, modules = _compile(self.SRC)
        _hpp, cpp = compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   thir_codegen=True))
        # The element literal's type must be RESOLVED before spelling --
        # the unresolved form would emit `std::tuple<1, Box>`.
        assert "::tpy::__hash__(std::tuple<int32_t, Box>{1, Box(5)})" in cpp

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)


class TestOwnElemSlotCallBindsBare:
    """An owning CALL whose result IS the `Own[...]` element slot binds
    bare. Split from the Ptr-row fixture below so this half's render
    assertions are ROUTED evidence, not AST re-emit."""

    SRC = _PRELUDE + (
        "from tpy import Own\n"
        "from tplib.rc import Rc\n"
        "class Node:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n"
        "        self.x = x\n"
        "def make_pair(i: Int32, v: Int32) -> Own[tuple[Int32, Rc[Node]]]:\n"
        "    return (i, Rc.new(Node(v)))\n"
        "def main() -> None:\n"
        "    pairs: list[tuple[Int32, Rc[Node]]] = []\n"
        "    pairs.append(make_pair(1, 10))\n"
        "    print(len(pairs))\n"
        "main()\n"
    )

    def test_routes_and_renders_bare(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "pairs.push_back(make_pair(1, 10));" in cpp
        assert "tuple_to_storage" not in cpp

    def test_witnesses_the_row_once(self):
        _thir, faces = _lower_ctx_witnessed(self.SRC)
        assert faces["arg.own_tuple_call_rvalue"] == 1


class TestPtrElemSlotInsertRows:
    """A record-element lvalue at a `Ptr` slot takes the `&(...)` lift; an
    already-pointer source passes bare. The body still falls back on an
    unrelated blocker (`p0 = ps[0]`), so the render assertions here are
    AST-oracle statements, not routing evidence -- the face counts are the
    lowering-side claim."""

    SRC = _PRELUDE + (
        "from tpy import Ptr\n"
        "class Node:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n"
        "        self.x = x\n"
        "def main() -> None:\n"
        "    items = [Node(1), Node(2)]\n"
        "    ps: list[Ptr[Node]] = []\n"
        "    ps.append(items[0])\n"
        "    print(len(ps))\n"
        "    p0 = ps[0]\n"
        "    ps.append(p0)\n"
        "    print(len(ps))\n"
        "main()\n"
    )

    def test_witnesses_the_lift_row_once(self):
        _thir, faces = _lower_ctx_witnessed(self.SRC)
        # EXACTLY once: `ps.append(p0)` feeds an ALREADY-pointer source to
        # the same slot and must ride `_ptr_pass_through_arg`, whose render
        # (`push_back(p0)`) differs from the lift only in the absence of
        # `&` -- so a count catches an over-capture that a lax `>= 1`
        # would not.
        assert faces["arg.ptr_addr_of_elem"] == 1

    def test_renders_the_lift_and_the_pass_through(self):
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _compile, _entry
        compiler, modules = _compile(self.SRC)
        _hpp, cpp = compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   thir_codegen=True))
        assert "ps.push_back(&::tpy::__getitem__(items, 0));" in cpp
        assert "ps.push_back(p0);" in cpp

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)


class TestPendingLocalAtProtocolSlot:
    """A literal-seeded local (`xs = [1, 2]`) is still `PendingListType` when
    it reaches a protocol param slot. Sema's resolution is final by lowering
    time, so asking for it gives the same type the AST reaches at its own
    later render point."""

    SRC = (
        "from typing import Iterator, Iterable\n"
        "def echo(it: Iterable[int]) -> Iterator[int]:\n"
        "    for x in it:\n"
        "        yield x\n"
        "def total(it: Iterable[int]) -> int:\n"
        "    n = 0\n"
        "    for x in it:\n"
        "        n += x\n"
        "    return n\n"
        "def main() -> None:\n"
        "    xs = [1, 2]\n"
        "    print(total(xs))\n"
        "    for v in echo(xs):\n"
        "        print(v)\n"
        "    ys: list[int] = [3, 4]\n"
        "    print(total(ys))\n"
        "main()\n"
    )

    def test_routes(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "main") is not None

    def test_the_pending_and_annotated_locals_render_alike(self):
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _compile, _entry
        compiler, modules = _compile(self.SRC)
        _hpp, cpp = compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   thir_codegen=True))
        # Both bind the protocol slot bare -- resolution changed the TYPE
        # the temp machinery sees, never the arg render.
        assert "total(xs)" in cpp
        assert "total(ys)" in cpp

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)


class TestInstantiationArgMovability:
    """The consuming `own_iter(std::move(x))` wrap at a container
    instantiation keys on MOVABILITY, not on last use alone. A borrowed
    param is a last use but never movable, and the AST renders it bare."""

    SRC = (
        "from tpy import Own\n"
        "def pairs_of() -> Own[list[str]]:\n"
        "    return [\"a\", \"b\"]\n"
        "def borrowed_param(items: list[str]) -> int:\n"
        "    copy = list(items)\n"
        "    return len(copy)\n"
        "def movable_local() -> int:\n"
        "    xs = pairs_of()\n"
        "    ys = list(xs)\n"
        "    return len(ys)\n"
        "def main() -> None:\n"
        "    print(borrowed_param([\"a\"]))\n"
        "    print(movable_local())\n"
        "main()\n"
    )

    def test_both_bodies_route_through_their_own_arms(self):
        # The load-bearing pin. THIR is byte-identical by design, so a
        # silent whole-body fallback reproduces the same C++ -- neither the
        # substring checks below nor byte-identity can tell "lowered
        # through this arm" from "fell back". Only routing + the two faces
        # can, which is why the bare arm was given a face of its own.
        thir, faces = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "borrowed_param") is not None
        assert _fn(thir, "movable_local") is not None
        assert faces["call.inst_bare_name_arg"] == 1
        assert faces["call.own_iter_arg"] == 1

    def test_borrowed_param_renders_bare_and_movable_local_wraps(self):
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _compile, _entry
        compiler, modules = _compile(self.SRC)
        _hpp, cpp = compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   thir_codegen=True))
        ctor = "::tpy::construct<std::vector<std::string>>"
        assert f"{ctor}(items)" in cpp
        assert f"{ctor}(::tpy::own_iter(std::move(xs)))" in cpp

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)
