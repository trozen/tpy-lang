# Generator-frame speed probe: the same filtered loop as a genexpr (closure
# render) and as a `def` generator (resumable frame), under a builtin consumer
# (`sum`), a user-defined Iterable consumer and a scanning `any`. Run with
# `uv run tpy scripts/perf/generator_frames.py` (add `--cxx clang` for the
# other toolchain). The goal is a `def` column close to the genexpr one.
# `any` compares like with like: `genb` yields the tested bool itself, since
# `any(v < 0 for v in gen(...))` would time a genexpr wrapped around a frame.
import time
from tpy import int32, int64
from typing import Iterable, Iterator


def gen(xs: list[int32], k: int32) -> Iterator[int64]:
    for x in xs:
        if x % 3 != k:
            yield int64(x) * 2


def genb(xs: list[int32], k: int32) -> Iterator[bool]:
    for x in xs:
        if x % 3 != k:
            yield int64(x) * 2 < 0


def total(it: Iterable[int64]) -> int64:
    s: int64 = 0
    for v in it:
        s += v
    return s


def main() -> None:
    xs = [i for i in range(2000000)]
    k = 1
    for rnd in range(3):
        t0 = time.perf_counter()
        a: int64 = 0
        for _ in range(50):
            a += sum(int64(x) * 2 for x in xs if x % 3 != k)
        t1 = time.perf_counter()
        b: int64 = 0
        for _ in range(50):
            b += sum(gen(xs, k))
        t2 = time.perf_counter()
        c: int64 = 0
        for _ in range(50):
            c += total(int64(x) * 2 for x in xs if x % 3 != k)
        t3 = time.perf_counter()
        d: int64 = 0
        for _ in range(50):
            d += total(gen(xs, k))
        t4 = time.perf_counter()
        e = 0
        for _ in range(50):
            if any(int64(x) * 2 < 0 for x in xs if x % 3 != k):
                e += 1
        t5 = time.perf_counter()
        f = 0
        for _ in range(50):
            if any(genb(xs, k)):
                f += 1
        t6 = time.perf_counter()
        print("sum  genexpr", round((t1 - t0) * 1000), "def", round((t2 - t1) * 1000))
        print("user genexpr", round((t3 - t2) * 1000), "def", round((t4 - t3) * 1000))
        print("any  genexpr", round((t5 - t4) * 1000), "def", round((t6 - t5) * 1000), a == b and c == d and e == f)


main()
