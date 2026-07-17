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

    def test_erased_operand_reject_composes(self):
        # An ERASED operand whose expression lowering rejects (walrus arg --
        # a landmark construct) falls back with the positional tag
        # (res.await_operand_shape), never routes a partial body.
        src = ("import asyncio\n"
               + _PRE
               + "async def snooze() -> None:\n"
               + "    await asyncio.sleep((d := 0.01))\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        assert _res_fallback(src).get("res.await_operand_shape") == 1


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

    def test_protocol_param_admits_iteration_still_defers(self):
        # A static-protocol param passes the param gate (the monomorphized
        # template frame is skeleton); this body still falls back --
        # honestly, at the protocol-iterable for-each arm (un-ported sync
        # territory), no longer at res.param_type. Byte-identity holds via
        # the fallback.
        src = (_PRE
               + "from typing import Iterable\n\n"
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(it: Iterable[Int32]) -> Int32:\n"
               + "    total: Int32 = 0\n"
               + "    for x in it:\n"
               + "        total = total + x\n"
               + "    return await step(total)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        fallback = _res_fallback(src)
        assert "res.param_type" not in fallback
        assert fallback.get("stmt.for_each:iter.user_iterator.name") == 1

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
        assert _res_fallback(src).get("res.local_storage") == 1
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

    def test_async_with_global_manager_rejects(self):
        # A global-manager async-with renders the manager as `CM*` already
        # (not the F1-record lvalue-name family) -- res.with_manager.
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
        assert _res_fallback(src).get("res.with_manager") == 1

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
    """str/bytes yields on the resumable frame: the owned return slot's ctor
    absorbs the bare source render (the sgen families' reasoning); the
    slot-literal retype mirrors gen_yield_value's target threading."""

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

    def test_record_loop_var_rejects(self):
        # A non-value (record) loop var binds a skeleton pointer/shadow form
        # the leaf reads can't mirror yet -- res.loop_var.
        src = (_PRE
               + "class R:\n    v: Int32\n"
               + "    def __init__(self, v: Int32) -> None:\n        self.v = v\n\n"
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    xs = [R(n)]\n    total = 0\n"
               + "    for r in xs:\n        total = await step(r.v)\n"
               + "    return total\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        assert _res_fallback(src).get("res.loop_var") == 1


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
        # generator __finally_stop render -- rejects via leaf-mode
        # res.leaf_return, keeping the whole body on AST.
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    try:\n        n = await step(n)\n"
               + "    finally:\n        return 0\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        assert _res_fallback(src).get("res.leaf_return") == 1

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

    def test_tuple_unpack_leaf_falls_back_cleanly(self):
        witnesses, fallback = _assert_identical(self.SRC)
        assert "res.body" not in witnesses
        assert fallback.get("resumable:stmt.for_each:tuple.iter_shape") == 1
