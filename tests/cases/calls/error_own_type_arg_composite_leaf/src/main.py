# Error: a generic whose SIGNATURE holds its own open type parameter inside a
# tuple or a union is refused at `Own[P]`: BUGS.md#own-type-arg-composite-leaf-refused.
import asyncio
from typing import Iterator
from tpy import Own, int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def pair[T](x: T, out: list[T]) -> tuple[T, int32]:
    out.append(x)  # tpyc: warning(/may copy T into owned storage/)
    return (x, 1)


def two[A, B](a: A, b: B) -> tuple[A, B]:
    return (a, b)


def count[T](*args: tuple[T, int32]) -> int32:
    return len(args)


def steps[T](x: T) -> Iterator[tuple[T, int32]]:
    yield (x, 1)


def either[T](x: T | int32) -> int32:
    return 1


def rows[T](xs: list[tuple[T, int32]]) -> int32:
    return len(xs)


async def co[T](x: T) -> tuple[T, int32]:
    return (x, 1)


class R:
    def opt[T](self, x: T) -> T | None:
        return x


def main() -> None:
    out: list[P] = []
    # The subject: `T` is a leaf of the `tuple[T, int32]` result.
    t = pair[Own[P]](P(2), out)  # tpyc: error(/not supported yet: 'T' appears inside 'tuple\[T, int32\]'/)
    print(t[1], len(out))
    # second parameter: `B` is a leaf too, not only the first one
    print(two[int32, Own[P]](1, P(3))[0])  # tpyc: error(/'B' appears inside 'tuple\[A, B\]'/)
    # `*args` element
    print(count[Own[P]]())  # tpyc: error(/'T' appears inside 'tuple\[T, int32\]'/)
    # generator step
    for s in steps[Own[P]](P(4)):  # tpyc: error(/'T' appears inside 'Iterator\[tuple\[T, int32\]\]'/)
        print(s[1])
    # a union other than Optional
    print(either[Own[P]](1))  # tpyc: error(/'T' appears inside 'T \| int32'/)
    # a generic method's own type parameter
    print(R().opt[Own[P]](P(5)) is None)  # tpyc: error(/type argument of 'opt' is not supported yet: 'T' appears inside 'T \| None'/)
    # a container's tuple element
    print(rows[Own[P]]([]))  # tpyc: error(/'T' appears inside 'list\[tuple\[T, int32\]\]'/)
    # a coroutine's result
    print(asyncio.run(co[Own[P]](P(6)))[1])  # tpyc: error(/type argument of 'co' is not supported yet: 'T' appears inside 'tuple\[T, int32\]'/)


main()
