# A second name bound to a generator object is the SAME generator (CPython
# aliases it): advancing through one name advances the other, in every position.
import asyncio
from typing import Iterable, Iterator, Protocol

from tpy import int32, dynamic, error_return, Own, ReturnException


def counter(start: int32) -> Iterator[int32]:
    i = start
    while True:
        i += 1
        yield i


def evens(start: int32) -> Iterator[int32]:
    i = start
    while i < start + 10:
        i += 2
        yield i


def pairs[T](a: T, b: T) -> Iterator[T]:
    yield a
    yield b


def first(g: Iterator[int32]) -> int32:
    for v in g:
        return v
    return -1


class Src:
    def __init__(self, n: int32) -> None:
        self.n = n

    def items(self) -> Iterator[int32]:
        i = 0
        while i < self.n:
            i += 1
            yield i * 10


class Holder:
    a: int32
    b: int32

    def __init__(self) -> None:
        g = counter(0)
        h = g  # tpyc: ok
        self.a = first(h)
        self.b = first(g)

    def run(self) -> None:
        g = counter(100)
        h = g  # tpyc: ok
        print("method", first(g), first(h), first(g))

    def last_use(self) -> int32:
        h = counter(30)
        first(h)
        # Method: the last use of a started generator aliases it.
        g = h  # tpyc: ok
        return first(g)


class ParseError(Exception, ReturnException):
    pass


@error_return(ParseError)
def er_body(n: int32) -> int32:
    g = counter(n)
    h = g  # tpyc: ok
    if first(h) < 0:
        raise ParseError()
    return first(g)


def gen_body() -> Iterator[int32]:
    g = counter(0)
    h = g  # tpyc: ok
    yield first(h)
    yield first(g)
    yield first(h)


async def async_body() -> int32:
    g = counter(0)
    h = g  # tpyc: ok
    a = first(h)
    await asyncio.sleep(0)
    return a * 10 + first(g)


def free_function() -> None:
    g = counter(0)
    print("free", first(g))
    # The subject: h is g, so pulling through h moves g on.
    h = g  # tpyc: ok
    print("free", first(h), first(g))


def loop_body_binding() -> None:
    for i in range(3):
        # Bound once per pass: each pass's generator is a new one.
        g = counter(i * 100)  # tpyc: ok
        print("loop", first(g), first(g))


def block_bind(c: bool) -> None:
    xs = [4, 5, 6]
    if c:
        # A generator first bound inside a block.
        it = iter_list(xs)  # tpyc: ok
        print("block", first(it), first(it))


def iter_list(xs: list[int32]) -> Iterator[int32]:
    for x in xs:
        yield x


def try_body() -> None:
    g = counter(0)
    try:
        h = g  # tpyc: ok
        print("try", first(h), first(g))
    except ValueError:
        print("try unreachable")


def with_body() -> None:
    g = counter(0)
    with open("frame_alias.txt", "w") as f:
        h = g  # tpyc: ok
        f.write("x")
        print("with", first(h), first(g))


def match_arm(k: int32) -> None:
    g = counter(0)
    match k:
        case 1:
            h = g  # tpyc: ok
            print("match", first(h), first(g))
        case _:
            print("match other")


def tuple_unpack() -> None:
    g, k = counter(0), 7
    h, j = g, k  # tpyc: ok
    print("unpack", first(h), first(g), j)


def swap() -> None:
    g = counter(0)
    h = counter(100)
    # Swapping the names swaps which generator each one reaches.
    g, h = h, g  # tpyc: ok
    print("swap", first(g), first(h))


def exhaust_one_name() -> None:
    g = evens(0)
    h = g  # tpyc: ok
    total = 0
    for v in h:
        total += v
    left = 0
    for v in g:
        left += 1
    print("exhaust", total, left)


def interleave() -> None:
    g = counter(0)
    h = g  # tpyc: ok
    out = []
    for v in g:
        out.append(v)
        out.append(first(h))
        if len(out) >= 6:
            break
    print("interleave", out)


def loop_reuse() -> None:
    h = counter(1000)
    for i in range(2):
        g = counter(i * 100)
        if i == 0:
            # Rebinding g reuses its storage, so h cannot keep the first one:
            # the warned divergence, which is why the read below prints a
            # fixed token rather than the value h reaches.
            h = g  # tpyc: warning(/'h' will not keep the object it was given.*'g' cannot be copied/)
    for v in h:
        print("loop_reuse read")
        break


def risky(fail: bool) -> Iterator[int32]:
    print("risky start")
    if fail:
        raise ValueError("boom")
    yield 1


def raise_before_first_yield() -> None:
    g = risky(True)
    try:
        first(g)
    except ValueError:
        print("raise_before_first_yield raised")
    # The subject: an exception before the first yield finishes the generator.
    print("raise_before_first_yield", first(g))  # tpyc: ok


def try_first_bound() -> None:
    try:
        # A generator first bound in a try body and read after it.
        g = counter(7)  # tpyc: ok
    except ValueError:
        return
    print("try_first_bound", first(g), first(g))


def comprehension() -> None:
    g = counter(0)
    h = g  # tpyc: ok
    print("comprehension", [first(h) for _ in range(3)], first(g))


def chained() -> None:
    g = h = counter(0)  # tpyc: ok
    print("chained", first(g), first(h))


class Bag[T]:
    a: T
    b: T

    def __init__(self, a: Own[T], b: Own[T]) -> None:
        self.a = a
        self.b = b

    def items(self) -> Iterator[T]:
        yield self.a
        yield self.b

    def count(self) -> int32:
        # A generic class's own generator, bound in its body.
        g = self.items()  # tpyc: ok
        h = g
        n = 0
        for v in h:
            n += 1
        return n


def closing(tag: int32) -> Iterator[int32]:
    try:
        yield tag
        yield tag + 1
    finally:
        print("closing finally", tag)


def loop_closes_each() -> None:
    for i in range(2):
        # The previous pass's generator closes when the next one is bound.
        g = closing(i * 10)  # tpyc: ok
        first(g)
        print("loop_closes_each pass", i)


def with_first_bound() -> None:
    with open("frame_alias.txt", "w") as f:
        f.write("y")
        # A generator first bound in a with body and read after it.
        g = counter(3)  # tpyc: ok
    print("with_first_bound", first(g), first(g))


def branch_first_bound(xs: list[int32], c: bool) -> None:
    # First bound in both branches (hoisted), borrowing a parameter.
    if c:
        h = iter_list(xs)  # tpyc: ok
    else:
        h = iter_list(xs)
    print("branch_first_bound", first(h), first(h))


def branch_alias(xs: list[int32], c: bool) -> None:
    h = relay(xs)
    if c:
        # An alias of a deduced frame bound inside a branch.
        j = h  # tpyc: ok
        print("branch_alias", first(j), first(h))


def make_list() -> Own[list[int32]]:
    return [70, 71]


def outer_lift() -> Iterator[int32]:
    # The temporary the inner generator borrows is a field declared ahead of
    # the inner generator's, so it outlives it when the frame is abandoned.
    g = iter_list(make_list())  # tpyc: ok
    yield first(g)
    yield first(g)


def reading_fin(xs: list[str]) -> Iterator[str]:
    try:
        for x in xs:
            yield x
    finally:
        print("reading_fin finally", len(xs), xs[0])


def frame_order(c: bool) -> Iterator[str]:
    xs = ["one", "two"]
    ys = xs
    if c:
        # An alias holds the old list, so xs takes a rebind slot of its own.
        xs = ["three", "four", "five"]
    # A generator held in the frame borrows that slot: it is declared after
    # it, so its finally still reads live storage when the frame is dropped.
    g = reading_fin(xs)  # tpyc: ok
    yield first_str(g)
    yield ys[0]


def first_str(g: Iterator[str]) -> str:
    for v in g:
        return v
    return ""


def closure() -> None:
    g = counter(0)

    def pull() -> int32:
        return first(g)

    # The nested def reads the same generator the enclosing body pulls.
    print("closure", pull(), first(g), pull())  # tpyc: ok


def method_generator() -> None:
    s = Src(5)
    g = s.items()
    h = g  # tpyc: ok
    print("member", first(g), first(h), first(g))


def relay(src: Iterable[int32]) -> Iterator[int32]:
    for v in src:
        yield v


def deduced_frame() -> None:
    # A generator whose callee takes a protocol param: its frame type is
    # deduced per call, and a second name still aliases it.
    xs = [1, 2, 3, 4]
    h = relay(xs)
    j = h  # tpyc: ok
    g, k = relay(xs), 9
    print("deduced", first(j), first(h), first(g), k)


def last_use_alias() -> None:
    h = counter(0)
    first(h)
    # h's last use aliases the started generator; it is never moved.
    g = h  # tpyc: ok
    print("last_use_alias", first(g))


def last_use_unpack() -> None:
    h = counter(10)
    first(h)
    # Tuple unpack: the same alias through the unpack's temporaries.
    g, k = h, 1  # tpyc: ok
    print("last_use_unpack", first(g), k)


def last_use_try() -> None:
    h = counter(20)
    first(h)
    try:
        # First bound in a try body: still an alias of the started generator.
        g = h  # tpyc: ok
    except ValueError:
        return
    print("last_use_try", first(g))


def loop_over_param(xs: list[int32]) -> Iterator[int32]:
    g = iter_list(xs)
    # A loop over a held generator that borrows only a parameter.
    for v in g:  # tpyc: ok
        yield v


async def consume(it: Iterator[int32]) -> int32:
    await asyncio.sleep(0)
    return first(it) * 1000


async def await_then_new_name() -> int32:
    g = counter(0)
    a = await consume(g)
    # A coroutine may keep what it was given, so the new generator takes a new name.
    g2 = counter(100)  # tpyc: ok
    return a + first(g2) + first(g)


def ternary_bound(xs: list[int32], ys: list[int32], c: bool) -> Iterator[int32]:
    # Bound by a ternary over two parameters, then looped over.
    g = iter_list(xs) if c else iter_list(ys)  # tpyc: ok
    for v in g:
        yield v


async def ternary_bound_async(xs: list[int32], ys: list[int32], c: bool) -> int32:
    # Async body: bound by a ternary over two parameters, then looped over.
    g = iter_list(xs) if c else iter_list(ys)  # tpyc: ok
    t = 0
    for v in g:
        await asyncio.sleep(0)
        t += v
    return t


def loop_var_over_list(rows: list[list[int32]]) -> Iterator[int32]:
    for row in rows:
        # The loop walks a list, whose elements stay put across passes.
        g = iter_list(row)  # tpyc: ok
        yield first(g)


def walrus_bound(xs: list[int32]) -> Iterator[int32]:
    # Bound by a walrus, then read again.
    yield first((g := iter_list(xs)))  # tpyc: ok
    yield first(g)


@dynamic
class Getter(Protocol):
    def get(self) -> int32: ...


class Val:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def get(self) -> int32:
        return self.v


def from_getter(s: Getter) -> Iterator[int32]:
    yield s.get()
    yield s.get() + 1


def relay_getter(s: Getter) -> Iterator[int32]:
    # A Getter-typed parameter passed on holds no temporary view.
    g = from_getter(s)  # tpyc: ok
    yield first(g)
    yield first(g)


def generic_generator() -> None:
    g = pairs("a", "b")
    h = g  # tpyc: ok
    for v in h:
        print("generic", v)
        break
    for v in g:
        print("generic", v)


def main() -> None:
    free_function()
    Holder().run()
    hd = Holder()
    print("ctor", hd.a, hd.b)
    try:
        print("error_return", er_body(0))
    except ParseError:
        print("error_return failed")
    print("generator", list(gen_body()))
    print("async", asyncio.run(async_body()))
    loop_body_binding()
    block_bind(True)
    try_body()
    with_body()
    match_arm(1)
    tuple_unpack()
    swap()
    exhaust_one_name()
    interleave()
    loop_reuse()
    closure()
    method_generator()
    deduced_frame()
    raise_before_first_yield()
    try_first_bound()
    comprehension()
    chained()
    print("generic_owner", Bag(1, 2).count())
    loop_closes_each()
    with_first_bound()
    branch_first_bound([40, 41], True)
    branch_alias([50, 51], True)
    print("outer_lift", list(outer_lift()))
    order = list(frame_order(True))
    print("frame_order", order)
    generic_generator()
    last_use_alias()
    last_use_unpack()
    last_use_try()
    print("last_use_method", Holder().last_use())
    print("loop_over_param", list(loop_over_param([60, 61])))
    print("await_then_new_name", asyncio.run(await_then_new_name()))
    print("ternary_bound", list(ternary_bound([80, 81], [90], True)))
    print("walrus_bound", list(walrus_bound([85, 86])))
    print("ternary_bound_async", asyncio.run(ternary_bound_async([1, 2], [3], False)))
    print("loop_var_over_list", list(loop_var_over_list([[5, 6], [7]])))
    print("relay_getter", list(relay_getter(Val(5))))


main()
