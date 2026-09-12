# A NON-unpack tuple loop variable in a resumable frame: `for t in xs` over a
# tuple whose element is a reference type. The frame field points at the source
# element tuple, so a mutation through the loop var after a suspension is
# visible at the source (CPython aliasing); the all-value and dict-items
# sections pin the two neighbouring field forms as unchanged.
import asyncio
from typing import Iterator

from tpy import Int32, Own, readonly


class A:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v


# free generator: reference element, mutated after a yield
def walk_free(xs: list[tuple[Int32, A]]) -> Iterator[Int32]:
    for t in xs:  # tpyc: ok
        yield t[0]
        t[1].v += 100
        yield t[1].v


# all-value element tuple: a plain value field, copy is unobservable
def walk_value(xs: list[tuple[Int32, Int32]]) -> Iterator[Int32]:
    for t in xs:  # tpyc: ok
        yield t[0]
        yield t[1]


class Holder:
    xs: list[tuple[Int32, A]]

    def __init__(self, xs: Own[list[tuple[Int32, A]]]) -> None:
        self.xs = xs

    # generator method: same field over a field-rooted source
    def walk(self) -> Iterator[Int32]:
        for t in self.xs:  # tpyc: ok
            yield t[0]
            t[1].v += 100
            yield t[1].v

    # readonly method: the source iterates const, so the field is `const T*`
    @readonly
    def peek(self) -> Iterator[Int32]:
        for t in self.xs:  # tpyc: ok
            yield t[0]
            yield t[1].v


# async def: the same classification serves the coroutine frame
async def walk_async(xs: list[tuple[Int32, A]]) -> Int32:
    total = 0
    for t in xs:  # tpyc: ok
        total += t[0]
        await asyncio.sleep(0)
        t[1].v += 100
        total += t[1].v
    return total


# dict_items proxy: `&(*it)` is ill-formed on the prvalue proxy, so this one
# keeps the borrow-form tuple field and its tuple_to_pointer bind
def walk_items(d: dict[Int32, A]) -> Iterator[Int32]:
    for kv in d.items():  # tpyc: ok
        yield kv[0]
        kv[1].v += 100
        yield kv[1].v


# async + dict_items with an ALL-VALUE element: the proxy cannot be
# address-taken, so this one keeps the plain value-tuple field and its bare bind
async def sum_items(d: dict[Int32, Int32]) -> Int32:
    total = 0
    for kv in d.items():  # tpyc: ok
        await asyncio.sleep(0)
        total += kv[0] + kv[1]
    return total


async def async_section() -> None:
    ays = [(5, A(50))]
    print("async", await walk_async(ays))
    print("async src", ays[0][1].v)
    print("async items", await sum_items({1: 2}))


def main() -> None:
    xs = [(1, A(10)), (2, A(20))]
    for n in walk_free(xs):
        print("free", n)
    print("free src", xs[0][1].v, xs[1][1].v)

    vs = [(1, 10), (2, 20)]
    for n in walk_value(vs):
        print("value", n)

    h = Holder([(3, A(30))])
    for n in h.walk():
        print("method", n)
    print("method src", h.xs[0][1].v)

    for n in h.peek():
        print("readonly", n)

    asyncio.run(async_section())

    d = {7: A(70)}
    for n in walk_items(d):
        print("items", n)
    print("items src", d[7].v)


main()
