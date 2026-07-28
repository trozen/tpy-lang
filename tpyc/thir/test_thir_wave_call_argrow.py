"""Pins for the free-call ARG-ROW frontier: the shapes the plain / native arg
disjunctions used to reject outright, plus the neighbours that must keep
falling back."""

from __future__ import annotations

from .testutil import (
    _assert_byte_identical,
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
        thir, faces = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "main") is not None
        assert faces["arg.native_int_literal"] >= 1

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)

    def test_literal_binop_arg_stays_ast(self):
        # The boundary: only a BARE literal takes the slot-threaded render.
        # A constant-valued BINOP keeps IntLiteralType through sema but the
        # AST renders it as an ordinary binop, so it must keep rejecting.
        src = _PRELUDE + (
            "def main() -> None:\n"
            "    ba = bytearray(4000000000 // 1000000000)\n"
            "    print(len(ba))\n"
        )
        assert _fn(_lower_ctx(src), "main") is None


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

    def test_the_arg_gate_no_longer_rejects(self):
        # This widening moves the reject one layer DOWN -- the body still
        # falls back, at the module-var read rather than at the arg gate.
        # That shift is the whole effect, and it is the only thing a pin can
        # honestly assert: a render check would pass under fallback too,
        # since the AST emits the same bytes.
        assert _fallback_reasons(self.SRC) == {"field.module_var_type"}

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

    def test_the_arg_gate_no_longer_rejects(self):
        # As with the module-var row: the reject moves from the arg gate to
        # the ternary's own lowering, which is not ported. The body still
        # falls back, so a render assertion here would be checking AST
        # output. `_own_move_source_slice` requires a TpyName, so the
        # ternary can only ever reach the copy-temp half, never the
        # temp-free move -- that exclusion is what the widening relies on.
        assert _fallback_reasons(self.SRC) == {"expr.ifexpr"}

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
