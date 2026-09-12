# Pins the value form of a generic instantiation at float32 and at an enum: a T
# slot borrows a value, and a generator / coroutine frame COPIES it rather than
# aliasing the caller's local across a suspension.
# BytesView cannot appear here -- BUGS.md#generic-slot-view-enum-arg-rejects.
import asyncio
from enum import Enum
from typing import Iterator

from tpy import float32


class Color(Enum):
    RED = 1
    GREEN = 2


def echo[T](v: T) -> T:
    return v


def has_item[T](xs: list[T], v: T) -> bool:
    for x in xs:
        if x == v:
            return True
    return False


def hold[T](x: T) -> Iterator[T]:
    yield x
    total = 0
    while total < 1:
        total += 1
    yield x


async def held[T](x: T) -> T:
    await asyncio.sleep(0)
    return x


class Cell[T]:
    def __init__(self, v: T) -> None:
        self.v = v  # tpyc: warning(/may copy T into field/)

    def get(self) -> T:
        return self.v

    def find(self, v: T) -> T | None:
        if self.v == v:
            return self.v
        return None


def free_function() -> None:
    fs: list[float32] = [0.5, 1.5]
    k: float32 = 1.5
    # The T param slot at float32, reached by an lvalue and by an rvalue.
    print("free_function:", has_item(fs, k), has_item(fs, float32(2.25)))
    print("free_function:", echo(k))  # tpyc: ok


def method() -> None:
    c = Cell(float32(1.5))
    hit = c.find(float32(1.5))
    miss = c.find(float32(2.25))
    # The val_or_ref_t<T> field read at float32. `find`'s `T | None` renders a
    # pointer for every T (decided per declaration, not by the trait); it is
    # pinned here as today's render, not as a value-form subject.
    print("method:", c.get(), hit is not None, miss is None)


def enum_instantiation() -> None:
    c = Cell(Color.RED)
    # The same field read at an enum -- the trait decides enums by kind.
    print("enum_instantiation:", c.get() == Color.RED, c.get() == Color.GREEN)


def generator_frame() -> None:
    v: float32 = 1.5
    it = hold(v)
    try:
        first = next(it)
        v = 2.5
        # The resumable frame's val_or_ref_t<T> field held a COPY of the caller's
        # local, so the second yield is still 1.5 -- it aliased `v` before the
        # runtime called float32 a value type.
        second = next(it)
        print("generator_frame:", first, second, v)
    except StopIteration:
        print("generator_frame: stop")


async def async_frame() -> None:
    v: float32 = 1.5
    c = held(v)
    v = 2.5
    # Same field in a coroutine frame, mutated between the call and the await.
    print("async_frame:", await c, v)


def main() -> None:
    free_function()
    method()
    enum_instantiation()
    generator_frame()
    asyncio.run(async_frame())


main()
