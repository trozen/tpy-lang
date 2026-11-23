# A borrow of a fresh argument kept past its statement dangles (a fresh
# argument lives for its statement, a compound statement's head for the whole
# statement): sema warns at a literal, a comprehension, `[x] * n`, an
# operator's result and a field of a fresh value as at a call result, free
# call and method alike, and at a borrow bound before the loop that reads it;
# a str or scalar literal is static and stays unwarned. The view local at
# the end is the rejected twin; the warned shapes cannot run (they read freed
# storage).
import asyncio
from typing import Iterator
from tpy import int32, Own, StrView, readonly


class Bag:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.xs = [n, n + 1]

    def items(self) -> readonly[list[int32]]:
        return self.xs


def peekl(xs: readonly[list[int32]]) -> readonly[list[int32]]:
    return xs


def mkb(n: int32) -> Own[Bag]:
    return Bag(n)


def items(b: Bag) -> readonly[list[int32]]:
    return b.xs


def walk(xs: readonly[list[int32]]) -> Iterator[int32]:
    for x in xs:
        yield x


class M:
    def __init__(self) -> None:
        pass

    def peekl(self, xs: readonly[list[int32]]) -> readonly[list[int32]]:
        return xs


def observe(b: Bag) -> readonly[Bag]:
    return b


def choose(flag: bool, first: Bag, second: Bag) -> readonly[Bag]:
    return first if flag else second


def shapes() -> None:
    m = M()
    a = peekl([1, 2])  # tpyc: warning(/temporary argument 'xs'/)
    b = peekl([x for x in range(3)])  # tpyc: warning(/temporary argument 'xs'/)
    c = peekl([0] * 4)  # tpyc: warning(/temporary argument 'xs'/)
    d = m.peekl([1, 2])  # tpyc: warning(/temporary argument 'xs'/)
    print(a, b, c, d)
    # a loop head keeps its fresh arguments for the loop; a borrow bound
    # before it does not
    r = items(mkb(40))  # tpyc: warning(/temporary argument 'b'/)
    for v in walk(r):
        print(v)
    owner = Bag(21)
    e = choose(True, owner, Bag(22))  # tpyc: warning(/temporary argument 'second'/)
    print(e.xs[0])
    k = 2
    while k > 0:
        # a loop body ends the temporary with its statement too
        f = observe(Bag(k))  # tpyc: warning(/temporary argument 'b'/)
        print(f.xs[0])
        k -= 1


def first(xs: list[Bag]) -> Bag | None:
    if len(xs) > 0:
        return xs[0]
    return None


def mkl(n: int32) -> Own[list[Bag]]:
    return [Bag(n), Bag(n + 1)]


def pick(b: Bag) -> tuple[Bag, int32]:
    return b, b.xs[0]


def selects(flag: bool) -> None:
    # a ternary's arms: a temporary of the arm's own expression
    s = observe(Bag(2)) if flag else observe(Bag(3))  # tpyc: warning(/temporary argument 'b'/) warning(/temporary argument 'b'/)
    print(s.xs[0])
    z: Bag | None = None
    if flag:
        # an Optional reseated from a borrow of a temporary
        z = first(mkl(7))  # tpyc: warning(/temporary argument 'xs'/)
    if z is not None:
        print(z.xs[0])


async def co() -> int32:
    # a coroutine body: the statement ends the temporary before the suspension
    a, n = pick(Bag(3))  # tpyc: warning(/temporary argument 'b'/)
    await asyncio.sleep(0)
    return a.xs[0] + n


def peeks(s: str) -> StrView:
    return s


def peekn(n: int32, xs: readonly[list[int32]]) -> readonly[list[int32]]:
    return xs


def operands() -> None:
    # an operator's result is a fresh argument too
    g = peekl(mkl_ints(1) + [2])  # tpyc: warning(/temporary argument 'xs'/)
    # an f-string is one as well, but the inferred view local takes its own
    # copy of the str before the statement ends: nothing dangles
    h = peeks(f"v{g[0]}")
    # a field of a fresh value dies with it
    i = peekl(mkb(1).xs)  # tpyc: warning(/temporary argument 'xs'/)
    # a str literal and a scalar literal are static: nothing dangles, so
    # diag.txt holds no warning for these lines
    j = peeks("static")
    k = peekn(3, g)
    print(h, i, j, k)


def mkl_ints(n: int32) -> Own[list[int32]]:
    return [n]


def mks() -> str:
    return "ab" + str(1)


def view() -> None:
    v: StrView = mks()  # tpyc: error(/temporary/)
    print(v)


def main() -> None:
    shapes()
    operands()
    selects(True)
    print(asyncio.run(co()))
    view()


main()
