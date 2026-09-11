# A borrowing VIEW (`Span[T]`, `StrView`, `BytesView`) is an admitted
# resumable-frame parameter, and the storage it aliases outlives the frame:
# a frame never receives a temporary, so every temporary argument to a
# generator/coroutine factory is hoisted to a named local of the calling block
# and the view is taken over that. Inside a RESUMABLE body the calling block is
# a `case` block of the enclosing frame, which every handle built there
# outlives, so the name is a field of that enclosing frame instead.
# The rule is about the frame receiving a NAME rather than about views: a plain
# container (`T&`) slot hoists the same way, and a comprehension argument --
# which renders its own slot-typed temp at a plain argument position -- becomes
# THAT one hoisted local instead of being wrapped in a second one.
from typing import Iterator

import asyncio

from tpy import BytesView, Int32, Span, StrView, readonly


class P:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


# free two-yield generator (off the simple-generator peephole): the frame field
# is the same view the sync signature spells, so writes through it land in the
# caller's storage instead of in a buffer copied into the frame.
def bump_scalars(s: Span[Int32]) -> Iterator[Int32]:  # tpyc: ok
    s[0] += 10
    yield s[0]
    s[1] += 10
    yield s[1]


# free generator / record element: the element is mutated through the view
# after a suspension.
def bump_records(s: Span[P]) -> Iterator[Int32]:  # tpyc: ok
    yield s[0].n
    s[0].n += 10
    yield s[0].n


# generator METHOD: the view rides beside the `self` capture.
class Scaler:
    factor: Int32

    def __init__(self, factor: Int32) -> None:
        self.factor = factor

    def scale(self, s: Span[Int32]) -> Iterator[Int32]:  # tpyc: ok
        s[0] *= self.factor
        yield s[0]
        s[1] *= self.factor
        yield s[1]


# `Span[readonly[Int32]]` (const element): reading the same slot either side of
# a suspension observes a source mutated in between. A frame that copied the
# buffer would repeat the first read.
def read_ro_elems(s: Span[readonly[Int32]]) -> Iterator[Int32]:  # tpyc: ok
    yield s[0]
    yield s[0]


# `readonly[Span[Int32]]` -- the const sits on the view rather than the
# element, and the sync param spells the same `std::span<int32_t>`.
def read_ro_span(s: readonly[Span[Int32]]) -> Iterator[Int32]:  # tpyc: ok
    yield s[1]
    yield s[1]


# Reads BOTH slots across the suspension: the second pull needs the whole
# backing array alive, not just the first element.
def read_pair(s: Span[readonly[Int32]]) -> Iterator[Int32]:  # tpyc: ok
    yield s[0]
    yield s[1]


def make_str(n: Int32) -> str:
    # An OWNED str whose buffer dies at the end of the calling statement.
    return "abcdefghij" * n


def make_bytes(n: Int32) -> bytes:
    return b"0123456789" * n


# Reads the CONTENT either side of a suspension, so a dangling view is a wrong
# answer rather than a stale length.
def head_tail(t: StrView) -> Iterator[str]:  # tpyc: ok
    yield t[0:4]
    yield t[len(t) - 4:len(t)]


def byte_ends(b: BytesView) -> Iterator[Int32]:  # tpyc: ok
    yield b[0]
    yield b[len(b) - 1]


class Tagger:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

    def tag(self, t: StrView) -> Iterator[str]:  # tpyc: ok
        yield self.name + ":" + t[0:3]
        yield self.name + ":" + t[len(t) - 3:len(t)]


async def async_head(t: StrView) -> str:  # tpyc: ok
    await asyncio.sleep(0.0)
    return t[0:4] + "/" + t[len(t) - 4:len(t)]


async def run_head() -> str:
    return await async_head(make_str(2))  # tpyc: ok


def count_view(it: Iterator[str]) -> Int32:
    n = 0
    for v in it:
        n += len(v)
    return n


def total(it: Iterator[Int32]) -> Int32:
    n = 0
    for v in it:
        n += v
    return n


# The str/bytes VIEWS ride the same admission and stay borrowed, unlike the
# `str`/`bytes` params the frame copies into owned storage.
def view_lens(t: StrView, b: BytesView) -> Iterator[Int32]:  # tpyc: ok
    yield len(t)
    yield len(b)


# async def: the write lands across a real suspension.
async def bump_async(s: Span[Int32]) -> Int32:  # tpyc: ok
    s[0] += 1
    await asyncio.sleep(0.0)
    s[1] += 1
    return s[0] + s[1]


# async METHOD flavour of the same capture.
class Adder:
    step: Int32

    def __init__(self, step: Int32) -> None:
        self.step = step

    async def add(self, s: Span[Int32]) -> Int32:  # tpyc: ok
        s[0] += self.step
        await asyncio.sleep(0.0)
        s[1] += self.step
        return s[0] + s[1]


# INLINE await inside a coroutine: the literal's storage is hoisted into THIS
# frame, not into the suspending block, so the sub-coroutine's view survives
# the resume.
async def run_bump() -> Int32:
    return await bump_async([7, 8])  # tpyc: ok


# -- a container (`T&`) slot, fed by a comprehension -------------------------
# The frame field is a `std::vector<int32_t>&`, so the comprehension's fresh
# vector still has to be named. Two yields keep both off the simple-generator
# peephole, and each reads a slot AFTER a suspension, so a buffer freed at the
# end of the calling statement is a wrong answer rather than a stale length.


def ends(xs: list[Int32]) -> Iterator[Int32]:  # tpyc: ok
    yield xs[0]
    yield xs[len(xs) - 1]


class Summer:
    base: Int32

    def __init__(self, base: Int32) -> None:
        self.base = base

    def pair(self, xs: list[Int32]) -> Iterator[Int32]:  # tpyc: ok
        yield self.base + xs[0]
        yield self.base + xs[len(xs) - 1]


# -- the temporary hoisted inside a RESUMABLE body ---------------------------
# Each section below builds the inner handle inside a generator/coroutine body
# and drains it across the ENCLOSING frame's own suspension, so the argument
# has to outlive the outer resume -- a `case`-block local would already be gone.


# enclosing two-yield generator, `for` head, rvalue `str` call
def outer_for() -> Iterator[str]:
    yield "start"
    for v in head_tail(make_str(2)):  # tpyc: ok
        yield v


# enclosing generator, `for` head, container literal at a `Span` param
def outer_span() -> Iterator[Int32]:
    yield 0
    for v in read_pair([5, 6]):  # tpyc: ok
        yield v


# enclosing generator, `for` head, comprehension at a FREE generator's
# container slot: the vector is seated on a field of THIS frame.
def outer_comp(src: list[Int32]) -> Iterator[Int32]:
    yield 0
    for v in ends([x * 3 for x in src]):  # tpyc: ok
        yield v


# enclosing generator, `for` head, comprehension at a generator METHOD's
# container slot -- the seam where the frame hoist re-lowers the argument at
# its owned slot and takes the comprehension's own temp rather than nesting a
# second one. The RECEIVER is an rvalue and takes a field of this frame too:
# `pair` reads `self.base` after each suspension, so a receiver left in the
# state's `case` block would print garbage from the second pull on.
def outer_comp_method(src: list[Int32]) -> Iterator[Int32]:
    yield 1
    for v in Summer(100).pair([x * 2 for x in src]):  # tpyc: ok
        yield v


# enclosing generator, `for` head, f-string source
def outer_fstring(n: Int32) -> Iterator[str]:
    yield "n"
    for v in head_tail(f"val-{n}-tail-pad"):  # tpyc: ok
        yield v


# enclosing generator METHOD: the hoisted field rides beside the `self` capture.
class Outer:
    prefix: str

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix

    def run(self) -> Iterator[str]:
        yield self.prefix
        for v in head_tail(make_str(2)):  # tpyc: ok
            yield self.prefix + v

    # enclosing generator METHOD, rvalue RECEIVER: the receiver's own frame
    # field rides beside the `self` capture.
    def run_recv(self, xs: list[Int32]) -> Iterator[Int32]:
        yield len(self.prefix)
        for v in Summer(100).pair(xs):  # tpyc: ok
            yield v + len(self.prefix)


# enclosing async def, coroutine handle bound to a local and awaited later
async def outer_bind() -> str:
    await asyncio.sleep(0.0)
    c = async_head(make_str(2))  # tpyc: ok
    await asyncio.sleep(0.0)
    return await c


# enclosing async def, the escaping-Task form (not an inline await)
async def outer_task() -> str:
    t = asyncio.create_task(async_head(make_str(2)))  # tpyc: ok
    await asyncio.sleep(0.0)
    return await t


# -- the seated field re-emplaced by a LOOP ----------------------------------
# There is one seat per call SITE, so a site inside a loop shares it across
# iterations. That is sound exactly where no handle built there is still alive
# when the site re-executes; a site whose handle outlives the iteration is a
# loud reject instead (`tests/cases/async/error_task_temp_in_loop`).


# enclosing generator, `for` head inside a loop: the loop statement drains and
# destroys its iterator, so the field is free to take the next iteration's
# argument. Each pull must read THIS iteration's list, across the outer yield
# in between.
def outer_loop_for(n: Int32) -> Iterator[Int32]:
    for i in range(n):
        for v in read_pair([i, i + 10]):  # tpyc: ok
            yield v


# enclosing async def, a coroutine handle bound inside a loop and awaited in
# the same iteration: one name is one slot, so at most one handle is live and
# the per-site field serves every iteration. The bind crosses a suspension.
async def outer_loop_bind(n: Int32) -> str:
    out = ""
    for i in range(n):
        c = async_head(f"val{i}-tailpad{i}")  # tpyc: ok
        await asyncio.sleep(0.0)
        out += await c + ";"
    return out


# -- the rvalue RECEIVER inside a resumable body -----------------------------
# A factory METHOD's receiver is captured by the frame as `<Class>&` for the
# handle's whole life, exactly like a borrowed argument, so inside a resumable
# body it is seated on a field of the ENCLOSING frame too. Each section reads a
# receiver FIELD (`self.base` / `self.step`) after the outer frame has
# suspended. An rvalue receiver at a local BIND or an inline `await` stays a
# loud reject instead (`tests/cases/async/error_bind_receiver_temp`,
# `error_await_temp_receiver`): there the receiver is named by the user.


# enclosing async def, `for` head over a generator method: the receiver
# outlives the outer coroutine's own suspension inside the loop body.
async def outer_async_recv(src: list[Int32]) -> Int32:
    await asyncio.sleep(0.0)
    n = 0
    for v in Summer(100).pair(src):  # tpyc: ok
        await asyncio.sleep(0.0)
        n += v
    return n


# enclosing async def, the escaping-Task form: the receiver AND the argument of
# one call each take a field of this frame.
async def outer_task_recv() -> Int32:
    t = asyncio.create_task(Adder(5).add([7, 8]))  # tpyc: ok
    await asyncio.sleep(0.0)
    return await t


def main() -> None:
    xs = [1, 2]
    for v in bump_scalars(xs):
        print("gen:", v)
    print("gen: after", xs[0], xs[1])

    ps = [P(1)]
    for v in bump_records(ps):
        print("gen-record:", v)
    print("gen-record: after", ps[0].n)

    ms = [3, 4]
    for v in Scaler(2).scale(ms):
        print("method:", v)
    print("method: after", ms[0], ms[1])

    ro = [5, 6]
    pulls = 0
    for v in read_ro_elems(ro):
        print("ro-elem:", v)
        pulls += 1
        if pulls == 1:
            # Mutating the source between pulls: the second read must see 50.
            ro[0] = 50

    pulls = 0
    for v in read_ro_span(ro):
        print("ro-span:", v)
        pulls += 1
        if pulls == 1:
            ro[1] = 60

    for v in view_lens("abc", b"de"):
        print("views:", v)

    aa = [7, 8]
    print("async:", asyncio.run(bump_async(aa)))
    print("async: after", aa[0], aa[1])

    ab = [7, 8]
    print("async-method:", asyncio.run(Adder(5).add(ab)))
    print("async-method: after", ab[0], ab[1])

    # -- rvalue argument: the view's backing storage is hoisted ---------------
    # A container literal at a view param is a fresh object the caller never
    # names, so the frame would alias a statement temporary. Each section below
    # reads across a suspension a slot it wrote (or a second slot) before it --
    # only live storage answers.

    # free generator
    for v in bump_scalars([1, 2]):  # tpyc: ok
        print("lit-gen:", v)

    # free generator / record element
    for v in bump_records([P(1)]):  # tpyc: ok
        print("lit-record:", v)

    # generator method
    for v in Scaler(2).scale([3, 4]):  # tpyc: ok
        print("lit-method:", v)

    # readonly-element view
    for v in read_pair([5, 6]):  # tpyc: ok
        print("lit-ro:", v)

    # async def, awaited from a sync statement
    print("lit-async:", asyncio.run(bump_async([7, 8])))  # tpyc: ok

    # async method
    print("lit-async-method:", asyncio.run(Adder(5).add([7, 8])))  # tpyc: ok

    # inline await inside a coroutine (the frame-slot hoist)
    print("lit-await:", asyncio.run(run_bump()))

    # str / bytes LITERALS hoist too (`std::string __tmp = "abcd";`): the rule
    # is about the frame receiving a name, not about which C++ shapes alias.
    for v in view_lens("abcd", b"def"):  # tpyc: ok
        print("lit-views:", v)

    # -- rvalue str / bytes sources ------------------------------------------
    # The str-family sources whose OWNED buffer dies at the end of the
    # statement. Each section pulls TWICE and prints the second read's content,
    # so a view over a destroyed buffer is a wrong answer, not just a wrong
    # length (`len` alone reads only the view's size word).

    # free generator, rvalue call
    for v in head_tail(make_str(2)):  # tpyc: ok
        print("rv-call:", v)

    # free generator, binop
    for v in head_tail("ab" * 3):  # tpyc: ok
        print("rv-binop:", v)

    # free generator, method call
    src = "abcdefghij"
    for v in head_tail(src.upper()):  # tpyc: ok
        print("rv-method:", v)

    # free generator, f-string
    n = 7
    for v in head_tail(f"val-{n}-tail-pad"):  # tpyc: ok
        print("rv-fstring:", v)

    # free generator, bytes rvalue call
    for v in byte_ends(make_bytes(2)):  # tpyc: ok
        print("rv-bytes:", v)

    # free generator, bytes literal
    for v in byte_ends(b"0123456789abcdef"):  # tpyc: ok
        print("lit-bytes:", v)

    # generator METHOD
    for v in Tagger("t").tag(make_str(2)):  # tpyc: ok
        print("rv-method-recv:", v)

    # a str LOCAL is not a temporary: no hoist, and the view still reads it
    named = make_str(2)
    for v in head_tail(named):  # tpyc: ok
        print("name-str:", v)

    # async def, awaited from a sync statement
    print("rv-async:", asyncio.run(async_head(make_str(2))))  # tpyc: ok

    # inline await inside a coroutine (the awaiter's own frame slot)
    print("rv-await:", asyncio.run(run_head()))

    # nested call argument
    print("rv-nested:", count_view(head_tail(make_str(2))))  # tpyc: ok

    # -- the hoist position is the ENCLOSING statement's flush point ---------
    # Not only the bare `for`-head above: a nested argument list, a
    # comprehension's iterable and a `while` head each still have one, so the
    # decl lands there and the frame's view stays over live storage.

    # nested call argument
    print("lit-nested:", total(read_pair([5, 6])))  # tpyc: ok

    # comprehension iterable
    comp = [v for v in read_pair([5, 6])]  # tpyc: ok
    print("lit-comp:", comp[0], comp[1])

    # while condition (the restructured `while (true)` head)
    spins = 0
    while total(read_pair([5, 6])) > spins:  # tpyc: ok
        spins += 1
    print("lit-while:", spins)

    # -- a comprehension argument at a container (`T&`) slot -----------------
    # A comprehension renders its own slot-typed temp at a plain argument
    # position, so the frame rule takes THAT temp instead of nesting a second
    # one around it -- one `std::vector<int32_t> __tmp_N` per call.

    nums = [1, 2, 3]

    # free generator
    for v in ends([x * 5 for x in nums]):  # tpyc: ok
        print("comp-free:", v)

    # generator METHOD (the seam the two hoist rows met at)
    for v in Summer(10).pair([x * 2 for x in nums]):  # tpyc: ok
        print("comp-method:", v)

    # -- the hoist inside a resumable body -----------------------------------
    for v in outer_for():
        print("res-for:", v)

    for v in outer_span():
        print("res-span:", v)

    for v in outer_comp(nums):
        print("res-comp-free:", v)

    for v in outer_comp_method(nums):
        print("res-comp-method:", v)

    for v in outer_fstring(7):
        print("res-fstring:", v)

    for v in Outer("o").run():
        print("res-method:", v)

    print("res-bind:", asyncio.run(outer_bind()))
    print("res-task:", asyncio.run(outer_task()))

    for v in outer_loop_for(3):
        print("res-loop-for:", v)

    print("res-loop-bind:", asyncio.run(outer_loop_bind(3)))

    for v in Outer("o").run_recv(nums):
        print("res-recv-method:", v)

    print("res-recv-async:", asyncio.run(outer_async_recv(nums)))
    print("res-recv-task:", asyncio.run(outer_task_recv()))


main()
