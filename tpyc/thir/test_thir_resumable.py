"""Resumable (async) leaf routing -- the gen_async skeleton seam.

Pins the foundation slice: routed bodies are byte-identical to the AST
path with every leaf render witnessed, and each sliced-out shape rejects
with its named `res.*` reason (falling back to the AST leaves) instead of
routing wrong."""

from __future__ import annotations

import pytest

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


class TestDeclRegistrationOrder:
    """The CFG builder allocates join BBs BEFORE the body BBs they join, so
    a frame-field decl in a BB created past a suspension (higher id than the
    join) must already be registered when the join's leaves lower -- the
    pass-1 scope registration pins this (a walk-order-only registration
    rejected the join's read as an unknown name)."""

    def test_decl_after_suspension_read_after_join(self):
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    if n > 0:\n"
               + "        n = await step(n)\n"
               + "        x = n + 1\n"
               + "    else:\n"
               + "        return 0\n"
               + "    return x\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.body") == 2
        assert not any(k.startswith("resumable:") for k in fallback)


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

    def test_generic_record_method_routes(self):
        # (async @staticmethod / @property are parse-rejected upstream, so
        # res.static_method / res.property stay defensive.) A method coro on
        # a GENERIC record routes: the record's [T, ...] template header is
        # skeleton, so the leaves render as on a concrete record.
        gen_src = (_PRE
                   + "class G[T]:\n"
                   + "    v: T\n"
                   + "    def __init__(self, v: T) -> None:\n        self.v = v\n"
                   + "    async def get(self, n: Int32) -> Int32:\n        return n\n\n"
                   + "def main() -> None:\n    pass\nmain()\n")
        _, fallback = _assert_identical(gen_src)
        assert not [k for k in fallback if k.startswith("resumable:")]


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

    def test_bigint_literal_yield_is_target_typed(self):
        # `gen_yield_value` threads the yield type into the render, so a bare
        # literal at a BigInt slot spells the ctor wrap (`::tpy::BigInt(1)`),
        # not the position-blind `1` -- the divergence the corpus byte-diff
        # caught once with-regions admitted generator bodies carrying it.
        src = ("from typing import Iterator\n\n"
               "def gen(n: int) -> Iterator[int]:\n"
               "    yield 1\n"
               "    yield n\n\n"
               "def main() -> None:\n"
               "    for x in gen(3):\n        print(x)\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.yield_value") == 2
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "return ::tpy::BigInt(1);" in cpp

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

    def test_generator_leaf_return_routes(self):
        # A bare `return` nested in a leaf compound routes: scaffolding
        # (done state + StopIteration) stays skeleton via the return hook
        # (_make_generator_resumable_return), THIR marks the position.
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
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.nested_return") == 1
        assert not any(k.startswith("resumable:") for k in fallback)

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


    def test_erased_sleep_operand_routes(self):
        # ERASED await (`await asyncio.sleep(..)`): the whole operand renders
        # through the leaf; the skeleton keeps its emplace(std::move(..))
        # wrap. The Own[Task[None]] result is admitted only at SUSPEND use
        # (moved_ret_ok) -- the same call in a value slot still rejects.
        src = ("import asyncio\n\n"
               "async def snooze() -> None:\n"
               "    await asyncio.sleep(0.01)\n\n"
               "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.suspend_operand", 0) >= 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert (".emplace(std::move(::tpystd::asyncio::sleep(0.01)));"
                in cpp)

    def test_erased_gather_vararg_operand_routes(self):
        # A vararg pack inside an ERASED await operand (`await
        # asyncio.gather(t1, t2)`): the emplace is a statement position, so
        # the pack's std::array temp flushes before the suspend line exactly
        # where the AST flushes it.
        src = ("import asyncio\n"
               "from tpy import Int32\n\n"
               "async def fetch(n: Int32) -> Int32:\n"
               "    await asyncio.sleep(0.001)\n"
               "    return n * 2\n\n"
               "async def go() -> None:\n"
               "    t1 = asyncio.create_task(fetch(1))\n"
               "    t2 = asyncio.create_task(fetch(2))\n"
               "    rs = await asyncio.gather(t1, t2)\n"
               "    for r in rs:\n"
               "        print(r)\n\n"
               "def main() -> None:\n    asyncio.run(go())\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.suspend_operand", 0) >= 1
        assert witnesses.get("vararg.pack_ref", 0) >= 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "std::array<::tpystd::asyncio::_executor::Task<int32_t>*, 2> __tmp_1{&(*t1), &(*t2)};" in cpp

    def test_await_arg_families_route(self):
        # Await-arg slots share the DIRECT-param families (str/bytes/
        # F1-record) -- the emplace ctor param is the sync borrow shape, so
        # the args take the same `_lower_call_arg` rows as a sync call.
        src = (_PRE
               + "class R:\n"
               + "    v: Int32\n"
               + "    def __init__(self, v: Int32) -> None:\n        self.v = v\n\n"
               + "async def use(r: R, s: str) -> Int32:\n"
               + "    return r.v + Int32(len(s))\n\n"
               + "async def go(r: R) -> Int32:\n"
               + "    return await use(r, \"ab\")\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.await_args", 0) >= 1
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_subscript_optional_ptr_await_arg_routes(self):
        # A record-element lvalue subscript (`items[i]`) into a pointer-repr
        # Optional[record] await-arg slot lifts `&(::tpy::__getitem__(...))`
        # -- the subscript optional-ptr face, shared with the sync call path.
        src = (_PRE
               + "class P:\n"
               + "    n: Int32\n"
               + "    def __init__(self, n: Int32) -> None:\n        self.n = n\n\n"
               + "async def echo(n: Int32) -> Int32:\n    return n\n\n"
               + "async def takes(p: P | None) -> Int32:\n"
               + "    if p is not None:\n        return await echo(p.n)\n"
               + "    return await echo(-1)\n\n"
               + "async def driver() -> Int32:\n"
               + "    items: list[P] = []\n"
               + "    items.append(P(5))\n"
               + "    return await takes(items[0])\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "&(::tpy::__getitem__((*items), 0))" in cpp

    def test_erased_ternary_operand_routes(self):
        # A ternary of task handles as the ERASED await operand: the record
        # ifexpr arm (Form-threading wave) lowers the both-name ternary to a
        # BORROW lvalue the erased operand consumes -- byte-identical,
        # converted from the former reject-composition fence per the
        # un-defer rule. (The former walrus-arg witness routes
        # byte-identically now that the resolved-scalar arg row admits
        # literal-typed walruses.)
        src = ("import asyncio\n"
               + _PRE
               + "async def tick() -> Int32:\n"
               + "    await asyncio.sleep(0.01)\n"
               + "    return 1\n\n"
               + "async def snooze(c: bool) -> None:\n"
               + "    t = asyncio.create_task(tick())\n"
               + "    u = asyncio.create_task(tick())\n"
               + "    v: Int32 = await (t if c else u)\n"
               + "    print(v)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not fallback, fallback


class TestSlicedOutShapes:
    def test_str_param_routes_owned_reads(self):
        # A str param captures OWNED (std::string frame field, ctor-copied
        # from the sync view param); the leaf reads render through the same
        # form-agnostic helpers as owned str locals -- byte-identical.
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(s: str, n: Int32) -> Int32:\n"
               + "    total = n\n"
               + "    while total < 3:\n"
               + "        total = await step(total)\n"
               + "    return total + Int32(len(s))\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_own_record_param_routes(self):
        # An Own[R] param admits through the F1-record arm (the frame owns
        # the payload; leaf reads spell `r.v` either way) -- byte-identical,
        # so it shares the record-param slice rather than needing a rung.
        src = (_PRE
               + "from tpy import Own\n\n"
               + "class R:\n"
               + "    v: Int32\n"
               + "    def __init__(self, v: Int32) -> None:\n        self.v = v\n\n"
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(r: Own[R]) -> Int32:\n"
               + "    n = await step(r.v)\n"
               + "    return n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_pointer_optional_param_generator_routes(self):
        # A pointer-repr Optional[record] param (`p: R | None` -> a `R*` frame
        # field): `p != nullptr` predicates and `p->v` arrow reads route via
        # lc.pointers (seeded from the params) -- byte-identical.
        src = (_PRE
               + "from typing import Iterator\n"
               + "class R:\n"
               + "    v: Int32\n"
               + "    def __init__(self, v: Int32) -> None:\n        self.v = v\n\n"
               + "def gen(p: R | None, n: Int32) -> Iterator[Int32]:\n"
               + "    for _ in range(n):\n"
               + "        if p is not None:\n            yield p.v\n"
               + "        else:\n            yield -1\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "(p != nullptr)" in cpp and "return p->v;" in cpp

    def test_borrow_tuple_param_routes(self):
        # A borrow-form pointer-repr tuple param (`std::tuple<const Tag*, ...>`)
        # reads `std::get<N>(pair)->n` (subscript-yields-borrow-ptr arrow) --
        # byte-identical. @nocopy element proves borrow-not-copy.
        src = (_PRE
               + "from tpy import readonly, nocopy\n"
               + "@nocopy\nclass Tag:\n"
               + "    n: Int32\n"
               + "    def __init__(self, n: Int32) -> None:\n        self.n = n\n\n"
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def asum(pair: readonly[tuple[Tag, Tag]]) -> Int32:\n"
               + "    x = await step(pair[0].n)\n"
               + "    return x + pair[1].n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "std::get<0>(pair)->n" in cpp

    def test_value_tuple_param_routes(self):
        # A value-tuple param (`std::tuple<int32_t, int32_t>`) reads
        # `std::get<N>(t)` bare -- byte-identical. Pins the admission (no
        # corpus case exercises it).
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(t: tuple[Int32, Int32]) -> Int32:\n"
               + "    x = await step(t[0])\n"
               + "    return x + t[1]\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "std::get<0>(t)" in cpp

    def test_protocol_param_iteration_routes(self):
        # A static-protocol param passes the param gate (the monomorphized
        # template frame is skeleton) and a suspension-free loop over it is
        # one leaf statement: the same universal `::tpy::__iter__` shape a
        # sync body renders, inside the case block (`auto& __src_0 = it;`
        # -- the param frame field reads bare).
        src = (_PRE
               + "from typing import Iterable\n\n"
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(it: Iterable[Int32]) -> Int32:\n"
               + "    total: Int32 = 0\n"
               + "    for x in it:\n"
               + "        total = total + x\n"
               + "    return await step(total)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        _, hpp, _cpp = _gen(src, thir=True)
        # The template frame emits inline in the header.
        assert "auto& __src_0 = it;" in hpp

    def test_generic_param_routes(self):
        # A generic async def's frame is a template, but its capture form
        # (`val_or_ref_t<T>`) is resolved at instantiation and every leaf
        # reads the field bare -- so the T param/return route.
        src = (_PRE
               + "async def ident[T](x: T) -> T:\n"
               + "    return x\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _, fallback = _assert_identical(src)
        assert not [k for k in fallback if k.startswith("resumable:")]

    def test_generic_bare_t_local_still_defers(self):
        # The bare-`T` admission is CAPTURE-only (`_res_capture_ok`): a `T`
        # LOCAL is emitted as a pointer ALIAS (`y = &(x)` / `(*y)`), not the
        # bare field a captured T reads as, so it must stay deferred
        # (res.local_storage). Guards the sibling-predicate drift: admitting a
        # bare T to `_res_value_ok` would leak it into `_res_local_ok` and
        # silently emit `y = x` against a `T*` field.
        src = ("from tpy import Int32\nimport asyncio\n\n"
               + "async def ident[T](x: T) -> T:\n"
               + "    y = x\n"
               + "    await asyncio.sleep(0)\n"
               + "    return y\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        assert _res_fallback(src).get("res.alias_bind") == 1
        _assert_identical(src)

    def test_generic_optional_param_still_defers(self):
        # An `Optional[T]` param composes the bare-T admission with the
        # Optional rung: `_optional_ptr_borrow` resolves no pointer-repr
        # borrow for an unbounded T, so it stays deferred (res.param_type)
        # rather than riding the bare-T capture branch. (The AST frame for
        # this shape is itself a known-buggy `T*` -- see BUGS.md.)
        src = (_PRE
               + "async def f[T](p: T | None) -> Int32:\n"
               + "    return 1\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        assert _res_fallback(src).get("res.param_type") == 1

    def test_optional_local_none_init_routes(self):
        # A pointer-repr Optional[record] frame local's writes ride the
        # SYNC reseat arms (pass-1 registration makes every frame decl a
        # reassign): the None init renders the assign-only `o = nullptr;`.
        # The lvalue-lift sibling is pinned separately below.
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
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("reseat.opt_none") == 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "o = nullptr;" in cpp

    def test_optional_local_lvalue_reseat_routes(self):
        # The probe-caught divergence pinned permanently: the old
        # position-blind member assign rendered `x = b;` where the AST
        # lifts `x = &(b);` -- the sync reseat arm now owns the write.
        src = ("import asyncio\n" + _PRE
               + "class R:\n"
               + "    v: Int32\n"
               + "    def __init__(self, v: Int32) -> None:\n        self.v = v\n\n"
               + "async def f(b: R) -> Int32:\n"
               + "    x: R | None = None\n"
               + "    await asyncio.sleep(0)\n"
               + "    x = b\n"
               + "    if x is not None:\n"
               + "        return x.v\n"
               + "    return 0\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("reseat.opt_lvalue") == 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "x = &(b);" in cpp

    def test_rebind_slot_holder_routes_frame_slot(self):
        # An rvalue-reassigned Optional-ptr frame local must never reach
        # the sync rebind-slot arm (its `&*(__slot_N = ...)` references
        # storage only the sync THIRPtrLocalDecl pre-declares). The FRAME
        # arm captures the shape FIRST (`x = &*(__ptr_slot_f0 = R(5));`
        # -- the prescanned per-write frame field), so the sync arm
        # stays unreachable by construction.
        src = ("import asyncio\n" + _PRE
               + "class R:\n"
               + "    v: Int32\n"
               + "    def __init__(self, v: Int32) -> None:\n        self.v = v\n\n"
               + "async def f() -> Int32:\n"
               + "    x: R | None = None\n"
               + "    await asyncio.sleep(0)\n"
               + "    x = R(5)\n"
               + "    if x is not None:\n"
               + "        return x.v\n"
               + "    return 0\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not fallback, fallback
        _, _hpp, cpp = _gen(src, thir=True)
        assert "x = &*(__ptr_slot_f0 = R(5));" in cpp

    def test_frame_slot_del_routes_after_await(self):
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    n = await step(n)\n"
               + "    xs = [n]\n"
               + "    del xs\n"
               + "    return n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _, fallback = _assert_identical(src)
        # `xs` is a resumable frame slot; the del move-sink renders the
        # same position-blind bare member move, so the body routes.
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_global_lowering_reject_falls_back_after_await(self):
        # A BYTES global stays unseeded (the AST's bytes view-assign is
        # a pre-existing bug, BUGS.md -- unsafe to mirror), so its
        # `global` declaration rejects; str/scalar/Ptr-value globals DO
        # seed now, which is why this fixture is bytes.
        src = (_PRE
               + "message = b'before'\n\n"
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    global message\n"
               + "    n = await step(n)\n"
               + "    return n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _, fallback = _assert_identical(src)
        assert fallback.get("resumable:stmt.global:global.unseeded") == 1

    def test_scalar_global_write_routes_after_await(self):
        # A `global`-declared scalar write in a resumable renders the same
        # module-slot `counter = ...;` as a sync body's (a global is never
        # a frame field).
        src = (_PRE
               + "counter: Int32 = 0\n\n"
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    global counter\n"
               + "    n = await step(n)\n"
               + "    counter = counter + n\n"
               + "    return counter\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _, fallback = _assert_identical(src)
        assert not fallback

    def test_raise_expr_after_await_routes(self):
        # The `resumable_leaf_mode` guard that used to reject this was
        # CONSERVATIVE, not required -- `_gen_raise`'s expr branch emits
        # `<expr>.__raise__();` identically in every context (TODO.md's
        # exceptions-trio item (1), which this closes).
        src = (_PRE
               + "err = ValueError('bad')\n\n"
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    n = await step(n)\n"
               + "    raise err\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _, fallback = _assert_identical(src)
        assert not fallback

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
               + "def eat(xs: list[Int32]) -> Int32:\n"
               + "    xs.append(1)\n"
               + "    return len(xs)\n\n"
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    n = await step(n)\n"
               + "    x = eat([1, 2]) if n > 0 else 0\n"
               + "    return x\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _, fallback = _assert_identical(src)
        assert fallback.get("resumable:expr.call") == 1

    def test_async_with_global_manager_routes(self):
        # RE-PINNED ROUTED (thir-wave-next6): a global manager seeds as a
        # pointer slot; the skeleton stores the bare slot pointer
        # (`__with_ctx_0 = cm;` -- the leaf's deref is stripped, the
        # skeleton owns the indirect handling). Byte-verified by the
        # async_with_global_manager corpus case.
        src = ("import asyncio\n\n"
               + "class ACM:\n"
               + "    async def __aenter__(self) -> None:\n"
               + "        await asyncio.sleep(0)\n"
               + "    async def __aexit__(self, exc_type: None, exc_val: None,"
               + " exc_tb: None) -> None:\n"
               + "        await asyncio.sleep(0)\n\n"
               + "cm = ACM()\n\n"
               + "async def f() -> None:\n"
               + "    async with cm:\n        await asyncio.sleep(0)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        assert not _res_fallback(src).get("res.with_manager")

    def test_nested_frame_write_routes(self):
        # A name-write nested inside a leaf compound routes through the
        # POSITION-BLIND frame arm (`_lower_frame_field_assign` -- never the
        # sync assign arm's target-typed render), keyed on
        # lc.plain_frame_fields.
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    n = await step(n)\n"
               + "    if n > 1:\n        n = n + 1\n"
               + "    print(n)\n    return n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.branch_frame_write", 0) >= 1
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_leaf_return_routes(self):
        # A return nested in a suspension-free leaf compound routes: the
        # async-return scaffolding (done state, __tpy_async_ret bind, Poll
        # wrap) stays skeleton via the return hook (_make_async_return),
        # whose value render re-enters the seam's return_values table.
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    n = await step(n)\n"
               + "    if n > 2:\n        return 99\n"
               + "    return n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.nested_return") == 1
        assert not any(k.startswith("resumable:") for k in fallback)

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


class TestContainerAndNoneParams:
    """Container params capture as reference frame fields (`std::vector<T>&`,
    like the F1-record `Record&`) and None-typed params as `std::monostate`
    value fields -- both pure skeleton, so the leaves read them through the
    already-ported sync rows."""

    def test_container_params_route(self):
        src = (_PRE
               + "async def tick() -> Int32:\n    return 1\n\n"
               + "async def f(xs: list[Int32], d: dict[Int32, Int32],"
               + " s: set[Int32]) -> Int32:\n"
               + "    t = len(xs) + len(d) + len(s)\n"
               + "    t = t + await tick()\n"
               + "    xs.append(t)\n"
               + "    return t\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.body") == 2
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_container_param_generator_routes(self):
        src = (_PRE
               + "from typing import Iterator\n\n"
               + "def gen(xs: list[Int32]) -> Iterator[Int32]:\n"
               + "    for x in xs:\n"
               + "        yield x + 1\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_await_arg_container_slot_routes(self):
        # The INLINE emplace arg at a container param slot takes the same
        # `_lower_call_arg` row as a sync call (the emplace ctor param is the
        # sync borrow shape, `std::vector<T>&`).
        src = (_PRE
               + "async def takes(xs: list[Int32]) -> Int32:\n"
               + "    return Int32(len(xs))\n\n"
               + "async def go(xs: list[Int32]) -> Int32:\n"
               + "    return await takes(xs)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.await_args", 0) >= 1
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_frame_slot_init_record_call_routes(self):
        # A module-qualified F1-record-returning call as a frame_slot decl
        # init (`t = asyncio.create_task(sub())`): the emplace arg is a
        # storage sink, so admission matches the sync storage decl
        # (record_ret_ok for rvalue sources); the render stays the
        # position-blind gen_expr inside `t.emplace(...)`.
        src = ("import asyncio\n"
               + "from tpy import Int32\n"
               + "from asyncio import Task\n\n"
               + "async def sub() -> Int32:\n"
               + "    return Int32(42)\n\n"
               + "async def go() -> Int32:\n"
               + "    t: Task[Int32] = asyncio.create_task(sub())\n"
               + "    val = await t\n"
               + "    return val\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.frame_slot_write", 0) >= 1
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_none_typed_aexit_params_route(self):
        # The async-CM `__aexit__(et, ev, tb)` None-typed triple: monostate
        # value fields, position-blind capture and (unused) reads.
        src = (_PRE
               + "import asyncio\n\n"
               + "class Guard:\n"
               + "    n: Int32\n"
               + "    def __init__(self, n: Int32) -> None:\n"
               + "        self.n = n\n"
               + "    async def __aenter__(self) -> Int32:\n"
               + "        await asyncio.sleep(0.001)\n"
               + "        return self.n\n"
               + "    async def __aexit__(self, et: None, ev: None,"
               + " tb: None) -> None:\n"
               + "        await asyncio.sleep(0.001)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)


class TestOwnContainerFrameFields:
    """An `Own[container]` PARAM becomes a plain owned frame FIELD. The sync
    `name.own_read` reject exists because `seed_param_locals` marks such a
    param movable, so its last-use read renders `std::move(p)` -- a binding a
    frame body does not have (the payload was moved into the frame at
    construction). Body reads are bare member reads; an OWNING sink still
    takes the shared movable-last-use row, identically on both paths."""

    def test_own_container_frame_reads_route(self):
        src = (_PRE
               + "import asyncio\n"
               + "from typing import Iterator\n"
               + "from tpy import Own\n\n"
               + "def take(v: Own[list[Int32]]) -> Int32:\n"
               + "    return Int32(len(v))\n\n"
               + "def gen_plain(xs: Own[list[Int32]]) -> Iterator[Int32]:\n"
               + "    yield len(xs)\n"
               + "    yield xs[0]\n\n"
               + "def gen_owning(xs: Own[list[Int32]]) -> Iterator[Int32]:\n"
               + "    yield len(xs)\n"
               + "    yield take(xs)\n\n"
               + "def gen_dict(d: Own[dict[Int32, Int32]]) -> Iterator[Int32]:\n"
               + "    yield len(d)\n"
               + "    yield d[1]\n\n"
               + "async def coro_own(xs: Own[list[Int32]]) -> Int32:\n"
               + "    await asyncio.sleep(0.0)\n"
               + "    return take(xs)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("name.frame_own_field", 0) >= 5
        # Pin the WHOLE dict: a resumable:-only filter lets a body: key
        # through, and a frame body that stopped routing would land there.
        # The one entry is `take`, the SYNC helper whose Own-param read is
        # the deliberate boundary (test_sync_own_container_param_read...).
        assert fallback == {"body:stmt.return:name.own_read": 1}

    def test_own_container_frame_owning_sink_moves(self):
        # The owning sink inside the frame renders `take(std::move(xs))` on
        # BOTH paths: the widening only removes the reject, it does not
        # suppress the shared movable-last-use row.
        src = (_PRE
               + "from typing import Iterator\n"
               + "from tpy import Own\n\n"
               + "def take(v: Own[list[Int32]]) -> Int32:\n"
               + "    return Int32(len(v))\n\n"
               + "def gen_owning(xs: Own[list[Int32]]) -> Iterator[Int32]:\n"
               + "    yield len(xs)\n"
               + "    yield take(xs)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        # Mechanical routing claim: the render asserts below are satisfied
        # by a whole-body fallback, so the frame body must be pinned here.
        # The single entry is the SYNC helper's deliberate boundary reject.
        assert fallback == {"body:stmt.return:name.own_read": 1}
        _c, _hpp, cpp = _gen(src, thir=True)
        assert "return take(std::move(xs));" in cpp
        assert "return ::tpy::__len__(xs);" in cpp

    def test_sync_own_container_param_read_stays_ast(self):
        # BOUNDARY: the same param in a SYNC body keeps the reject -- there
        # the movable seeding is real and the AST's last-use render is the
        # unmirrored `std::move(p)` shape the verdict names.
        src = (_PRE
               + "from tpy import Own\n\n"
               + "def take(v: Own[list[Int32]]) -> Int32:\n"
               + "    return Int32(len(v))\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert fallback.get("body:stmt.return:name.own_read") == 1, fallback


class TestStrBytesReturns:
    """Owned str/bytes async returns: the view->owned copy is the shared
    form-keyed wrap (`_wrap_view_owned_return`) -- a borrow-form (view)
    source copies explicitly, a literal or owned rvalue returns bare."""

    def test_view_param_return_wraps(self):
        src = ("import asyncio\n\n"
               + "async def echo(tag: str) -> str:\n"
               + "    await asyncio.sleep(0)\n"
               + "    return tag\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "std::string __tpy_async_ret = std::string(tag);" in cpp

    def test_literal_return_stays_bare(self):
        src = ("import asyncio\n\n"
               + "async def greet() -> str:\n"
               + "    await asyncio.sleep(0)\n"
               + "    return \"hi\"\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "std::string __tpy_async_ret = \"hi\";" in cpp

    def test_owned_rvalue_return_stays_bare(self):
        src = ("import asyncio\n\n"
               + "async def shout(tag: str) -> str:\n"
               + "    await asyncio.sleep(0)\n"
               + "    return tag + \"!\"\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_bytes_view_param_return_wraps(self):
        src = ("import asyncio\n\n"
               + "async def echo(b: bytes) -> bytes:\n"
               + "    await asyncio.sleep(0)\n"
               + "    return b\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_bytes_literal_return_stays_bare(self):
        src = ("import asyncio\n\n"
               + "async def blob() -> bytes:\n"
               + "    await asyncio.sleep(0)\n"
               + "    return b\"hi\"\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_bytes_owned_rvalue_return_stays_bare(self):
        src = ("import asyncio\n\n"
               + "async def join(b: bytes) -> bytes:\n"
               + "    await asyncio.sleep(0)\n"
               + "    return b + b\"!\"\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)


class TestStrBytesYields:
    """str/bytes yields on the resumable frame. The owned iterator slot's ctor
    absorbs the bare source render whenever the source is already owned -- a
    str/bytes PARAM is copied into owned frame storage on the way in, so it
    stays bare (the sgen families' reasoning). A source that is still a VIEW in
    the frame takes the explicit view->owned copy instead, since
    `string_view -> string` is not implicit. The slot-literal retype mirrors
    gen_yield_value's target threading."""

    def test_str_yields_route(self):
        src = ("from typing import Iterator\n\n"
               + "def greetings(name: str) -> Iterator[str]:\n"
               + "    yield \"hello \" + name\n"
               + "    yield \"goodbye \" + name\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.yield_value", 0) >= 2
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_bytes_yield_routes(self):
        src = ("from typing import Iterator\n\n"
               + "def chunks(b: bytes) -> Iterator[bytes]:\n"
               + "    n = 0\n"
               + "    while n < 2:\n"
               + "        yield b\n"
               + "        n += 1\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)

    # A LITERAL source has static storage, so view deduction leaves it a view
    # even in a frame -- the one source shape that still meets the owning
    # iterator slot as a `std::string_view`.
    STATIC_VIEW_SRC = (
        "from typing import Iterator\n\n"
        + "def parts() -> Iterator[str]:\n"
        + "    lit = \"static\"\n"
        + "    yield lit\n"
        + "    yield \"end\"\n\n"
        + "def main() -> None:\n    pass\nmain()\n")

    def test_static_view_yield_materializes(self):
        # Without the copy the owning slot rejects the view (string_view ->
        # expected<string> has no implicit conversion).
        witnesses, fallback = _assert_identical(self.STATIC_VIEW_SRC)
        assert witnesses.get("res.yield_value", 0) >= 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(self.STATIC_VIEW_SRC, thir=True)
        assert "return std::string(lit);" in cpp

    def test_promoted_local_yield_stays_bare(self):
        # Boundary: a frame-unsafe source resolves OWNED during view deduction,
        # so the same yield sink must leave it alone rather than copy twice.
        src = ("from typing import Iterator\n"
               + "from tpy import Int32\n\n"
               + "def keys(d: dict[str, Int32]) -> Iterator[str]:\n"
               + "    for k in d:\n"
               + "        yield k\n"
               + "    yield \"end\"\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "return k;" in cpp
        assert "std::string(k)" not in cpp

    def test_owned_param_yield_stays_bare(self):
        # Boundary: the adjacent shape that must NOT take the copy. `name` is
        # captured owned, so wrapping it would copy an owned string again.
        src = ("from typing import Iterator\n\n"
               + "def twice(name: str) -> Iterator[str]:\n"
               + "    yield name\n"
               + "    yield name\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "return name;" in cpp
        assert "std::string(name)" not in cpp


class TestStaticProtocolParams:
    def test_protocol_param_routes(self):
        # A static-protocol param monomorphizes the frame over the conforming
        # type (template machinery, skeleton); leaf reads are bare.
        src = ("import asyncio\n"
               + "from typing import Protocol\n"
               + "from tpy import Int32\n\n"
               + "class Sized(Protocol):\n"
               + "    def size(self) -> Int32: ...\n\n"
               + "class Box:\n"
               + "    n: Int32\n"
               + "    def __init__(self, n: Int32) -> None:\n"
               + "        self.n = n\n"
               + "    def size(self) -> Int32:\n"
               + "        return self.n\n\n"
               + "async def measure(s: Sized) -> Int32:\n"
               + "    await asyncio.sleep(0)\n"
               + "    return s.size()\n\n"
               + "async def go() -> Int32:\n"
               + "    b = Box(3)\n"
               + "    return await measure(b)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_protocol_param_generator_routes(self):
        # The same capture-position admission on a resumable GENERATOR: the
        # param gate is shared, and the monomorphized frame is skeleton for
        # both callable kinds.
        src = ("from typing import Iterator, Protocol\n"
               + "from tpy import Int32\n\n"
               + "class Sized(Protocol):\n"
               + "    def size(self) -> Int32: ...\n\n"
               + "class Box:\n"
               + "    n: Int32\n"
               + "    def __init__(self, n: Int32) -> None:\n"
               + "        self.n = n\n"
               + "    def size(self) -> Int32:\n"
               + "        return self.n\n\n"
               + "def counted(s: Sized) -> Iterator[Int32]:\n"
               + "    i: Int32 = 0\n"
               + "    while i < s.size():\n"
               + "        yield i\n"
               + "        i += 1\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)


class TestBoundCoroAwaits:
    """Bound-coroutine handles: the binding is a factory-call emplace into
    the handle's own frame slot (`c.emplace(add_one(1))`), and `await c`
    polls that slot in place -- both suspension sides are skeleton."""

    _PRELUDE = ("import asyncio\n"
                + "from tpy import Int32\n\n"
                + "class Counter:\n"
                + "    base: Int32\n"
                + "    def __init__(self, base: Int32) -> None:\n"
                + "        self.base = base\n"
                + "    async def bump(self, n: Int32) -> Int32:\n"
                + "        return self.base + n\n\n"
                + "async def add_one(n: Int32) -> Int32:\n"
                + "    return n + 1\n\n")

    def test_free_and_method_factory_bind_await(self):
        src = (self._PRELUDE
               + "async def main_coro() -> None:\n"
               + "    c = add_one(1)\n"
               + "    print(await c)\n"
               + "    w = Counter(10)\n"
               + "    m = w.bump(5)\n"
               + "    print(await m)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.coro_handle_write", 0) >= 2
        assert witnesses.get("res.await_prebuilt", 0) >= 2
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_name_source_rebind_routes(self):
        # `c2 = c` (handle move-bind) renders the two-statement
        # `emplace(std::move(*c)); c.reset();` pair (THIRCoroHandleMove).
        src = (self._PRELUDE
               + "async def main_coro() -> None:\n"
               + "    c = add_one(1)\n"
               + "    c2 = c\n"
               + "    print(await c2)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.coro_handle_move", 0) >= 1
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_erased_handle_local_routes(self):
        # An ERASED handle local (a helper returning Own[Cancellable[T]]):
        # the dedicated decl arm member-assigns the own-arg render -- here
        # the FORWARD verdict, so the already-erased call result binds bare
        # (`c = spawn();`, never the frame_slot emplace); the await polls
        # the unique_ptr in place (skeleton).
        src = (self._PRELUDE
               + "from tpy import Own\n"
               + "from tpy.coro import Cancellable\n\n"
               + "def spawn() -> Own[Cancellable[Int32]]:\n"
               + "    return add_one(1)\n\n"
               + "async def main_coro() -> None:\n"
               + "    c = spawn()\n"
               + "    print(await c)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        assert witnesses.get("res.erased_handle_write", 0) >= 1
        _, _hpp, cpp = _gen(src, thir=True)
        assert "c = spawn();" in cpp
        assert ".emplace(spawn()" not in cpp

    def test_generic_method_factory_routes(self):
        # A generic async METHOD factory bound to a frame handle
        # (`m = b.echo(5)` with echo[T]): the call spells inline with its
        # method targs and the SKELETON owns the templated frame-type
        # spelling, so the handle-write arm routes it (the fence's
        # "different render" reason was removed with the factory-position
        # targ admission).
        src = ("import asyncio\n"
               + "from tpy import Int32\n\n"
               + "class Box:\n"
               + "    def __init__(self) -> None:\n"
               + "        pass\n"
               + "    async def echo[T](self, x: T) -> T:\n"
               + "        return x\n\n"
               + "async def main_coro() -> None:\n"
               + "    b = Box()\n"
               + "    m = b.echo(5)\n"
               + "    print(await m)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        assert witnesses.get("res.coro_handle_write")


class TestBorrowTupleLocals:
    """Borrow-form tuple frame fields (`std::tuple<..., T*>`): the literal
    builder renders `&(name)` lifts under the spelled slot type; writes are
    bare frame-field assigns; reads take the existing arrow rows."""

    _PRE_BOX = ("import asyncio\n"
                + "from tpy import Int32\n\n"
                + "class Box:\n"
                + "    val: Int32\n"
                + "    def __init__(self, v: Int32) -> None:\n"
                + "        self.val = v\n\n")

    def test_borrow_form_source_writes_bare(self):
        # A borrow-tuple reseat from another borrow-tuple NAME writes bare
        # (`u = t;` -- _maybe_wrap_tuple_to_pointer no-ops for a
        # non-storage source); keyed on the lowered form fact.
        src = (self._PRE_BOX
               + "async def f(b: Box) -> Int32:\n"
               + "    t = (1, b)\n"
               + "    u = t\n"
               + "    await asyncio.sleep(0)\n"
               + "    return u[0]\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.btuple_write", 0) >= 2
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "u = t;" in cpp

    def test_coro_borrow_tuple_local_routes(self):
        src = (self._PRE_BOX
               + "async def bump(b: Box) -> None:\n"
               + "    t = (1, b)\n"
               + "    await asyncio.sleep(0)\n"
               + "    t[1].val = 99\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.btuple_write", 0) >= 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "t = std::tuple<int32_t, Box*>{1, &(b)};" in cpp

    def test_borrow_tuple_literal_yield_routes(self):
        # Two yields keep the generator off the simple-gen peephole (the
        # resumable frame is the seam under test).
        src = (self._PRE_BOX.replace("import asyncio\n", "")
               + "from typing import Iterator\n\n"
               + "def pairs(xs: list[Box]) -> Iterator[tuple[Int32, Box]]:\n"
               + "    i = 0\n"
               + "    while i < len(xs):\n"
               + "        yield (i, xs[i])\n"
               + "        yield (i + 1, xs[i])\n"
               + "        i += 1\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.btuple_yield", 0) >= 2
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_generic_tuple_yield_routes(self):
        # CONVERTED (the generic tuple-yield builder): a
        # TypeParamRef element spells `val_or_ptr_t<T>` with to_val_or_ptr
        # wraps -- the generic builder now serves the yield slot, NOT the
        # concrete value/borrow builders (whose misuse was the divergence
        # the corpus byte-diff once caught here).
        src = ("from typing import Iterator\n\n"
               + "def zip_pairs[K, V](ks: list[K], vs: list[V])"
               + " -> Iterator[tuple[K, V]]:\n"
               + "    i = 0\n"
               + "    while i < len(ks):\n"
               + "        yield (ks[i], vs[i])\n"
               + "        yield (ks[i], vs[i])\n"
               + "        i += 1\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.btuple_yield_generic", 0) >= 2
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_self_element_sync_method_passes_bare(self):
        # `self` in a sync method is the prvalue pointer `this` -- it passes
        # BARE into the `T*` slot (`{1, this}`, never the ill-formed
        # `&(this)`). Regression for the reviewer-caught divergence.
        src = ("from tpy import Int32\n\n"
               + "class Box:\n"
               + "    val: Int32\n"
               + "    def __init__(self, v: Int32) -> None:\n"
               + "        self.val = v\n"
               + "    def make_tuple(self) -> Int32:\n"
               + "        t = (1, self)\n"
               + "        return t[1].val\n\n"
               + "def main() -> None:\n"
               + "    b = Box(5)\n"
               + "    print(b.make_tuple())\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not fallback

    def test_value_tuple_literal_yield_routes(self):
        src = ("from tpy import Int32\n"
               + "from typing import Iterator\n\n"
               + "def pairs(n: Int32) -> Iterator[tuple[Int32, Int32]]:\n"
               + "    i: Int32 = 0\n"
               + "    while i < n:\n"
               + "        yield (i, i + 1)\n"
               + "        yield (i + 1, i)\n"
               + "        i += 1\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.btuple_yield", 0) >= 2
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_optional_element_yield_routes(self):
        # A pointer-repr Optional PARAM element is already the pointer and
        # passes bare at the yield slot -- the same-repr pointer-name row;
        # it deferred while the builder only knew addr_of. NB
        # the rvalue-into-borrow machinery is unreachable from these
        # sinks: sema rejects rvalue elements at borrow yield slots
        # outright, and decl rvalues go VALUE-capture (storage).
        src = (self._PRE_BOX.replace("import asyncio\n", "")
               + "from typing import Iterator, Optional\n\n"
               + "def pairs(b: Box, o: Optional[Box], n: Int32)"
               + " -> Iterator[tuple[Int32, Optional[Box]]]:\n"
               + "    i: Int32 = 0\n"
               + "    while i < n:\n"
               + "        yield (i, o)\n"
               + "        yield (i + 1, o)\n"
               + "        i += 1\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        fallback = _res_fallback(src)
        assert not fallback, fallback
        _assert_identical(src)

    def test_call_init_at_borrow_slot_routes(self):
        # A borrow-FORM tuple-returning CALL init writes bare (`t =
        # pick(b);` -- the C++ return type IS the borrow tuple); the
        # storage-call families (the skeleton's third OWNING signal)
        # stay excluded by type.
        src = (self._PRE_BOX
               + "def pick(b: Box) -> tuple[Int32, Box]:\n"
               + "    return (1, b)\n\n"
               + "async def go(b: Box) -> None:\n"
               + "    t = pick(b)\n"
               + "    await asyncio.sleep(0)\n"
               + "    print(t[1].val)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.btuple_write") == 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "t = pick(b);" in cpp

    def test_const_ref_capture_decl_defers(self):
        # A CONST_REF-captured element (`const T*` slot) at a sync decl is
        # sliced out (the const-element read rows are unverified for
        # locals); the readonly param source forces the const capture.
        src = ("from tpy import Int32, readonly\n\n"
               + "class Box:\n"
               + "    val: Int32\n"
               + "    def __init__(self, v: Int32) -> None:\n"
               + "        self.val = v\n\n"
               + "def peek(b: readonly[Box]) -> Int32:\n"
               + "    t = (1, b)\n"
               + "    return t[1].val\n\n"
               + "def main() -> None:\n"
               + "    b = Box(5)\n"
               + "    print(peek(b))\nmain()\n")
        c, _hpp, _cpp = _gen(src, thir=True)
        assert any("tuple_literal" in k for k in c._thir_fallback)
        _assert_identical(src)

    def test_owned_rvalue_element_tuple_routes(self):
        # A VALUE-captured owned-rvalue element (`(1, make())`) renders bare
        # into its value slot inside the spelled tuple -- both paths agree
        # byte-for-byte (sema's capture annotation, not the owning
        # classification, drives the form here).
        src = (self._PRE_BOX
               + "from tpy import Own\n\n"
               + "def make() -> Own[Box]:\n"
               + "    return Box(1)\n\n"
               + "async def go() -> None:\n"
               + "    t = (1, make())\n"
               + "    await asyncio.sleep(0)\n"
               + "    print(t[1].val)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)


class TestBorrowTupleArgs:
    """Tuple literals at call-arg tuple slots: lvalue elements ride the
    borrow builder; rvalue elements the tuple_value_to_borrow source-tuple
    helper (full-expression lifetime covers the call); value tuples the
    spelled value render."""

    _PRE_BOX = ("from tpy import Int32\n\n"
                + "class Box:\n"
                + "    val: Int32\n"
                + "    def __init__(self, v: Int32) -> None:\n"
                + "        self.val = v\n\n"
                + "def take(t: tuple[Int32, Box]) -> Int32:\n"
                + "    return t[1].val\n\n")

    def test_lvalue_element_arg_routes(self):
        src = (self._PRE_BOX
               + "def main() -> None:\n"
               + "    b = Box(5)\n"
               + "    print(take((1, b)))\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("btuple.literal", 0) >= 1
        assert not fallback

    def test_rvalue_element_arg_routes(self):
        src = (self._PRE_BOX
               + "def main() -> None:\n"
               + "    print(take((2, Box(7))))\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("btuple.value_to_borrow", 0) >= 1
        assert not fallback
        _, _hpp, cpp = _gen(src, thir=True)
        assert ("::tpy::tuple_value_to_borrow<std::tuple<int32_t, Box*>>"
                "(std::tuple<int32_t, Box>{2, Box(7)})") in cpp

    def test_mixed_elements_arg_routes(self):
        # An lvalue element inside the rvalue path keeps its `&(...)` lift
        # within the source tuple (its src slot is already the pointer part).
        src = ("from tpy import Int32\n\n"
               + "class Box:\n"
               + "    val: Int32\n"
               + "    def __init__(self, v: Int32) -> None:\n"
               + "        self.val = v\n\n"
               + "def take2(t: tuple[Box, Box]) -> Int32:\n"
               + "    return t[0].val + t[1].val\n\n"
               + "def main() -> None:\n"
               + "    b = Box(5)\n"
               + "    print(take2((b, Box(7))))\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("btuple.value_to_borrow", 0) >= 1
        assert not fallback

    def test_single_element_rvalue_arg_parenthesizes(self):
        # The 1-element source tuple takes the paren form (GCC brace-init
        # ambiguity with std::tuple ctors), like the value arm.
        src = ("from tpy import Int32\n\n"
               + "class Box:\n"
               + "    val: Int32\n"
               + "    def __init__(self, v: Int32) -> None:\n"
               + "        self.val = v\n\n"
               + "def take1(t: tuple[Box]) -> Int32:\n"
               + "    return t[0].val\n\n"
               + "def main() -> None:\n"
               + "    print(take1((Box(9),)))\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not fallback
        _, _hpp, cpp = _gen(src, thir=True)
        assert "(std::tuple<Box>(Box(9)))" in cpp

    def test_value_tuple_arg_routes(self):
        src = ("from tpy import Int32\n\n"
               + "def total(t: tuple[Int32, Int32]) -> Int32:\n"
               + "    return t[0] + t[1]\n\n"
               + "def main() -> None:\n"
               + "    print(total((3, 4)))\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("btuple.value_arg", 0) >= 1
        assert not fallback


class TestOwnCancellableArgs:
    """The Own[@dynamic P] arg family: a factory CALL erases via
    make_adapter (already routed); a BOUND-HANDLE name unwraps + moves into
    the adapter (`make_adapter<...>(std::move(*(c)))`); the await-position
    slot admits beside _res_param_ok (the emplace arg is the sync call-arg
    render)."""

    def test_bound_handle_create_task_routes(self):
        src = ("import asyncio\n"
               + "from tpy import Int32\n\n"
               + "async def add_one(n: Int32) -> Int32:\n"
               + "    return n + 1\n\n"
               + "async def main_coro() -> None:\n"
               + "    c = add_one(41)\n"
               + "    t = asyncio.create_task(c)\n"
               + "    print(await t)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("call.coro_handle_adapter", 0) >= 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "(std::move(*(c)))" in cpp

    def test_factory_arg_at_await_routes(self):
        # wait_for's Own[Cancellable[T]] slot admits at the AWAIT position;
        # the emplace arg is the make_adapter render.
        src = ("import asyncio\n"
               + "from tpy import Int32\n\n"
               + "async def compute() -> Int32:\n"
               + "    await asyncio.sleep(0.001)\n"
               + "    return 7\n\n"
               + "async def main_coro() -> None:\n"
               + "    v = await asyncio.wait_for(compute(), 5.0)\n"
               + "    print(v)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_readonly_own_slot_defers(self):
        # An `Own[readonly[P]]` slot never takes the adapter render on the
        # AST side (the ReadonlyType wrapper defeats its is_dyn_protocol
        # key), so the gates must reject it RAW-wrapped -- routing it wrapped
        # was a probe-caught divergence. (The AST's own emission for this
        # spelling is itself broken -- see the BUGS.md entry -- so fallback
        # preserves the oracle either way.)
        src = ("import asyncio\n"
               + "from tpy import Int32, Own, readonly\n"
               + "from tpy.coro import Cancellable\n\n"
               + "async def add_one(n: Int32) -> Int32:\n"
               + "    return n + 1\n\n"
               + "def park(coro: Own[readonly[Cancellable[Int32]]]) -> None:\n"
               + "    pass\n\n"
               + "async def main_coro() -> None:\n"
               + "    c = add_one(1)\n"
               + "    park(c)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        fallback = _res_fallback(src)
        assert sum(fallback.values()) >= 1
        _assert_identical(src)

    def test_erased_param_forward_routes(self):
        # An already-ERASED Own[Cancellable] PARAM: the frame captures it
        # as a bare `unique_ptr<P>` field (the Own[dyn P] param family) and
        # the forward into the create_task slot moves it WITHOUT a re-wrap
        # (`std::move(coro)` -- the forward verdict, never the handle
        # wrap's `std::move(*(coro))`).
        src = ("import asyncio\n"
               + "from tpy import Int32, Own\n"
               + "from tpy.coro import Cancellable\n\n"
               + "async def add_one(n: Int32) -> Int32:\n"
               + "    return n + 1\n\n"
               + "async def spawn(coro: Own[Cancellable[Int32]]) -> Int32:\n"
               + "    t = asyncio.create_task(coro)\n"
               + "    return await t\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "std::move(coro)" in cpp
        assert "std::move(*(coro))" not in cpp


class TestMatchDispatch:
    """A suspending `match`'s dispatch routes through the THIR match tiers
    with arm BODIES hooked back to the skeleton's BB walker -- the seam
    replacement for gen_match + resumable_arm_emitter."""

    _ENUM = ("from typing import Iterator\n"
             + "from enum import Enum\n"
             + "from tpy import Int32\n\n"
             + "class Color(Enum):\n"
             + "    RED = 1\n"
             + "    GREEN = 2\n\n")

    def test_enum_switch_dispatch_routes(self):
        src = (self._ENUM
               + "def emit(c: Color) -> Iterator[Int32]:\n"
               + "    match c:\n"
               + "        case Color.RED:\n"
               + "            yield 1\n"
               + "            yield 2\n"
               + "        case Color.GREEN:\n"
               + "            yield 3\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.match_dispatch", 0) >= 1
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_guarded_switch_dispatch_routes(self):
        # A guarded arm on an Int32 subject: the switch_primitive tier's
        # grouped-entries shape (guards inside the case block), awaits in
        # arms.
        src = ("import asyncio\n"
               + "from tpy import Int32\n\n"
               + "async def step(n: Int32) -> Int32:\n"
               + "    return n + 1\n\n"
               + "async def pick(n: Int32) -> Int32:\n"
               + "    match n:\n"
               + "        case 1 if n > 0:\n"
               + "            return await step(10)\n"
               + "        case 2:\n"
               + "            return await step(20)\n"
               + "        case _:\n"
               + "            return await step(0)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.match_dispatch", 0) >= 1
        assert witnesses.get("match.switch_primitive", 0) >= 1
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_guarded_chain_dispatch_routes(self):
        # A str subject below the switch threshold with a guard: the
        # genuine if_elif_guarded tier (standalone-if + goto shape).
        src = ("import asyncio\n"
               + "from tpy import Int32\n\n"
               + "async def step(n: Int32) -> Int32:\n"
               + "    return n + 1\n\n"
               + "async def pick(s: str, flag: bool) -> Int32:\n"
               + "    match s:\n"
               + "        case \"a\" if flag:\n"
               + "            return await step(10)\n"
               + "        case _:\n"
               + "            return await step(0)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("match.if_elif_guarded", 0) >= 1
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_or_pattern_labels_route(self):
        src = (self._ENUM.replace("    GREEN = 2\n",
                                  "    GREEN = 2\n    BLUE = 3\n")
               + "def emit(c: Color) -> Iterator[Int32]:\n"
               + "    match c:\n"
               + "        case Color.RED | Color.BLUE:\n"
               + "            yield 1\n"
               + "            yield 2\n"
               + "        case Color.GREEN:\n"
               + "            yield 3\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.match_dispatch", 0) >= 1
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_str_switch_tier_defers(self):
        # A str subject at/over the switch-dispatch threshold takes the
        # switch_str tier -- not in the dispatch-hook slice.
        src = ("from typing import Iterator\n"
               + "from tpy import Int32\n\n"
               + "def emit(s: str) -> Iterator[Int32]:\n"
               + "    match s:\n"
               + "        case \"a\":\n            yield 1\n"
               + "        case \"b\":\n            yield 2\n"
               + "        case \"c\":\n            yield 3\n"
               + "        case \"d\":\n            yield 4\n"
               + "        case \"e\":\n            yield 5\n"
               + "        case _:\n            yield 0\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        fallback = _res_fallback(src)
        assert sum(fallback.values()) >= 1
        _assert_identical(src)

    def test_union_dispatch_routes(self):
        # A union-subject match stamps arm narrowings (entry_narrowings);
        # the narrowed-BB scope + the switch_union hook tier route it whole
        # (was the res.narrowed_resume interlock).
        src = ("from typing import Iterator\n"
               + "from tpy import Int32\n\n"
               + "class A:\n"
               + "    def __init__(self) -> None:\n        pass\n\n"
               + "class B:\n"
               + "    def __init__(self) -> None:\n        pass\n\n"
               + "def describe(x: A | B, n: Int32) -> Iterator[Int32]:\n"
               + "    match x:\n"
               + "        case A():\n"
               + "            yield 1\n"
               + "            yield 2\n"
               + "        case B():\n"
               + "            yield n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        fallback = _res_fallback(src)
        assert sum(fallback.values()) == 0
        _assert_identical(src)

    def test_binding_arm_routes_frame_assign(self):
        # `case _ as y:` binds the subject into `y`'s frame field -- the
        # copy mode re-keys to the plain frame-field assign
        # (_hook_mode_binding) and the dispatch routes.
        src = (self._ENUM
               + "def emit(c: Color) -> Iterator[Int32]:\n"
               + "    match c:\n"
               + "        case Color.RED:\n"
               + "            yield 1\n"
               + "            yield 2\n"
               + "        case _ as y:\n"
               + "            yield 9\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        assert witnesses.get("res.match_dispatch", 0) >= 1
        _, _hpp, cpp = _gen(src, thir=True)
        assert "y = __match_subject_1;" in cpp


class TestNarrowedResume:
    """The narrowed-BB scope (variant-get slice): a union frame field proven
    a concrete member, with a suspension splitting the narrowed region. The
    skeleton emits every extraction local (`__{var}` at branch arms / resume
    cases / the match join; the tier's `__case_{i}` inside a match arm);
    leaves lower renamed to that alias with the subject retyped."""

    _UNION = ("from typing import Iterator\n"
              + "from tpy import Int32\n\n"
              + "class Dog:\n"
              + "    def sound(self) -> str:\n        return \"woof\"\n\n"
              + "class Cat:\n"
              + "    def sound(self) -> str:\n        return \"meow\"\n\n"
              + "async def step(n: Int32) -> Int32:\n"
              + "    return n + 1\n\n")

    def test_postif_leaf_return_routes(self):
        # An early-return narrowing LEAF if (no suspension inside): the
        # post-if extraction (`auto& __a = ...`) emits as part of the leaf
        # (THIRStmtSeq) and the ReturnT value renders under the alias --
        # mirroring _lower_stmts' post-if arm, BB-locally.
        src = (self._UNION
               + "async def describe(a: Dog | Cat) -> str:\n"
               + "    await step(0)\n"
               + "    if isinstance(a, Dog):\n"
               + "        return \"dog\"\n"
               + "    return a.sound()\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.postif_narrow") == 1
        assert witnesses.get("res.nested_return") == 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "__a.sound()" in cpp

    def test_postif_raise_terminated_routes(self):
        # A RaiseT-terminated BB also admits the post-if arm: the
        # fall-through print renders under the alias, the raise stays the
        # terminator leaf.
        src = (self._UNION
               + "async def bark(a: Dog | Cat) -> str:\n"
               + "    await step(0)\n"
               + "    if isinstance(a, Dog):\n"
               + "        return a.sound()\n"
               + "    print(a.sound())\n"
               + "    raise ValueError(\"cat\")\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.postif_narrow") == 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "__a.sound()" in cpp

    def test_postif_generator_return_routes(self):
        # The generator flavor of the post-if + nested-return composition:
        # the bare return inside the leaf if takes the StopIteration hook,
        # the fall-through read renders under the alias.
        src = (self._UNION
               + "def voices(a: Dog | Cat) -> Iterator[str]:\n"
               + "    yield \"start\"\n"
               + "    yield \"mid\"\n"
               + "    if isinstance(a, Dog):\n"
               + "        return\n"
               + "    print(a.sound())\n"
               + "    return\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.postif_narrow") == 1
        assert witnesses.get("res.nested_return") == 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "__a.sound()" in cpp

    def test_narrowing_assert_leaf_routes(self):
        # A top-level narrowing assert in the flat BB walk: the AST's
        # _gen_assert emits the persistent extraction inline. The flat walk
        # used to reject rather than silently DROP that alias; it now
        # appends it (res.flat_assert_narrow), which is what the pin was
        # really guarding -- the alias must not go missing.
        src = (self._UNION
               + "async def f(a: Dog | Cat) -> str:\n"
               + "    await step(0)\n"
               + "    assert isinstance(a, Dog)\n"
               + "    return a.sound()\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.flat_assert_narrow", 0) == 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "__a.sound()" in cpp

    def test_postif_crossing_suspension_defers(self):
        # The post-if scope is BB-LOCAL: a suspension after the narrowing
        # if would carry the fact across BBs (the env walk doesn't model
        # mid-BB facts) -- the body falls back whole.
        src = (self._UNION
               + "async def describe(a: Dog | Cat) -> str:\n"
               + "    if isinstance(a, Dog):\n"
               + "        return \"dog\"\n"
               + "    await step(0)\n"
               + "    return a.sound()\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        assert _res_fallback(src).get("res.narrowed_resume") == 1
        _assert_identical(src)

    def test_if_narrow_across_suspend_routes(self):
        # Suspension inside the narrowed then-arm: the resume case
        # re-establishes `__a`; the else-arm reads its own `__a` (Cat).
        src = (self._UNION
               + "async def voice(a: Dog | Cat) -> str:\n"
               + "    await step(0)\n"
               + "    if isinstance(a, Dog):\n"
               + "        await step(1)\n"
               + "        return a.sound()\n"
               + "    return a.sound()\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, _ = _assert_identical(src)
        assert witnesses.get("res.narrow_scope")
        assert sum(_res_fallback(src).values()) == 0

    def test_match_arm_alias_split_routes(self):
        # One body, both spellings: the Cat arm's read emits inline in the
        # dispatch (`__case_1`), the Dog arm's post-yield read lands in the
        # resume case (`__a`) -- the byte-diff pins both against the AST.
        src = (self._UNION
               + "def voices(a: Dog | Cat) -> Iterator[str]:\n"
               + "    match a:\n"
               + "        case Dog():\n"
               + "            yield \"is-dog\"\n"
               + "            yield a.sound()\n"
               + "        case Cat():\n"
               + "            yield a.sound()\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, _ = _assert_identical(src)
        assert witnesses.get("res.narrow_scope")
        assert witnesses.get("res.match_dispatch")
        assert sum(_res_fallback(src).values()) == 0

    def test_nested_if_in_match_arm_routes(self):
        # Mixed env inside the arm chain: the subject keeps the tier's
        # `__case_0` while the nested isinstance binds `__b`; the resume
        # case after the yield re-establishes both as `__{var}`.
        src = (self._UNION
               + "def mix(a: Dog | Cat, b: Dog | Cat) -> Iterator[str]:\n"
               + "    match a:\n"
               + "        case Dog():\n"
               + "            if isinstance(b, Cat):\n"
               + "                yield b.sound()\n"
               + "                yield a.sound()\n"
               + "            yield \"end-dog\"\n"
               + "        case Cat():\n"
               + "            yield \"cat\"\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, _ = _assert_identical(src)
        assert witnesses.get("res.narrow_scope")
        assert sum(_res_fallback(src).values()) == 0

    def test_readonly_union_cond_routes(self):
        # A readonly-qualified union subject narrows with const-qualified
        # alternatives (F2, `_isinstance_narrow_info`'s readonly unwrap);
        # the resumable Branch machinery rides it -- dualgen-verified
        # identical.
        src = ("from typing import Iterator\n"
               + "from tpy import readonly\n\n"
               + "class Dog:\n"
               + "    def __init__(self) -> None:\n        pass\n\n"
               + "class Cat:\n"
               + "    def __init__(self) -> None:\n        pass\n\n"
               + "@readonly\n"
               + "def codes(a: Dog | Cat) -> Iterator[int]:\n"
               + "    yield 0\n"
               + "    if isinstance(a, Dog):\n"
               + "        yield 1\n"
               + "    else:\n"
               + "        yield 2\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        fallback = _res_fallback(src)
        assert sum(fallback.values()) == 0
        _assert_identical(src)

    def test_while_narrow_across_suspend_routes(self):
        # The while-body BB carries the condition's facts (the same Branch
        # machinery as if-arms); the resume inside re-establishes `__a`.
        src = (self._UNION
               + "def voices(a: Dog | Cat) -> Iterator[str]:\n"
               + "    while isinstance(a, Dog):\n"
               + "        yield a.sound()\n"
               + "        break\n"
               + "    yield \"done\"\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, _ = _assert_identical(src)
        assert witnesses.get("res.narrow_scope")
        assert sum(_res_fallback(src).values()) == 0

    def test_default_arm_in_suspending_match_routes(self):
        # A wildcard arm carries no facts: a bare body_key hook with no
        # scope, beside a narrowed suspending arm.
        src = (self._UNION
               + "def voices(a: Dog | Cat) -> Iterator[str]:\n"
               + "    match a:\n"
               + "        case Dog():\n"
               + "            yield \"d\"\n"
               + "            yield a.sound()\n"
               + "        case _:\n"
               + "            yield \"other\"\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, _ = _assert_identical(src)
        assert witnesses.get("res.narrow_scope")
        assert sum(_res_fallback(src).values()) == 0

    def test_double_suspend_in_narrowed_arm_routes(self):
        # Two suspensions inside one narrowed arm: each resume case
        # re-establishes the alias independently.
        src = (self._UNION
               + "def voices(a: Dog | Cat) -> Iterator[str]:\n"
               + "    match a:\n"
               + "        case Dog():\n"
               + "            yield \"one\"\n"
               + "            yield a.sound()\n"
               + "            yield a.sound()\n"
               + "        case Cat():\n"
               + "            yield \"cat\"\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, _ = _assert_identical(src)
        assert witnesses.get("res.narrow_scope")
        assert sum(_res_fallback(src).values()) == 0

    def test_union_local_narrow_routes(self):
        # Union LOCALS route since the union frame-slot cell (formerly
        # blocked on the un-routed decl family, res.local_storage).
        src = (self._UNION
               + "def vals() -> Iterator[str]:\n"
               + "    a: Dog | Cat = Dog()\n"
               + "    if isinstance(a, Dog):\n"
               + "        yield \"d\"\n"
               + "        yield a.sound()\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        fallback = _res_fallback(src)
        assert not fallback, fallback
        _assert_identical(src)

    def test_narrowed_rebind_defers(self):
        # Rebinding a narrowed name un-narrows MID-BB, which the
        # entry-level scope cannot mirror -- the _rebinds_narrowed guard
        # falls such a body back. Today the guard is DEFENSIVE: sema
        # forbids reassigning params, and union LOCALS reject earlier at
        # res.local_storage (this pin's shape) -- it becomes load-bearing
        # the day union locals route.
        src = (self._UNION
               + "def vals() -> Iterator[str]:\n"
               + "    a: Dog | Cat = Dog()\n"
               + "    if isinstance(a, Dog):\n"
               + "        yield a.sound()\n"
               + "        a = Cat()\n"
               + "        yield \"end\"\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        fallback = _res_fallback(src)
        assert sum(fallback.values()) >= 1
        _assert_identical(src)

    def test_poly_self_narrow_routes(self):
        # `isinstance(self, Sub)` in a resumable now ROUTES (the round C
        # poly-self cell): the Branch cond renders the no-alias
        # dynamic_cast check and the arm reads the SPELLED
        # `__self_narrowed` re-extraction (skeleton emission per resume
        # case -- no cross-BB state).
        src = ("from typing import Protocol, Iterator\n"
               + "from tpy import dynamic\n\n"
               + "@dynamic\n"
               + "class Tagged(Protocol):\n    pass\n\n"
               + "class Pet(Tagged):\n"
               + "    def names(self) -> Iterator[str]:\n"
               + "        yield \"pet\"\n"
               + "        if isinstance(self, Dog):\n"
               + "            yield \"dog\"\n\n"
               + "class Dog(Pet):\n    pass\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        fallback = _res_fallback(src)
        assert fallback == {}, fallback
        _assert_identical(src)

    def test_union_fact_defers(self):
        # `isinstance(a, (Dog, Cat))` on a 3-member union narrows to a
        # SMALLER UNION -- no single extraction local; sliced out.
        src = (self._UNION
               + "class Bird:\n"
               + "    def sound(self) -> str:\n        return \"tweet\"\n\n"
               + "async def pick(a: Dog | Cat | Bird) -> Int32:\n"
               + "    if isinstance(a, (Dog, Cat)):\n"
               + "        await step(0)\n"
               + "        return 1\n"
               + "    return 2\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        fallback = _res_fallback(src)
        assert fallback.get("res.narrowed_resume", 0) >= 1
        _assert_identical(src)

    def test_guarded_union_dispatch_defers(self):
        # A guard sends the dispatch to the guarded_union tier -- no hook
        # support yet (filed); the body falls back whole.
        src = (self._UNION
               + "def pick(a: Dog | Cat, flag: bool) -> Iterator[str]:\n"
               + "    match a:\n"
               + "        case Dog() if flag:\n"
               + "            yield \"d\"\n"
               + "            yield a.sound()\n"
               + "        case _:\n"
               + "            yield \"o\"\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        fallback = _res_fallback(src)
        assert fallback.get("res.match_strategy", 0) >= 1
        _assert_identical(src)

    def test_union_binding_arm_routes_frame_emplace(self):
        # `case Dog() as d:` binds the extracted member into `d`'s
        # frame_slot -- the copy mode re-keys to the slot emplace
        # (_hook_mode_binding) and the dispatch routes.
        src = (self._UNION
               + "def voices(a: Dog | Cat) -> Iterator[str]:\n"
               + "    match a:\n"
               + "        case Dog() as d:\n"
               + "            yield \"got-dog\"\n"
               + "            yield d.sound()\n"
               + "        case Cat():\n"
               + "            yield a.sound()\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "d.emplace(" in cpp


class TestTryRegions:
    """R6: try/except (no finally) around a suspension. The region replay --
    catch headers, sub-future resets, handler try-wraps -- is skeleton; the
    try-body and handler-body leaves route through the seam."""

    def test_try_except_around_await_routes(self):
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    try:\n"
               + "        n = await step(n)\n"
               + "        print(n)\n"
               + "    except ValueError:\n"
               + "        n = 0\n"
               + "    return n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.body") == 2
        assert witnesses.get("res.try_region") == 1
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_handler_binding_reads_route(self):
        # The `as`-binding is the catch parameter (a C++ local, never a frame
        # field); handler leaves read it bare with the exception type.
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    try:\n        n = await step(n)\n"
               + "    except ValueError as e:\n        print(e)\n"
               + "    return n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.try_region") == 1
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_returns_in_try_and_handler_route(self):
        # ReturnT terminators inside the region: the pre-finally capture and
        # pending-slot paths stay unreachable (no finally/with regions), so
        # both returns take the plain ready render.
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    try:\n"
               + "        n = await step(n)\n"
               + "        return n\n"
               + "    except ValueError:\n"
               + "        return 0\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.return_value", 0) >= 2
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_raise_in_handler_routes(self):
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    try:\n        n = await step(n)\n"
               + "    except ValueError:\n"
               + "        raise RuntimeError(\"boom\")\n"
               + "    return n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.try_region") == 1
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_binding_frame_field_collision_rejects(self):
        # A handler binding sharing a frame-field name would mistype the flat
        # scope -- reject rather than risk a divergent render.
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    e = n\n"
               + "    try:\n        n = await step(n)\n"
               + "    except ValueError as e:\n        print(e)\n"
               + "    return n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        assert _res_fallback(src).get("res.handler_binding") == 1

    def test_own_with_target_takes_frame_slot(self):
        # An `Own[T]` frame local over a REFERENCE type is the object T, built at
        # the binding rather than at frame creation, so the plan gives it a
        # frame_slot whose placement-new runs the real constructor there. A bare
        # field would default-construct with the frame -- deleted outright once T
        # has a non-default-constructible member -- and it disagreed with the
        # spelling an ordinary `a = make()` of the same value already got.
        src = (_PRE
               + "from typing import Iterator\n"
               + "from tpy import Own\n\n"
               + "class Item:\n"
               + "    def __init__(self, v: Int32) -> None:\n"
               + "        self.v = v\n\n"
               + "class Fresh:\n"
               + "    def __enter__(self) -> Own[Item]:\n"
               + "        return Item(5)\n"
               + "    def __exit__(self, et: None, ev: None,\n"
               + "                 tb: None) -> None:\n"
               + "        pass\n\n"
               + "def gen() -> Iterator[Int32]:\n"
               + "    with Fresh() as a:\n"
               + "        yield a.v\n"
               + "        yield a.v\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _, fallback = _assert_identical(src)
        assert "res.local_storage" not in fallback
        _, hpp, _cpp = _gen(src, thir=True)
        assert "::tpy::frame_slot<Item> a;" in hpp
        assert "\n    Item a;\n" not in hpp


class TestSyncLoops:
    """R3: a sync for-loop whose body suspends decomposes into
    AsyncForIterSetup + AsyncForAdvance. The iteration strategy (range /
    begin_end / next / iter_next), counters, exhaustion test and loop-var
    bind are skeleton; the only user renders are the iterable (once, reused by
    the advance) and, for range, each bound."""

    def test_range_loop_with_await_routes(self):
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    total = 0\n"
               + "    for i in range(n):\n"
               + "        total = await step(total)\n"
               + "    return total\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.sync_loop") == 1
        assert witnesses.get("res.for_iter_setup") == 1
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_range3_loop_routes(self):
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    total = 0\n"
               + "    for i in range(1, n, 2):\n"
               + "        total = await step(total)\n"
               + "    return total\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_list_local_iterable_routes(self):
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    xs = [n, n, n]\n"
               + "    total = 0\n"
               + "    for x in xs:\n"
               + "        total = await step(x)\n"
               + "    return total\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.sync_loop") == 1
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_loop_body_try_composes(self):
        # A break/await-forced region inside a decomposed loop routes as
        # ordinary try-region leaves.
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    total = 0\n"
               + "    for i in range(n):\n"
               + "        try:\n            total = await step(total)\n"
               + "        except ValueError:\n            total = 0\n"
               + "    return total\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.sync_loop") == 1
        assert witnesses.get("res.try_region") == 1
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_record_loop_var_routes(self):
        # A1: pointer-form loop var (begin_end over list[R]) -- the skeleton
        # binds `r = &(*it++);`, reads ride lc.pointers (`r->v`).
        src = (_PRE
               + "class R:\n    v: Int32\n"
               + "    def __init__(self, v: Int32) -> None:\n        self.v = v\n\n"
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    xs = [R(n)]\n    total = 0\n"
               + "    for r in xs:\n        total = await step(r.v)\n"
               + "    return total\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.loop_ptr_bind") == 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "r = &(*((*__for_it_0))++);" in cpp
        assert "__sub_0.emplace(r->v);" in cpp

    def test_postloop_pointer_read_routes(self):
        # Reads after the loop hit the same `T*` frame field -- no shadow /
        # post-loop peel duality on the resumable path.
        src = (_PRE
               + "class R:\n    v: Int32\n"
               + "    def __init__(self, v: Int32) -> None:\n        self.v = v\n\n"
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    xs = [R(n)]\n    total = 0\n"
               + "    for r in xs:\n        total = await step(r.v)\n"
               + "    return total + r.v\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_generic_slot_loop_var_routes(self):
        # A2: a generic-T loop var over an Iterable[T] param is a frame_slot
        # (iter_next strategy) whose payload comes from the source, so the same
        # bind serves whichever form the trait picks: `unwrap_ref_move` hands
        # `emplace` a reference to bind or a value to move in. Reads `(*x)`.
        src = ("from typing import Iterator, Iterable\n"
               + "from tpy import Int32\n\n"
               + "def take[T](it: Iterable[T], n: Int32) -> Iterator[T]:\n"
               + "    c: Int32 = 0\n"
               + "    for x in it:\n"
               + "        if c >= n:\n            break\n"
               + "        yield x\n"
               + "        c += 1\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.loop_slot_bind") == 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, hpp, _cpp = _gen(src, thir=True)
        assert "x.emplace(::tpy::unwrap_ref_move(*(*__for_r_0)));" in hpp
        assert "return (*x);" in hpp
        # The field's payload is the trait, not the TPy element type -- a `T`
        # here would be the classification this strategy no longer does.
        assert "::tpy::frame_slot<::tpy::for_elem_next_t<T_it>> x;" in hpp

    def test_pointer_loop_var_value_yield_derefs(self):
        # An Iterator[T] param loop var is pointer-form (`T* x`); yielding it
        # is a VALUE use, so the render derefs (`return (*x);`) where arrow /
        # pass positions stay bare.
        src = ("from typing import Iterator\n"
               + "from tpy import Int32\n\n"
               + "def take_iter[T](it: Iterator[T], n: Int32) -> Iterator[T]:\n"
               + "    c: Int32 = 0\n"
               + "    for x in it:\n"
               + "        if c >= n:\n            break\n"
               + "        yield x\n"
               + "        c += 1\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.loop_ptr_bind") == 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, hpp, _cpp = _gen(src, thir=True)
        assert "return (*x);" in hpp
        # Boundary against the iter_next sibling (test_generic_slot_loop_var_
        # routes), which spells its field from the source because an arbitrary
        # `__next__` may lend or hand back fresh. THIS strategy's source always
        # lends its live yield slot, so the element type decides and the field
        # stays a legible `T*`. Pinned so widening the trait to `next` /
        # `begin_end` has to argue with a test rather than just churn snapshots.
        assert "T* x = nullptr;" in hpp
        assert "for_elem_next_t" not in hpp

    def test_tuple_unpack_loop_routes(self):
        # A non-value tuple-unpack loop: the pointer-form holder binds at
        # the skeleton advance; the head unpack derefs it and re-points
        # the alias target (the alias-binds cell).
        src = (_PRE
               + "class R:\n    v: Int32\n"
               + "    def __init__(self, v: Int32) -> None:\n        self.v = v\n\n"
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    xs = [(n, R(n))]\n    total = 0\n"
               + "    for i, r in xs:\n        total = await step(r.v)\n"
               + "    return total\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        # The LOOP routes (alias cell), and so does the `[(n, R(n))]`
        # list-literal init: the frame-emplace position spells its tuple
        # element BARE (no `tuple_to_storage` wrap -- the emplaced brace is
        # already storage-typed), which is the frame flavor of the sync
        # element row.
        assert _res_fallback(src) == {}
        _assert_identical(src)

    def test_dict_items_loop_var_defers(self):
        # A value-element dict_items loop var (`kv` over dict[int, int])
        # now classifies as a value-tuple local, reaches the advance, and
        # defers there (res.loop_var): the whole-var items bind is not an
        # admitted family (only unpack HOLDERS take the value-tuple bind).
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    d = {1: 2}\n    total = 0\n"
               + "    for kv in d.items():\n        total = await step(kv[1])\n"
               + "    return total\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        assert _res_fallback(src).get("res.loop_var") == 1

    def test_dict_items_nonvalue_loop_var_routes(self):
        # A proxy-ref tuple loop var over dict[int, Record].items(): the
        # advance binds the borrow-form tuple field (skeleton) and the
        # element reads ride the borrow-tuple subscript family -- routed
        # and byte-identical.
        src = (_PRE
               + "class Box:\n    v: Int32\n"
               + "    def __init__(self, v: Int32) -> None:\n"
               + "        self.v = v\n\n"
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    d = {n: Box(n)}\n    total = 0\n"
               + "    for kv in d.items():\n        total = await step(kv[0])\n"
               + "    return total\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert not fallback
        assert witnesses.get("res.loop_btuple_bind", 0) >= 1

    def test_dict_items_whole_tuple_loop_var_routes(self):
        # The WHOLE-tuple proxy-ref form (`for kv in d.items()`): kv is
        # the borrow-form tuple frame field itself; element reads
        # (`kv[0]`, `kv[1].v` mutation across the await) ride the
        # borrow-tuple subscript family. Routed and byte-identical -- the
        # only committed witness of this shape in the resumable path (the
        # corpus twin's whole-tuple body takes the sgen peephole).
        src = (_PRE
               + "class Box:\n    v: Int32\n"
               + "    def __init__(self, v: Int32) -> None:\n"
               + "        self.v = v\n\n"
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    d = {n: Box(n)}\n    total = 0\n"
               + "    for kv in d.items():\n"
               + "        kv[1].v = kv[1].v + 1\n"
               + "        total = await step(kv[0])\n"
               + "    return total + d[n].v\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert not fallback
        assert witnesses.get("res.loop_btuple_bind", 0) >= 1

    def test_user_iterator_tuple_unpack_holder_defers(self):
        # BOUNDARY: a tuple-unpack loop over a USER-defined iterator
        # (iter_next strategy) never sets borrow_tuple_loop_var, so its
        # `__for_tup_N` holder sits outside every admitted set and the
        # body must keep falling back -- the admit is keyed on the
        # skeleton's per-loop fact, not the holder's type shape.
        src = (_PRE
               + "from tpy import Own\n\n"
               + "class Box:\n    v: Int32\n"
               + "    def __init__(self, v: Int32) -> None:\n"
               + "        self.v = v\n\n"
               + "class Pairs:\n"
               + "    i: Int32\n"
               + "    def __init__(self) -> None:\n"
               + "        self.i = 0\n"
               + "    def __iter__(self) -> 'Pairs':\n"
               + "        return self\n"
               + "    def __next__(self) -> tuple[Int32, Own[Box]]:\n"
               + "        self.i = self.i + 1\n"
               + "        if self.i > 2:\n"
               + "            raise StopIteration\n"
               + "        return (self.i, Box(self.i))\n\n"
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f() -> Int32:\n"
               + "    total = 0\n"
               + "    for k, b in Pairs():\n"
               + "        total = await step(k + b.v)\n"
               + "    return total\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        assert _res_fallback(src), _res_fallback(src)

    def test_value_tuple_loop_head_unpack_routes(self):
        # A VALUE-tuple holder loop (`for a, b in xs`): the holder binds
        # bare at the advance and the head unpack ref-binds it
        # (`const auto& __tup_N = __for_tup_0;` + frame_assign targets).
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    xs = [(n, n + 1)]\n"
               + "    total = 0\n"
               + "    for a, b in xs:\n"
               + "        total = await step(a + b)\n"
               + "    return total\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.loop_tuple_bind") == 1
        assert witnesses.get("res.frame_unpack") == 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "const auto& __tup_1 = __for_tup_0;" in cpp
        assert "a = std::get<0>(__tup_1);" in cpp

    def test_await_pointer_loop_var_routes(self):
        # `await t` where t is a pointer-form loop var: the SUSPEND
        # position consumes the pointer BARE (`__sub_0 = t;`) -- the
        # skeleton's already-pointer wrap; no re-taken address.
        src = ("import asyncio\nfrom tpy import Int32\n"
               + "from asyncio import Task\n\n"
               + "async def work(n: Int32) -> Int32:\n    return n\n\n"
               + "async def f(tasks: list[Task[Int32]]) -> Int32:\n"
               + "    total = 0\n"
               + "    for t in tasks:\n        total += await t\n"
               + "    return total\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.loop_ptr_bind") == 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "__sub_0 = t;" in cpp
        assert "t = &(*((*__for_it_0))++);" in cpp


class TestFrameTupleUnpack:
    """Frame-target tuple unpacks (`a, b = pair()` across a suspension):
    a call rvalue source materializes by value (`auto __tup_N = ...`),
    scalar targets take the plain member assign, frame_slot targets the
    `.emplace()` (with the per-element move for Own elements)."""

    def test_call_source_scalar_targets_route(self):
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "def pair(n: Int32) -> tuple[Int32, Int32]:\n"
               + "    return (n, n + 1)\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    a, b = pair(n)\n"
               + "    n = await step(n)\n"
               + "    return a + b + n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.frame_unpack") == 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "a = std::get<0>(__tup_1);" in cpp

    def test_own_record_element_emplaces(self):
        src = ("import asyncio\nfrom tpy import Int32, Own\n\n"
               + "class Box:\n"
               + "    v: Int32\n"
               + "    def __init__(self, v: Int32) -> None:\n"
               + "        self.v = v\n\n"
               + "def make(n: Int32) -> tuple[Own[Box], Int32]:\n"
               + "    return (Box(n), n + 1)\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    b, m = make(n)\n"
               + "    await asyncio.sleep(0)\n"
               + "    return b.v + m\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.frame_unpack") == 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "b.emplace(std::move(std::get<0>(__tup_1)));" in cpp

    def test_discard_target_routes(self):
        # `a, _ = pair()`: the discard slot emits nothing (binds None).
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "def pair(n: Int32) -> tuple[Int32, Int32]:\n"
               + "    return (n, n + 1)\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    a, _ = pair(n)\n"
               + "    n = await step(n)\n"
               + "    return a + n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.frame_unpack") == 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "std::get<1>" not in cpp

    def test_branch_reassign_unpack_routes(self):
        # A branch RE-unpack of already-registered frame targets routes:
        # member assigns are position-independent, so the branch position
        # changes nothing (only branch FIRST-DECL unpacks reject, via the
        # declared guard).
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "def pair(n: Int32) -> tuple[Int32, Int32]:\n"
               + "    return (n, n + 1)\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    a = 0\n"
               + "    b = 0\n"
               + "    if n > 2:\n"
               + "        a, b = pair(n)\n"
               + "    n = await step(n)\n"
               + "    return a + b + n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.frame_unpack") == 1
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_branch_first_decl_unpack_defers(self):
        # A nested-in-branch FIRST-DECL unpack stays unregistered by pass 1
        # -- the arm's declared guard rejects (never a KeyError).
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "def pair(n: Int32) -> tuple[Int32, Int32]:\n"
               + "    return (n, n + 1)\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    if n > 2:\n"
               + "        a, b = pair(n)\n"
               + "        print(a + b)\n"
               + "    n = await step(n)\n"
               + "    return n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        assert _res_fallback(src).get("res.unpack") == 1
        _assert_identical(src)

    def test_value_tuple_literal_init_routes(self):
        # A value-tuple literal at the bare tuple frame field renders the
        # same spelled `std::tuple<...>{...}` as the sync decl arm in the
        # position-blind member assign; the name-source unpack then reads
        # it via the cref arm.
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    t = (n, n + 1)\n"
               + "    a, b = t\n"
               + "    n = await step(n)\n"
               + "    return a + b + n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.frame_tuple_literal") == 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "t = std::tuple<int32_t, int32_t>{n, " in cpp


_POLLPAIR = (
    "from tpy import Int32, Own, nocopy\n"
    "from tpy.coro import Poll, Waker, poll_ready\n\n"
    "@nocopy\n"
    "class Counter:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n"
    "        self.n = n\n"
    "    def bump(self) -> None:\n"
    "        self.n += 1\n\n"
    "@nocopy\n"
    "class OwnPair:\n"
    "    def cancel(self) -> None:\n"
    "        pass\n"
    "    def __poll__(self, w: Waker) -> Own[Poll[tuple[Own[Counter], Int32]]]:\n"
    "        return poll_ready((Counter(10), Int32(99)))\n\n")


class TestAwaitLiftUnpack:
    """One-shot `__await_lift_*` tuple move-outs (the streams family): the
    holder classifies frame_slot (skeleton-owning), the unpack rvalue-ref-
    binds the deref'd slot (`auto&& __tup_N = (*__await_lift_M);`) and moves
    the owned elements out at their frame targets; an Own[record] target is
    an owning frame_slot with the same `(*name)` reads as its bare-typed
    sibling."""

    def test_own_tuple_moveout_routes(self):
        src = (_POLLPAIR
               + "async def f() -> None:\n"
               + "    c, tag = await OwnPair()\n"
               + "    c.bump()\n"
               + "    print(c.n, tag)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.unpack_oneshot") == 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "auto&& __tup_1 = (*__await_lift_0);" in cpp
        assert "c.emplace(std::move(std::get<0>(__tup_1)));" in cpp
        assert "tag = std::get<1>(__tup_1);" in cpp
        assert "(*c).bump();" in cpp

    def test_value_tuple_lift_keeps_cref_arm(self):
        # A pure-value one-shot lift is a BARE field (no owned element), so
        # the AST takes the const-ref name ladder, not the auto&& arm --
        # and so does THIR (the value-tuple name-source arm).
        src = ("from tpy import Int32, Own, nocopy\n"
               "from tpy.coro import Poll, Waker, poll_ready\n\n"
               "@nocopy\n"
               "class ValPair:\n"
               "    def cancel(self) -> None:\n"
               "        pass\n"
               "    def __poll__(self, w: Waker) -> Own[Poll[tuple[Int32, Int32]]]:\n"
               "        return poll_ready((Int32(1), Int32(2)))\n\n"
               "async def f() -> None:\n"
               "    a, b = await ValPair()\n"
               "    print(a, b)\n\n"
               "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.frame_unpack") == 1
        assert not witnesses.get("res.unpack_oneshot")
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "const auto& __tup_1 = __await_lift_0;" in cpp
        assert "a = std::get<0>(__tup_1);" in cpp

    def test_loop_reunpack_reemplaces(self):
        # The accept-loop shape: a while-repeated one-shot unpack must
        # re-emplace the Own[record] frame_slot target each iteration.
        src = (_POLLPAIR
               + "async def f() -> None:\n"
               + "    i: Int32 = 0\n"
               + "    while i < 3:\n"
               + "        c, tag = await OwnPair()\n"
               + "        c.bump()\n"
               + "        print(c.n, tag)\n"
               + "        i += 1\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.unpack_oneshot") == 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "c.emplace(std::move(std::get<0>(__tup_1)));" in cpp

    def test_discarded_owned_element_routes(self):
        # `_, m = await OwnPair()`: sema keeps is_owned on the discarded
        # slot, so both paths take the one-shot arm -- the discard slot
        # emits nothing and the holder still rvalue-ref-binds (the
        # any(is_owned) gate tracks the AST's source_is_oneshot condition
        # through the discard).
        src = (_POLLPAIR
               + "async def f() -> None:\n"
               + "    _, m = await OwnPair()\n"
               + "    print(m)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.unpack_oneshot") == 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "auto&& __tup_1 = (*__await_lift_0);" in cpp
        assert "m = std::get<1>(__tup_1);" in cpp
        assert "std::get<0>" not in cpp

    def test_ref_element_alias_target_routes(self):
        # A reference-element (non-Own) lift tuple aliases its target INTO
        # the lift slot: the storage tuple lifts to borrow form
        # (`tuple_to_pointer<...>((*lift))`) and the alias target re-points
        # off the lifted element (the alias-binds cell).
        src = ("from tpy import Int32, Own, nocopy\n"
               "from tpy.coro import Poll, Waker, poll_ready\n\n"
               "@nocopy\n"
               "class RefPair:\n"
               "    def cancel(self) -> None:\n"
               "        pass\n"
               "    def __poll__(self, w: Waker) -> Own[Poll[tuple[list[Int32], Int32]]]:\n"
               "        xs: list[Int32] = [10, 20]\n"
               "        return poll_ready((xs, Int32(2)))\n\n"
               "async def f() -> None:\n"
               "    lst, m = await RefPair()\n"
               "    lst.append(30)\n"
               "    print(len(lst), m)\n\n"
               "def main() -> None:\n    pass\nmain()\n")
        _, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "::tpy::tuple_to_pointer<" in cpp
        assert ("lst = &(::tpy::unwrap_ref(::tpy::tuple_elem_ref("
                "std::get<0>(__tup_1))));") in cpp

    def test_durable_own_tuple_local_routes(self):
        # A DURABLE (non-lift) Own-element tuple local is skeleton-owning
        # too: the owning-tuple frame slot classification admits it (the
        # gen_tuple_own_local family's emplace/(*t) renders) -- formerly a
        # res.local_storage fence.
        src = ("import asyncio\nfrom tpy import Int32, Own\n\n"
               "class Box:\n"
               "    v: Int32\n"
               "    def __init__(self, v: Int32) -> None:\n"
               "        self.v = v\n\n"
               "def make(n: Int32) -> tuple[Own[Box], Int32]:\n"
               "    return (Box(n), n + 1)\n\n"
               "async def f(n: Int32) -> Int32:\n"
               "    t = make(n)\n"
               "    await asyncio.sleep(0)\n"
               "    return t[1]\n\n"
               "def main() -> None:\n    pass\nmain()\n")
        assert not _res_fallback(src)
        _assert_identical(src)


class TestResumableGlobals:
    """Read-only value-global seeding in resumable bodies (the sync
    `_seed_readonly_globals` reused): a global is never a frame field, so
    reads render the sync spellings (bare same-module, qualified imported)
    with no frame peel. `global`-write names stay unseeded -- a TpyGlobal
    statement still rejects the body."""

    def test_module_global_read_routes(self):
        src = ("import asyncio\nfrom tpy import Int32\n\n"
               + "LIMIT: Int32 = 5\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    await asyncio.sleep(0)\n"
               + "    return n + LIMIT\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("name.global_seeded")
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_imported_constant_read_routes(self):
        src = ("import asyncio\nfrom tpy import Int32\n"
               + "from socket import AF_INET\n\n"
               + "async def f() -> Int32:\n"
               + "    await asyncio.sleep(0)\n"
               + "    return Int32(AF_INET)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("name.global_imported")
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "::tpystd::socket::AF_INET" in cpp

    def test_native_global_read_routes(self):
        # A same-module native-linkage global reads through the
        # `native_globals` map (qualify_native_name spelling) -- the
        # branch the imported-constant pin does not reach.
        src = ("import asyncio\nfrom tpy import Int32\n"
               + "from tpy.extern import native_global\n\n"
               + "COUNT: Int32 = native_global(\"g_count\", binding=\"C\")\n\n"
               + "async def f() -> Int32:\n"
               + "    await asyncio.sleep(0)\n"
               + "    return COUNT\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("name.global_native")
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "g_count" in cpp

    def test_str_global_read_routes(self):
        # An owned-str global carries the same view/owned form duality as
        # locals; the seeded read is STORAGE (bare owned lvalue) in the
        # resumable exactly as in a sync body.
        src = ("import asyncio\nfrom tpy import Int32\n\n"
               + "GREETING: str = \"hi\"\n\n"
               + "async def f() -> Int32:\n"
               + "    await asyncio.sleep(0)\n"
               + "    return len(GREETING)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("name.global_seeded")
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_scalar_global_write_seeds_and_routes(self):
        # The write half seeds in resumables too: a
        # `global`-declared scalar write renders the sync module-slot
        # `counter = 1;` -- no frame field, no fallback.
        src = ("import asyncio\nfrom tpy import Int32\n\n"
               + "counter: Int32 = 0\n\n"
               + "async def f() -> None:\n"
               + "    global counter\n"
               + "    counter = 1\n"
               + "    await asyncio.sleep(0)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        fallback = _res_fallback(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        _assert_identical(src)


class TestLeafTryExcept:
    """Except-only leaf trys (no finally): the sync throw tier renders
    byte-identically mid-state (no finally frame, no __state /
    pending-return interaction); finally tiers stay a named rung."""

    def test_except_only_leaf_try_routes(self):
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    n = await step(n)\n"
               + "    try:\n"
               + "        n = n // (n - n)\n"
               + "    except ZeroDivisionError:\n"
               + "        n = 7\n"
               + "    return n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.leaf_try_except") == 1
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_multi_handler_leaf_try_routes(self):
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    n = await step(n)\n"
               + "    try:\n"
               + "        n = n // (n - n)\n"
               + "    except ZeroDivisionError:\n"
               + "        n = 7\n"
               + "    except ValueError:\n"
               + "        n = 8\n"
               + "    return n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.leaf_try_except") == 1
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_finally_leaf_try_mid_frame_routes(self):
        # A leaf finally MID-FRAME (an await before it, so the try is a leaf
        # inside a multi-state frame) still renders as the plain sync try --
        # no crossing, so no finally-frame scaffolding is involved. A
        # crossing keeps rejecting; TestResumableLeafFinally pins that.
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    n = await step(n)\n"
               + "    try:\n        print(n)\n"
               + "    finally:\n        print(0)\n"
               + "    return n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.leaf_try_finally", 0) == 1
        assert not any(k.startswith("resumable:") for k in fallback)


_ALIAS_PRE = ("import asyncio\nfrom tpy import Int32\n\n"
              "class Box:\n"
              "    n: Int32\n"
              "    def __init__(self, n: Int32):\n        self.n = n\n\n")


class TestAliasBinds:
    """Pointer-alias frame binds (the skeleton's pointer_alias_locals /
    pointer_form_unpack_targets contracts): `T*` fields aliasing live
    storage. Reads ride lc.pointers; each bind family renders at its own
    arm -- `= &(<lvalue>)` single-assign, `= &(unwrap_ref(tuple_elem_ref(
    get)))` off borrow-tuple sources, `= &(get)` off the deref'd loop
    holder."""

    def test_single_assign_subscript_routes(self):
        src = (_ALIAS_PRE
               + "async def bump(items: list[Box]) -> Int32:\n"
               + "    a = items[0]\n"
               + "    await asyncio.sleep(0)\n"
               + "    a.n = a.n + 1\n"
               + "    return items[0].n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.alias_bind") == 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "a = &(::tpy::__getitem__(items, 0));" in cpp
        assert "a->n" in cpp

    def test_single_assign_field_source_routes(self):
        src = (_ALIAS_PRE
               + "class Holder:\n"
               + "    inner: Box\n"
               + "    def __init__(self, b: Box):\n        self.inner = b\n\n"
               + "async def peek(o: Holder) -> Int32:\n"
               + "    a = o.inner\n"
               + "    await asyncio.sleep(0)\n"
               + "    return a.n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.alias_bind") == 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "a = &(o.inner);" in cpp

    def test_borrow_call_unpack_routes(self):
        src = (_ALIAS_PRE
               + "def first_two(items: list[Box]) -> tuple[Box, Box]:\n"
               + "    return (items[0], items[1])\n\n"
               + "async def bump(items: list[Box]) -> Int32:\n"
               + "    a, b = first_two(items)\n"
               + "    await asyncio.sleep(0)\n"
               + "    a.n = a.n + 1\n"
               + "    return a.n + b.n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.frame_unpack") == 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "auto __tup_1 = first_two(items);" in cpp
        assert ("a = &(::tpy::unwrap_ref(::tpy::tuple_elem_ref("
                "std::get<0>(__tup_1))));") in cpp

    def test_name_source_unpack_ref_binds(self):
        # A stable borrow-tuple frame local source: the MUTABLE ref-bind
        # (`auto& __tup_N = t;` -- the is_ref rule drops the const), and
        # the producer's borrow-call write lands bare (`t = first_two(..)`).
        src = (_ALIAS_PRE
               + "def first_two(items: list[Box]) -> tuple[Box, Box]:\n"
               + "    return (items[0], items[1])\n\n"
               + "async def f(items: list[Box]) -> Int32:\n"
               + "    t = first_two(items)\n"
               + "    a, b = t\n"
               + "    await asyncio.sleep(0)\n"
               + "    return a.n + b.n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.btuple_write") == 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "t = first_two(items);" in cpp
        assert "auto& __tup_1 = t;" in cpp

    def test_loop_head_unpack_routes(self):
        # The pointer-form holder loop: skeleton binds the holder at the
        # advance, the head unpack derefs it and re-points the alias
        # target into the container's live storage tuple.
        src = (_ALIAS_PRE
               + "from typing import Iterator\n\n"
               + "def gen(rows: list[tuple[Int32, Box]]) -> Iterator[Int32]:\n"
               + "    for idx, it in rows:\n"
               + "        yield idx\n"
               + "        it.n = it.n + 1\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "auto& __tup_1 = (*__for_tup_0);" in cpp
        assert "it = &(std::get<1>(__tup_1));" in cpp

    def test_async_for_value_holder_routes(self):
        # The async-for VALUE-tuple holder: skeleton ASSIGN bind, head
        # unpack via the const-ref value-tuple name-source arm.
        src = ("import asyncio\nfrom tpy import Int32\n"
               + "from tpy.coro import Poll, Waker, poll_ready\n\n"
               + "class Pairs:\n"
               + "    n: Int32\n"
               + "    def __init__(self, n: Int32):\n        self.n = n\n"
               + "    def __aiter__(self) -> 'Pairs':\n        return self\n"
               + "    async def __anext__(self) -> tuple[Int32, Int32]:\n"
               + "        if self.n <= 0:\n"
               + "            raise StopAsyncIteration()\n"
               + "        self.n -= 1\n"
               + "        return (self.n, self.n * self.n)\n\n"
               + "async def f(p: Pairs) -> Int32:\n"
               + "    total: Int32 = 0\n"
               + "    async for k, sq in p:\n"
               + "        total += sq\n"
               + "    return total\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "const auto& __tup_1 = __for_tup_0;" in cpp

    def test_literal_decomposition_routes(self):
        # `a, b = (items[0], items[1])` decomposes into synthetic
        # `__unpack_*` alias temps; each takes the single-assign alias
        # bind (`__unpack_0_0 = &(<elem>);`) and the user names copy the
        # live pointer bare (`a = __unpack_0_0;`) -- routed and
        # byte-identical.
        src = (_ALIAS_PRE
               + "async def work(items: list[Box]) -> Int32:\n"
               + "    a, b = (items[0], items[1])\n"
               + "    await asyncio.sleep(0)\n"
               + "    return a.n + b.n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert not fallback
        assert witnesses.get("res.alias_bind", 0) >= 2

    def test_own_tuple_call_routes_via_owning_slot(self):
        # Formerly the corpus-caught divergence's fence: an
        # `Own[tuple[...]]`-declared callee is the skeleton's OWNING
        # signal -- the owning-tuple frame slot now mirrors it (emplace
        # write, (*t) reads), so the shape routes byte-identically.
        src = (_ALIAS_PRE
               + "from tpy import Own\n\n"
               + "def make_pair(n: Int32) -> Own[tuple[Int32, Box]]:\n"
               + "    return (n, Box(n))\n\n"
               + "async def f() -> Int32:\n"
               + "    t = make_pair(9)\n"
               + "    await asyncio.sleep(0)\n"
               + "    return t[0]\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        assert not _res_fallback(src)
        _assert_identical(src)

    def test_method_call_source_defers(self):
        # The TpyMethodCall admission at the unpack/btuple arms is inert
        # today: the method-call RETURN gate has no borrow-tuple row, so
        # the source rejects there (safe fallback). Becomes a route pin
        # when that gate widens.
        src = (_ALIAS_PRE
               + "class Store:\n"
               + "    items: list[Box]\n"
               + "    def __init__(self):\n"
               + "        self.items = [Box(1), Box(2)]\n"
               + "    def first_two(self) -> tuple[Box, Box]:\n"
               + "        return (self.items[0], self.items[1])\n\n"
               + "async def f(s: Store) -> Int32:\n"
               + "    a, b = s.first_two()\n"
               + "    await asyncio.sleep(0)\n"
               + "    return a.n + b.n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        fb = _res_fallback(src)
        assert fb.get("expr.method_call") == 1
        _assert_identical(src)

    def test_discarded_borrow_element_routes(self):
        # A discarded pointer-repr element emits nothing on both paths
        # (the target loops skip None slots); only the kept alias binds.
        src = (_ALIAS_PRE
               + "def first_two(items: list[Box]) -> tuple[Box, Box]:\n"
               + "    return (items[0], items[1])\n\n"
               + "async def f(items: list[Box]) -> Int32:\n"
               + "    a, _ = first_two(items)\n"
               + "    await asyncio.sleep(0)\n"
               + "    return a.n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "std::get<0>(__tup_1)" in cpp
        assert "std::get<1>" not in cpp

    def test_ctor_rvalue_alias_source_defers(self):
        # A single-assign bind whose source is not a proven lvalue
        # (a ctor rvalue would dangle) keeps the named reject; sema
        # classifies `a = Box(1)` OWNING (frame_slot), so the defensive
        # arm is exercised via a call source aliasing nothing -- pin the
        # subscript-of-rvalue shape instead.
        src = (_ALIAS_PRE
               + "from tpy import Own\n\n"
               + "def make(n: Int32) -> Own[list[Box]]:\n"
               + "    return [Box(n)]\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    a = make(n)[0]\n"
               + "    await asyncio.sleep(0)\n"
               + "    return a.n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        fb = _res_fallback(src)
        assert sum(fb.values()) >= 1
        _assert_identical(src)


class TestValueTupleReturns:
    """Value-tuple async return slots: a literal source renders the spelled
    brace-init against the slot (the sync return arm's render); the
    `__tpy_async_ret` decl + Poll wrap stay skeleton. Generic TypeParamRef
    elements ride the val_or_ptr bridge (spelled slots + to_val_or_ptr
    wraps); view-element tuples stay out (the view rungs)."""

    def test_own_element_literal_return_routes(self):
        src = ("import asyncio\nfrom tpy import Int32, Own\n\n"
               + "class Box:\n"
               + "    v: Int32\n"
               + "    def __init__(self, v: Int32):\n        self.v = v\n\n"
               + "async def make_pair() -> tuple[Own[Box], Int32]:\n"
               + "    await asyncio.sleep(0)\n"
               + "    return (Box(10), 99)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.return_tuple_literal") == 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert ("std::tuple<Box, int32_t> __tpy_async_ret = "
                "std::tuple<Box, int32_t>{Box(10), 99};") in cpp

    def test_method_coro_field_elements_route(self):
        # The __anext__ flavor: a method coro's literal reads receiver
        # fields through the frame's __self spelling.
        src = ("import asyncio\nfrom tpy import Int32\n\n"
               + "class Pair:\n"
               + "    a: Int32\n"
               + "    b: Int32\n"
               + "    def __init__(self, a: Int32, b: Int32):\n"
               + "        self.a = a\n"
               + "        self.b = b\n\n"
               + "    async def pair(self) -> tuple[Int32, Int32]:\n"
               + "        await asyncio.sleep(0)\n"
               + "        return (self.a, self.b)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.return_tuple_literal") == 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "std::tuple<int32_t, int32_t>{__self.a, __self.b}" in cpp

    def test_generic_ref_tuple_return_routes(self):
        # The generic bridge rung: a TypeParamRef element slot spells
        # `::tpy::val_or_ptr_t<T>` and wraps its element in `to_val_or_ptr`
        # (value-vs-pointer decided at instantiation); the declared
        # `std::tuple<K, V>` ret local absorbs the spelled literal.
        # Spelled `tuple[K, V]`; sema resolves the elements to Ref[K]/Ref[V]
        # (the corpus generic_async_free_func_multi_T shape). Generic
        # bodies emit in the HEADER.
        src = ("import asyncio\nfrom tpy import Int32\n\n"
               + "async def pick[K, V](k: K, v: V) -> tuple[K, V]:\n"
               + "    await asyncio.sleep(0)\n"
               + "    return (k, v)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.return_generic_tuple") == 1
        assert witnesses.get("gentuple.literal") == 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, hpp, _cpp = _gen(src, thir=True)
        assert ("std::tuple<K, V> __tpy_async_ret = "
                "std::tuple<::tpy::val_or_ptr_t<K>, ::tpy::val_or_ptr_t<V>>"
                "{::tpy::to_val_or_ptr<::tpy::val_or_ptr_t<K>>(k), "
                "::tpy::to_val_or_ptr<::tpy::val_or_ptr_t<V>>(v)};") in hpp

    def test_generic_method_mixed_str_element_routes(self):
        # The method flavor with a CONCRETE str element beside a T: the str
        # slot spells its base type and the field element reads bare
        # through the frame's __self (the corpus generic_async_method
        # shape); only the T element takes the to_val_or_ptr wrap.
        src = ("import asyncio\nfrom tpy import Int32\n\n"
               + "class Container:\n"
               + "    def __init__(self, label: str):\n"
               + "        self.label = label\n\n"
               + "    async def labeled[T](self, x: T) -> tuple[str, T]:\n"
               + "        await asyncio.sleep(0)\n"
               + "        return (self.label, x)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.return_generic_tuple") == 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, hpp, _cpp = _gen(src, thir=True)
        assert ("std::tuple<std::string, ::tpy::val_or_ptr_t<T>>"
                "{__self.label, "
                "::tpy::to_val_or_ptr<::tpy::val_or_ptr_t<T>>(x)};") in hpp

    def test_generic_scalar_element_mix_routes(self):
        # A concrete SCALAR element beside a T: the scalar slot spells its
        # base type and reads bare (the _eligible_scalar branch of the
        # concrete-element row); only the T element wraps.
        src = ("import asyncio\nfrom tpy import Int32\n\n"
               + "async def tag[T](n: Int32, x: T) -> tuple[Int32, T]:\n"
               + "    await asyncio.sleep(0)\n"
               + "    return (n, x)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.return_generic_tuple") == 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, hpp, _cpp = _gen(src, thir=True)
        assert ("std::tuple<int32_t, ::tpy::val_or_ptr_t<T>>"
                "{n, ::tpy::to_val_or_ptr<::tpy::val_or_ptr_t<T>>(x)};"
                ) in hpp

    def test_generic_single_element_tuple_routes(self):
        # A single-element tuple[T] return: the emit parenthesizes
        # (`std::tuple<...>(...)`, the brace-init-ambiguity rule) with the
        # wrap template composing into the single slot.
        src = ("import asyncio\nfrom tpy import Int32\n\n"
               + "async def solo[T](x: T) -> tuple[T]:\n"
               + "    await asyncio.sleep(0)\n"
               + "    return (x,)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.return_generic_tuple") == 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, hpp, _cpp = _gen(src, thir=True)
        assert ("std::tuple<::tpy::val_or_ptr_t<T>>"
                "(::tpy::to_val_or_ptr<::tpy::val_or_ptr_t<T>>(x));") in hpp

    def test_generic_tuple_rvalue_element_defers(self):
        # A CALL rvalue at a T element slot: the AST routes it through
        # `_wrap_for_owned_slot`, not the to_val_or_ptr wrap -- the builder
        # admits plain declared NAMES only (gentuple.elem_source).
        src = ("import asyncio\nfrom tpy import Int32\n\n"
               + "def ident[T](x: T) -> T:\n"
               + "    return x\n\n"
               + "async def pick[K, V](k: K, v: V) -> tuple[K, V]:\n"
               + "    await asyncio.sleep(0)\n"
               + "    return (k, ident(v))\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        assert _res_fallback(src).get("expr.tuple_literal") == 1
        _assert_identical(src)

    def test_generic_tuple_own_element_mix_defers(self):
        # An Own[record] element beside a T is outside both tuple-return
        # families (no witness): _generic_value_tuple_return requires the
        # concrete elements to be narrow value elements, so the signature
        # gate rejects.
        src = ("import asyncio\nfrom tpy import Int32, Own\n\n"
               + "class Box:\n"
               + "    v: Int32\n"
               + "    def __init__(self, v: Int32):\n        self.v = v\n\n"
               + "async def pick[V](b: Own[Box], v: V) -> tuple[Own[Box], V]:\n"
               + "    await asyncio.sleep(0)\n"
               + "    return (b, v)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        assert _res_fallback(src).get("res.return_type") == 1
        _assert_identical(src)

    def test_value_opt_element_literal_routes(self):
        # A value-opt ELEMENT slot routes: both paths spell the None
        # element against the slot (std::nullopt) -- the AST's async
        # return render targets tuple literals like the sync arm.
        src = ("import asyncio\nfrom tpy import Int32\n\n"
               + "async def f(n: Int32) -> tuple[Int32, Int32 | None]:\n"
               + "    await asyncio.sleep(0)\n"
               + "    return (n, None)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.return_tuple_literal") == 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert ("std::tuple<int32_t, std::optional<int32_t>>"
                "{n, std::nullopt}") in cpp

    def test_own_element_name_source_routes(self):
        # An Own-element NAME source routes: both paths move the frame
        # local into the by-value slot (`std::move((*b))`) -- the
        # slot-targeted render; a bare copy would not compile for a
        # @nocopy element. Ctor-rvalue sources (the routed pin above)
        # render identically either way.
        src = ("import asyncio\nfrom tpy import Int32, Own\n\n"
               + "class Box:\n"
               + "    v: Int32\n"
               + "    def __init__(self, v: Int32):\n        self.v = v\n\n"
               + "async def f() -> tuple[Own[Box], Int32]:\n"
               + "    b = Box(10)\n"
               + "    await asyncio.sleep(0)\n"
               + "    return (b, 99)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.return_tuple_literal") == 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "std::tuple<Box, int32_t>{std::move((*b)), 99}" in cpp

    def test_value_opt_str_element_literal_routes(self):
        # The str-opt element family shares the lifted gate with the
        # scalar-opt pin above: None spells the slot's std::nullopt, a
        # str literal lands bare (the optional's converting ctor).
        src = ("import asyncio\nfrom tpy import Int32\n\n"
               + "async def f(n: Int32) -> tuple[Int32, str | None]:\n"
               + "    await asyncio.sleep(0)\n"
               + "    return (n, None)\n\n"
               + "async def g(n: Int32) -> tuple[Int32, str | None]:\n"
               + "    await asyncio.sleep(0)\n"
               + "    return (n, \"hi\")\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.return_tuple_literal") == 2
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert ("std::tuple<int32_t, std::optional<std::string>>"
                "{n, std::nullopt}") in cpp
        assert ("std::tuple<int32_t, std::optional<std::string>>"
                "{n, \"hi\"}") in cpp

    def test_nested_tuple_element_literal_routes(self):
        # A nested value-tuple element spells its inner brace-init
        # recursively against the element slot.
        src = ("import asyncio\nfrom tpy import Int32\n\n"
               + "async def f(n: Int32) -> tuple[tuple[Int32, Int32], Int32]:\n"
               + "    await asyncio.sleep(0)\n"
               + "    return ((n, n), 7)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.return_tuple_literal") == 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert ("std::tuple<std::tuple<int32_t, int32_t>, int32_t>"
                "{std::tuple<int32_t, int32_t>{n, n}, 7}") in cpp

    def test_tuple_literal_return_through_finally_targets(self):
        # A tuple-literal return under try/finally exercises the
        # OTHER _async_return_value_cpp scaffolding sites (pre-finally
        # capture / pending-slot store), which are syntactically
        # distinct from the direct-ready site the pins above cover.
        # The AST render assertion is the payload: the THIR body may
        # legitimately fall back whole on the finally machinery
        # (fallback emits identical AST, keeping _assert_identical
        # trivially green), but the targeted brace-init must appear at
        # the capture site either way.
        src = ("import asyncio\nfrom tpy import Int32\n\n"
               + "async def f(n: Int32) -> tuple[Int32, Int32 | None]:\n"
               + "    try:\n"
               + "        await asyncio.sleep(0)\n"
               + "        return (n, None)\n"
               + "    finally:\n"
               + "        print(\"f\")\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _assert_identical(src)
        _, _hpp, cpp = _gen(src, thir=True)
        assert ("__tpy_async_ret_0 = std::tuple<int32_t, "
                "std::optional<int32_t>>{n, std::nullopt};") in cpp

    def test_name_and_call_sources_route_position_blind(self):
        # NAME and CALL sources of NARROW value tuples ride the
        # position-blind tail (bare renders; the __tpy_async_ret decl
        # absorbs the copy). Widened-source shapes reject upstream at
        # their declaration/call gates (probe-verified).
        src = ("import asyncio\nfrom tpy import Int32\n\n"
               + "def pair(n: Int32) -> tuple[Int32, Int32]:\n"
               + "    return (n, n + 1)\n\n"
               + "async def f(n: Int32) -> tuple[Int32, Int32]:\n"
               + "    await asyncio.sleep(0)\n"
               + "    return pair(n)\n\n"
               + "async def g(n: Int32) -> tuple[Int32, Int32]:\n"
               + "    t = (n, n + 1)\n"
               + "    await asyncio.sleep(0)\n"
               + "    return t\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "__tpy_async_ret = pair(n);" in cpp
        assert "__tpy_async_ret = t;" in cpp

    def test_view_element_tuple_return_defers(self):
        # A StrView element keeps the tuple outside the return family
        # (the view-pinning rung) -- the signature gate rejects.
        src = ("import asyncio\nfrom tpy import Int32, StrView\n\n"
               + "async def f(s: StrView) -> tuple[StrView, Int32]:\n"
               + "    await asyncio.sleep(0)\n"
               + "    return (s, 1)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        assert _res_fallback(src).get("res.return_type") == 1
        _assert_identical(src)


class TestContainerReturns:
    """Container storage returns (`Poll<std::vector<T>>`): every source rides
    the position-blind tail bare -- the `__tpy_async_ret` decl supplies the
    type -- except the empty literal, which the AST leaves untyped."""

    def test_own_container_literal_routes(self):
        src = ("import asyncio\nfrom tpy import Int32, Own\n\n"
               + "async def f() -> Own[list[Int32]]:\n"
               + "    await asyncio.sleep(0)\n"
               + "    return [1, 2, 3]\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "std::vector<int32_t> __tpy_async_ret = {1, 2, 3};" in cpp

    def test_owned_name_source_returns_moved(self):
        # A frame-slot container name at its last use moves out of the slot
        # (the frame is completing; a copy broke @nocopy Own returns).
        src = ("import asyncio\nfrom tpy import Int32, Own\n\n"
               + "async def f() -> Own[list[Int32]]:\n"
               + "    xs = [1, 2]\n"
               + "    await asyncio.sleep(0)\n"
               + "    xs.append(3)\n"
               + "    return xs\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "__tpy_async_ret = std::move((*xs));" in cpp

    def test_bare_container_await_result_rejected(self):
        # The BARE `-> list[T]` slot is a borrow contract (the async
        # borrow-return ABI, sync-aligned): an awaited OWNED result is a
        # frame temporary that cannot be handed out as a borrow, so sema
        # rejects with the Own fix-hint -- the `-> Own[list[T]]` spelling
        # (previous test) is the routed shape.
        src = ("import asyncio\nfrom tpy import Int32, Own\n\n"
               + "async def g() -> Own[list[Int32]]:\n"
               + "    await asyncio.sleep(0)\n"
               + "    return [1]\n\n"
               + "async def f() -> list[Int32]:\n"
               + "    return await g()\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        with pytest.raises(Exception, match="Own\\[list\\[Int32\\]\\]"):
            _gen(src, thir=False)

    def test_container_return_through_finally_routes(self):
        # The pre-finally capture scaffolding site (a DIFFERENT
        # `_async_return_value_cpp` call site than direct-ready): the
        # value is bound to `__tpy_async_ret_N` before the finally chain
        # runs, and must render the same bare source there.
        src = ("import asyncio\nfrom tpy import Int32, Own\n\n"
               + "async def f() -> Own[list[Int32]]:\n"
               + "    try:\n"
               + "        await asyncio.sleep(0)\n"
               + "        return [1, 2]\n"
               + "    finally:\n"
               + "        print(\"f\")\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "__tpy_async_ret_0 = {1, 2};" in cpp

    def test_dict_and_set_literals_route(self):
        src = ("import asyncio\nfrom tpy import Int32, Own\n\n"
               + "async def d() -> Own[dict[Int32, Int32]]:\n"
               + "    await asyncio.sleep(0)\n"
               + "    return {1: 2}\n\n"
               + "async def s() -> Own[set[Int32]]:\n"
               + "    await asyncio.sleep(0)\n"
               + "    return {1, 2}\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_empty_container_literal_defers(self):
        # The AST spells an empty literal's type only when a target is
        # passed (`_gen_array_literal`'s T*-ambiguity guard); this render
        # has none, so it emits `= {}` where the lowering spells
        # `std::vector<int32_t>{}`. The sync return slot IS targeted --
        # hence the opposite rule there.
        src = ("import asyncio\nfrom tpy import Int32, Own\n\n"
               + "async def f() -> Own[list[Int32]]:\n"
               + "    await asyncio.sleep(0)\n"
               + "    return []\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        fb = _res_fallback(src)
        assert fb.get("stmt.return:return.empty_container_literal") == 1
        _assert_identical(src)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "__tpy_async_ret = {};" in cpp

    def test_empty_dict_literal_defers(self):
        # The guard's dict arm: emptiness is `children()` (keys + values),
        # since a dict literal carries no `elements`.
        src = ("import asyncio\nfrom tpy import Int32, Own\n\n"
               + "async def f() -> Own[dict[Int32, Int32]]:\n"
               + "    await asyncio.sleep(0)\n"
               + "    return {}\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        fb = _res_fallback(src)
        assert fb.get("stmt.return:return.empty_container_literal") == 1
        _assert_identical(src)


class TestValueOptReturns:
    """Value-repr Optional[scalar] async returns (`std::optional<T>
    __tpy_async_ret = ...`): None spells std::nullopt, a value-opt param
    name passes whole, scalars ride the position-blind tail."""

    def test_scalar_and_none_returns_route(self):
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def afind(n: Int32) -> Int32 | None:\n"
               + "    n = await step(n)\n"
               + "    if n > 2:\n"
               + "        return n * 3\n"
               + "    return None\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.return_value", 0) >= 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "std::optional<int32_t> __tpy_async_ret = std::nullopt;" in cpp

    def test_whole_param_pass_routes(self):
        # The value-opt param family now admits, so `return p` takes the
        # whole-optional name pass end-to-end.
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def relay(p: Int32 | None, n: Int32) -> Int32 | None:\n"
               + "    n = await step(n)\n"
               + "    return p\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_narrowed_param_read_routes(self):
        # A narrowed READ of the value-opt-scalar param through the frame
        # (`p.has_value()` test + `(*p)` deref) -- the deref arm of the
        # newly-admitted param family, not just the whole-name pass.
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(p: Int32 | None, n: Int32) -> Int32:\n"
               + "    n = await step(n)\n"
               + "    if p is not None:\n        return p + n\n"
               + "    return n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "(p.has_value())" in cpp
        assert "(*p)" in cpp


class TestContainerYieldBorrow:
    """Container yield slots (val_or_ref<C> skeleton signature): a yielded
    frame_slot LOCAL hands out the deref borrow `(*buf)`; other value
    shapes at the slot stay a named rung."""

    def test_frame_local_list_yield_routes(self):
        src = ("from tpy import Int32\nfrom typing import Iterator\n\n"
               + "def gen(n: Int32) -> Iterator[list[Int32]]:\n"
               + "    buf = [n]\n"
               + "    yield buf\n"
               + "    buf.append(n + 1)\n"
               + "    yield buf\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.yield_container_borrow", 0) >= 2
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "return (*buf);" in cpp

    def test_frame_local_dict_yield_routes(self):
        # The dict flavor of the container-slot gate (same deref arm).
        src = ("from tpy import Int32\nfrom typing import Iterator\n\n"
               + "def gen(n: Int32) -> Iterator[dict[Int32, Int32]]:\n"
               + "    d = {n: n}\n"
               + "    yield d\n"
               + "    d[n + 1] = n\n"
               + "    yield d\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.yield_container_borrow", 0) >= 2
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_param_source_yield_defers(self):
        # A container yield whose source is a PARAM (not a frame_slot
        # local) stays rejected -- the param field's bare read is a
        # different render than the frame-slot deref. (A literal/call
        # source is sema-rejected outright: "cannot yield local or
        # temporary as reference", so the name arm's reject is the only
        # live non-frame-slot shape.)
        src = ("from tpy import Int32\nfrom typing import Iterator\n\n"
               + "def gen(xs: list[Int32]) -> Iterator[list[Int32]]:\n"
               + "    yield xs\n"
               + "    yield xs\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        assert _res_fallback(src).get("res.yield_type") == 1
        _assert_identical(src)

    def test_record_loop_var_yield_routes(self):
        # Record yield slot: a pointer-form loop var name hands out the
        # deref borrow (`return (*b);`) -- the record sibling of the
        # container arm, unlocked by the loop-var bind admission.
        src = ("from tpy import Int32\nfrom typing import Iterator\n\n"
               + "class Box:\n    v: Int32\n"
               + "    def __init__(self, v: Int32) -> None:\n"
               + "        self.v = v\n\n"
               + "def twice(xs: list[Box]) -> Iterator[Box]:\n"
               + "    for b in xs:\n"
               + "        yield b\n"
               + "        yield b\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.yield_record_borrow", 0) >= 2
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "return (*b);" in cpp

    def test_record_param_source_yield_defers(self):
        # A record yield of a PARAM name (bare `Record&` field read, and
        # possibly narrowed if Optional) stays rejected -- only the routed
        # loop-var / frame_slot names take the deref arm.
        src = ("from tpy import Int32\nfrom typing import Iterator\n\n"
               + "class Box:\n    v: Int32\n"
               + "    def __init__(self, v: Int32) -> None:\n"
               + "        self.v = v\n\n"
               + "def rep(b: Box) -> Iterator[Box]:\n"
               + "    yield b\n"
               + "    yield b\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        assert _res_fallback(src).get("res.yield_type") == 1
        _assert_identical(src)

    def test_value_opt_loop_var_narrowed_yield_routes(self):
        # A value-opt-scalar dict-view loop var in a generator frame: the
        # pass-1 AsyncForAdvance registration keys its reads to the
        # binding arms, so the narrowed yield derefs (`return (*val);`)
        # and the None-test reads the whole optional -- without the
        # registration the narrowed read renders bare `val` (the repro-1
        # divergence).
        src = ("from tpy import Int32\nfrom typing import Iterator\n\n"
               + "def g_loop(d: dict[str, Int32 | None]) -> Iterator[Int32]:\n"
               + "    for val in d.values():\n"
               + "        if val is not None:\n"
               + "            yield val\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "return (*val);" in cpp
        assert "val.has_value()" in cpp

    def test_value_opt_unpack_target_narrowed_yield_routes(self):
        # The items() unpack sibling in a frame: the pass-1 tuple-unpack
        # registration keys the value-opt target, so the narrowed yield
        # derefs (`return (*v);`).
        src = ("from tpy import Int32\nfrom typing import Iterator\n\n"
               + "def g_items(d: dict[str, Int32 | None])"
               + " -> Iterator[Int32]:\n"
               + "    for k, v in d.items():\n"
               + "        if v is not None and len(k) > 0:\n"
               + "            yield v\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "return (*v);" in cpp

    def test_multi_var_isinstance_frame_cond_defers(self):
        # The multi-var isinstance compound is a SYNC-body admission; the
        # resumable narrow model (`_narrow_cond_info` at the Branch) has
        # no multi-var arm -- the frame lane keeps rejecting.
        src = ("from tpy import Int32\nfrom typing import Iterator\n\n"
               + "class A:\n    x: Int32\n"
               + "    def __init__(self, x: Int32) -> None:\n"
               + "        self.x = x\n\n"
               + "class B:\n    y: Int32\n"
               + "    def __init__(self, y: Int32) -> None:\n"
               + "        self.y = y\n\n"
               + "def g(a: A | B, b: A | B) -> Iterator[Int32]:\n"
               + "    if isinstance(a, A) and isinstance(b, B):\n"
               + "        yield a.x + b.y\n"
               + "    yield -1\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        assert _res_fallback(src)
        _assert_identical(src)

    def test_value_opt_view_loop_var_yield_defers(self):
        # The VIEW flavor (`dict[str, str | None]`) stays out:
        # `_container_value_opt_scalar_elem` is scalar-only, so the
        # dict-view iterable gate rejects and the body falls back whole.
        src = ("from typing import Iterator\n\n"
               + "def g_loop(d: dict[str, str | None]) -> Iterator[str]:\n"
               + "    for val in d.values():\n"
               + "        if val is not None:\n"
               + "            yield val\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        assert _res_fallback(src)
        _assert_identical(src)


class TestFrameFamilyAdmissions:
    """The local/param family admissions (value tuples, Optional-ptr
    locals, optional-view / Fn / value-union / Own-container params):
    each routes where its read machinery exists and defers with the
    honest next-blocker where it does not."""

    def test_value_tuple_local_await_bind_routes(self):
        # E: a value-tuple local bound from an await result (skeleton
        # bind) reads via bare std::get.
        src = (_PRE
               + "async def pair(n: Int32) -> tuple[Int32, Int32]:\n"
               + "    return (n, n + 1)\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    p = await pair(n)\n"
               + "    return p[0] + p[1]\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        fb = _res_fallback(src)
        # Both bodies route since the value-tuple return cell landed
        # (the producer's literal return takes the spelled-brace-init arm).
        assert fb == {}
        _assert_identical(src)
        _, hpp, cpp = _gen(src, thir=True)
        assert "std::get<0>(p)" in hpp + cpp

    def test_optional_ptr_local_await_bind_routes(self):
        # C: a pointer-repr Optional local bound from an await result
        # (skeleton bind) reads via lc.pointers (null test + arrow); the
        # PRODUCER body keeps res.return_type (Optional-record returns are
        # their own rung).
        src = (_PRE
               + "class Box:\n    val: Int32\n"
               + "    def __init__(self, v: Int32) -> None:\n"
               + "        self.val = v\n\n"
               + "async def get(b: Box, n: Int32) -> Box | None:\n"
               + "    if n > 0:\n        return b\n"
               + "    return None\n\n"
               + "async def f(b: Box, n: Int32) -> Int32:\n"
               + "    t = await get(b, n)\n"
               + "    if t is not None:\n        return t.val\n"
               + "    return -1\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        fb = _res_fallback(src)
        # get only; f routes. The producer's fence moved from the slot gate
        # (res.return_type) to the source rung (return.borrow_form) when
        # the optional-return slots were admitted -- a bare NAME source at
        # the BORROW slot stays out (only the field lift is admitted).
        assert fb.get("stmt.return:return.borrow_form") == 1
        assert len(fb) == 1
        _assert_identical(src)

    def test_optional_view_param_routes(self):
        # F1: an Optional[str] param captures owned (skeleton OWNED_COPY);
        # None-test + narrowed reads ride the value-opt-view arms.
        src = ("from typing import Optional\nfrom tpy import Int32\n\n"
               + "async def str_len(s: Optional[str]) -> Int32:\n"
               + "    if s is None:\n        return -1\n"
               + "    return len(s)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "(!s.has_value())" in cpp
        assert "::tpy::__len__((*s))" in cpp

    def test_fn_param_routes(self):
        # F2: an Fn param is a templated frame field; the leaf read is the
        # bare call `pred(x)`.
        src = ("from typing import Iterator\nfrom tpy import Fn, Int32\n\n"
               + "def keep(pred: Fn[[Int32], bool], xs: list[Int32])"
               + " -> Iterator[Int32]:\n"
               + "    for x in xs:\n"
               + "        if pred(x):\n            yield x\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        _, hpp, cpp = _gen(src, thir=True)
        assert "if (pred(x))" in hpp + cpp

    def test_value_union_param_routes(self):
        # F3: a value-union param (`int | str` -> moved std::variant
        # capture); the narrowed read rides the entry-narrowings
        # std::get/__a alias machinery.
        src = ("from typing import Iterator\nfrom tpy import Int32\n\n"
               + "def gen(a: int | str) -> Iterator[int]:\n"
               + "    if isinstance(a, int):\n        yield a\n"
               + "    yield 0\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        _, hpp, cpp = _gen(src, thir=True)
        assert "std::holds_alternative<::tpy::BigInt>(a)" in hpp + cpp

    def test_own_container_param_routes(self):
        # F6: an Own[list] param admits (moved value-container field); its
        # subscript READ rides the Own-receiver row and the len-position
        # NAME read rides the frame-field row (CONVERTED from a
        # name.own_read fence: the reject named the sync param's movable
        # last-use render, which a frame body does not have).
        src = ("from typing import Iterator\nfrom tpy import Int32, Own\n\n"
               + "def gen_own(xs: Own[list[Int32]]) -> Iterator[Int32]:\n"
               + "    yield xs[0]\n"
               + "    yield len(xs)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        assert not _res_fallback(src)
        _assert_identical(src)


class TestSuspendMethodOperands:
    """ERASED/BORROWED await operands that are METHOD calls (`await
    tx.send(x)`): the whole operand is consumed by the skeleton's
    emplace/move wrap, so the method gate's result-type check is vacuous
    under SUSPEND use (`suspend_ok`); receiver/arg/fi gates still apply."""

    def test_channel_method_await_routes(self):
        src = ("import asyncio\n"
               + "from tpy import Int32, Own\n"
               + "from tpy.channel import channel, Sender, Receiver, "
               + "ChannelClosed\n\n"
               + "async def producer(tx: Own[Sender[Int32]]) -> None:\n"
               + "    await tx.send(1)\n"
               + "    tx.close()\n\n"
               + "async def consumer(rx: Own[Receiver[Int32]]) -> None:\n"
               + "    try:\n"
               + "        v = await rx.recv()\n"
               + "        print(v)\n"
               + "    except ChannelClosed:\n"
               + "        pass\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.suspend_operand", 0) >= 2
        resumable = {k: v for k, v in fallback.items()
                     if k.startswith("resumable:")}
        assert not resumable

    def test_rejecting_arg_still_falls_back(self):
        # suspend_ok blanks only the RESULT-type check; an off-slice ARG
        # (an f-string) still rejects the operand, falling back whole.
        src = ("import asyncio\n"
               + "from tpy import Int32, Own\n"
               + "from tpy.channel import channel, Sender, Receiver, "
               + "ChannelClosed\n\n"
               + "async def producer(tx: Own[Sender[str]], n: Int32)"
               + " -> None:\n"
               + "    await tx.send(f\"v{n}\")\n"
               + "    tx.close()\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        fallback = _res_fallback(src)
        assert sum(fallback.values()) >= 1
        _assert_identical(src)


class TestBranchFrameDecls:
    """Branch-nested decls of plain frame fields: the same position-blind
    member assign as the top-level leaf decl arm (`_lower_frame_field_assign`
    shared by both); types register via the nested pass-1 walk."""

    def test_branch_frame_decl_routes(self):
        # A local first-assigned inside if/else branches and read across a
        # suspension: both branch decls are plain frame-field assigns
        # (position-blind member writes), registered by the nested pass-1
        # walk.
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    if n == 0:\n"
               + "        r = 100\n"
               + "    else:\n"
               + "        r = n + 1\n"
               + "    n = await step(n)\n"
               + "    return r + n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.branch_frame_write", 0) >= 2
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_branch_frame_reassign_routes(self):
        # The reassign flavor: a top-level-declared frame field reassigned
        # inside a leaf branch takes the same member-assign arm.
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    r = 0\n"
               + "    if n > 2:\n"
               + "        r = 5\n"
               + "    n = await step(n)\n"
               + "    return r + n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.branch_frame_write", 0) >= 1
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_match_dispatch_arm_frame_decl_routes(self):
        # A frame-field decl inside a SUSPENDING match's arm: the arm body
        # is a BB chain (MatchDispatch hook mode), so the decl is a
        # top-level BB leaf taking the pre-existing frame arm -- pinning
        # the match-territory composition (a non-suspending leaf match
        # takes the sync tiers instead -- pinned below).
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    match n:\n"
               + "        case 0:\n"
               + "            r = 100\n"
               + "            n = await step(n)\n"
               + "        case _:\n"
               + "            r = n + 1\n"
               + "    return r + n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.match_dispatch") == 1
        assert witnesses.get("res.decl_assign", 0) >= 2
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_async_helper_bare_return_defers(self):
        # A BARE return in an ASYNC finally helper: rejected by the
        # is_generator disjunct alone (the value disjunct is False here) --
        # guards the generator-only admission against a future gate slip.
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> None:\n"
               + "    try:\n        n = await step(n)\n"
               + "    finally:\n        return\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        assert _res_fallback(src).get("res.finally_return") == 1

    def test_branch_frame_slot_decl_routes(self):
        # A frame_slot local (owning non-value, `.emplace()` render) first-
        # declared in a branch: every frame write is position-blind, so the
        # branch arm reuses the leaf arm's `_lower_frame_slot_write`.
        src = (_PRE
               + "class Holder:\n"
               + "    v: Int32\n"
               + "    def __init__(self, v: Int32) -> None:\n"
               + "        self.v = v\n\n"
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    if n > 2:\n"
               + "        b = Holder(1)\n"
               + "    else:\n"
               + "        b = Holder(2)\n"
               + "    n = await step(n)\n"
               + "    return n + b.v\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.branch_frame_slot_write", 0) >= 2
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_branch_borrow_tuple_decl_routes(self):
        # A borrow-form tuple frame field (`std::tuple<int32_t, Holder*>`)
        # bound on each branch: the bare member assign, shared with the leaf
        # arm via `_lower_borrow_tuple_frame_write`.
        src = (_PRE
               + "from typing import Iterator\n\n"
               + "class Holder:\n"
               + "    v: Int32\n"
               + "    def __init__(self, v: Int32) -> None:\n"
               + "        self.v = v\n\n"
               + "def gen(b: Holder, c: Holder, cond: bool)"
               + " -> Iterator[tuple[Int32, Holder]]:\n"
               + "    if cond:\n"
               + "        t = (1, b)\n"
               + "    else:\n"
               + "        t = (2, c)\n"
               + "    yield t\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.branch_btuple_write", 0) >= 2
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_try_body_frame_slot_decl_routes(self):
        # An except-only leaf try routes through the sync tiers, so a frame
        # decl inside its body reaches lowering -- and the nested pass-1
        # walk must have registered its type (try_body is part of the walk).
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    try:\n"
               + "        xs = [n, n]\n"
               + "    except ValueError:\n"
               + "        xs = [0]\n"
               + "    n = await step(n)\n"
               + "    return n + len(xs)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.branch_frame_slot_write", 0) >= 1
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_leaf_match_routes_through_sync_tiers(self):
        # A NON-SUSPENDING match in a resumable body is suspension-free by
        # construction (the CFG builder turns a suspending match into a
        # MatchDispatch terminator), and its dispatch touches no __state /
        # finally scaffolding -- so the sync match tiers render it
        # byte-identically mid-state, like the except-only leaf try.
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    match n:\n"
               + "        case 0:\n            r = 100\n"
               + "        case _:\n            r = n + 1\n"
               + "    n = await step(n)\n"
               + "    return r + n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.leaf_match_sync", 0) >= 1
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_leaf_match_unmirrored_arm_body_defers(self):
        # BOUNDARY: the fall-through does not blanket-admit match -- an arm
        # body carrying a shape the sync tiers reject (here the
        # Optional-enter with target, with.frame_target_family) still
        # falls back.
        src = (_PRE
               + "class Guard:\n"
               + "    def __enter__(self) -> Int32 | None:\n        return 1\n"
               + "    def __exit__(self, exc_type, exc_val, exc_tb)"
               + " -> None:\n        pass\n\n"
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    r = 0\n"
               + "    match n:\n"
               + "        case 0:\n"
               + "            with Guard() as g:\n"
               + "                if g is not None:\n                    r = g\n"
               + "        case _:\n            r = n + 1\n"
               + "    n = await step(n)\n"
               + "    return r + n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        assert sum(_res_fallback(src).values()) >= 1
        _assert_identical(src)

    def test_branch_coro_handle_decl_defers(self):
        # BOUNDARY: a concrete-coro handle slot keeps the named reject in
        # branch position -- its leaf arm gates on a factory-call source and
        # its NAME-source render is the two-statement emplace/reset pair.
        src = ("import asyncio\n" + _PRE
               + "async def step(n: Int32) -> Int32:\n"
               + "    await asyncio.sleep(0)\n"
               + "    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    if n > 2:\n"
               + "        h = step(1)\n"
               + "    else:\n"
               + "        h = step(2)\n"
               + "    return await h\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        assert _res_fallback(src).get("res.leaf_field_write") == 1
        _assert_identical(src)

    def test_try_finally_frame_decl_routes(self):
        # A frame-slot decl inside a crossing-free leaf finally now reaches
        # the branch arm (the enclosing leaf no longer rejects wholesale):
        # the slot type and the write agree here (`frame_slot<std::vector<
        # int32_t>>` / `.emplace(std::vector<int32_t>{..})`). The BUGS.md
        # try-body slot-type defect needs a try/EXCEPT pair declaring the
        # same name with differently-shaped literals per arm, which this
        # single-decl try/finally never forms.
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    try:\n"
               + "        xs = [n, n]\n"
               + "    finally:\n"
               + "        print(\"done\")\n"
               + "    n = await step(n)\n"
               + "    return n + len(xs)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.leaf_try_finally", 0) == 1
        assert witnesses.get("res.branch_frame_slot_write", 0) == 1
        assert not any(k.startswith("resumable:") for k in fallback)


class TestFinallyHelper:
    """R6-finally-helper: a suspension-free `finally` body (emitted as a
    `__finally_<n>()` member fn) around a suspension. The helper body lowers
    into the leaves table; gen_coro_finally_top_def emits it through the seam.
    CFG-based finallies (the body suspends) keep rejecting."""

    def test_helper_finally_around_await_routes(self):
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    try:\n"
               + "        n = await step(n)\n"
               + "        print(n)\n"
               + "    finally:\n"
               + "        print(n)\n"
               + "    return n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.finally_helper") == 1
        assert witnesses.get("res.try_region") == 1
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_helper_finally_with_except_routes(self):
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    try:\n        n = await step(n)\n"
               + "    except ValueError:\n        n = 0\n"
               + "    finally:\n        print(n)\n"
               + "    return n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.finally_helper") == 1
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_return_in_helper_finally_rejects(self):
        # A `return` inside the finally helper needs the async Poll replay /
        # generator __finally_stop render -- rejects via res.finally_return
        # (a named rung outside the leaf-return hook), keeping the whole
        # body on AST.
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    try:\n        n = await step(n)\n"
               + "    finally:\n        return 0\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        assert _res_fallback(src).get("res.finally_return") == 1

    def test_generator_helper_return_routes(self):
        # Generator helper bare return: the fixed `__finally_stop = true;
        # return;` pair renders via the hook's in_generator_finally_helper
        # arm (return in finally suppresses any pending exception).
        src = ("from tpy import Int32\nfrom typing import Iterator\n\n"
               + "def gen(n: Int32) -> Iterator[Int32]:\n"
               + "    try:\n"
               + "        i = 0\n"
               + "        while i < n:\n"
               + "            yield i\n"
               + "            i = i + 1\n"
               + "    finally:\n"
               + "        return\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.finally_stop") == 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "this->__finally_stop = true;" in cpp

    def test_generator_helper_nested_return_routes(self):
        # A return nested in a leaf `if` INSIDE the helper body: the same
        # dispatch arm fires, emitted inside the THIR-lowered compound.
        src = ("from tpy import Int32\nfrom typing import Iterator\n\n"
               + "def gen(n: Int32) -> Iterator[Int32]:\n"
               + "    try:\n"
               + "        i = 0\n"
               + "        while i < n:\n"
               + "            yield i\n"
               + "            i = i + 1\n"
               + "    finally:\n"
               + "        if n > 3:\n"
               + "            return\n"
               + "        print(n)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.finally_stop") == 1
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_narrowing_if_in_helper_defers(self):
        # A narrowing early-return `if` inside a finally helper: the guard
        # rejects with res.narrowed_resume BEFORE the nested return's own
        # res.finally_return -- pinning that the helper walk names its
        # missing post-if arm, not just the return render. (Raise-arm ifs
        # produce no post-if fact on either path -- `_post_if_narrow_fact`
        # is return-terminated only -- so the return flavor is the guard's
        # whole domain.)
        src = ("from tpy import Int32\n\n"
               + "class Dog:\n"
               + "    def sound(self) -> str:\n        return \"woof\"\n\n"
               + "class Cat:\n"
               + "    def sound(self) -> str:\n        return \"meow\"\n\n"
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(a: Dog | Cat, n: Int32) -> Int32:\n"
               + "    try:\n        n = await step(n)\n"
               + "    finally:\n"
               + "        if isinstance(a, Dog):\n"
               + "            return 0\n"
               + "        print(a.sound())\n"
               + "    return n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        assert _res_fallback(src).get("res.narrowed_resume") == 1
        _assert_identical(src)

    def test_generator_helper_finally_routes(self):
        # A generator with a try/finally around a yield: the finally helper
        # (suspension-free) routes; a bare return stays skeleton.
        src = ("from tpy import Int32\nfrom typing import Iterator\n\n"
               + "def gen(n: Int32) -> Iterator[Int32]:\n"
               + "    i = 0\n"
               + "    try:\n"
               + "        while i < n:\n"
               + "            yield i\n"
               + "            i = i + 1\n"
               + "    finally:\n"
               + "        print(i)\n\n"
               + "def main() -> None:\n"
               + "    for x in gen(3):\n        print(x)\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.finally_helper") == 1
        assert not any(k.startswith("resumable:") for k in fallback)


class TestAsyncLoopAndWith:
    """R3-async-for + R5-async-with: the synthetic __anext__ / __aenter__ /
    __aexit__ yields and the StopAsyncIteration / CFG-finally regions they
    synthesize are all skeleton; the only user render is the iterable /
    manager expression. Verified byte-identical on the corpus cases
    async_for_own_iterable / async_with_borrowed_manager."""

    def test_async_with_borrowed_manager_routes(self):
        src = ("import asyncio\nfrom tpy import Int32\n\n"
               + "class Counter:\n    n: Int32\n"
               + "    def __init__(self) -> None:\n        self.n = 0\n"
               + "    async def __aenter__(self) -> None:\n"
               + "        await asyncio.sleep(0)\n        self.n += 1\n"
               + "    async def __aexit__(self, exc_type: None, exc_val: None,"
               + " exc_tb: None) -> None:\n"
               + "        await asyncio.sleep(0)\n\n"
               + "async def f() -> Int32:\n"
               + "    c = Counter()\n"
               + "    async with c:\n        await asyncio.sleep(0)\n"
               + "    return c.n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        # _assert_identical enforces byte-identity. `f` routes the async-with;
        # __aexit__'s None-typed params keep it on AST (res.param_type, a
        # separate method / gate) -- so the case carries that one fallback.
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.async_with", 0) >= 1
        assert set(fallback) <= {"resumable:res.param_type"}

    def test_async_for_local_iterator_routes(self):
        src = ("import asyncio\nfrom tpy import Int32\n\n"
               + "class AIter:\n    i: Int32\n    n: Int32\n"
               + "    def __init__(self, n: Int32) -> None:\n"
               + "        self.i = 0\n        self.n = n\n"
               + "    def __aiter__(self) -> \"AIter\":\n        return self\n"
               + "    async def __anext__(self) -> Int32:\n"
               + "        await asyncio.sleep(0)\n"
               + "        if self.i >= self.n:\n"
               + "            raise StopAsyncIteration()\n"
               + "        self.i += 1\n        return self.i\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    total = 0\n"
               + "    it = AIter(n)\n"
               + "    async for x in it:\n        total = total + x\n"
               + "    return total\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.async_loop", 0) >= 1
        assert not any(k.startswith("resumable:") for k in fallback)


class TestCfgFinally:
    """R6-finally-cfg: a finally body that itself suspends lives in the state
    machine (TryRegion.captured_exc_field, FinallyRegion, AsyncFinallyExit).
    The pending-return replay + exc rethrow are pure skeleton (zero new
    renders); the finally body statements are ordinary BB leaves."""

    def test_await_in_finally_routes(self):
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    total = n\n"
               + "    try:\n        total = await step(total)\n"
               + "    finally:\n        total = await step(total)\n"
               + "    return total\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.try_region") == 1
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_return_in_try_with_await_finally_routes(self):
        # A `return` in the try body routes through the pending-return slot
        # (to_borrow=False store); its value render is the seam's and stays
        # identical for the value-scalar slice.
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    total = n\n"
               + "    try:\n"
               + "        total = await step(total)\n"
               + "        return total\n"
               + "    finally:\n        total = await step(total)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.return_value", 0) >= 1
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_return_await_in_finally_routes(self):
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    total = n\n"
               + "    try:\n        total = await step(total)\n"
               + "    finally:\n        return await step(total)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)


_CM = ("class CM:\n"
       "    n: Int32\n"
       "    def __init__(self, n: Int32) -> None:\n        self.n = n\n"
       "    def __enter__(self) -> Int32:\n        return self.n\n"
       "    def __exit__(self, exc_type, exc_val, exc_tb) -> None:\n"
       "        print(self.n)\n\n")


class TestWithRegions:
    """R6-with: sync `with` around a suspension. The `__with_ctx_<n>` bind
    wrap, `__enter__` call, `__exit__` catch replay and normal-exit calls
    are skeleton; the manager expression is the one leaf render
    (render_region_expr)."""

    def test_with_around_await_routes(self):
        src = (_PRE + _CM
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    with CM(n):\n        n = await step(n)\n"
               + "    return n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.with_region") == 1
        assert witnesses.get("res.with_ctx") == 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "__with_ctx_0.emplace(CM(n));" in cpp

    def test_with_as_target_routes(self):
        # The `as`-target bind is a skeleton frame write; later leaves read
        # the target with the sema enter type.
        src = (_PRE + _CM
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    with CM(n) as base:\n"
               + "        n = await step(n)\n"
               + "        n = n + base\n"
               + "    return n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.with_ctx") == 1
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_borrowed_manager_routes(self):
        # An lvalue manager binds borrowed: the &(..) wrap is skeleton, the
        # frame-slot deref `(*cm)` comes from the leaf's name render.
        src = (_PRE + _CM
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    cm = CM(n)\n"
               + "    with cm:\n        n = await step(n)\n"
               + "    return n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.with_ctx") == 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "__with_ctx_0 = &((*cm));" in cpp

    def test_return_inside_with_captures_before_exit(self):
        # A ReturnT inside the with-region reaches _make_async_return's
        # pre-finally capture (finally_stack holds the with-exit closure):
        # the typed `__tpy_async_ret_N` local is skeleton, the value render
        # is the seam's -- identical for value scalars.
        src = (_PRE + _CM
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    with CM(n):\n"
               + "        n = await step(n)\n"
               + "        return n + 1\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.return_value", 0) >= 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert ("int32_t __tpy_async_ret_0 = "
                "(::tpy::add_check<int32_t>(n, 1));") in cpp

    def test_non_lvalue_field_manager_rejects(self):
        # A borrowed manager that is not a plain declared name (here a field
        # access) stays outside the admitted manager families.
        src = (_PRE + _CM
               + "class Holder:\n"
               + "    cm: CM\n"
               + "    def __init__(self, n: Int32) -> None:\n"
               + "        self.cm = CM(n)\n\n"
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    h = Holder(n)\n"
               + "    with h.cm:\n        n = await step(n)\n"
               + "    return n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        assert _res_fallback(src).get("res.with_manager") == 1


_SELF_CM = ("class SCM:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n        self.n = n\n"
            '    def __enter__(self) -> "SCM":\n        return self\n'
            "    def __exit__(self, exc_type, exc_val, exc_tb) -> None:\n"
            "        pass\n\n")


class TestLeafWith:
    """A LEAF `with` (suspension-free body) in a resumable frame lowers
    through the sync with arm: frame-resident targets take the FRAME_SLOT
    emplace / FRAME_FIELD assign binds, an owned manager whose target field
    points into it takes the skeleton-declared `__with_ctx_<K>` frame home
    (`with_owned_ctx_map`), and a borrowed frame-slot manager binds through
    the leaf's `(*name)` render."""

    def test_frame_slot_target_routes(self):
        # Self-returning manager, target read across a later suspension:
        # `guard.emplace(__ctx_N.__enter__())` off the borrowed `(*c)` bind.
        src = (_PRE + _SELF_CM
               + "from typing import Iterator\n"
               + "def gen() -> Iterator[Int32]:\n"
               + "    c = SCM(5)\n"
               + "    with c as guard:\n"
               + "        pass\n"
               + "    yield 1\n"
               + "    yield guard.n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("with.frame_slot_target", 0) >= 1
        assert witnesses.get("with.manager_borrowed", 0) >= 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "auto& __ctx_1 = (*c);" in cpp
        assert "guard.emplace(__ctx_1.__enter__());" in cpp

    def test_frame_field_value_target_routes(self):
        # A VALUE enter target is a plain frame field: `x = __enter__();`,
        # plus the no-target item alongside.
        src = (_PRE + _CM
               + "from typing import Iterator\n"
               + "def gen() -> Iterator[Int32]:\n"
               + "    with CM(5) as x:\n"
               + "        pass\n"
               + "    with CM(6):\n"
               + "        pass\n"
               + "    yield 1\n"
               + "    yield x\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("with.frame_field_target", 0) >= 1
        assert witnesses.get("with.no_target", 0) >= 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "x = __ctx_1.__enter__();" in cpp

    def test_owned_manager_frame_home_routes(self):
        # An OWNED rvalue manager whose target frame field aliases
        # `__enter__()`'s result: the `__with_ctx_<K>` frame home replaces
        # the sync function-scope slot.
        src = (_PRE + _SELF_CM
               + "from typing import Iterator\n"
               + "def gen() -> Iterator[Int32]:\n"
               + "    with SCM(7) as view:\n"
               + "        pass\n"
               + "    yield 1\n"
               + "    yield view.n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("with.manager_frame_ctx", 0) >= 1
        assert witnesses.get("with.frame_slot_target", 0) >= 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "__with_ctx_0.emplace(SCM(7));" in cpp
        assert "auto& __ctx_1 = (*__with_ctx_0);" in cpp

    def test_frame_field_str_target_routes(self):
        # The FRAME_FIELD family's str member (`__enter__() -> str`, the
        # owned `std::string` frame field): same plain assign bind, reads
        # after the suspension render bare. The corpus witness is
        # control_flow/with_target_view_enter_owns; this pins the arm at
        # unit level across the admitted family, not just scalars.
        src = (_PRE
               + "class SM:\n"
               + "    def __enter__(self) -> str:\n"
               + '        return "tag"\n'
               + "    def __exit__(self, exc_type, exc_val, exc_tb)"
               + " -> None:\n        pass\n\n"
               + "from typing import Iterator\n"
               + "def gen() -> Iterator[Int32]:\n"
               + "    with SM() as s:\n"
               + "        pass\n"
               + "    yield 1\n"
               + "    yield len(s)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("with.frame_field_target", 0) >= 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "s = __ctx_1.__enter__();" in cpp

    def test_frame_borrow_tuple_target_routes(self):
        # A BORROW-form tuple enter target (`std::tuple<Item*, Int32>`):
        # it subtracts itself from plain_frame_fields because its
        # decl/reassign render differs, but the with-bind is the same
        # plain member assign. Rebound by a second `with` so the assign
        # (not a decl) is what is witnessed.
        src = (_PRE
               + "class Item:\n"
               + "    v: Int32\n"
               + "    def __init__(self, v: Int32) -> None:\n"
               + "        self.v = v\n"
               + "class PM:\n"
               + "    item: Item\n"
               + "    tag: Int32\n"
               + "    def __init__(self, v: Int32) -> None:\n"
               + "        self.item = Item(v)\n"
               + "        self.tag = v\n"
               + "    def __enter__(self) -> tuple[Item, Int32]:\n"
               + "        return (self.item, self.tag)\n"
               + "    def __exit__(self, exc_type, exc_val, exc_tb)"
               + " -> None:\n        pass\n\n"
               + "from typing import Iterator\n"
               + "def gen() -> Iterator[Int32]:\n"
               + "    a = PM(7)\n"
               + "    b = PM(9)\n"
               + "    with a as p:\n"
               + "        pass\n"
               + "    yield 1\n"
               + "    with b as p:\n"
               + "        pass\n"
               + "    yield 2\n"
               + "    item, tag = p\n"
               + "    item.v += 1\n"
               + "    yield item.v\n"
               + "    yield b.item.v\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("with.frame_btuple_target", 0) >= 2
        assert not any(k.startswith("resumable:") for k in fallback)
        _, hpp, cpp = _gen(src, thir=True)
        assert "std::tuple<Item*, int32_t> p;" in hpp
        assert cpp.count("p = __ctx_1.__enter__();") == 1
        assert cpp.count("p = __ctx_2.__enter__();") == 1

    def test_leaf_return_defers(self):
        # A return nested in a leaf with renders its __exit__ chain through
        # the ctx hook's finally_stack, which the THIR with emit does not
        # populate -- the body falls back byte-identically.
        src = (_PRE + _CM
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    n = await step(n)\n"
               + "    if n > 0:\n"
               + "        with CM(n) as base:\n"
               + "            return base + 1\n"
               + "    return n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _assert_identical(src)
        assert _res_fallback(src).get("stmt.with:with.leaf_return") == 1

    def test_frame_target_family_defers(self):
        # A leaf with-as target outside the frame_slot / plain-field pair
        # (here the enter type is Optional -- an optional frame family)
        # keeps rejecting with the named reason, byte-identically.
        src = (_PRE
               + "class OCM:\n"
               + "    n: Int32\n"
               + "    def __init__(self, n: Int32) -> None:\n"
               + "        self.n = n\n"
               + "    def __enter__(self) -> Int32 | None:\n"
               + "        return self.n\n"
               + "    def __exit__(self, exc_type, exc_val, exc_tb) -> None:\n"
               + "        pass\n\n"
               + "from typing import Iterator\n"
               + "def gen() -> Iterator[Int32]:\n"
               + "    with OCM(5) as x:\n"
               + "        pass\n"
               + "    yield 1\n"
               + "    if x is not None:\n"
               + "        yield x\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _assert_identical(src)
        assert _res_fallback(src).get(
            "stmt.with:with.frame_target_family") == 1


class TestLeafReturns:
    """Returns nested in non-suspending leaf compounds: the scaffolding
    (done state, `__tpy_async_ret` bind, Poll wrap / StopIteration,
    finally-chain walk) stays skeleton via the emit hook
    (`_make_async_return` / `_make_generator_resumable_return`); THIR
    supplies the position (THIRResumableReturn) and the value render
    through the seam's return_values table."""

    def test_async_void_leaf_return_routes(self):
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> None:\n"
               + "    n = await step(n)\n"
               + "    if n > 2:\n        return\n"
               + "    print(n)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.nested_return") == 1
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_owned_str_leaf_return_wraps(self):
        # The view->owned copy (`_wrap_view_owned_return`) fires identically
        # for a nested return's value -- shared with the ReturnT arm.
        src = ("import asyncio\nfrom tpy import Int32\n\n"
               + "async def pick(tag: str, n: Int32) -> str:\n"
               + "    await asyncio.sleep(0)\n"
               + "    if n > 2:\n        return tag\n"
               + "    return \"lo\"\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.nested_return") == 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "std::string __tpy_async_ret = std::string(tag);" in cpp

    def test_leaf_return_under_with_walks_finally(self):
        # The nested return inside a with region walks the live finally
        # frame (__exit__ before Poll::ready) -- the hook re-enters the
        # skeleton's chain machinery unchanged.
        src = (_PRE + _CM
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    with CM(n):\n"
               + "        n = await step(n)\n"
               + "        if n > 2:\n            return 99\n"
               + "    return n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.nested_return") == 1
        assert not any(k.startswith("resumable:") for k in fallback)

class TestFrameFieldShadowing:
    """A for-each loop var inside a resumable body binds a C++ local that
    shadows its same-named frame field: reads inside the body render BARE
    (the AST registers register_frame_field_shadow and suppresses the
    `(*name)` peel; THIR masks the name out of lc.frame_slots for the
    body). Regression pin for the merged resumable x for-each composition
    (async/coro_for_loop_ref_iter divergence)."""

    SRC = ("import asyncio\nfrom tpy import Int32\n\n"
           "class Item:\n    n: Int32\n"
           "    def __init__(self, n: Int32) -> None:\n        self.n = n\n\n"
           "class Container:\n    items: list[Item]\n"
           "    def __init__(self) -> None:\n        self.items = []\n"
           "    async def total(self) -> Int32:\n"
           "        s: Int32 = 0\n"
           "        await asyncio.sleep(0)\n"
           "        for it in self.items:\n"
           "            s += it.n\n"
           "        return s\n")

    def test_loop_var_shadow_reads_bare(self):
        witnesses, fallback = _assert_identical(self.SRC)
        assert "res.body" in witnesses  # the coro routed
        _, _hpp, cpp = _gen(self.SRC, thir=True)
        assert "it.n" in cpp
        assert "(*it).n" not in cpp


class TestFrameStaleViewDecl:
    """A generator frame local annotated owned but RESOLVED view (`label:
    str = sv` where the frame field is string_view) peels the stale
    view->owned coerce at the leaf decl like the sync arm -- materializing
    would bind the view field to a temporary dying at end of statement.
    Regression pin for the merged str-param widening x stale-view
    composition (view_lifetime/annotated_local_view_source divergence)."""

    SRC = ("from typing import Iterator\nfrom tpy import StrView\n\n"
           "def gen_frame(sv: StrView) -> Iterator[int]:\n"
           "    label: str = sv\n"
           "    print(label)\n"
           "    yield len(label)\n")

    def test_stale_view_frame_decl_renders_bare(self):
        witnesses, fallback = _assert_identical(self.SRC)
        assert "res.body" in witnesses  # the generator routed resumable
        _, _hpp, cpp = _gen(self.SRC, thir=True)
        assert "label = sv;" in cpp
        assert "std::string(sv)" not in cpp


class TestFrameFieldShadowingTupleUnpack:
    """The shadow mask also covers tuple-unpack for-each targets
    (shadow_names includes the unpack targets), but no currently-admitted
    resumable shape reaches that branch: a tuple-elem iterable rejects at
    the leaf for-each gate first. Pin the clean fallback (named tag,
    byte-identical); when a widening admits the shape, flip this to a
    bare-reads assertion like TestFrameFieldShadowing's."""

    SRC = ("import asyncio\nfrom tpy import Int32\n\n"
           "class Container:\n    pairs: list[tuple[Int32, Int32]]\n"
           "    def __init__(self) -> None:\n        self.pairs = []\n"
           "    async def total(self) -> Int32:\n"
           "        s: Int32 = 0\n"
           "        await asyncio.sleep(0)\n"
           "        for k, v in self.pairs:\n"
           "            s += k + v\n"
           "        return s\n")

    def test_tuple_unpack_leaf_routes(self):
        # The field-iterable unpack head landed (the long-tail ref-target
        # cell), so the resumable body now routes -- the flip this pin's
        # docstring predicted.
        witnesses, fallback = _assert_identical(self.SRC)
        assert "res.body" in witnesses
        assert not fallback


class TestQualcallRecordDiscardStorage:
    """The qualcall record-result rows added for the create_task cluster:
    a DISCARDED record-family result renders the bare call statement
    (`asyncio.create_task(...);`), a record RVALUE at a storage sink lands
    bare in the frame-slot emplace (Task[bytes] -- non-F1), and a
    module-qualified ASYNC factory nested in the adapter position lowers
    through the marker lane (`create_task(asyncio.wait_for(...))`)."""

    _PRE = ("import asyncio\n"
            "from tpy import Int32\n\n"
            "async def sub() -> bytes:\n"
            '    return b"x"\n\n')

    def test_discarded_create_task_routes(self):
        src = (self._PRE
               + "async def main_coro() -> None:\n"
               + "    asyncio.create_task(sub())\n"
               + "    await asyncio.sleep(0.001)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("method.qualcall.record_discard", 0) >= 1
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_nonf1_record_storage_decl_routes(self):
        # Task[bytes] fails _f1_record (reference-type targ); the storage
        # row admits the rvalue call whole into the emplace.
        src = (self._PRE
               + "async def main_coro() -> None:\n"
               + "    t = asyncio.create_task(sub())\n"
               + "    r = await t\n"
               + "    print(len(r))\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("method.qualcall.record_storage", 0) >= 1
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_nested_marker_async_factory_routes(self):
        # The inner wait_for is an ASYNC module function: the coro_factory
        # lift admits it only inside the Own[@dynamic] adapter position.
        src = (self._PRE
               + "async def main_coro() -> None:\n"
               + "    t = asyncio.create_task(asyncio.wait_for(sub(), 5.0))\n"
               + "    r = await t\n"
               + "    print(len(r))\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("call.coro_factory_adapter", 0) >= 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert ("::tpy::make_adapter<::tpystd::coro::Cancellable<"
                "std::vector<uint8_t>>>(::tpystd::asyncio::wait_for<"
                in cpp)

    def test_unawaited_async_factory_decl_routes_erased(self):
        # An async factory bound at a decl slot IS the erased-handle
        # position: the dedicated decl arm renders the own-arg wrap
        # (`c = ::tpy::make_adapter<...>(wait_for(...));`), never a bare
        # position-blind assign (which would silently drop the erasure).
        src = (self._PRE
               + "async def main_coro() -> None:\n"
               + "    c = asyncio.wait_for(sub(), 5.0)\n"
               + "    r = await c\n"
               + "    print(len(r))\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        assert witnesses.get("res.erased_handle_write", 0) >= 1
        _, _hpp, cpp = _gen(src, thir=True)
        assert "c = ::tpy::make_adapter<" in cpp

    def test_set_result_none_unit_arg_routes(self):
        # `fut.set_result(None)` on Future[None]: the substituted T=None
        # param takes the bare `std::monostate{}` (the none-unit row in the
        # record-method arg ladder).
        src = ("import asyncio\n"
               + "from asyncio import Future\n\n"
               + "async def producer(fut: Future[None]) -> None:\n"
               + "    fut.set_result(None)\n\n"
               + "async def main_coro() -> None:\n"
               + "    fut: Future[None] = Future[None]()\n"
               + "    asyncio.create_task(producer(fut))\n"
               + "    await fut\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "set_result(std::monostate{})" in cpp


class TestResForHeadIterableUse:
    """The res-lane for-head iterable lowers under the ITERABLE result use
    (mirroring the sync for-head): a sub-generator FACTORY call delegates
    through the `__for_src` frame field; the generic-factory spelling stays
    unprobed and falls back."""

    def test_subgenerator_factory_iterable_routes(self):
        src = ("from typing import Iterator\n"
               "from tpy import Int32\n\n"
               "def src() -> Iterator[Int32]:\n"
               "    yield 1\n    yield 2\n\n"
               "def gen() -> Iterator[Int32]:\n"
               "    yield 0\n"
               "    for x in src():\n"
               "        yield x\n"
               "    yield 9\n\n"
               "def main() -> None:\n    pass\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_container_call_iterable_routes(self):
        # A container-returning call iterable in a resumable for-head:
        # newly reachable under ITERABLE use; the skeleton captures the
        # bare call render.
        src = ("from typing import Iterator\n"
               "from tpy import Int32, Own\n\n"
               "def make_list() -> Own[list[Int32]]:\n"
               "    return [1, 2, 3]\n\n"
               "def gen() -> Iterator[Int32]:\n"
               "    yield 0\n"
               "    for x in make_list():\n"
               "        yield x\n\n"
               "def main() -> None:\n    pass\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_generic_factory_iterable_routes(self):
        # CONVERTED: the GENERIC generator factory in the
        # resumable for-head routes -- the explicit-targ spelling with the
        # ref-slot literal temps flushed at the setup statement
        # (`__for_src_N.emplace(pair<int32_t>(__tmp_1, __tmp_2));`).
        from .testutil import _assert_routes_byte_identical
        src = ("from typing import Iterator\n"
               "from tpy import Int32\n\n"
               "def pair[T](a: T, b: T) -> Iterator[T]:\n"
               "    yield a\n    yield b\n\n"
               "def gen() -> Iterator[Int32]:\n"
               "    yield 0\n"
               "    for x in pair(7, 8):\n"
               "        yield x\n\n"
               "def main() -> None:\n"
               "    for v in gen():\n        print(v)\n"
               "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "pair<int32_t>(__tmp_1, __tmp_2)" in _hpp + cpp


class TestResMatchValueHoists:
    """Res-match hook mode admits VALUE-kind hoists (a resumable's locals
    are frame fields, so the hoist is a no-op decl) and ASSIGN-mode
    whole-subject bindings (`v = __match_subject_N;` -- the frame-field
    write both paths emit); the copy/ref bind modes (arm-block locals)
    keep falling back."""

    def test_hoisted_capture_with_guard_routes(self):
        src = ("from typing import Iterator\n\n"
               "def gen(items: list[int]) -> Iterator[int]:\n"
               "    for it in items:\n"
               "        match it:\n"
               "            case 0:\n"
               "                break\n"
               "            case v if v > 10:\n"
               "                yield v\n"
               "                yield v + 100\n"
               "            case v:\n"
               "                yield v\n"
               "    yield -1\n\n"
               "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("match.hoist_value_frame", 0) >= 1
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_single_arm_capture_routes_frame_assign(self):
        # A single-arm capture is not sema-hoisted (copy/ref mode), but the
        # name is a frame field -- the mode re-keys to the plain assign
        # (_hook_mode_binding) and the dispatch routes.
        src = ("from typing import Iterator\n\n"
               "def gen(items: list[int]) -> Iterator[int]:\n"
               "    for it in items:\n"
               "        match it:\n"
               "            case 0:\n"
               "                yield 100\n"
               "            case v:\n"
               "                yield v\n\n"
               "def main() -> None:\n    pass\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "v = __match_subject_1;" in cpp


class TestMatchDispatchTierBoundaries:
    """Edges of the record / union-capture / optional hook-mode admissions
    (each dualgen-probed when the rows landed): the or-bind arm, guarded
    tiers, and the pointer-repr optional partition keep rejecting; the
    record chain and the O2 value dispatch (incl. its multi-arm literal
    inner chain) route with frame-keyed captures."""

    def test_record_capture_dispatch_routes(self):
        # The if_elif_record dispatch: a field capture assigns its frame
        # field before the arm hook (`v = __match_subject_N.lives;`).
        src = ("from typing import Iterator\n\n"
               "class Cat:\n"
               "    lives: int\n"
               "    def __init__(self, lives: int) -> None:\n"
               "        self.lives = lives\n\n"
               "def counts(a: Cat) -> Iterator[int]:\n"
               "    match a:\n"
               "        case Cat(lives=v):\n"
               "            yield v\n"
               "            yield v + 1\n\n"
               "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("match.if_elif_record", 0) >= 1
        assert witnesses.get("res.match_dispatch", 0) >= 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "v = __match_subject_1.lives;" in cpp

    def test_optional_value_multi_arm_inner_routes(self):
        # The O2 value dispatch under hooks: None arm + literal inner arm +
        # capture arm, the inner `==` chain against `__match_inner_N`.
        src = ("from typing import Iterator, Optional\n"
               "from tpy import Int32\n\n"
               "def gen(x: Optional[Int32]) -> Iterator[Int32]:\n"
               "    match x:\n"
               "        case None:\n"
               "            yield -1\n"
               "        case 0:\n"
               "            yield 100\n"
               "        case v:\n"
               "            yield v\n"
               "            yield v * 2\n\n"
               "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("match.optional_value_dispatch", 0) >= 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "v = __match_inner_1;" in cpp

    def test_reused_slot_capture_emplaces_both(self):
        # REGRESSION (review round): a frame_slot capture name reused
        # across TWO dispatches -- the second dispatch computes "assign"
        # (the name entered `declared` at the first dispatch's site), but
        # frame_slot has no operator=, so the frame_slots re-key must win
        # for EVERY incoming mode and both binds emplace.
        src = ("from typing import Iterator\n\n"
               "class Dog:\n"
               "    def sound(self) -> str:\n"
               '        return "woof"\n\n'
               "class Cat:\n"
               "    def sound(self) -> str:\n"
               '        return "meow"\n\n'
               "def gen(a: Dog | Cat, b: Dog | Cat) -> Iterator[str]:\n"
               "    match a:\n"
               "        case Cat() as c:\n"
               "            yield c.sound()\n"
               "        case Dog():\n"
               '            yield "dog-a"\n'
               "    match b:\n"
               "        case Cat() as c:\n"
               "            yield c.sound()\n"
               "        case Dog():\n"
               '            yield "dog-b"\n\n'
               "def main() -> None:\n    pass\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert cpp.count("c.emplace(") == 2
        assert "c = std::get" not in cpp

    def test_or_bind_arm_defers(self):
        # The or-bind arm re-walks one body per alternative; re-walking an
        # arm's BB chain would re-split its resume cases -- rejected.
        src = ("from typing import Iterator\n\n"
               "class A:\n"
               "    x: int\n"
               "    def __init__(self, x: int) -> None:\n"
               "        self.x = x\n\n"
               "class B:\n"
               "    x: int\n"
               "    def __init__(self, x: int) -> None:\n"
               "        self.x = x\n\n"
               "def gen(u: A | B) -> Iterator[int]:\n"
               "    match u:\n"
               "        case A(x=n) | B(x=n):\n"
               "            yield 1\n"
               "            yield n\n\n"
               "def main() -> None:\n    pass\nmain()\n")
        _assert_identical(src)
        assert _res_fallback(src).get("res.match_strategy") == 1

    def test_guarded_record_dispatch_defers(self):
        # guarded_record stays outside the hook-admitted kinds.
        src = ("from typing import Iterator\n\n"
               "class Cat:\n"
               "    lives: int\n"
               "    def __init__(self, lives: int) -> None:\n"
               "        self.lives = lives\n\n"
               "def gen(a: Cat) -> Iterator[int]:\n"
               "    match a:\n"
               "        case Cat(lives=v) if v > 3:\n"
               "            yield v\n"
               "            yield v + 1\n"
               "        case _:\n"
               "            yield 0\n\n"
               "def main() -> None:\n    pass\nmain()\n")
        _assert_identical(src)
        assert _res_fallback(src).get("res.match_strategy") == 1

    def test_pointer_repr_optional_dispatch_defers(self):
        # Only the value-repr optional dispatch is hook-admitted; the
        # pointer-repr partition rejects upstream, byte-identically.
        src = ("from typing import Iterator, Optional\n\n"
               "class Rec:\n"
               "    n: int\n"
               "    def __init__(self, n: int) -> None:\n"
               "        self.n = n\n\n"
               "def gen(x: Optional[Rec]) -> Iterator[int]:\n"
               "    match x:\n"
               "        case None:\n"
               "            yield -1\n"
               "        case r:\n"
               "            yield 1\n"
               "            yield r.n\n\n"
               "def main() -> None:\n    pass\nmain()\n")
        _assert_identical(src)
        assert sum(_res_fallback(src).values()) >= 1


class TestMemberCoroFactoryArg:
    """A MEMBER async-method factory into the Own[@dynamic P] adapter slot
    (`asyncio.run(b.take())`): the method call spells inline inside
    make_adapter, its receiver riding the ordinary method-call arm."""

    _PRE = ("import asyncio\n"
            "from tpy import Int32\n\n"
            "class Box:\n"
            "    v: Int32\n"
            "    def __init__(self, v: Int32) -> None:\n"
            "        self.v = v\n"
            "    async def take(self) -> Int32:\n"
            "        await asyncio.sleep(0.001)\n"
            "        return self.v\n\n")

    def test_member_factory_routes(self):
        src = (self._PRE
               + "def main() -> None:\n"
               + "    b = Box(7)\n"
               + "    print(asyncio.run(b.take()))\n\n"
               + "main()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not fallback
        _, _hpp, cpp = _gen(src, thir=True)
        assert ("::tpystd::asyncio::run<int32_t>(::tpy::make_adapter<"
                "::tpystd::coro::Cancellable<int32_t>>(b.take()))" in cpp)

    def test_awaitable_record_factory_routes(self):
        # A NON-async method returning a concrete awaitable record
        # (`loop.sock_recv(...)` -> _SockRecv) into wait_for's adapter
        # slot takes the method-rvalue CONFORMER face (make_adapter over
        # the record prvalue).
        src = ("import asyncio\n"
               "from socket import socketpair\n\n"
               "async def main_coro() -> None:\n"
               "    loop = asyncio.get_running_loop()\n"
               "    a, b = socketpair()\n"
               "    b.setblocking(False)\n"
               "    data = await asyncio.wait_for(loop.sock_recv(b, 16), 0.5)\n"
               "    print(len(data))\n\n"
               "def main() -> None:\n    pass\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not fallback, fallback


class TestResForHeadDictViewIterable:
    """Review-probe pin (thir-wave-next5): the ITERABLE-use change also
    admits dict-view iterables inside a resumable for-head
    (`for v in d.values():` in a generator) -- byte-identical."""

    def test_dict_view_iterable_routes(self):
        src = ("from typing import Iterator\n"
               "from tpy import Int32\n\n"
               "def gen(d: dict[str, Int32]) -> Iterator[Int32]:\n"
               "    yield 0\n"
               "    for v in d.values():\n"
               "        yield v\n"
               "    for k in d.keys():\n"
               "        yield len(k)\n\n"
               "def main() -> None:\n    pass\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)


class TestHoistedLoopVarOptStorage:
    """The hoisted loop-var OPTIONAL_STORAGE flavor at the for-head: the
    shared if/try/with classifier threaded to the foreach site. Sync
    predecls `std::optional<T> name;` + per-iteration binds + post-loop
    deref reads; the RESUMABLE rung opens for leaf-mode non-frame-field
    names only (the loop and post-use share one case block)."""

    def test_resumable_leaf_local_routes(self):
        src = (_PRE
               + "import asyncio\n\n"
               + "class Item:\n"
               + "    n: Int32\n"
               + "    def __init__(self, n: Int32) -> None:\n"
               + "        self.n = n\n\n"
               + "async def scan() -> Int32:\n"
               + "    await asyncio.sleep(0)\n"
               + "    items = [Item(1), Item(2)]\n"
               + "    for it in items:\n"
               + "        pass\n"
               + "    return it.n\n\n"
               + "def main() -> None:\n"
               + "    print(asyncio.run(scan()))\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "std::optional<Item> it;" in cpp

    def test_in_loop_await_hoist_also_routes(self):
        # An await INSIDE the loop body still leaves the hoisted var a
        # leaf-local (routes byte-identically) -- a frame_slots exclusion
        # on this rung was ablated as DEAD; a genuine frame-field flavor
        # would surface in the corpus byte-diff.
        src = (_PRE
               + "import asyncio\n\n"
               + "class Item:\n"
               + "    n: Int32\n"
               + "    def __init__(self, n: Int32) -> None:\n"
               + "        self.n = n\n\n"
               + "async def scan() -> Int32:\n"
               + "    items = [Item(1), Item(2)]\n"
               + "    for it in items:\n"
               + "        await asyncio.sleep(0)\n"
               + "    return it.n\n\n"
               + "def main() -> None:\n"
               + "    print(asyncio.run(scan()))\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_suspension_crossing_hoist_also_routes(self):
        # The suspension-crossing flavor routes byte-identically too (the
        # skeleton's frame planning keeps `it` out of lc.frame_slots for
        # this shape); the classifier's frame_slots exclusion stays as the
        # defensive fence for shapes where it does not.
        src = (_PRE
               + "import asyncio\n\n"
               + "class Item:\n"
               + "    n: Int32\n"
               + "    def __init__(self, n: Int32) -> None:\n"
               + "        self.n = n\n\n"
               + "async def scan() -> Int32:\n"
               + "    items = [Item(1), Item(2)]\n"
               + "    for it in items:\n"
               + "        pass\n"
               + "    await asyncio.sleep(0)\n"
               + "    return it.n\n\n"
               + "def main() -> None:\n"
               + "    print(asyncio.run(scan()))\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)


class TestOptionalReturnCoroFamily:
    """The optional-return coro slots (designed-queue item 6 Wave 2): the
    STORAGE `Own[Box] | None` slot (ctor rvalue rides the tail, None spells
    nullopt) and the BORROW `Box | None` slot (the field optional_to_ptr
    lift only -- name sources keep the return.borrow_form fence). Plus the
    `std::optional<Record>` VALUE frame local (ValueOptKind.RECORD)."""

    _SRC = (_PRE
            + "import asyncio\n"
            + "from tpy import Own\n\n"
            + "class Box:\n"
            + "    def __init__(self, v: int) -> None:\n"
            + "        self.v = v\n\n"
            + "class H:\n"
            + "    opt: Box | None\n"
            + "    def __init__(self, b: Box | None) -> None:\n"
            + "        self.opt = b\n\n"
            + "async def get(h: H) -> Box | None:\n"
            + "    await asyncio.sleep(0)\n"
            + "    return h.opt\n\n"
            + "async def make(p: bool) -> Own[Box] | None:\n"
            + "    await asyncio.sleep(0)\n"
            + "    if p:\n"
            + "        return Box(3)\n"
            + "    return None\n\n"
            + "async def drive() -> None:\n"
            + "    h = H(Box(1))\n"
            + "    t = await get(h)\n"
            + "    print(\"got\" if t is not None else \"none\")\n"
            + "    owned = await make(True)\n"
            + "    print(owned.v if owned is not None else -1)\n\n"
            + "def main() -> None:\n"
            + "    asyncio.run(drive())\nmain()\n")

    def test_both_slots_and_frame_local_route(self):
        witnesses, fallback = _assert_identical(self._SRC)
        assert not any(k.startswith("resumable:") for k in fallback)
        assert witnesses.get("res.return_opt_record_none")
        assert witnesses.get("res.return_ptr_opt_field")
        _, _hpp, cpp = _gen(self._SRC, thir=True)
        assert "::tpy::optional_to_ptr(h.opt)" in cpp
        assert "= std::nullopt;" in cpp

    def test_borrow_name_source_still_defers(self):
        # BOUNDARY: a NAME source at the BORROW slot keeps the
        # return.borrow_form fence -- only the field lift is admitted.
        src = self._SRC.replace(
            "    return h.opt\n",
            "    t = h.opt\n"
            "    return t\n")
        witnesses, fallback = _assert_identical(src)
        assert any("return.borrow_form" in k for k in fallback)


class TestXmodCtorAsyncSinks:
    """Module-qualified ctor rvalues at the resumable with-manager and
    async-for source sinks (`async with svc.Gate()` / `async for v in
    svc.Ticker(3)`): the qualified spelling rides the marker ctor arm; the
    owned `__with_ctx_N` / `__for_itr` captures consume the rvalue whole
    (ctx_manager / ITERABLE use at the setup lowerings)."""

    def _fixture(self, tmp_path, main_src: str, extra_helper: str = ""):
        from ..compiler import Compiler
        from .testutil import _STDLIB_DIRS
        (tmp_path / "helper.py").write_text(
            "import asyncio\n"
            "from tpy import Int32\n"
            "class Gate:\n"
            "    async def __aenter__(self) -> Int32:\n"
            "        await asyncio.sleep(0)\n"
            "        return 5\n"
            "    async def __aexit__(self, et: None, ev: None,"
            " tb: None) -> None:\n"
            "        await asyncio.sleep(0)\n"
            + extra_helper)
        (tmp_path / "main.py").write_text(main_src)
        compiler = Compiler(tmp_path / "main.py", lib_dirs=_STDLIB_DIRS)
        modules = compiler.compile()
        entry = next(m for m in modules if m.is_entry_point)
        outs = {}
        for thir in (False, True):
            c2 = Compiler(tmp_path / "main.py", lib_dirs=_STDLIB_DIRS)
            mods2 = c2.compile()
            e2 = next(m for m in mods2 if m.is_entry_point)
            outs[thir] = c2.generate_code_to_strings(
                e2, options=CodeGenOptions(emit_source_comments=False,
                                           thir_codegen=thir))
            if thir:
                fb = c2._thir_fallback
        return outs, fb

    def test_qualified_ctor_manager_routes(self, tmp_path):
        outs, fb = self._fixture(tmp_path, (
            "import asyncio\n"
            "import helper\n"
            "async def go() -> None:\n"
            "    async with helper.Gate() as v:\n"
            "        print(v)\n"
            "def main() -> None:\n"
            "    asyncio.run(go())\nmain()\n"))
        assert outs[False] == outs[True]
        assert not any(k.startswith("resumable:") for k in fb)

    def test_qualified_factory_call_manager_still_defers(self, tmp_path):
        # BOUNDARY: a module-qualified NON-ctor factory call manager
        # (`helper.make_gate()`) is outside `_module_qual_ctor_shape`
        # (is_constructor only) and keeps the res.with_manager fence.
        # (A NAME-bound manager is NOT a fence: it rides the pre-existing
        # borrowed F1-lvalue slice.)
        outs, fb = self._fixture(tmp_path, (
            "import asyncio\n"
            "import helper\n"
            "async def go() -> None:\n"
            "    async with helper.make_gate() as v:\n"
            "        print(v)\n"
            "def main() -> None:\n"
            "    asyncio.run(go())\nmain()\n"), extra_helper=(
            "from tpy import Own\n"
            "def make_gate() -> Own[Gate]:\n"
            "    return Gate()\n"))
        assert outs[False] == outs[True]
        assert any(k.startswith("resumable:") for k in fb)


class TestPtrValueLocalAndCallNoneTest:
    """A raw `Ptr[T]` VALUE local in a resumable frame (bare `T*` field:
    plain frame assign, `== nullptr` None-test, bare arg pass) and the
    Ptr-returning CALL rvalue `is None` subject (the name row's rvalue
    sibling) -- sync and resumable alike."""

    _SRC = (_PRE
            + "import asyncio\n"
            + "from tpy import Ptr\n\n"
            + "class Cell:\n"
            + "    def __init__(self, v: Int32) -> None:\n"
            + "        self.v = v\n\n"
            + "_slot: Ptr[Cell] = None\n\n"
            + "def get_cell() -> Ptr[Cell]:\n"
            + "    return _slot\n\n"
            + "async def coro_probe() -> None:\n"
            + "    await asyncio.sleep(0)\n"
            + "    p = get_cell()\n"
            + "    print(p is None)\n"
            + "    if p is not None:\n"
            + "        print(p.v)\n\n"
            + "def sync_probe() -> None:\n"
            + "    print(get_cell() is None)\n\n"
            + "def main() -> None:\n"
            + "    sync_probe()\n"
            + "    asyncio.run(coro_probe())\nmain()\n")

    def test_ptr_local_and_call_subject_route(self):
        witnesses, fallback = _assert_identical(src := self._SRC)
        assert not any(k.startswith("resumable:") for k in fallback)
        assert not any(k.startswith("body:") for k in fallback)
        _, hpp, cpp = _gen(src, thir=True)
        joined = hpp + cpp
        assert "(get_cell() == nullptr)" in joined
        assert "(p == nullptr)" in joined

    def test_non_ptr_call_subject_still_defers(self):
        # BOUNDARY: a record-returning call is not a None-testable subject
        # in TPy (sema rejects) -- instead pin the adjacent still-gated
        # shape: an Optional[record]-returning call subject in a SYNC body
        # keeps its own row's verdict (value_opt rvalues only; a ptr-repr
        # Optional call subject stays rejected).
        src = (_PRE
               + "class Rec:\n"
               + "    def __init__(self, v: Int32) -> None:\n"
               + "        self.v = v\n\n"
               + "_r: Rec | None = None\n\n"
               + "def find() -> Rec | None:\n"
               + "    return _r\n\n"
               + "def main() -> None:\n"
               + "    print(find() is None)\nmain()\n")
        compiler, _hpp, _cpp = _gen(src, thir=True)
        assert any(k.startswith("body:") or k.startswith("top_level:")
                   for k in compiler._thir_fallback)


class TestVoidReturnNone:
    """`return None` at a VOID async slot is skeleton-only (the AST keys
    POLL_VOID_READY_RETURN on VoidType, never rendering the value); an
    OPTIONAL slot still lowers its value (std::nullopt)."""

    def test_void_slot_return_none_routes(self):
        src = (_PRE
               + "import asyncio\n\n"
               + "async def void_ret() -> None:\n"
               + "    return None\n\n"
               + "def main() -> None:\n"
               + "    asyncio.run(void_ret())\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert ("::tpystd::tpy::Poll<::std::monostate>::ready("
                "::std::monostate{})") in cpp

    def test_optional_slot_return_none_still_lowers_value(self):
        # BOUNDARY: `-> Int32 | None` renders the value (nullopt), so the
        # skip must key on VoidType, not on the None literal.
        src = (_PRE
               + "import asyncio\n\n"
               + "async def opt_ret(flag: bool) -> Int32 | None:\n"
               + "    await asyncio.sleep(0)\n"
               + "    if flag:\n"
               + "        return 7\n"
               + "    return None\n\n"
               + "def main() -> None:\n"
               + "    print(asyncio.run(opt_ret(False)))\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        assert witnesses.get("res.return_value")


class TestForNarrowedOptionalIterable:
    """A narrowed value-Optional iterable (`str|None` proven non-None) at a
    resumable begin_end for-head: the skeleton owns the unwrap
    (`_maybe_unwrap_narrowed_optional` over the leaf render), so the leaf
    supplies the BARE name -- a deref'd leaf would double-deref."""

    _SRC = (_PRE
            + "import asyncio\n\n"
            + "async def count_s(s: str | None) -> int:\n"
            + "    if s is None:\n"
            + "        return -1\n"
            + "    n = 0\n"
            + "    for _c in s:\n"
            + "        await asyncio.sleep(0)\n"
            + "        n += 1\n"
            + "    return n\n\n"
            + "def main() -> None:\n"
            + "    print(asyncio.run(count_s(\"abc\")))\nmain()\n")

    def test_narrowed_value_opt_iterable_routes_bare(self):
        witnesses, fallback = _assert_identical(self._SRC)
        assert witnesses.get("res.for_narrowed_opt_src")
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(self._SRC, thir=True)
        assert "((*s)).begin()" in cpp
        assert "(*(*s))" not in cpp

    def test_ptr_repr_optional_container_param_still_defers(self):
        # BOUNDARY: the pointer-repr Optional[container] sibling stays fenced
        # UPSTREAM (the frame-param gate) -- it never reaches the for-head
        # strip, whose contract is checked only for the value-opt families.
        src = (_PRE
               + "import asyncio\n\n"
               + "async def sum_l(xs: list[Int32] | None) -> Int32:\n"
               + "    if xs is None:\n"
               + "        return -1\n"
               + "    t = 0\n"
               + "    for v in xs:\n"
               + "        await asyncio.sleep(0)\n"
               + "        t += v\n"
               + "    return t\n\n"
               + "def main() -> None:\n"
               + "    print(asyncio.run(sum_l([1, 2, 3])))\nmain()\n")
        _assert_identical(src)
        fb = _res_fallback(src)
        assert fb.get("res.param_type")


class TestAwaitOwnValueArgSlots:
    """The `Own[value]` await-arg slot (a generic Own[T] param at a value
    instantiation): the emplace arg rides the sync `_lower_call_arg` rows --
    the copy-temp+move for a live name, the temp-free move at last use, bare
    for a literal. The emplace is a statement position, so the arg temp
    flushes before the suspend line (`temp_args=True` at the INLINE loop)."""

    _SINK = (_PRE
             + "import asyncio\n"
             + "from tpy import Own\n\n"
             + "async def sink(x: Own[Int32]) -> None:\n"
             + "    await asyncio.sleep(0)\n"
             + "    print(x)\n\n")

    def test_nonlast_name_hoists_copy_temp(self):
        src = (self._SINK
               + "async def go() -> None:\n"
               + "    i = 1\n"
               + "    await sink(i)\n"
               + "    print(i)\n\n"
               + "def main() -> None:\n    asyncio.run(go())\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("argtemp.own_copy")
        # The DRIVER must route; the only tolerated fallback is the sink
        # callee's own Own[Int32] frame param (res.param_type -- a body this
        # cell does not touch). An exact-set pin: any driver fallback adds a
        # different key and fails.
        assert set(fallback) <= {"resumable:res.param_type"}
        _, _hpp, cpp = _gen(src, thir=True)
        assert "auto __tmp_1 = i;" in cpp
        assert "std::move(__tmp_1)" in cpp

    def test_last_use_name_moves_without_temp(self):
        src = (self._SINK
               + "async def go() -> None:\n"
               + "    j = 2\n"
               + "    await sink(j)\n\n"
               + "def main() -> None:\n    asyncio.run(go())\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        # Same tolerated-set pin as the copy-temp flavor above.
        assert set(fallback) <= {"resumable:res.param_type"}
        _, _hpp, cpp = _gen(src, thir=True)
        assert "__tmp_" not in cpp

    def test_own_str_slot_still_defers(self):
        # BOUNDARY: an Own[str] await slot is outside the value families --
        # the owned brace-init temp render is unwitnessed at this position.
        src = (_PRE
               + "import asyncio\n"
               + "from tpy import Own\n\n"
               + "async def sink_s(s: Own[str]) -> None:\n"
               + "    await asyncio.sleep(0)\n"
               + "    print(s)\n\n"
               + "async def go() -> None:\n"
               + "    label = \"hey\"\n"
               + "    await sink_s(label)\n"
               + "    print(label)\n\n"
               + "def main() -> None:\n    asyncio.run(go())\nmain()\n")
        _assert_identical(src)
        fb = _res_fallback(src)
        assert fb.get("res.await_param_type")


class TestAwaitArgDcbpConstWrap:
    _SRC = (_PRE
            + "from tpy import Own\n\n"
            + "class A:\n"
            + "    x: Int32\n"
            + "    def __init__(self) -> None:\n        self.x = 1\n\n"
            + "class B:\n"
            + "    y: Int32\n"
            + "    def __init__(self) -> None:\n        self.y = 2\n\n"
            + "class Holder[T]:\n"
            + "    v: T\n"
            + "    def __init__(self, v: Own[T]) -> None:\n        self.v = v\n\n"
            + "    async def show(self, u: A | B) -> Int32:\n"
            + "        if isinstance(u, A):\n            return u.x\n"
            + "        return u.y\n\n"
            + "class PlainHolder:\n"
            + "    v: Int32\n"
            + "    def __init__(self, v: Int32) -> None:\n        self.v = v\n\n"
            + "    async def show(self, u: A | B) -> Int32:\n"
            + "        if isinstance(u, A):\n            return u.x\n"
            + "        return u.y\n\n"
            + "async def go() -> Int32:\n"
            + "    h = Holder(Int32(5))\n"
            + "    a = A()\n"
            + "    p = PlainHolder(7)\n"
            + "    b = B()\n"
            + "    r1 = await h.show(a)\n"
            + "    r2 = await p.show(b)\n"
            + "    return r1 + r2\n\n"
            + "def main() -> None:\n    pass\nmain()\n")

    def test_generic_receiver_keeps_const_wrap(self):
        # The deep-const verdict lives on the RAW fi; a generic receiver's
        # substituted fi reads None and used to drop the const wrap at the
        # sub-coro emplace arg (both paths -- the AST read the substituted
        # fi, the THIR await-arg lowering never consulted the verdict).
        witnesses, fallback = _assert_identical(self._SRC)
        assert witnesses.get("res.await_args", 0) >= 2
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(self._SRC, thir=True)
        assert cpp.count("std::variant<const A*, const B*>{") >= 2
        assert "std::variant<A*, B*>{" not in cpp

    def test_mutating_method_stays_nonconst(self):
        # Inverse: a self-mutating method is not readonly, so its union
        # param carries no deep-const verdict -- the emplace arg must keep
        # the mutable ptr-variant spelling.
        src = (_PRE
               + "class A:\n"
               + "    x: Int32\n"
               + "    def __init__(self) -> None:\n        self.x = 1\n\n"
               + "class B:\n"
               + "    y: Int32\n"
               + "    def __init__(self) -> None:\n        self.y = 2\n\n"
               + "class Counter:\n"
               + "    n: Int32\n"
               + "    def __init__(self) -> None:\n        self.n = 0\n\n"
               + "    async def poke(self, u: A | B) -> Int32:\n"
               + "        self.n = self.n + 1\n"
               + "        if isinstance(u, A):\n            return u.x\n"
               + "        return u.y\n\n"
               + "async def go() -> Int32:\n"
               + "    c = Counter()\n"
               + "    a = A()\n"
               + "    return await c.poke(a)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _assert_identical(src)
        _, _hpp, cpp = _gen(src, thir=True)
        assert "std::variant<A*, B*>{" in cpp
        assert "std::variant<const A*, const B*>{" not in cpp

    _TUPLE_PRE = (_PRE
                  + "from tpy import Own\n\n"
                  + "class A:\n"
                  + "    x: Int32\n"
                  + "    def __init__(self) -> None:\n        self.x = 1\n\n"
                  + "class Keeper:\n"
                  + "    pair: tuple[A, A]\n"
                  + "    def __init__(self, a: Own[A], b: Own[A]) -> None:\n"
                  + "        self.pair = (a, b)\n\n")

    def test_tuple_param_factory_and_arg_agree_const(self):
        # An inferred deep-const tuple param (no yield escape): the coro
        # factory/frame spelling and the emplace arg BOTH carry the const
        # slots -- the factory consults the verdict like the union arm, the
        # arg threads target_const_borrow like the sync call site.
        src = (self._TUPLE_PRE
               + "    async def total(self, p: tuple[A, A]) -> Int32:\n"
               + "        return p[0].x + p[1].x\n\n"
               + "async def go() -> Int32:\n"
               + "    k = Keeper(A(), A())\n"
               + "    return await k.total(k.pair)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _assert_identical(src)
        _, hpp, cpp = _gen(src, thir=True)
        both = hpp + cpp
        assert "std::tuple<const A*, const A*>" in both
        assert "tuple_to_pointer<std::tuple<const A*, const A*>>" in both
        assert "std::tuple<A*, A*>" not in both

    def test_free_fn_tuple_param_agrees_const(self):
        # Free async defs read the same verdict off their registry fi.
        src = (self._TUPLE_PRE
               + "async def total(p: tuple[A, A]) -> Int32:\n"
               + "    return p[0].x + p[1].x\n\n"
               + "async def go() -> Int32:\n"
               + "    k = Keeper(A(), A())\n"
               + "    return await total(k.pair)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _assert_identical(src)
        _, hpp, cpp = _gen(src, thir=True)
        both = hpp + cpp
        assert "std::tuple<const A*, const A*>" in both
        assert "std::tuple<A*, A*>" not in both

    def test_generator_tuple_param_agrees_const(self):
        # The __gen_ frame shares _classify_params: a generator method's
        # inferred deep-const tuple param spells const like its sync call
        # site (which threads target_const_borrow) -- the two must agree.
        src = (self._TUPLE_PRE.replace(
                   "from tpy import Own",
                   "from typing import Iterator\nfrom tpy import Own")
               + "    def vals(self, p: tuple[A, A]) -> Iterator[Int32]:\n"
               + "        yield p[0].x\n"
               + "        yield p[1].x\n\n"
               + "def main() -> None:\n"
               + "    k = Keeper(A(), A())\n"
               + "    for v in k.vals(k.pair):\n"
               + "        print(v)\n\nmain()\n")
        _assert_identical(src)
        _, hpp, cpp = _gen(src, thir=True)
        both = hpp + cpp
        assert "std::tuple<const A*, const A*>" in both
        assert "std::tuple<A*, A*>" not in both

    def test_yield_escaping_tuple_param_stays_mutable(self):
        # BOUNDARY: a generator that yields its tuple param hands out
        # mutable element pointers -- the verdict excludes it (the yield
        # branch's borrow-tuple escape marking), so factory, call-site
        # lift, and frame all stay the MUTABLE spelling in agreement.
        src = (self._TUPLE_PRE.replace(
                   "from tpy import Own",
                   "from typing import Iterator\nfrom tpy import Own")
               + "def relay(p: tuple[A, A]) -> Iterator[tuple[A, A]]:\n"
               + "    yield p\n\n"
               + "def main() -> None:\n"
               + "    k = Keeper(A(), A())\n"
               + "    for pair in relay(k.pair):\n"
               + "        print(pair[0].x)\n\nmain()\n")
        _assert_identical(src)
        _, hpp, cpp = _gen(src, thir=True)
        both = hpp + cpp
        assert "std::tuple<A*, A*>" in both
        assert "std::tuple<const A*, const A*>" not in both

    def test_ternary_alias_yield_stays_mutable(self):
        # A ternary-bound alias of the tuple param yielded by name: the
        # bind-time source recording reaches p through the local, so the
        # verdict stays mutable everywhere.
        src = (self._TUPLE_PRE.replace(
                   "from tpy import Own",
                   "from typing import Iterator\nfrom tpy import Own")
               + "def relay(p: tuple[A, A], q: tuple[A, A], cond: bool) -> Iterator[tuple[A, A]]:\n"
               + "    u = p if cond else q\n"
               + "    yield u\n\n"
               + "def main() -> None:\n"
               + "    k = Keeper(A(), A())\n"
               + "    k2 = Keeper(A(), A())\n"
               + "    for pair in relay(k.pair, k2.pair, True):\n"
               + "        print(pair[0].x)\n\nmain()\n")
        _assert_identical(src)
        _, hpp, cpp = _gen(src, thir=True)
        both = hpp + cpp
        assert "std::tuple<A*, A*>" in both
        assert "std::tuple<const A*, const A*>" not in both

    def test_branch_alias_yield_marks_both_params(self):
        # BOUNDARY (the flow-merge regression): an if/else binding the alias
        # to a DIFFERENT param per branch must mark BOTH -- the source roots
        # ride BindingProvenance's union-merge at the join, so neither param
        # may keep the const verdict.
        src = (self._TUPLE_PRE.replace(
                   "from tpy import Own",
                   "from typing import Iterator\nfrom tpy import Own")
               + "def relay(t1: tuple[A, A], t2: tuple[A, A], cond: bool) -> Iterator[tuple[A, A]]:\n"
               + "    if cond:\n"
               + "        u = t1\n"
               + "    else:\n"
               + "        u = t2\n"
               + "    yield u\n\n"
               + "def main() -> None:\n"
               + "    k = Keeper(A(), A())\n"
               + "    k2 = Keeper(A(), A())\n"
               + "    for pair in relay(k.pair, k2.pair, True):\n"
               + "        print(pair[0].x)\n\nmain()\n")
        _assert_identical(src)
        _, hpp, cpp = _gen(src, thir=True)
        both = hpp + cpp
        assert "std::tuple<A*, A*>" in both
        assert "std::tuple<const A*, const A*>" not in both

    def test_method_self_yield_drops_readonly(self):
        # A method generator yielding a self-sourced borrow tuple hands the
        # consumer a writable path into self -- the method must NOT infer
        # readonly (a const receiver could not source the mutable slot).
        src = (self._TUPLE_PRE.replace(
                   "from tpy import Own",
                   "from typing import Iterator\nfrom tpy import Own")
               + "    def items(self) -> Iterator[tuple[A, A]]:\n"
               + "        yield self.pair\n\n"
               + "def main() -> None:\n"
               + "    k = Keeper(A(), A())\n"
               + "    for pair in k.items():\n"
               + "        print(pair[0].x)\n\nmain()\n")
        _assert_identical(src)
        _, hpp, cpp = _gen(src, thir=True)
        assert "__gen_Keeper_items items();" in hpp + cpp
        assert "items() const" not in hpp + cpp

    def test_overloaded_generator_reads_impl_verdict(self):
        # The free-fn verdict lookup takes overloads[-1] (the implementation)
        # -- an @overload'd generator with an unmutated, non-escaping tuple
        # param renders the impl's const verdict at the factory.
        src = (self._TUPLE_PRE.replace(
                   "from tpy import Own",
                   "from typing import Iterator, overload\nfrom tpy import Own")
               + "@overload\n"
               + "def vals(p: tuple[A, A]) -> Iterator[Int32]: ...\n"
               + "@overload\n"
               + "def vals(p: tuple[A, A], n: Int32) -> Iterator[Int32]: ...\n"
               + "def vals(p: tuple[A, A], n: Int32 = 1) -> Iterator[Int32]:\n"
               + "    yield p[0].x * n\n\n"
               + "def main() -> None:\n"
               + "    k = Keeper(A(), A())\n"
               + "    for v in vals(k.pair):\n"
               + "        print(v)\n\nmain()\n")
        _assert_identical(src)
        _, hpp, cpp = _gen(src, thir=True)
        both = hpp + cpp
        # The factory reads the impl's verdict (overloads[-1]) -> const;
        # the sync call-site lift reads the resolved STUB's empty verdict
        # -> mutable, absorbed by std::tuple's converting ctor (the same
        # benign class as the tuple-literal render gap; see TODO.md).
        assert "std::tuple<const A*, const A*> p" in both
        assert "tuple_to_pointer<std::tuple<A*, A*>>" in cpp

    def test_verdict_const_tuple_element_alias_field_spelling(self):
        # TEXT-LEVEL pin for the alias-facing verdict sync: an element alias
        # of a verdict-const tuple param gets a `const A*` frame field (the
        # CFG-window seeding + the classifier's verdict-subscript rule).
        # Emission-only -- the shape's ASSIGNMENT still mis-renders as
        # address-of the element slot (the pre-existing &-wrap defect in
        # BUGS.md, g++-caught), so no runnable corpus case can guard this
        # until that fix lands; this pin keeps the const half from
        # regressing invisibly in the meantime.
        src = (self._TUPLE_PRE.replace(
                   "from tpy import Own",
                   "from typing import Iterator\nfrom tpy import Own")
               + "def show(p: tuple[A, A]) -> Iterator[Int32]:\n"
               + "    a = p[0]\n"
               + "    yield a.x\n"
               + "    yield a.x\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _, hpp, _cpp = _gen(src, thir=True)
        assert "std::tuple<const A*, const A*> p" in hpp
        assert "const A* a" in hpp


class TestResumableStrFieldSinks:
    # A str-family FIELD reads bare at both resumable str sinks -- the async
    # return's `__tpy_async_ret` decl and the yield expression -- the same
    # `_str_field_value_read` precheck the sync return tail threads. A slot
    # needing conversion arrives as a coerce instead, so these arms admit
    # only the no-conversion form.
    _BOX = (
        "from typing import Iterator\n"
        "from tpy import StrView\n"
        "class Box:\n"
        "    s: str\n"
        "    v: StrView\n"
        "    opt: str | None\n"
        "    def __init__(self, s: str, v: StrView) -> None:\n"
        "        self.s = s\n        self.v = v\n        self.opt = None\n")

    def test_async_return_str_field_routes(self):
        src = (self._BOX
               + "    async def own(self) -> str:\n        return self.s\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.return_str_field", 0) == 1
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_yield_str_field_routes(self):
        # Owned member and a NARROWED `str | None` member (whose read renders
        # the `(*__self.opt)` unwrap on both paths) take this arm. The StrView
        # member at an OWNED-str slot does not: it needs the view->owned copy,
        # so sema wraps a materializing coerce and it rides the coerce arm's
        # own str-field threading -- routed either way, hence the flat count.
        src = (self._BOX
               + "    def gen(self) -> Iterator[str]:\n"
               + "        yield self.s\n        yield self.v\n"
               + "        if self.opt is not None:\n"
               + "            yield self.opt\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.yield_str_field", 0) == 2
        assert witnesses.get("field.narrowed_deref", 0) == 1
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_nested_field_receiver_still_defers(self):
        # BOUNDARY: the precheck rides `_str_field_value_read`, whose receiver
        # gate admits a bare NAME only -- a nested `self.inner.s` receiver has
        # no admitted binding, so the body must keep falling back rather than
        # render an unvetted receiver chain.
        src = ("from typing import Iterator\n"
               "class Inner:\n"
               "    s: str\n"
               "    def __init__(self, s: str) -> None:\n        self.s = s\n"
               "class Outer:\n"
               "    inner: Inner\n"
               "    def __init__(self, i: Inner) -> None:\n"
               "        self.inner = i\n"
               "    def gen(self) -> Iterator[str]:\n"
               "        yield self.inner.s\n"
               "def main() -> None:\n    pass\nmain()\n")
        assert "field.result_type" in str(_res_fallback(src))


class TestResumableContainerFieldForHead:
    # A container FIELD as the for-head iterable inside a RESUMABLE frame:
    # begin()/end() are taken off the bare member read (`(__self.xs).begin()`).
    # The sync for-head and the simple-generator peephole have their own arms,
    # so this needs a frame-forcing shape (a yield before the loop).
    _BAG = (
        "from typing import Iterator\n"
        "from tpy import Int32\n"
        "class Bag:\n"
        "    xs: list[Int32]\n"
        "    def __init__(self) -> None:\n        self.xs = [1, 2]\n")

    def test_container_field_for_head_routes(self):
        src = (self._BAG
               + "    def gen(self) -> Iterator[Int32]:\n"
               + "        yield 0\n"
               + "        for x in self.xs:\n            yield x\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("field.container_iterable", 0) == 1
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_narrowed_optional_container_field_still_defers(self):
        # BOUNDARY: a NARROWED `Optional[list]` field types as a plain
        # container on the EXPR but the AST unwraps that read, so the arm
        # keys on the DECLARED type and this must keep falling back.
        src = ("from typing import Iterator, Optional\n"
               "from tpy import Int32\n"
               "class H:\n"
               "    xs: Optional[list[Int32]]\n"
               "    def __init__(self) -> None:\n        self.xs = None\n"
               "    def gen(self) -> Iterator[Int32]:\n"
               "        yield 0\n"
               "        if self.xs is not None:\n"
               "            for x in self.xs:\n                yield x\n"
               "def main() -> None:\n    pass\nmain()\n")
        assert _res_fallback(src)


class TestResumableLeafFinally:
    # A leaf try/FINALLY in a resumable renders as the plain sync
    # duplicated-body try -- no finally-frame scaffolding -- when nothing
    # crosses it. A crossing RETURN rides the leaf finally bridge: THIR
    # mirrors the frame onto the AST finally stack (shared guard numbering
    # and liveness) so _make_async_return's chain walk inlines the finally
    # body. Break/continue crossings stay fenced.
    _PRE = "from tpy import Int32\n\n"

    def test_leaf_finally_without_crossing_routes(self):
        src = (self._PRE
               + "async def f(n: Int32) -> Int32:\n"
               + "    total = 0\n"
               + "    try:\n        total = total + n\n"
               + "    finally:\n        print('cleanup')\n"
               + "    return total\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.leaf_try_finally", 0) == 1
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_leaf_finally_with_except_routes(self):
        src = (self._PRE
               + "async def f(n: Int32) -> Int32:\n"
               + "    total = 0\n"
               + "    try:\n        total = total + n\n"
               + "    except ValueError:\n        print('handler')\n"
               + "    finally:\n        print('cleanup')\n"
               + "    return total\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, _fallback = _assert_identical(src)
        assert witnesses.get("res.leaf_try_finally", 0) == 1

    def test_return_inside_try_routes(self):
        # CONVERTED (wave 13, the leaf finally bridge): the crossing
        # return now rides the hook's chain walk -- the frame mirrors onto
        # the AST finally stack with the shared guard.
        from .testutil import _assert_routes_byte_identical
        src = (self._PRE
               + "async def f(n: Int32) -> Int32:\n"
               + "    try:\n        return n\n"
               + "    finally:\n        print('cleanup')\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        # Shared guard numbering + liveness: the hook references the guard
        # THIR declared, and the eager value capture precedes the chain.
        assert "bool __fin_ran_1 = false;" in cpp
        assert "__tpy_async_ret_0 = n;" in cpp
        assert "if (!__fin_ran_1) {" in cpp

    def test_self_contained_break_still_defers(self):
        # The walk does not track which loop a break binds to, so a break
        # bound by a loop INSIDE the try rejects even though it never leaves
        # the finally. Pinned as the deliberate over-rejection it is: cheap
        # to relax later, and a fallback costs nothing but coverage.
        src = (self._PRE
               + "async def f(n: Int32) -> Int32:\n"
               + "    total = 0\n"
               + "    try:\n"
               + "        for i in range(n):\n"
               + "            if i > 2:\n                break\n"
               + "            total = total + i\n"
               + "    finally:\n        print('cleanup')\n"
               + "    return total\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        assert "leaf_try" in str(_res_fallback(src))

    def test_escaping_continue_still_defers(self):
        # A continue bound by a loop OUTSIDE the try genuinely crosses the
        # finally -- the case the conservative walk exists for.
        src = (self._PRE
               + "async def f(n: Int32) -> Int32:\n"
               + "    total = 0\n"
               + "    for i in range(n):\n"
               + "        try:\n"
               + "            if i > 2:\n                continue\n"
               + "            total = total + i\n"
               + "        finally:\n            print('cleanup')\n"
               + "    return total\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        assert "leaf_try" in str(_res_fallback(src))

    def test_nested_return_inside_try_routes(self):
        # CONVERTED (wave 13): a nested crossing return rides the same
        # bridge; the break/continue slice keeps the fence
        # (test_self_contained_break_still_defers).
        src = (self._PRE
               + "async def f(n: Int32) -> Int32:\n"
               + "    try:\n"
               + "        if n > 0:\n            return n\n"
               + "        print('zero')\n"
               + "    finally:\n        print('cleanup')\n"
               + "    return 0\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        assert not _res_fallback(src)

    def test_nested_finally_frames_route(self):
        # Two frames on the stack: the chain walk inlines BOTH finally
        # bodies (inner first), each behind its own shared guard.
        from .testutil import _assert_routes_byte_identical
        src = (self._PRE
               + "async def f(n: Int32) -> Int32:\n"
               + "    try:\n"
               + "        try:\n            return n\n"
               + "        finally:\n            print('inner')\n"
               + "    finally:\n        print('outer')\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert cpp.count("bool __fin_ran_") == 2

    def test_terminating_finally_routes(self):
        # A finally that itself returns terminates the chain -- the
        # crossing return's capture is dead after the walk, and both
        # paths must agree on that render.
        from .testutil import _assert_routes_byte_identical
        src = (self._PRE
               + "async def f(n: Int32) -> Int32:\n"
               + "    try:\n        return n\n"
               + "    finally:\n        return 99\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _assert_routes_byte_identical(src)


class TestResumableFlatAssertNarrow:
    # A top-level narrowing `assert isinstance` in a resumable flat BB: the
    # AST appends the extraction alias inline, and the alias is BB-LOCAL --
    # the CFG flows the assert's then_type_facts into every successor's
    # entry_narrowings, and each resume case re-establishes its stamped facts
    # as `__{var}`. That is why this needs no scope guard, unlike the post-if
    # fact whose live scope the env walk does not model.
    _PRE = "from typing import Iterator\n\n"

    def test_flat_assert_narrow_routes(self):
        src = (self._PRE
               + "def checked(a: int | str) -> Iterator[str]:\n"
               + "    assert isinstance(a, int)\n"
               + '    yield "checked"\n'
               + "    yield str(a + 100)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert witnesses.get("res.flat_assert_narrow", 0) == 1
        assert not any(k.startswith("resumable:") for k in fallback)

    def test_reassert_after_suspension_defers(self):
        # BOUNDARY, and the cost of the scope fix: a SECOND assert on the
        # same subject lands in a later BB, whose boundary restore has
        # already popped the persistent-narrowing fact `_reassert_bump_info`
        # needs -- so it no longer classifies as a bump and the body falls
        # back. That is the safe direction: the alternative was leaking the
        # narrowing across BBs, which emitted an out-of-scope alias.
        src = (self._PRE
               + "def reassert(a: int | str) -> Iterator[str]:\n"
               + "    assert isinstance(a, int)\n"
               + "    yield str(a + 1)\n"
               + "    assert isinstance(a, int)\n"
               + "    yield str(a + 2)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        assert _res_fallback(src)
        _assert_identical(src)

    def test_frame_field_alias_collision_still_defers(self):
        # BOUNDARY: when the alias name `__{var}` collides with a real frame
        # field, the AST bumps it via _fresh_alias_local's rename, which this
        # mirror does not reproduce -- the same fence _apply_leaf_post_if and
        # the entry-narrowing gate carry.
        src = (self._PRE
               + "def gen(a: int | str) -> Iterator[str]:\n"
               + "    __a = 5\n"
               + "    assert isinstance(a, int)\n"
               + "    yield str(a + __a)\n"
               + "    yield str(__a)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        assert _res_fallback(src).get("res.narrowed_resume") == 1


class TestFlatAssertNarrowScoping:
    # REGRESSION for the BB-scope leak: `_append_assert_narrow` mutates
    # lc.narrow in place and rebinds declared[var] with no restore of its
    # own, and the BB driver's `saved_narrow = lc.narrow` holds a REFERENCE
    # -- so without the arm's own snapshot the restore is a no-op and the
    # narrowing leaks into every later BB (the driver's snapshot sits inside
    # `if env:`, and the asserting BB's entry env is empty by construction).
    def test_narrowing_does_not_leak_past_its_bb(self):
        # `a` is narrowed only inside the if-arm; the else-arm reads it
        # UN-narrowed, which the print sink cannot route IN A RESUMABLE --
        # the union-returns wave's STR print row is fenced out of
        # resumable bodies precisely because the AST's persistent
        # assert-narrow alias leaks past its branch in the flat CFG
        # (`__str__(__a)` in the else arm) while THIR's BB snapshot
        # restores. So the body must FALL BACK at the union-name print.
        # MUTATION-CHECKED: with the snapshot removed this same body
        # ROUTES (fallback {}) because the leaked narrowing makes the
        # else-arm read look narrowed. Asserting the reason, not merely
        # "identical", is what makes this fail.
        src = (self._PRE
               + "def gen(a: int | str, flag: bool) -> Iterator[str]:\n"
               + "    if flag:\n"
               + "        assert isinstance(a, int)\n"
               + "        yield str(a + 1)\n"
               + "    else:\n"
               + "        print(a)\n"
               + '    yield "end"\n\n'
               + "def main() -> None:\n    pass\nmain()\n")
        fb = _res_fallback(src)
        assert "print.arg.union_name" in str(fb), fb
        _assert_identical(src)

    _PRE = "from typing import Iterator\n\n"


class TestValueTupleOptionalElem:
    def test_value_opt_elem_tuple_routes(self):
        # Routing + identity for the value-repr Optional[scalar] tuple
        # element: the frame field is a bare std::tuple<int32_t,
        # std::optional<int32_t>>, the subscript reads render bare
        # std::get, and the extracted element narrows as a value-opt
        # local.
        src = ("import asyncio\nfrom tpy import Int32\n\n"
               + "async def pick(n: Int32) -> tuple[Int32, Int32 | None]:\n"
               + "    await asyncio.sleep(0)\n"
               + "    if n > 0:\n"
               + "        return (n, n * 2)\n"
               + "    return (n, None)\n\n"
               + "async def main_coro() -> None:\n"
               + "    a = await pick(5)\n"
               + "    v = a[1]\n"
               + "    if v is not None:\n"
               + "        print(a[0], v)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not fallback

    def test_ptr_optional_elem_tuple_stays_out(self):
        # BOUNDARY for the value-opt element widening: a POINTER-repr
        # Optional element (`Box | None`) makes the tuple borrow-form --
        # admitting it through the value-tuple family would read a `T*`
        # element as a bare value. The body must keep falling back.
        src = ("import asyncio\nfrom tpy import Int32\n\n"
               + "class Box:\n"
               + "    n: Int32\n\n"
               + "    def __init__(self, n: Int32) -> None:\n"
               + "        self.n = n\n\n"
               + "async def pick(b: Box) -> tuple[Int32, Box | None]:\n"
               + "    await asyncio.sleep(0)\n"
               + "    return (1, b)\n\n"
               + "async def main_coro() -> None:\n"
               + "    b = Box(3)\n"
               + "    a = await pick(b)\n"
               + "    v = a[1]\n"
               + "    if v is not None:\n"
               + "        print(a[0], v.n)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        fb = _res_fallback(src)
        assert fb, fb


class TestFrameLayoutPrecedence:
    def test_source_form_beats_pointer_alias_for_reused_loop_var(self):
        # One NAME is a loop var under two strategies: over a list[Point]
        # param (begin_end -> pointer-form alias) and over an
        # Iterable[Point] protocol param (iter_next -> source-form slot).
        # The builder's arm order is load-bearing: the source-form verdict
        # must win -- its frame_slot field serves whichever element form
        # the trait picks, while a bare `T*` field cannot hold an owned
        # fresh element. No corpus case collides the two strategies on one
        # name, so this pin is the only witness of the order.
        src = (_PRE
               + "import asyncio\n"
               + "from typing import Iterable\n\n"
               + "class Point:\n"
               + "    x: Int32\n\n"
               + "    def __init__(self, x: Int32) -> None:\n"
               + "        self.x = x\n\n"
               + "async def f(items: list[Point], it: Iterable[Point]) -> Int32:\n"
               + "    total = 0\n"
               + "    for p in items:\n"
               + "        await asyncio.sleep(0)\n"
               + "        total = total + p.x\n"
               + "    for p in it:\n"
               + "        await asyncio.sleep(0)\n"
               + "        total = total + p.x\n"
               + "    return total\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        from ..codegen_cpp import resumable_cfg as rcfg
        compiler, modules = _compile(src)
        entry = _entry(modules)
        compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=True))
        func = next(fn for fn in entry.ast.functions if fn.name == "f")
        plan = rcfg.resumable_state(func).frame_layout
        assert plan is not None
        assert (plan.bindings["p"].kind
                is rcfg.FrameLocalKind.SOURCE_FORM_SLOT)

    def test_mixed_own_tuple_local_is_its_own_slot_kind(self):
        # A MIXED owned+borrow tuple frame local (`tuple[Own[Box], Box]`)
        # carries an Own element, so it lands in the owning-tuple arm -- but
        # it has no fully-owned storage form to hold: the borrowed element
        # must keep pointing at the caller's object. Its own kind carries the
        # mixed render as the payload; sharing OWNING_TUPLE_SLOT would spell
        # `frame_slot<std::tuple<Box, Box>>` and copy the borrowed half.
        src = (_PRE
               + "from typing import Iterator\n"
               + "from tpy import Own\n\n"
               + "class Box:\n"
               + "    val: Int32\n\n"
               + "    def __init__(self, val: Int32) -> None:\n"
               + "        self.val = val\n\n"
               + "def make_mixed(b: Box) -> tuple[Own[Box], Box]:\n"
               + "    return (Box(1), b)\n\n"
               + "def f(b: Box) -> Iterator[Int32]:\n"
               + "    p = make_mixed(b)\n"
               + "    yield p[0].val\n"
               + "    yield p[1].val\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        from ..codegen_cpp import resumable_cfg as rcfg
        compiler, modules = _compile(src)
        entry = _entry(modules)
        compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=True))
        func = next(fn for fn in entry.ast.functions if fn.name == "f")
        plan = rcfg.resumable_state(func).frame_layout
        assert plan is not None
        verdict = plan.bindings["p"]
        assert verdict.kind is rcfg.FrameLocalKind.MIXED_TUPLE_SLOT
        assert verdict.payload is not None and verdict.payload.endswith("Box*>")


class TestOwnDynParamFamily:
    """The Own[@dynamic P] frame-PARAM family + the Own[T] return slot
    (the async coro-param drill): a `unique_ptr<P>` param field with
    forwarded-move reads, and the generic ownership-transfer Poll<T>
    payload."""

    def test_method_own_protocol_param_routes(self):
        # The witness shape: an async METHOD with an `Own[Cancellable[T]]`
        # param and an `Own[T]` return. The param captures as the bare
        # unique_ptr field (skeleton) and its only leaf read is the
        # forwarded move into the wait_for emplace.
        src = ("import asyncio\n"
               "from tpy import Own\n"
               "from tpy.coro import Cancellable\n\n\n"
               "class Runner:\n"
               "    async def run[T](self, coro: Own[Cancellable[T]],\n"
               "                     timeout: float) -> Own[T]:\n"
               "        return await asyncio.wait_for(coro, timeout)\n\n\n"
               "def main() -> None:\n    pass\nmain()\n")
        _, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        _, hpp, _cpp = _gen(src, thir=True)
        assert "__sub_0.emplace(std::move(coro), timeout);" in hpp

    def test_own_bare_t_param_still_defers(self):
        # BOUNDARY: an `Own[T]` PARAM (bare type param under Own) is not in
        # the param families -- only the RETURN slot admits Own[T]. The
        # capture form for an owned open-T param is unverified; it must
        # keep res.param_type.
        src = ("import asyncio\nfrom tpy import Own\n\n\n"
               "async def ident[T](x: Own[T]) -> Own[T]:\n"
               "    await asyncio.sleep(0.001)\n"
               "    return x\n\n\n"
               "def main() -> None:\n    pass\nmain()\n")
        fallback = _res_fallback(src)
        assert fallback.get("res.param_type") == 1
        _assert_identical(src)


class TestErasedHandleWrites:
    """The owned-erased @dynamic frame local's dedicated decl arm
    (`_lower_erased_handle_write`): member-assign of the own-arg render."""

    _PRE = ("import asyncio\n\n\n"
            "async def add_one(n: int) -> int:\n"
            "    await asyncio.sleep(0.001)\n"
            "    return n + 1\n\n\n")

    def test_erased_handle_rebind_routes(self):
        # Two factory binds to one erased frame local: both render the
        # member assign through the make_adapter wrap (the decl arm serves
        # first bind and rebind alike -- every frame decl is an assign).
        src = (self._PRE
               + "async def main_coro() -> None:\n"
               + "    d = asyncio.wait_for(add_one(1), 5.0)\n"
               + "    t = asyncio.create_task(d)\n"
               + "    print(await t)\n"
               + "    d = asyncio.wait_for(add_one(8), 5.0)\n"
               + "    t2 = asyncio.create_task(d)\n"
               + "    print(await t2)\n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        assert witnesses.get("res.erased_handle_write", 0) >= 2
        _, _hpp, cpp = _gen(src, thir=True)
        assert cpp.count("d = ::tpy::make_adapter<") == 2
        # The forward move-out at the create_task slot, both times.
        assert cpp.count("(std::move(d))") == 2

    def test_erased_handle_branch_bind_routes(self):
        # A branch-nested erased bind that survives a suspension: the
        # branch is CFG-split, so the decl is a BB leaf and the erased arm
        # serves it (never the position-blind branch-decl assign, which
        # would silently drop the erasure wrap).
        src = (self._PRE
               + "async def main_coro(flag: bool) -> None:\n"
               + "    if flag:\n"
               + "        d = asyncio.wait_for(add_one(1), 5.0)\n"
               + "        await asyncio.sleep(0.001)\n"
               + "        t = asyncio.create_task(d)\n"
               + "        print(await t)\n"
               + "    else:\n"
               + "        print(0)\n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        assert witnesses.get("res.erased_handle_write", 0) >= 1
        _, _hpp, cpp = _gen(src, thir=True)
        assert "d = ::tpy::make_adapter<" in cpp


class TestOptTupleUnpackHolder:
    """The `__for_tup_*` VALUE holder with STORAGE-optional elements: the
    head unpack mutable-ref-binds the holder and lifts each ptr-repr
    Optional target via optional_to_ptr."""

    _PRE = ("from typing import Iterator, Optional\n"
            "from tpy import Int32\n\n\n"
            "class P:\n"
            "    x: Int32\n\n"
            "    def __init__(self, x: Int32) -> None:\n"
            "        self.x = x\n\n\n")

    def test_optional_pair_unpack_routes(self):
        src = (self._PRE
               + "def gen(pairs: list[tuple[Optional[P], Optional[P]]]"
               + ") -> Iterator[Int32]:\n"
               + "    for a, b in pairs:\n"
               + "        if a is not None:\n"
               + "            yield a.x\n"
               + "        if b is not None:\n"
               + "            yield b.x\n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        assert witnesses.get("res.unpack_opt_ptr", 0) >= 2
        _, _hpp, cpp = _gen(src, thir=True)
        assert "auto& __tup_1 = __for_tup_0;" in cpp
        assert "a = ::tpy::optional_to_ptr(std::get<0>(__tup_1));" in cpp

    def test_mixed_optional_value_unpack_routes(self):
        # A MIXED holder (optional + value elements): the optional target
        # lifts, the value target takes the plain frame assign.
        src = (self._PRE
               + "def gen(pairs: list[tuple[Optional[P], Int32]]"
               + ") -> Iterator[Int32]:\n"
               + "    for a, n in pairs:\n"
               + "        if a is not None:\n"
               + "            yield a.x\n"
               + "        yield n\n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        assert witnesses.get("res.unpack_opt_ptr", 0) >= 1
        _, _hpp, cpp = _gen(src, thir=True)
        assert "a = ::tpy::optional_to_ptr(std::get<0>(__tup_1));" in cpp
        assert "n = std::get<1>(__tup_1);" in cpp

    def test_container_optional_elem_still_defers(self):
        # BOUNDARY: a CONTAINER-optional element (`Optional[list[Int32]]`)
        # is outside the narrow `_optional_ptr_borrow` accessor, so the
        # holder stays unadmitted and the body keeps res.local_storage.
        src = ("from typing import Iterator, Optional\n"
               "from tpy import Int32\n\n\n"
               "def gen(pairs: list[tuple[Optional[list[Int32]], Int32]]"
               ") -> Iterator[Int32]:\n"
               "    for xs, n in pairs:\n"
               "        if xs is not None:\n"
               "            yield len(xs)\n"
               "        yield n\n\n\n"
               "def main() -> None:\n    pass\nmain()\n")
        fallback = _res_fallback(src)
        assert fallback.get("res.local_storage") == 1
        _assert_identical(src)



class TestFramePtrSlotReseats:
    """Flat-tail wave 4: the resumable frame-field materialization rows --
    an rvalue reseat of a ptr-form frame local (`saved = &*(__ptr_slot_f0 =
    Point(9));`), the Own-opt-call fill+re-lift twin, and the slotless
    subscript-element re-point. Corpus witnesses:
    generators/gen_ptr_local_rvalue_frame, gen_ptr_slot_drop_timing,
    async/async_ptr_local_rvalue_frame."""

    def test_frame_rvalue_reseat_routes(self):
        src = ("from typing import Iterator, Optional\n"
               "from tpy import Int32\n"
               "class Point:\n"
               "    x: Int32\n"
               "    def __init__(self, x: Int32) -> None:\n"
               "        self.x = x\n"
               "def gen() -> Iterator[Int32]:\n"
               "    saved: Optional[Point] = None\n"
               "    yield 1\n"
               "    saved = Point(9)\n"
               "    if saved is not None:\n"
               "        yield saved.x\n"
               "def main() -> None:\n"
               "    for v in gen():\n        print(v)\n"
               "main()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not fallback, fallback
        _, _hpp, cpp = _gen(src, thir=True)
        assert "saved = &*(__ptr_slot_f0 = Point(9));" in cpp

    def test_frame_storage_call_fill_and_relift_routes(self):
        # Each Own-optional call write fills its OWN prescanned frame
        # field and re-lifts the pointer (the OPT_STORAGE_CALL decl's
        # resumable twin) -- covers both the first write and the rebind.
        src = ("from typing import Iterator, Optional\n"
               "from tpy import Int32, Own\n"
               "class Point:\n"
               "    x: Int32\n"
               "    def __init__(self, x: Int32) -> None:\n"
               "        self.x = x\n"
               "def make_opt(n: Int32) -> Own[Optional[Point]]:\n"
               "    if n > 0:\n        return Point(n)\n"
               "    return None\n"
               "def gen() -> Iterator[Int32]:\n"
               "    got = make_opt(3)\n"
               "    yield 1\n"
               "    got = make_opt(9)\n"
               "    yield 2\n"
               "    if got is not None:\n"
               "        yield got.x\n"
               "def main() -> None:\n"
               "    for v in gen():\n        print(v)\n"
               "main()\n")
        witnesses, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert witnesses.get("reseat.opt_frame_storage_call", 0) >= 2
        _, _hpp, cpp = _gen(src, thir=True)
        assert "__ptr_slot_f0 = make_opt(3);" in cpp
        assert "got = ::tpy::optional_to_ptr(__ptr_slot_f0);" in cpp
        assert "__ptr_slot_f1 = make_opt(9);" in cpp
        assert "got = ::tpy::optional_to_ptr(__ptr_slot_f1);" in cpp

    def test_subscript_elem_reseat_routes(self):
        # A mutable container ELEMENT re-pointing the slotless Optional
        # frame local: the plain pointer chain's subscript arm, Optional
        # flavor -- an alias, no frame slot involved.
        src = ("from typing import Iterator, Optional\n"
               "from tpy import Int32\n"
               "class Point:\n"
               "    x: Int32\n"
               "    def __init__(self, x: Int32) -> None:\n"
               "        self.x = x\n"
               "def gen(items: list[Point]) -> Iterator[Int32]:\n"
               "    saved: Optional[Point] = None\n"
               "    yield 1\n"
               "    saved = items[0]\n"
               "    if saved is not None:\n"
               "        yield saved.x\n"
               "def main() -> None:\n"
               "    pts = [Point(4)]\n"
               "    for v in gen(pts):\n        print(v)\n"
               "main()\n")
        witnesses, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert witnesses.get("reseat.subscript_elem", 0) >= 1
        _, _hpp, cpp = _gen(src, thir=True)
        assert "saved = &(::tpy::__getitem__(items, 0));" in cpp

    def test_frame_subclass_rvalue_reseat_routes(self):
        # A plain-class SUBCLASS ctor rvalue is admitted in the FRAME
        # flavor only: the base-typed frame field takes the warned
        # slicing upcast. The @dynamic sibling never reaches lowering
        # (sema rejects it first -- error_gen_dyn_opt_rebind).
        src = ("from typing import Iterator, Optional\n"
               "class Animal:\n"
               "    kind: str\n"
               "    def __init__(self) -> None:\n"
               "        self.kind = \"animal\"\n"
               "class Cat(Animal):\n"
               "    def __init__(self) -> None:\n"
               "        self.kind = \"cat\"\n"
               "def gen() -> Iterator[str]:\n"
               "    p: Optional[Animal] = None\n"
               "    yield \"start\"\n"
               "    p = Cat()\n"
               "    if p is not None:\n"
               "        yield p.kind\n"
               "def main() -> None:\n"
               "    for s in gen():\n        print(s)\n"
               "main()\n")
        witnesses, fallback = _assert_identical(src)
        assert not fallback, fallback
        assert witnesses.get("reseat.opt_frame_slot", 0) >= 1
        _, _hpp, cpp = _gen(src, thir=True)
        assert "p = &*(__ptr_slot_f0 = Cat());" in cpp


class TestForwardedProtoParamAlias:
    """A forwarded proto-param alias in a resumable (`xs = it`): the decl
    is a compile-time rename -- it emits nothing (trivia only,
    decl.forwarded_alias) and every read renders the BACKING param (the
    AST's generator_storage_name substitution, name.forwarded_alias). The
    oracle contains no `xs` at all."""

    _SRC = (
        "from typing import Iterator, Iterable\n"
        "def echo(it: Iterable[int]) -> Iterator[int]:\n"
        "    xs = it\n"
        "    for x in xs:\n"
        "        yield x\n"
        "        yield x\n"
        "def main() -> None:\n"
        "    for v in echo([1, 2]):\n"
        "        print(v)\n"
        "main()\n"
    )

    def test_forwarded_alias_routes(self):
        # The resumable body lowers at GENERATE time (not lower_module),
        # so the witnesses come off the generating compiler.
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _assert_routes_byte_identical
        outs = _assert_routes_byte_identical(self._SRC)
        assert "resumable_iter_init(__for_itr_0, it)" in outs[0]
        assert "xs" not in outs[0].replace("// xs = it", "")
        compiler, modules = _compile(self._SRC)
        entry = _entry(modules)
        compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))
        faces = compiler._thir_face_witnesses
        assert faces.get("decl.forwarded_alias", 0) >= 1
        assert faces.get("name.forwarded_alias", 0) >= 1


class TestFrameCompWrite:
    """A comprehension init at a frame_slot write (`rows = [[i, i+1] for
    i in range(3) if i > 0]` across a yield): the ordinary comp
    statement-expression renders inside the emplace arg
    (`rows.emplace(({ ... })));` -- the sync decl's comp branch at the
    frame sink, res.frame_comp_write)."""

    _SRC = (
        "from typing import Iterator\n"
        "from tpy import Int32\n"
        "def gen() -> Iterator[Int32]:\n"
        "    rows = [[i, i + 1] for i in range(3) if i > 0]\n"
        "    for r in rows:\n"
        "        yield r[0]\n"
        "        yield r[1]\n"
        "def main() -> None:\n"
        "    for v in gen():\n"
        "        print(v)\n"
        "main()\n"
    )

    def test_frame_comp_write_routes(self):
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _assert_routes_byte_identical
        outs = _assert_routes_byte_identical(self._SRC)
        joined = outs[0] + outs[1]
        assert "rows.emplace(({" in joined
        compiler, modules = _compile(self._SRC)
        entry = _entry(modules)
        compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))
        assert compiler._thir_face_witnesses.get(
            "res.frame_comp_write", 0) >= 1


class TestResumableYieldShapes:
    # Yield-slot rows: Own[container] slots peel Own (the borrow render
    # is the plain container's), a ternary of frame-slot containers hands out
    # the branch-picked borrow, and a record field off self reads bare.
    _IT = "from typing import Iterator\nfrom tpy import Int32, Own\n\n"

    def test_own_container_yield_routes(self):
        from .testutil import _assert_routes_byte_identical
        src = (self._IT
               + "def lists() -> Iterator[Own[list[Int32]]]:\n"
               + "    a: list[Int32] = [1]\n"
               + "    yield a\n"
               + "    b: list[Int32] = [2]\n"
               + "    yield b\n\n"
               + "def main() -> None:\n"
               + "    for xs in lists():\n        print(xs[0])\n"
               + "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "return (*a);" in cpp

    def test_container_ternary_yield_routes(self):
        from .testutil import _assert_routes_byte_identical
        src = (self._IT
               + "def gen(flag: bool) -> Iterator[list[Int32]]:\n"
               + "    i = 0\n"
               + "    while i < 2:\n"
               + "        a: list[Int32] = [7]\n"
               + "        b: list[Int32] = [8]\n"
               + "        yield (a if flag else b)\n"
               + "        print(len(a if flag else b))\n"
               + "        i += 1\n\n"
               + "def main() -> None:\n"
               + "    for xs in gen(True):\n        print(len(xs))\n"
               + "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "return ((flag) ? ((*a)) : ((*b)));" in cpp
        assert "::tpy::__len__(((flag) ? ((*a)) : ((*b))))" in cpp

    def test_record_field_yield_routes(self):
        from .testutil import _assert_routes_byte_identical
        src = (self._IT
               + "class Node:\n"
               + "    v: Int32\n"
               + "    def __init__(self, v: Int32) -> None:\n"
               + "        self.v = v\n\n"
               + "class Holder:\n"
               + "    a: Node\n"
               + "    b: Node\n"
               + "    def __init__(self) -> None:\n"
               + "        self.a = Node(1)\n"
               + "        self.b = Node(2)\n"
               + "    def nodes(self) -> Iterator[Node]:\n"
               + "        yield self.a\n"
               + "        yield self.b\n\n"
               + "def main() -> None:\n"
               + "    h = Holder()\n"
               + "    for n in h.nodes():\n        print(n.v)\n"
               + "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "return __self.a;" in cpp

    def test_nonself_field_yield_defers(self):
        # BOUNDARY: a field yield off a non-self receiver stays fenced.
        src = (self._IT
               + "class Node:\n"
               + "    v: Int32\n"
               + "    def __init__(self, v: Int32) -> None:\n"
               + "        self.v = v\n\n"
               + "class Holder:\n"
               + "    a: Node\n"
               + "    def __init__(self) -> None:\n"
               + "        self.a = Node(5)\n\n"
               + "def gen(h: Holder) -> Iterator[Node]:\n"
               + "    yield h.a\n\n"
               + "def main() -> None:\n"
               + "    h = Holder()\n"
               + "    for n in gen(h):\n        print(n.v)\n"
               + "main()\n")
        assert "res.yield_type" in str(_res_fallback(src))

    def test_walrus_yield_defers(self):
        # BOUNDARY (PARKED design): the AST plants a dead frame_slot member
        # AND a shadowing case-block pointer local for a yield-position
        # borrow walrus -- fenced until the AST wart is resolved (TODO.md).
        src = (self._IT
               + "def gen() -> Iterator[list[Int32]]:\n"
               + "    i = 0\n"
               + "    while i < 2:\n"
               + "        buf: list[Int32] = [3]\n"
               + "        yield (x := buf)\n"
               + "        print(len(buf))\n"
               + "        i += 1\n\n"
               + "def main() -> None:\n"
               + "    for xs in gen():\n        print(len(xs))\n"
               + "main()\n")
        assert "res.yield_type" in str(_res_fallback(src))

    def test_own_record_yield_routes(self):
        # The Own peel also reaches the RECORD yield family
        # (`Iterator[Own[Node]]` on a resumable): pinned routed after the
        # safety review hand-verified byte-identity -- the render is the
        # frame-slot borrow deref, ownership rides the consumer binding.
        from .testutil import _assert_routes_byte_identical
        src = (self._IT
               + "class Node:\n"
               + "    v: Int32\n"
               + "    def __init__(self, v: Int32) -> None:\n"
               + "        self.v = v\n\n"
               + "def nodes() -> Iterator[Own[Node]]:\n"
               + "    n = Node(1)\n"
               + "    yield n\n"
               + "    m = Node(2)\n"
               + "    yield m\n\n"
               + "def main() -> None:\n"
               + "    for x in nodes():\n        print(x.v)\n"
               + "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "return (*n);" in cpp


class TestResumableBoundFieldLoop:
    # A non-suspending for over an open-T SELF field in a
    # resumable leaf. An Iterable-bound T takes the universal
    # `::tpy::__iter__` loop over the member lvalue capture; a
    # NativeIterable/Spannable-bound T takes the begin/end member loop
    # (foreach.native_bound_field). Non-self receivers keep the fence.

    def test_iterable_bound_self_field_routes(self):
        from .testutil import _assert_routes_byte_identical
        src = ("import asyncio\n"
               "from typing import Iterable\n"
               "from tpy import Int32\n\n"
               "class Summer[T: Iterable[Int32]]:\n"
               "    items: T\n"
               "    def __init__(self, items: T) -> None:\n"
               "        self.items = items\n"
               "    async def total(self) -> Int32:\n"
               "        result: Int32 = 0\n"
               "        for x in self.items:\n"
               "            result += x\n"
               "        return result\n\n"
               "def main() -> None:\n"
               "    xs: list[Int32] = [1, 2]\n"
               "    print(asyncio.run(Summer(xs).total()))\n"
               "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "auto& __src_0 = __self.items;" in _hpp + cpp
        assert "::tpy::__iter__(__src_0)" in _hpp + cpp

    def test_native_bound_self_field_routes(self):
        from .testutil import _assert_routes_byte_identical
        src = ("import asyncio\n"
               "from tpy import Int32, NativeIterable\n\n"
               "class Wrap[T: NativeIterable[Int32]]:\n"
               "    items: T\n"
               "    def __init__(self, items: T) -> None:\n"
               "        self.items = items\n"
               "    async def total(self) -> Int32:\n"
               "        result: Int32 = 0\n"
               "        for x in self.items:\n"
               "            result += x\n"
               "        return result\n\n"
               "def main() -> None:\n"
               "    xs: list[Int32] = [1, 2]\n"
               "    print(asyncio.run(Wrap(xs).total()))\n"
               "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "auto& __obj_0 = __self.items;" in _hpp + cpp
        assert "__obj_0.begin();" in _hpp + cpp

    def test_nonself_bound_field_defers(self):
        # BOUNDARY: the leaf gate admits SELF-field iterables only; a
        # bound field off a monomorphized param receiver stays fenced.
        src = ("import asyncio\n"
               "from typing import Iterable\n"
               "from tpy import Int32\n\n"
               "class Summer[T: Iterable[Int32]]:\n"
               "    items: T\n"
               "    def __init__(self, items: T) -> None:\n"
               "        self.items = items\n\n"
               "async def total(s: Summer[list[Int32]]) -> Int32:\n"
               "    result: Int32 = 0\n"
               "    for x in s.items:\n"
               "        result += x\n"
               "    return result\n\n"
               "def main() -> None:\n"
               "    xs: list[Int32] = [1, 2]\n"
               "    print(asyncio.run(total(Summer(xs))))\n"
               "main()\n")
        fb = _res_fallback(src)
        assert any("field.result_type" in k or "for_each" in k
                   for k in fb), fb


class TestResumableTupleYieldSources:
    # Tuple-yield sources: a GENERIC tuple literal spells the
    # val_or_ptr_t brace-init with to_val_or_ptr wraps (subscript elements
    # included), and a tuple NAME outside the storage-form sets passes bare
    # (value aliases and borrow-form params alike).

    def test_generic_tuple_literal_yield_routes(self):
        from .testutil import _assert_routes_byte_identical
        src = ("from typing import Iterator\n\n"
               "def zip_pairs[K, V](ks: list[K], vs: list[V])"
               " -> Iterator[tuple[K, V]]:\n"
               "    i = 0\n"
               "    try:\n"
               "        while i < len(ks) and i < len(vs):\n"
               "            yield (ks[i], vs[i])\n"
               "            i += 1\n"
               "    finally:\n"
               "        print('done')\n\n"
               "def main() -> None:\n"
               "    for k, v in zip_pairs([1, 2], ['a', 'b']):\n"
               "        print(k, v)\n"
               "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        out = _hpp + cpp
        assert "::tpy::to_val_or_ptr<::tpy::val_or_ptr_t<K>>" in out
        assert "std::tuple<::tpy::val_or_ptr_t<K>, ::tpy::val_or_ptr_t<V>>" in out

    def test_value_alias_and_param_tuple_yields_route(self):
        from .testutil import _assert_routes_byte_identical
        src = ("from typing import Iterator\n"
               "from tpy import Int32\n\n"
               "class P:\n"
               "    x: Int32\n"
               "    def __init__(self, x: Int32) -> None:\n"
               "        self.x = x\n\n"
               "def val_gen() -> Iterator[tuple[Int32, Int32]]:\n"
               "    t = (1, 2)\n"
               "    u = t\n"
               "    yield u\n"
               "    yield u\n\n"
               "def ref_gen(p: tuple[P, Int32]) -> Iterator[tuple[P, Int32]]:\n"
               "    yield p\n"
               "    yield p\n\n"
               "def main() -> None:\n"
               "    for pair in val_gen():\n"
               "        print(pair[0] + pair[1])\n"
               "    items = (P(5), 6)\n"
               "    for q, n in ref_gen(items):\n"
               "        q.x += 1\n"
               "        print(q.x + n)\n"
               "    print(items[0].x)\n"
               "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "return u;" in _hpp + cpp
        assert "return p;" in _hpp + cpp

    def test_generic_elem_narrowed_subscript_recv_defers(self):
        # BOUNDARY: the narrowed-receiver shape rejects UPSTREAM at the
        # frame param gate (res.param_type -- an Optional[list[T]] param
        # never enters the frame), so the subscript leg's own narrowed
        # fence sits behind it as defense in depth.
        src = ("from typing import Iterator, Optional\n\n"
               "def pick[T](ks: Optional[list[T]]) -> Iterator[tuple[T, T]]:\n"
               "    if ks is not None:\n"
               "        yield (ks[0], ks[0])\n"
               "        yield (ks[0], ks[0])\n\n"
               "def main() -> None:\n"
               "    for a, b in pick([1, 2]):\n"
               "        print(a, b)\n"
               "main()\n")
        fb = _res_fallback(src)
        assert any("res.param_type" in k for k in fb), fb


class TestResumableGenericCoroFactory:
    # Generic coro factories route where the factory spelling is
    # consumed -- the coro-handle frame write (`c.emplace(ident<int32_t>(
    # __tmp_1));`, ref-slot literal temps flushed at the decl) and the
    # make_adapter erasure boundary (`await ident("hi")` direct).

    def test_generic_handle_bind_and_direct_await_route(self):
        from .testutil import _assert_routes_byte_identical
        src = ("import asyncio\n\n"
               "async def ident[T](v: T) -> T:\n"
               "    return v\n\n"
               "async def main_coro() -> None:\n"
               "    c = ident(41)\n"
               "    r = await c\n"
               "    print(r)\n"
               "    print(await ident('hi'))\n\n"
               "def main() -> None:\n"
               "    asyncio.run(main_coro())\n"
               "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        out = _hpp + cpp
        assert "c.emplace(ident<int32_t>(__tmp_1));" in out


class TestGenericFactoryBoundaries:
    # The coro_factory_ok widening admits generic factories
    # ONLY where the factory spelling is consumed; other positions and
    # owned-tuple param yields keep their fences.

    def test_generic_factory_container_literal_defers(self):
        # A generic factory call inside a container literal is not an
        # admitted factory position -- the body stays AST-side.
        src = ("import asyncio\n\n"
               "async def ident[T](v: T) -> T:\n"
               "    return v\n\n"
               "async def main_coro() -> None:\n"
               "    xs = [ident(1)]\n"
               "    print(len(xs))\n\n"
               "def main() -> None:\n"
               "    asyncio.run(main_coro())\n"
               "main()\n")
        fb = _res_fallback(src)
        assert any("container_literal" in k for k in fb), fb

    def test_own_tuple_param_yield_defers(self):
        # An Own[tuple-with-ptr-elem] PARAM rejects upstream at the frame
        # param gate (res.param_type); the bare-yield leg's Own exclusion
        # is defense in depth behind it.
        src = ("from typing import Iterator\n"
               "from tpy import Int32, Own\n\n"
               "class P:\n"
               "    x: Int32\n"
               "    def __init__(self, x: Int32) -> None:\n"
               "        self.x = x\n\n"
               "def g(p: Own[tuple[P, Int32]]) -> Iterator[tuple[P, Int32]]:\n"
               "    yield p\n"
               "    yield p\n\n"
               "def main() -> None:\n"
               "    for q, n in g((P(1), 2)):\n"
               "        print(q.x + n)\n"
               "main()\n")
        fb = _res_fallback(src)
        assert any("res.param_type" in k for k in fb), fb

    def test_spannable_bound_self_field_routes(self):
        # The native-bound field leg's SPANNABLE flavor (synthesized
        # begin/end off __span__) -- previously admitted unwitnessed.
        from .testutil import _assert_routes_byte_identical
        src = ("import asyncio\n"
               "from tpy import Int32, Spannable\n\n"
               "class Wrap[T: Spannable[Int32]]:\n"
               "    items: T\n"
               "    def __init__(self, items: T) -> None:\n"
               "        self.items = items\n"
               "    async def total(self) -> Int32:\n"
               "        result: Int32 = 0\n"
               "        for x in self.items:\n"
               "            result += x\n"
               "        return result\n\n"
               "def main() -> None:\n"
               "    xs: list[Int32] = [1, 2]\n"
               "    print(asyncio.run(Wrap(xs).total()))\n"
               "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "__obj_0.begin();" in _hpp + cpp


class TestMixedLoopUnpackBindingFences:
    # The unpack-target promotion (_var_decl_names includes TpyTupleUnpack
    # targets) promotes UP FRONT while the AST promotes at the unpack
    # statement. The timing skew could only be observed by a name bound by
    # BOTH an earlier non-consuming loop and a later unpack -- and both
    # flavors of that shape are closed by upstream fences, pinned here.

    def test_loop_unpack_then_unpack_defers(self):
        src = ("from typing import Iterator\n"
               "from tpy import Int32\n\n"
               "def gen(items: list[tuple[Int32, Int32]]) -> Iterator[Int32]:\n"
               "    yield 0\n"
               "    total = 0\n"
               "    for a, n in items:\n"
               "        total += a + n\n"
               "    a, n = (5, 6)\n"
               "    yield total + a + n\n\n"
               "def main() -> None:\n"
               "    for v in gen([(1, 2), (3, 4)]):\n"
               "        print(v)\n"
               "main()\n")
        fb = _res_fallback(src)
        assert any("tuple.reused_target" in k for k in fb), fb

    def test_loop_var_then_unpack_defers(self):
        src = ("from typing import Iterator\n"
               "from tpy import Int32\n\n"
               "def gen(xs: list[Int32]) -> Iterator[Int32]:\n"
               "    yield 0\n"
               "    total = 0\n"
               "    for a in xs:\n"
               "        total += a\n"
               "    a, n = (5, 6)\n"
               "    yield total + a + n\n\n"
               "def main() -> None:\n"
               "    for v in gen([1, 2]):\n"
               "        print(v)\n"
               "main()\n")
        fb = _res_fallback(src)
        assert any("foreach.var_shadow" in k for k in fb), fb


class TestOwningTupleFrameSlot:
    """The owning-tuple frame slot (the skeleton's third owning signal):
    an Own[tuple]-declared callee emplaces (`t.emplace(make_pair(9));`)
    and element reads deref the slot VALUE-form (`std::get<1>((*t)).val`
    -- the binding-form fact, dot not arrow). The subscript-lift twin
    aliases a container element via tuple_to_pointer."""

    _PRE = (
        "from typing import Iterator\n"
        "from tpy import Int32, Own\n"
        "class Box:\n"
        "    val: Int32\n"
        "    def __init__(self, v: Int32) -> None:\n"
        "        self.val = v\n"
        "def make_pair(n: Int32) -> Own[tuple[Int32, Box]]:\n"
        "    return (n, Box(n))\n")

    def test_own_emplace_and_subscript_lift_route(self):
        from .testutil import (_assert_routes_byte_identical,
                               _lower_ctx_witnessed)
        src = self._PRE + (
            "def gen() -> Iterator[Int32]:\n"
            "    t = make_pair(9)\n"
            "    t[1].val = 50\n"
            "    yield t[0]\n"
            "    yield t[1].val\n"
            "def gen2() -> Iterator[Int32]:\n"
            "    items: list[tuple[Int32, Box]] = [(1, Box(5))]\n"
            "    t = items[0]\n"
            "    t[1].val = 99\n"
            "    yield items[0][1].val\n"
            "def main() -> None:\n"
            "    for v in gen():\n"
            "        print(v)\n"
            "    for w in gen2():\n"
            "        print(w)\n"
            "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        # The witness fires in the resumable leaf pass, which only the
        # full codegen runs -- read it off that compiler.
        c, mods = _compile(src)
        c.generate_code_to_strings(
            _entry(mods), options=CodeGenOptions(thir_codegen=True))
        assert c._thir_face_witnesses.get("res.frame_slot_write", 0) >= 1
        assert c._thir_face_witnesses.get("call.own_tuple_storage_ret",
                                          0) >= 1
        assert "t.emplace(make_pair(9));" in cpp
        assert "std::get<1>((*t)).val = 50;" in cpp
        assert ("t = ::tpy::tuple_to_pointer<std::tuple<int32_t, Box*>>"
                "(::tpy::__getitem__((*items), 0));") in cpp

    def test_own_elem_and_reassigned_defer(self):
        # BOUNDARY: an Own-ELEMENT tuple callee and a REASSIGNED owning
        # tuple (not the never-reassigned third signal) keep deferring.
        src = self._PRE + (
            "def make_own_elem(n: Int32) -> Own[tuple[Own[Box], Int32]]:\n"
            "    return (Box(n), n)\n"
            "def gen_own_elem() -> Iterator[Int32]:\n"
            "    t = make_own_elem(4)\n"
            "    yield t[0].val\n"
            "def gen_reassigned() -> Iterator[Int32]:\n"
            "    t = make_pair(1)\n"
            "    yield t[0]\n"
            "    t = make_pair(2)\n"
            "    yield t[1].val\n")
        from .testutil import _assert_byte_identical, _fn, _lower_ctx
        _assert_byte_identical(src)
        thir = _lower_ctx(src)
        assert _fn(thir, "gen_own_elem") is None
        assert _fn(thir, "gen_reassigned") is None


class TestWithOptionalEnterFrameTarget:
    """An Optional-enter `with` target in a resumable frame: the `P* m;`
    frame member takes the FRAME_FIELD bare-pointer bind
    (`m = __ctx_N.__enter__();`) and post-suspension reads ride the
    registered pointer-binding arms (null test + deref + mutation)."""

    def test_opt_and_ref_enter_targets_route(self):
        src = (
            "from typing import Iterator\n"
            "from tpy import Int32\n"
            "class Box:\n"
            "    def __init__(self, n: Int32):\n"
            "        self.n = n\n"
            "class Holder:\n"
            "    box: Box\n"
            "    def __init__(self, n: Int32):\n"
            "        self.box = Box(n)\n"
            "    def __enter__(self) -> \"Box | None\":\n"
            "        return self.box\n"
            "    def __exit__(self, et, ev, tb) -> None:\n"
            "        pass\n"
            "def gen_opt() -> Iterator[Int32]:\n"
            "    h = Holder(7)\n"
            "    with h as m:\n"
            "        pass\n"
            "    yield 1\n"
            "    if m is not None:\n"
            "        m.n += 1\n"
            "        yield m.n\n"
            "def main() -> None:\n"
            "    for x in gen_opt():\n"
            "        print(x)\n"
            "main()\n")
        fb = _res_fallback(src)
        assert not fb, fb
        _assert_identical(src)
        c, _hpp, cpp = _gen(src, thir=True)
        assert "m = __ctx_1.__enter__();" in cpp

    def test_value_repr_optional_enter_defers(self):
        # BOUNDARY: a VALUE-repr Optional enter (Int32 | None -- the
        # std::optional<int32_t> frame member) is outside the opt-ptr
        # gate; the with target keeps deferring.
        src = (
            "from typing import Iterator\n"
            "from tpy import Int32\n"
            "class Holder:\n"
            "    def __init__(self, n: Int32):\n"
            "        self.n = n\n"
            "    def __enter__(self) -> \"Int32 | None\":\n"
            "        return self.n\n"
            "    def __exit__(self, et, ev, tb) -> None:\n"
            "        pass\n"
            "def gen_val() -> Iterator[Int32]:\n"
            "    h = Holder(7)\n"
            "    with h as m:\n"
            "        pass\n"
            "    yield 1\n"
            "    if m is not None:\n"
            "        yield m\n"
            "def main() -> None:\n"
            "    for x in gen_val():\n"
            "        print(x)\n"
            "main()\n")
        fb = _res_fallback(src)
        assert any("with" in k for k in fb), fb
        _assert_identical(src)


class TestUnionFrameSlot:
    """A ptr-repr UNION generator local is an ordinary frame_slot
    (`frame_slot<std::variant<...>>`): writes emplace, and the narrowing
    subject derefs the slot (`holds_alternative<T>((*t))` /
    `std::get<T>((*t))` -- the R1c deref threaded through
    _narrow_variant_cpp). A VALUE-union local keeps deferring."""

    def test_record_union_slot_routes(self):
        src = (
            "from typing import Iterator\n"
            "from tpy import Int32\n"
            "class A:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "def gen_record_union(items: list[A | str]) -> Iterator[Int32]:\n"
            "    t = items.pop()\n"
            "    yield 1\n"
            "    if isinstance(t, A):\n"
            "        t.n += 1\n"
            "        yield t.n\n"
            "def main() -> None:\n"
            "    xs: list[A | str] = [A(3)]\n"
            "    for y in gen_record_union(xs):\n"
            "        print(y)\n"
            "main()\n")
        fb = _res_fallback(src)
        assert not fb, fb
        _assert_identical(src)
        c, _hpp, cpp = _gen(src, thir=True)
        out = _hpp + cpp
        assert "std::holds_alternative<A>((*t))" in out
        assert "std::get<A>((*t))" in out

    def test_value_union_slot_defers(self):
        # BOUNDARY: a VALUE-union generator local is outside the ptr-repr
        # slot admission; the body stays AST.
        src = (
            "from typing import Iterator\n"
            "from tpy import Int32\n"
            "def gen_value_union() -> Iterator[Int32]:\n"
            "    v: Int32 | str = 5\n"
            "    yield 1\n"
            "    if isinstance(v, Int32):\n"
            "        yield v\n"
            "def main() -> None:\n"
            "    for x in gen_value_union():\n"
            "        print(x)\n"
            "main()\n")
        fb = _res_fallback(src)
        assert any("res.local_storage" in k for k in fb), fb
        _assert_identical(src)

    def test_sync_body_union_pop_decl_defers(self):
        # BOUNDARY (the container_union_ret rung's shield): a sync-body
        # `t = items.pop()` decl of a ptr-variant union defers DOWNSTREAM
        # (decl.ptr_union_source) -- the rung admits the pop, the decl
        # slot rejects; both byte-identical.
        src = (
            "from tpy import Int32\n"
            "class A:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "def f(items: list[A | str]) -> None:\n"
            "    t = items.pop()\n"
            "    if isinstance(t, A):\n"
            "        print(t.n)\n"
            "def main() -> None:\n"
            "    f([A(1)])\n"
            "main()\n")
        from .testutil import (_assert_byte_identical, _fn, _lower_ctx)
        _assert_byte_identical(src)
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None


class TestResAliasNameSource:
    """`ys = xs` in a resumable body off a frame-slot container: the alias
    field re-addresses the deref (`ys = &((*xs));`)."""

    def test_frame_slot_alias_source_routes(self):
        from .testutil import (_assert_routes_byte_identical,
                               _lower_ctx_witnessed)
        src = (
            "import asyncio\n"
            "from tpy import Int32, Own\n"
            "async def make() -> Own[list[Int32]]:\n"
            "    xs = [1, 2, 3]\n"
            "    ys = xs\n"
            "    try:\n"
            "        return xs\n"
            "    finally:\n"
            "        ys.append(4)\n"
            "def main() -> None:\n"
            "    print(asyncio.run(make()))\n"
            "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "ys = &((*xs));" in (_hpp + cpp)


class TestResReassignedNeedsCopyParams:
    """Resumable reassigned needs-copy params (str/bytes/BigInt) need no
    gate: the frame member respells owned at the skeleton and the body
    reads ride the ordinary frame-field arms."""

    def test_reassigned_view_param_routes(self):
        from .testutil import _assert_routes_byte_identical
        src = (
            "import asyncio\n"
            "from tpy import Int32\n"
            "async def str_while(t: str) -> Int32:\n"
            "    n = 0\n"
            "    while t:\n"
            "        await asyncio.sleep(0)\n"
            "        n += 1\n"
            "        t = \"\"\n"
            "    return n\n"
            "def main() -> None:\n"
            "    print(asyncio.run(str_while(\"go\")))\n"
            "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "std::string t;" in _hpp


class TestValueOptFrameShapes:
    """A VALUE-repr `Optional[T]` crosses the frame boundary whole: the
    frame field takes `std::nullopt`, and the yield slot passes a narrowed
    source un-dereferenced. Every row is keyed on the SLOT -- the same
    source shape must still deref at a non-optional slot."""

    _IT = "from tpy import Int32\nfrom typing import Iterator\n\n"
    _MAIN = "\ndef main() -> None:\n    pass\nmain()\n"

    def test_frame_field_none_reassign_routes(self):
        from .testutil import _assert_routes_byte_identical
        src = self._IT + (
            "def g(v: Int32 | None) -> Iterator[Int32]:\n"
            "    while v:\n"
            "        yield 1\n"
            "        v = None\n"
            "    yield 2\n") + self._MAIN
        witnesses, fallback = _assert_identical(src)
        assert witnesses["res.frame_opt_none"] >= 1
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "v = std::nullopt;" in cpp

    def test_frame_field_none_decl_routes(self):
        from .testutil import _assert_routes_byte_identical
        src = self._IT + (
            "def g(p: Int32 | None) -> Iterator[Int32]:\n"
            "    q: Int32 | None = None\n"
            "    if p is not None:\n"
            "        q = p\n"
            "    yield 0\n"
            "    if q is not None:\n"
            "        yield q\n") + self._MAIN
        witnesses, _fallback = _assert_identical(src)
        assert witnesses["res.frame_opt_none"] >= 1
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "q = std::nullopt;" in cpp

    def test_value_opt_yield_slot_routes(self):
        from .testutil import _assert_routes_byte_identical
        src = self._IT + (
            "def g(p: Int32 | None) -> Iterator[Int32 | None]:\n"
            "    if p is not None:\n"
            "        yield p\n"
            "    yield None\n") + self._MAIN
        witnesses, _fallback = _assert_identical(src)
        assert witnesses["res.yield_value_opt_none"] >= 1
        _hpp, cpp = _assert_routes_byte_identical(src)
        # The narrowed source passes WHOLE (no `(*p)`), the None spells
        # nullopt (a NoneType STORAGE literal would render monostate).
        assert "return p;" in cpp
        assert "return std::nullopt;" in cpp
        assert "std::monostate" not in cpp

    def test_yield_slot_keys_the_deref_not_the_source(self):
        # THE doctrinal pin: ONE module, ONE source shape (a narrowed
        # value-opt loop var), two yield slots. The `Int32` slot must keep
        # its deref and the `Int32 | None` slot must not gain one.
        from .testutil import _assert_routes_byte_identical
        src = self._IT + (
            "def gd(d: dict[str, Int32 | None]) -> Iterator[Int32]:\n"
            "    for val in d.values():\n"
            "        if val is not None:\n"
            "            yield val\n"
            "def go(d: dict[str, Int32 | None]) -> Iterator[Int32 | None]:\n"
            "    for val in d.values():\n"
            "        if val is not None:\n"
            "            yield val\n"
            "    yield None\n") + self._MAIN
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "return (*val);" in cpp
        assert "return val;" in cpp

    def test_unnarrowed_and_expr_sources_route_at_the_opt_slot(self):
        from .testutil import _assert_routes_byte_identical
        src = self._IT + (
            "def gw(p: Int32 | None) -> Iterator[Int32 | None]:\n"
            "    yield p\n"
            "def ge(p: Int32 | None) -> Iterator[Int32 | None]:\n"
            "    if p is not None:\n"
            "        yield p + 1\n"
            "def gl() -> Iterator[Int32 | None]:\n"
            "    yield 1\n") + self._MAIN
        _assert_routes_byte_identical(src)

    def test_str_optional_yield_slot_still_defers(self):
        # The value-repr optional gate admits on its INNER's capture; an
        # owned-view inner has its own conversion rules at the sink.
        src = ("from typing import Iterator\n\n"
               "def g(s: str | None) -> Iterator[str | None]:\n"
               "    yield s\n"
               "    yield None\n") + self._MAIN
        assert _res_fallback(src).get("res.yield_type") == 1
        _assert_identical(src)

    def test_record_optional_yield_slot_still_defers(self):
        # A pointer-repr Optional yield slot is a plain `T*` -- not this
        # row's whole-optional shape.
        src = self._IT + (
            "class Box:\n"
            "    v: Int32\n"
            "    def __init__(self, v: Int32) -> None:\n"
            "        self.v = v\n\n"
            "def g(b: Box | None) -> Iterator[Box | None]:\n"
            "    yield b\n"
            "    yield None\n") + self._MAIN
        assert _res_fallback(src).get("res.yield_type") == 1
        _assert_identical(src)


class TestFrameFieldWalrusFence:
    """A walrus whose target is a resumable FRAME FIELD must reject.

    The AST used to pre-declare a case-block local in front of the field and
    write the shadow, so THIR's sync walrus rungs happened to byte-match. Now
    that the AST writes the field, those rungs are wrong for a frame target and
    the arm fences instead -- the frame-field write families are their own
    porting row. Without the fence THIR would re-emit the shadow.
    """

    _MAIN = ("\ndef main() -> None:\n"
             "    for u in g():\n"
             "        u[:] = []\nmain()\n")

    _PRE = "from typing import Iterator\nfrom tpy import Int32\n\n"

    def _frame_src(self, walrus_stmt: str) -> str:
        # The loop-body-local yield is what forces a resumable frame; the
        # walrus sits away from the yield slot, which has its own earlier gate.
        return (self._PRE
                + "def g() -> Iterator[list[Int32]]:\n"
                + "    i = 0\n"
                + "    while i < 2:\n"
                + "        buf: list[Int32] = []\n"
                + "        buf.append(3)\n"
                + "        yield buf\n"
                + walrus_stmt
                + "        i += 1\n") + self._MAIN

    def test_frame_field_walrus_rejects(self):
        src = self._frame_src("        if (m := i * 2) > 0:\n"
                              "            print(\"m\", m)\n")
        assert _res_fallback(src).get("expr.walrus") == 1
        _assert_identical(src)

    def test_sync_walrus_is_untouched(self):
        # The fence keys on `frame_local_types`, which is empty for a sync
        # body -- the sync walrus rungs must keep routing.
        src = (self._PRE
               + "def f(n: Int32) -> Int32:\n"
               + "    if (m := n * 2) > 0:\n"
               + "        return m\n"
               + "    return 0\n\n"
               + "def main() -> None:\n    print(f(3))\nmain()\n")
        _witnesses, fallback = _assert_identical(src)
        assert not any(k.startswith("body:") and "walrus" in k
                       for k in fallback)
