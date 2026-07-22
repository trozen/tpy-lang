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

    def test_rebind_slot_holder_defers(self):
        # An rvalue-reassigned Optional-ptr frame local must never reach
        # the sync rebind-slot arm (its `&*(__slot_N = ...)` references
        # storage only the sync THIRPtrLocalDecl pre-declares). Observed:
        # the sync reseat arm's own source gate rejects first
        # (decl.opt_reseat_source) -- the resumable-side rebind-slot
        # guard (res.leaf_field_write) stays defensive behind it.
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
        assert _res_fallback(src).get(
            "stmt.var_decl:decl.opt_reseat_source") == 1
        _assert_identical(src)

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
        # A STR global is not an eligible-scalar write slot, so its
        # `global` declaration stays unseeded (the write-seeding admits
        # scalar / Ptr-value globals only, resumables included).
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

    def test_name_source_rebind_defers(self):
        # `c2 = c` (handle move-bind) is the two-statement
        # `emplace(std::move(*c)); c.reset();` render -- sliced out.
        src = (self._PRELUDE
               + "async def main_coro() -> None:\n"
               + "    c = add_one(1)\n"
               + "    c2 = c\n"
               + "    print(await c2)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        fallback = _res_fallback(src)
        assert fallback.get("res.coro_handle_source") == 1

    def test_erased_handle_local_defers(self):
        # An ERASED handle local (a helper returning Own[Cancellable[T]]
        # erases the concrete frame via make_adapter): its AST write is
        # `=` through the adapter wrap, not the frame_slot emplace --
        # classification must reject the body whole, never route it
        # through the emplace-shaped arm.
        src = (self._PRELUDE
               + "from tpy import Own\n"
               + "from tpy.coro import Cancellable\n\n"
               + "def spawn() -> Own[Cancellable[Int32]]:\n"
               + "    return add_one(1)\n\n"
               + "async def main_coro() -> None:\n"
               + "    c = spawn()\n"
               + "    print(await c)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        fallback = _res_fallback(src)
        assert fallback.get("res.local_storage") == 1

    def test_generic_method_factory_defers(self):
        # A generic async METHOD factory (`c.echo(5)` with echo[T]) stays
        # out, mirroring the free-call generic-coro-factory gate.
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
        fallback = _res_fallback(src)
        assert "res.coro_handle_write" not in fallback
        assert sum(fallback.values()) >= 1


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

    def test_generic_tuple_yield_defers(self):
        # A TypeParamRef element spells `val_or_ptr_t<T>` with to_val_or_ptr
        # wraps (the generic builder rung) -- must fall back, not take the
        # concrete value/borrow builders. Regression for the divergence the
        # corpus byte-diff caught on gen_generic_tuple_yield (two yields:
        # the resumable frame, not the peephole).
        src = ("from typing import Iterator\n\n"
               + "def zip_pairs[K, V](ks: list[K], vs: list[V])"
               + " -> Iterator[tuple[K, V]]:\n"
               + "    i = 0\n"
               + "    while i < len(ks):\n"
               + "        yield (ks[i], vs[i])\n"
               + "        yield (ks[i], vs[i])\n"
               + "        i += 1\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        fallback = _res_fallback(src)
        assert sum(fallback.values()) >= 1
        _assert_identical(src)

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

    def test_optional_element_yield_defers(self):
        # A pointer-repr Optional element slot spells its own form
        # (`T*` via the Optional arm of the slot-info ladder, None ->
        # nullptr) -- sliced out (btuple.elem_slot). NB the rvalue-into-
        # borrow machinery is unreachable from these sinks: sema rejects
        # rvalue elements at borrow yield slots outright, and decl rvalues
        # go VALUE-capture (storage) -- the btuple.elem_rvalue guard is
        # defensive here; the live rvalue shapes are call args (their own
        # cell).
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
        assert sum(fallback.values()) >= 1
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

    def test_erased_param_forward_defers(self):
        # An already-ERASED Own[Cancellable] PARAM forwarded into the slot
        # renders WITHOUT the re-wrap (a different face) -- must fall back,
        # never take the handle wrap.
        src = ("import asyncio\n"
               + "from tpy import Int32, Own\n"
               + "from tpy.coro import Cancellable\n\n"
               + "async def add_one(n: Int32) -> Int32:\n"
               + "    return n + 1\n\n"
               + "async def spawn(coro: Own[Cancellable[Int32]]) -> Int32:\n"
               + "    t = asyncio.create_task(coro)\n"
               + "    return await t\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        fallback = _res_fallback(src)
        assert sum(fallback.values()) >= 1
        _assert_identical(src)


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

    def test_binding_arm_defers(self):
        # `case _ as y:` binds the subject -- a frame-field write in a
        # resumable, not gen_match's local decl; sliced out.
        src = (self._ENUM
               + "def emit(c: Color) -> Iterator[Int32]:\n"
               + "    match c:\n"
               + "        case Color.RED:\n"
               + "            yield 1\n"
               + "            yield 2\n"
               + "        case _ as y:\n"
               + "            yield 9\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        fallback = _res_fallback(src)
        assert sum(fallback.values()) >= 1
        _assert_identical(src)


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

    def test_narrowing_assert_leaf_defers(self):
        # A top-level narrowing assert in the flat BB walk: the AST's
        # _gen_assert emits the persistent extraction inline; only
        # _lower_stmts' post-assert arm mirrors that (compound bodies), so
        # the flat walk rejects rather than silently dropping the alias.
        src = (self._UNION
               + "async def f(a: Dog | Cat) -> str:\n"
               + "    await step(0)\n"
               + "    assert isinstance(a, Dog)\n"
               + "    return a.sound()\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        assert _res_fallback(src).get("res.narrowed_resume") == 1
        _assert_identical(src)

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

    def test_readonly_union_cond_defers(self):
        # A readonly-qualified union subject is a SYNC slice-out too
        # (`_isinstance_narrow_info` requires a bare declared type -- the
        # ptr_variant_to_const chain stays AST); the Branch cond rejects
        # res.cond and the body falls back byte-identically.
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
        assert fallback.get("res.cond", 0) >= 1
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

    def test_union_local_narrow_blocked_on_local_storage(self):
        # The narrowing admission covers union LOCALS (gen_local_types),
        # but the union frame-field decl family itself is un-routed
        # (res.local_storage) -- the body falls back there first. Flips to
        # a route pin when union locals land.
        src = (self._UNION
               + "def vals() -> Iterator[str]:\n"
               + "    a: Dog | Cat = Dog()\n"
               + "    if isinstance(a, Dog):\n"
               + "        yield \"d\"\n"
               + "        yield a.sound()\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        fallback = _res_fallback(src)
        assert sum(fallback.values()) >= 1
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

    def test_poly_self_narrow_defers(self):
        # `isinstance(self, Sub)`: the dynamic_cast family (alias
        # `__self_narrowed`, if-init locals) -- its own cell; whole-body
        # fallback stays byte-identical.
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
        assert fallback.get("res.narrowed_resume", 0) >= 1
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

    def test_union_binding_arm_defers(self):
        # `case Dog() as d:` binds against the alias -- a case-block local
        # the BB walk can't see; kept on res.match_binding.
        src = (self._UNION
               + "def voices(a: Dog | Cat) -> Iterator[str]:\n"
               + "    match a:\n"
               + "        case Dog() as d:\n"
               + "            yield \"got-dog\"\n"
               + "            yield d.sound()\n"
               + "        case Cat():\n"
               + "            yield a.sound()\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        fallback = _res_fallback(src)
        assert fallback.get("res.match_binding", 0) >= 1
        _assert_identical(src)


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
        # A2: a generic-T loop var over an Iterable[T] param is a
        # frame_slot (iter_next strategy): the skeleton binds
        # `x.emplace(unwrap_ref(*r))`, reads render `(*x)`.
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
        assert "x.emplace(::tpy::unwrap_ref(*(*__for_r_0)));" in hpp
        assert "return (*x);" in hpp

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
        # The LOOP routes (alias cell); the `[(n, R(n))]` list-literal
        # init keeps the frame-position rung: master's sync
        # tuple_to_storage element wrap diverges from the AST's BARE
        # frame-emplace element spelling (merge-caught), so the frame
        # flavor rejects by name.
        fb = _res_fallback(src)
        assert fb == {"expr.tuple_literal.frame_elem": 1}
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

    def test_dict_items_nonvalue_loop_var_defers(self):
        # A proxy-ref tuple loop var over dict[int, Record].items() reaches
        # the advance and takes its borrow-tuple reject (res.loop_var): the
        # element reads/unpacks are their own rung.
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
        assert _res_fallback(src).get("res.loop_var") == 1

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

    def test_durable_own_tuple_local_defers(self):
        # A DURABLE (non-lift) Own-element tuple local is skeleton-owning
        # too, but its decl/read arms are unrouted (the gen_tuple_own_local
        # family) -- it keeps res.local_storage.
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
        assert _res_fallback(src).get("res.local_storage") == 1
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

    def test_finally_leaf_try_defers(self):
        src = (_PRE
               + "async def step(n: Int32) -> Int32:\n    return n + 1\n\n"
               + "async def f(n: Int32) -> Int32:\n"
               + "    n = await step(n)\n"
               + "    try:\n        print(n)\n"
               + "    finally:\n        print(0)\n"
               + "    return n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        assert _res_fallback(src).get("res.leaf_try") == 1
        _assert_identical(src)


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

    def test_literal_decomposition_defers(self):
        # `a, b = (items[0], items[1])` decomposes into synthetic
        # `__unpack_*` alias temps -- an unmirrored render family; the
        # synthetics keep the classification reject.
        src = (_ALIAS_PRE
               + "async def work(items: list[Box]) -> Int32:\n"
               + "    a, b = (items[0], items[1])\n"
               + "    await asyncio.sleep(0)\n"
               + "    return a.n + b.n\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        assert _res_fallback(src).get("res.local_storage") == 1
        _assert_identical(src)

    def test_own_tuple_call_at_borrow_slot_defers(self):
        # The corpus-caught divergence's unit pin: an `Own[tuple[...]]`-
        # declared callee is the skeleton's OWNING signal (frame_slot +
        # emplace/(*t) renders), invisible on the call EXPR's peeled type
        # -- _own_declared_call_ret keeps the write on the named reject.
        src = (_ALIAS_PRE
               + "from tpy import Own\n\n"
               + "def make_pair(n: Int32) -> Own[tuple[Int32, Box]]:\n"
               + "    return (n, Box(n))\n\n"
               + "async def f() -> Int32:\n"
               + "    t = make_pair(9)\n"
               + "    await asyncio.sleep(0)\n"
               + "    return t[0]\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        fb = _res_fallback(src)
        assert fb.get("res.btuple_source") == 1
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

    def test_owned_name_source_returns_bare(self):
        # A frame-slot container name returns bare (`(*xs)`); the decl-init
        # does the ownership transfer, so no move wrap appears here.
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
        assert "__tpy_async_ret = (*xs);" in cpp

    def test_bare_container_await_result_routes(self):
        # The BARE `-> list[T]` slot (no Own) -- the axis on which this
        # predicate is wider than its sync sibling. Sema admits it only
        # for an await source (the corpus gather_helper shape), and the
        # return is pure skeleton (`auto __ret0 = std::move(__r0).value();`).
        src = ("import asyncio\nfrom tpy import Int32, Own\n\n"
               + "async def g() -> Own[list[Int32]]:\n"
               + "    await asyncio.sleep(0)\n"
               + "    return [1]\n\n"
               + "async def f() -> list[Int32]:\n"
               + "    return await g()\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        _, fallback = _assert_identical(src)
        assert not any(k.startswith("resumable:") for k in fallback)
        _, _hpp, cpp = _gen(src, thir=True)
        assert ("::tpystd::tpy::Poll<std::vector<int32_t>>::ready("
                "std::move(__ret0));") in cpp

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
        assert fb.get("res.return_type") == 1  # get only; f routes
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

    def test_own_container_param_admits_reads_defer(self):
        # F6: an Own[list] param admits (moved value-container field);
        # its subscript READ still defers (subscript.recv_type) -- the
        # honest next-blocker, not res.param_type.
        src = ("from typing import Iterator\nfrom tpy import Int32, Own\n\n"
               + "def gen_own(xs: Own[list[Int32]]) -> Iterator[Int32]:\n"
               + "    yield xs[0]\n"
               + "    yield len(xs)\n\n"
               + "def main() -> None:\n    pass\nmain()\n")
        fb = _res_fallback(src)
        assert "res.param_type" not in fb
        assert fb.get("subscript.recv_type") == 1
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
        # still rejects whole via res.leaf_match).
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

    def test_branch_frame_slot_decl_defers(self):
        # A frame_slot local (owning non-value, `.emplace()` render) first-
        # declared in a branch stays a named reject -- only the PLAIN
        # member-assign family routes in branch position. (A list-literal
        # local in this shape hits a pre-existing AST codegen crash --
        # PendingListType in typed_brace_init, see BUGS.md -- so the pin
        # uses a record local.)
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
        assert _res_fallback(src).get("res.leaf_field_write") == 1
        _assert_identical(src)


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

    def test_tuple_unpack_leaf_falls_back_cleanly(self):
        witnesses, fallback = _assert_identical(self.SRC)
        assert "res.body" not in witnesses
        assert fallback.get("resumable:stmt.for_each:tuple.iter_shape") == 1
