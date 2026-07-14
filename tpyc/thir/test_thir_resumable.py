"""Resumable (async) leaf routing -- the gen_async skeleton seam.

Pins the foundation slice: routed bodies are byte-identical to the AST
path with every leaf render witnessed, and each sliced-out shape rejects
with its named `res.*` reason (falling back to the AST leaves) instead of
routing wrong."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .testutil import _compile, _entry

_PRE = "from tpy import Int32, Int64\n\n"


def _gen(src: str, thir: bool):
    compiler, modules = _compile(src)
    entry = _entry(modules)
    hpp, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=True,
                                      thir_codegen=thir))
    return compiler, hpp, cpp


def _assert_identical(src: str) -> 'tuple[dict, dict]':
    """Byte-compare THIR vs AST output; return (witnesses, fallback)."""
    _, hpp_ast, cpp_ast = _gen(src, thir=False)
    c, hpp_thir, cpp_thir = _gen(src, thir=True)
    assert hpp_ast == hpp_thir
    assert cpp_ast == cpp_thir
    return c._thir_face_witnesses, c._thir_fallback


def _res_fallback(src: str) -> dict:
    c, _hpp, _cpp = _gen(src, thir=True)
    return {k.split(":", 1)[1]: n for k, n in c._thir_fallback.items()
            if k.startswith("resumable:")}


BASIC = (_PRE
         + "async def step(n: Int32) -> Int32:\n"
         + "    return n + 1\n\n"
         + "async def runner(a: Int32) -> Int32:\n"
         + "    n = a\n"
         + "    total: Int32 = 0\n"
         + "    big: Int64 = 0\n"
         + "    while n < 3:\n"
         + "        n = await step(n)\n"
         + "        if n > 1:\n"
         + "            print(n)\n"
         + "        total = total + n\n"
         + "        big = big + Int64(n)\n"
         + "    print(total)\n"
         + "    return total\n\n"
         + "def main() -> None:\n    pass\nmain()\n")


class TestRoutedFoundation:
    def test_byte_identical_and_witnessed(self):
        witnesses, fallback = _assert_identical(BASIC)
        assert witnesses.get("res.body") == 2  # step + runner
        # Only the decomposed while's condition is a Branch terminator; the
        # suspension-free `if` stays a leaf compound (lowered whole).
        assert witnesses.get("res.branch_cond", 0) >= 1
        assert witnesses.get("res.await_args", 0) >= 1
        assert witnesses.get("res.return_value", 0) >= 2
        # Top-level frame-field decls (n / total / big) take the decl-arm
        # render with the assignment shape -- the BigInt-family regression
        # pin (`big = 0;`, never `::tpy::BigInt(0)`).
        assert witnesses.get("res.decl_assign", 0) >= 3
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_await_kinds_route(self):
        # DISCARD (expr-stmt await), RETURN (`return await f()`), and a
        # zero-arg awaited callee all stay byte-identical and routed.
        src = (_PRE
               + "async def zero() -> Int32:\n    return 1\n\n"
               + "async def go() -> Int32:\n"
               + "    await zero()\n"
               + "    return await zero()\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, _ = _assert_identical(src)
        assert witnesses.get("res.body") == 2

    def test_literal_return_is_position_blind(self):
        # `_make_async_return` binds the value to a typed ret_tmp local, so a
        # literal at a wider slot stays bare (`__tpy_async_ret = 42;`), not
        # the sync return arm's target-typed `::tpy::BigInt(42)`. Regression
        # for the BigInt-return divergence the corpus byte-diff caught.
        src = ("async def f(n: int) -> int:\n"
               + "    x = n\n"
               + "    return 42\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _assert_identical(src)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "::tpy::BigInt __tpy_async_ret = 42;" in cpp

    def test_raise_terminator_routes(self):
        src = (_PRE
               + "async def boom(n: Int32) -> Int32:\n"
               + "    if n > 0:\n"
               + "        raise ValueError(\"neg\")\n"
               + "    return n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, _ = _assert_identical(src)
        assert witnesses.get("res.body") == 1


class TestMethodCoros:
    METHOD = (_PRE
              + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
              + "class C:\n"
              + "    base: Int32\n"
              + "    def __init__(self, b: Int32) -> None:\n        self.base = b\n"
              + "    def bump(self, x: Int32) -> Int32:\n        return x + self.base\n"
              + "    async def run(self, a: Int32) -> Int32:\n"
              + "        total = self.bump(a)\n"       # self-method call
              + "        while total < a:\n"
              + "            total = await step(total)\n"
              + "        self.base = total\n"          # self-field write
              + "        return total + self.base\n\n"  # self-field read
              + "def main() -> None:\n    pass\nmain()\n")

    def test_method_coro_routes_byte_identical(self):
        witnesses, fallback = _assert_identical(self.METHOD)
        assert witnesses.get("res.body") == 2  # step (free) + run (method)
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_self_renders_as_reference_field(self):
        # __self is a `Record&` frame field, so self.x renders `.` not `->`
        # (the plain-method `this->x`); the seam must produce __self, never
        # this, inside a coro body.
        c, _hpp, cpp = _gen(self.METHOD, thir=True)
        assert "__self.base = total;" in cpp
        assert "total = __self.bump(a);" in cpp
        assert "this->" not in cpp.split("__coro_C_run")[-1].split("};")[0] \
            if "__coro_C_run" in cpp else True

    def test_record_param_on_method_coro(self):
        # R2 (__self) + R5c-param (F1-record param) compose: a method coro
        # with both `self` and a record-typed non-self param reads `__self.x`
        # and the bare record param `b.get()` in the same body.
        src = (_PRE
               + "class Box2:\n"
               + "    v: Int32\n"
               + "    def __init__(self, v: Int32) -> None:\n        self.v = v\n"
               + "    def get(self) -> Int32:\n        return self.v\n\n"
               + "class Svc:\n"
               + "    base: Int32\n"
               + "    def __init__(self, b: Int32) -> None:\n        self.base = b\n"
               + "    async def step(self, n: Int32) -> Int32:\n"
               + "        return n + self.base\n"
               + "    async def run(self, b: Box2, n: Int32) -> Int32:\n"
               + "        total = n\n"
               + "        while total < 3:\n"
               + "            total = await self.step(total)\n"
               + "        return total + b.get() + self.base\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.body") == 2
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "b.get()" in cpp        # record param: bare `.`
        assert "__self.base" in cpp    # self: __self reference

    def test_generic_record_method_rejects(self):
        # (async @staticmethod / @property are parse-rejected upstream, so
        # res.static_method / res.property stay defensive.) A method coro on
        # a GENERIC record folds the record's [T, ...] into a template frame
        # -- deferred (res.generic_record).
        gen_src = (_PRE
                   + "class G[T]:\n"
                   + "    v: T\n"
                   + "    def __init__(self, v: T) -> None:\n        self.v = v\n"
                   + "    async def get(self, n: Int32) -> Int32:\n        return n\n\n"
                   + "def main() -> None:\n    pass\nmain()\n")
        assert _res_fallback(gen_src).get("res.generic_record") == 1


_ITER = "from tpy import Int32, Int64\nfrom typing import Iterator\n\n"


class TestGeneratorShape:
    # A non-simple generator (>1 yield / control flow around the yield) uses
    # the resumable skeleton; its leaf seam is only the yield value (bare
    # returns lower to StopIteration, skeleton-only).
    MULTI = (_ITER
             + "def counter(n: Int32) -> Iterator[Int64]:\n"
             + "    total = 0\n"
             + "    yield 99\n"           # literal at wider slot -> baked coerce
             + "    total = total + n\n"
             + "    if total > 5:\n"
             + "        yield total\n"     # yield in a leaf branch
             + "    yield total\n\n"
             + "def main() -> None:\n"
             + "    for x in counter(3):\n        print(x)\nmain()\n")

    def test_generator_routes_byte_identical(self):
        witnesses, fallback = _assert_identical(self.MULTI)
        assert witnesses.get("res.body") == 1
        assert witnesses.get("res.yield_value") == 3
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_generator_stop_iteration_is_skeleton(self):
        # The fall-off-end StopIteration return carries no leaf value.
        _, _hpp, cpp = _gen(self.MULTI, thir=True)
        assert "::tpy::make_unexpected(::tpy::StopIteration{});" in cpp

    def test_while_generator_routes(self):
        # A while-loop generator with >1 yield (so not the peephole) and a
        # yield in a leaf branch -- routes through the resumable seam.
        src = (_ITER
               + "def gen(n: Int32) -> Iterator[Int32]:\n"
               + "    i = 0\n"
               + "    while i < n:\n"
               + "        yield i\n"
               + "        if i > 1:\n"
               + "            yield i + i\n"
               + "        i = i + 1\n\n"
               + "def main() -> None:\n"
               + "    for x in gen(5):\n        print(x)\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.body") == 1
        assert witnesses.get("res.yield_value") == 2
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_generator_leaf_return_defers(self):
        # A bare `return` nested in a leaf compound rejects (res.leaf_return),
        # same as an async body -- leaf-nested returns are deferred.
        src = (_ITER
               + "def gen(n: Int32) -> Iterator[Int32]:\n"
               + "    i = 0\n"
               + "    while i < n:\n"
               + "        yield i\n"
               + "        if i == 2:\n"
               + "            return\n"
               + "        i = i + 1\n\n"
               + "def main() -> None:\n"
               + "    for x in gen(5):\n        print(x)\nmain()\n")
        _, fallback = _assert_identical(src)
        assert fallback.get("resumable:res.leaf_return") == 1

    def test_simple_generator_stays_peephole(self):
        # A single-yield-in-a-loop generator uses the lambda peephole, not
        # the resumable seam -- it routes through the sgen leaf seam and must
        # NOT tally the resumable component (see test_thir_simple_gen.py for
        # the peephole seam's own pins).
        src = (_ITER
               + "def gen(n: Int32) -> Iterator[Int32]:\n"
               + "    i = 0\n"
               + "    while i < n:\n"
               + "        yield i\n"
               + "        i = i + 1\n\n"
               + "def main() -> None:\n"
               + "    for x in gen(3):\n        print(x)\nmain()\n")
        c, _hpp, _cpp = _gen(src, thir=True)
        assert c._thir_face_witnesses.get("sgen.body") == 1
        assert not any(k.startswith("resumable:") for k in c._thir_fallback)

    def test_generator_method_routes(self):
        # A generator METHOD composes R2's __self machinery with R4's yield
        # seam -- the yields read self.base as __self.base, byte-identical.
        src = (_ITER
               + "class C:\n"
               + "    base: Int32\n"
               + "    def __init__(self, b: Int32) -> None:\n        self.base = b\n"
               + "    def counts(self, n: Int32) -> Iterator[Int32]:\n"
               + "        i = 0\n"
               + "        yield self.base\n"
               + "        while i < n:\n"
               + "            yield i + self.base\n"
               + "            i = i + 1\n\n"
               + "def main() -> None:\n"
               + "    c = C(7)\n"
               + "    for x in c.counts(3):\n        print(x)\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.body") == 1
        assert witnesses.get("res.yield_value") == 2
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "return __self.base;" in cpp

    def test_generator_method_with_frame_slot_local(self):
        # R4b (generator method) + R1c (frame_slot record local) compose:
        # __self reads and a frame_slot local's `.emplace()`/`(*a)` reads in
        # the same body.
        src = (_ITER
               + "class Acc:\n"
               + "    v: Int32\n"
               + "    def __init__(self, v: Int32) -> None:\n        self.v = v\n"
               + "    def get(self) -> Int32:\n        return self.v\n\n"
               + "class C:\n"
               + "    base: Int32\n"
               + "    def __init__(self, b: Int32) -> None:\n        self.base = b\n"
               + "    def counts(self, n: Int32) -> Iterator[Int32]:\n"
               + "        a = Acc(self.base)\n"
               + "        yield a.get()\n"
               + "        i = 0\n"
               + "        while i < n:\n"
               + "            yield i + self.base\n"
               + "            i = i + 1\n\n"
               + "def main() -> None:\n"
               + "    c = C(7)\n"
               + "    for x in c.counts(3):\n        print(x)\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.body") == 1
        assert witnesses.get("res.frame_slot_write") == 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "a.emplace(Acc(__self.base));" in cpp
        assert "(*a).get()" in cpp


class TestLocalStorage:
    def test_str_bytes_locals_route(self):
        # R1a: str/bytes frame locals are bare `std::string` / view fields,
        # reusing THIR's ported str slice -- no frame_slot machinery.
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    s = \"hello\"\n"
               + "    s2 = s\n"
               + "    b = b\"xy\"\n"
               + "    total = n\n"
               + "    while total < 3:\n"
               + "        total = await step(total)\n"
               + "    return total + Int32(len(s)) + Int32(len(s2)) + Int32(len(b))\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.body") == 2
        assert witnesses.get("res.decl_assign", 0) >= 4
        assert not any(k.startswith("resumable:") for k in fallback)

    RECORD = (_PRE
              + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
              + "class Acc:\n"
              + "    v: Int32\n"
              + "    def __init__(self, v: Int32) -> None:\n        self.v = v\n"
              + "    def add(self, x: Int32) -> None:\n        self.v = self.v + x\n"
              + "    def get(self) -> Int32:\n        return self.v\n\n"
              + "async def f(n: Int32) -> Int32:\n"
              + "    a = Acc(n)\n"                     # frame_slot: a.emplace(...)
              + "    total = n\n"
              + "    while total < 3:\n"
              + "        total = await step(total)\n"
              + "    a.add(total)\n"                   # (*a).add(...)
              + "    return a.get()\n\n"               # (*a).get()
              + "def main() -> None:\n    pass\nmain()\n")

    def test_frame_slot_record_routes(self):
        # R1c: an owning (non-alias) record local is a frame_slot -- write
        # `a.emplace(...)`, reads `(*a).method()`.
        witnesses, fallback = _assert_identical(self.RECORD)
        assert witnesses.get("res.body") == 2
        assert witnesses.get("res.frame_slot_write") == 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(self.RECORD, thir=True)
        assert "a.emplace(Acc(n));" in cpp
        assert "(*a).add(total);" in cpp
        assert "(*a).get();" in cpp

    def test_frame_slot_container_brace_init_prefix(self):
        # R1c container frame_slot: a list-literal init renders as a bare
        # brace `{n, n}` that the emplace write must PREFIX with the slot
        # type (typed_brace_init) so it binds to emplace's forwarding ref --
        # the exact arm the byte-diff caught. Pins the rendered C++, not just
        # byte-identity (the render arm is otherwise corpus-incidental).
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    xs = [n, n]\n"
               + "    total = n\n"
               + "    while total < 3:\n"
               + "        total = await step(total)\n"
               + "    xs.append(total)\n"
               + "    return Int32(len(xs))\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.frame_slot_write") == 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "xs.emplace(std::vector<int32_t>{n, n});" in cpp
        assert "(*xs).push_back(total);" in cpp


class TestAwaitModes:
    def test_bound_method_await_receiver_routes(self):
        # R5b: `await s.method(n)` prepends the receiver `(*s)` as the __self
        # ctor arg -- the receiver renders through the leaf.
        src = (_PRE
               + "class Svc:\n"
               + "    base: Int32\n"
               + "    def __init__(self, b: Int32) -> None:\n        self.base = b\n"
               + "    async def call(self, n: Int32) -> Int32:\n"
               + "        return n + self.base\n\n"
               + "async def g(n: Int32) -> Int32:\n"
               + "    s = Svc(n)\n"
               + "    r = await s.call(n)\n"
               + "    return r\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.suspend_expr", 0) >= 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "__sub_0.emplace((*s), n);" in cpp


    def test_record_param_routes(self):
        # A record param captures as a `Record&` reference frame field and
        # reads bare with `.` -- exactly like a sync record param, so it
        # routes with just the param gate widen (no coro-specific form).
        src = (_PRE
               + "class Box2:\n"
               + "    v: Int32\n"
               + "    def __init__(self, v: Int32) -> None:\n        self.v = v\n"
               + "    def get(self) -> Int32:\n        return self.v\n\n"
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(b: Box2, n: Int32) -> Int32:\n"
               + "    total = n\n"
               + "    while total < 3:\n"
               + "        total = await step(total)\n"
               + "    return total + b.get() + b.v\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.body") == 2
        assert not any(k.startswith("resumable:") for k in fallback)


class TestSlicedOutShapes:
    def test_str_param_still_defers(self):
        # A str param captures OWNED (std::string field), so its read form
        # differs from the sync view param's BORROW -- still deferred until
        # the coro-owned-str-param form rung.
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(s: str, n: Int32) -> Int32:\n"
               + "    total = n\n"
               + "    while total < 3:\n"
               + "        total = await step(total)\n"
               + "    return total + Int32(len(s))\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        assert _res_fallback(src).get("res.param_type") == 1

    def test_nonvalue_param_rejects(self):
        src = (_PRE
               + "async def f(s: str) -> Int32:\n    return len(s)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        assert _res_fallback(src).get("res.param_type") == 1

    def test_optional_local_still_defers(self):
        # A pointer-repr Optional[record] local is not a frame_slot (it has
        # its own T*/nullptr form) -- still deferred (res.local_storage).
        src = (_PRE
               + "class R:\n"
               + "    v: Int32\n"
               + "    def __init__(self, v: Int32) -> None:\n        self.v = v\n\n"
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    o: R | None = None\n"
               + "    total = n\n"
               + "    while total < 3:\n"
               + "        total = await step(total)\n"
               + "    return total\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        assert _res_fallback(src).get("res.local_storage") == 1

    def test_lowering_reject_falls_back_after_await(self):
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    n = await step(n)\n"
               + "    xs = [n]\n"
               + "    del xs\n"
               + "    return n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _, fallback = _assert_identical(src)
        # `xs` is a resumable frame slot -- the del move-sink has no mirrored
        # frame-slot render, so the body stays AST.
        assert fallback.get("resumable:stmt.del_var:binding") == 1

    def test_global_lowering_reject_falls_back_after_await(self):
        src = (_PRE
               + "message = 'before'\n\n"
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    global message\n"
               + "    n = await step(n)\n"
               + "    return n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _, fallback = _assert_identical(src)
        assert fallback.get("resumable:stmt.global:global.unseeded") == 1

    def test_raise_lowering_reject_falls_back_after_await(self):
        src = (_PRE
               + "err = ValueError('bad')\n\n"
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    n = await step(n)\n"
               + "    raise err\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _, fallback = _assert_identical(src)
        assert fallback.get("resumable:stmt.raise") == 1

    def test_wide_numeric_literals_route_after_await(self):
        src = (_PRE
               + "from tpy import UInt64\n\n"
               + "def widen(n: Int64) -> Int64:\n    return n\n\n"
               + "def widen_u(n: UInt64) -> UInt64:\n    return n\n\n"
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int64:\n"
               + "    n = await step(n)\n"
               + "    return widen(2147483648)\n\n"
               + "async def minimum(n: Int32) -> Int64:\n"
               + "    n = await step(n)\n"
               + "    return -9223372036854775808\n\n"
               + "async def maximum(n: Int32) -> UInt64:\n"
               + "    n = await step(n)\n"
               + "    return widen_u(18446744073709551615)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "static_cast<int64_t>(2147483648)" in cpp
        assert "static_cast<int64_t>((-9223372036854775807LL - 1))" in cpp
        assert "static_cast<uint64_t>(18446744073709551615ull)" in cpp

    def test_unhandled_expression_lowering_reject_falls_back_after_await(self):
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    n = await step(n)\n"
               + "    x = (y := n)\n"
               + "    return x\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _, fallback = _assert_identical(src)
        assert fallback.get("resumable:expr.named_expr") == 1

    def test_try_region_rejects(self):
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    try:\n        n = await step(n)\n"
               + "    except ValueError:\n        n = 0\n"
               + "    return n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        fb = _res_fallback(src)
        assert fb.get("res.region") == 1
        assert fb.get("res.method") is None

    def test_nested_frame_write_rejects(self):
        # A name-write nested inside a leaf compound would take the shared
        # assign arm's target-typed render (`::tpy::BigInt(7)`) where the
        # AST frame arm renders position-blind (`7`) -- rejected until the
        # R1 cell threads the frame fact through the shared lowering.
        # Byte-identity still holds (the body falls back whole).
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    n = await step(n)\n"
               + "    if n > 1:\n        n = n + 1\n"
               + "    print(n)\n    return n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _, fallback = _assert_identical(src)
        assert fallback.get("resumable:res.leaf_field_write") == 1

    def test_leaf_return_rejects(self):
        # A return nested in a suspension-free leaf compound would need the
        # async-return scaffolding inside THIR emit -- sliced out.
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    n = await step(n)\n"
               + "    if n > 2:\n        return 99\n"
               + "    return n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        assert _res_fallback(src).get("res.leaf_return") == 1

    def test_return_await_bound_method_routes(self):
        # `return await g.hi()` -- a RETURN-kind bound-method await of a
        # frame_slot receiver: G.hi routes (R2), g routes as a frame_slot
        # (R1c), and the await receiver routes (R5b), so the whole body routes.
        src = (_PRE
               + "class G:\n"
               + "    x: Int32\n"
               + "    def __init__(self) -> None:\n        self.x = 1\n"
               + "    async def hi(self) -> Int32:\n        return self.x\n\n"
               + "async def f() -> Int32:\n"
               + "    g = G()\n"
               + "    return await g.hi()\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.body") == 2
        assert not any(k.startswith("resumable:") for k in fallback)

    # (Non-simple generators route via this seam -- see TestGeneratorShape.
    # Simple peephole generators route via their own leaf seam, pinned in
    # test_thir_simple_gen.py.)
