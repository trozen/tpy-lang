# A declared float32 slot takes a wider float only when spelled float32(...);
# literals and float32 values convert on their own at every slot kind, and a
# float32 widens into a float slot. Mirrors the int32 slots' rule.
import asyncio
import math
from typing import Iterator
from tpy import float32


class Sample:
    v: float32
    xs: list[float32]

    def __init__(self, wide: float) -> None:
        # field: constructor init narrows explicitly, a literal adapts
        self.v = float32(wide)  # tpyc: ok
        self.xs = [1.5, 2.5]  # tpyc: ok

    def scale(self, wide: float) -> None:
        # field aug: the float result is narrowed explicitly, a literal adapts
        self.v = float32(self.v * wide)  # tpyc: ok
        self.v *= 2.0  # tpyc: ok


def narrow(wide: float) -> float32:
    # return: spelled
    return float32(wide)  # tpyc: ok


def show(x: float32) -> None:
    print("arg:", x)


def rescale(x: float32, wide: float) -> float32:
    # param: a plain store and an augmented one spell the narrowing
    x = float32(wide)  # tpyc: ok
    x = float32(x * wide)  # tpyc: ok
    x *= 2.0  # tpyc: ok
    return x


def accumulate(xs: list[float32], step: float) -> float32:
    # local aug: a float32 accumulator fed by float results spells the narrowing
    acc = float32(0.0)
    for x in xs:
        acc = float32(acc + math.sqrt(x) * step)  # tpyc: ok
        acc *= 0.5  # tpyc: ok
    return acc


def halves(xs: list[float32], wide: float) -> Iterator[float32]:
    # generator: a yielded float is spelled
    for x in xs:
        yield float32(x * wide)  # tpyc: ok


async def fetch(wide: float) -> float32:
    # async: the returned float is spelled
    await asyncio.sleep(0)
    return float32(wide)  # tpyc: ok


def bump(x: float32, wide: float) -> float32:
    # closure: a nonlocal store into the float32 parameter is spelled
    def inner() -> None:
        nonlocal x
        x = float32(x + wide)  # tpyc: ok
    inner()
    return x


def main(wide: float) -> None:
    # annotated local: initialiser spelled, literal adapts, float32 value fits
    a: float32 = float32(wide)  # tpyc: ok
    b: float32 = 0.25  # tpyc: ok
    c: float32 = a  # tpyc: ok
    print("local:", a, b, c)
    # call argument
    show(float32(wide))  # tpyc: ok
    show(0.75)  # tpyc: ok
    # return
    print("return:", narrow(wide))
    # param
    print("param:", rescale(a, wide))
    # field
    s = Sample(wide)
    s.scale(wide)
    print("field:", s.v, s.xs)
    # container element
    s.xs[0] = float32(wide)  # tpyc: ok
    s.xs.append(0.125)  # tpyc: ok
    d: dict[str, float32] = {"k": float32(wide)}  # tpyc: ok
    print("element:", s.xs, d)
    # comprehension: each element spelled
    ys: list[float32] = [float32(x * wide) for x in s.xs]  # tpyc: ok
    print("comprehension:", ys)
    # tuple element and Optional slot
    t: tuple[float32, int] = (float32(wide), 1)  # tpyc: ok
    o: float32 | None = float32(wide) if wide > 0 else None  # tpyc: ok
    print("tuple:", t, o)
    # generator, async, closure
    print("generator:", list(halves(s.xs, wide)))
    print("async:", asyncio.run(fetch(wide)))
    print("closure:", bump(a, wide))
    # widening: float32 into a float slot needs no spelling
    w: float = a  # tpyc: ok
    print("widen:", w, math.sqrt(a))
    print("accumulate:", accumulate(s.xs, wide))


main(0.1)
