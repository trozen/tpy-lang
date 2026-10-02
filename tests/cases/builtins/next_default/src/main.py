# next(it, default): the default instead of StopIteration once the iterator
# is exhausted; the one-argument form still raises.
from typing import Iterator


def letters() -> Iterator[str]:
    yield "a"
    yield "b"


def counting() -> Iterator[int]:
    yield 1
    yield 2
    yield 3


def pairs(it: Iterator[int]) -> Iterator[int]:
    # generator body: the default ends the pairing
    while True:
        a = next(it, -1)  # tpyc: ok
        if a < 0:
            return
        yield a + next(it, 0)  # tpyc: ok


def first_or(xs: list[int], fallback: int) -> int:
    # parameter: the first element, or the fallback for an empty list
    it = iter(xs)
    return next(it, fallback)  # tpyc: ok


def main() -> None:
    xs = [3, 1, 2]
    it = iter(xs)
    # list iterator: three values, then the default each time
    print("list:", next(it, -1), next(it, -1), next(it, -1), next(it, -1), next(it, -1))  # tpyc: ok
    g = letters()
    # generator
    print("generator:", next(g, "?"), next(g, "?"), next(g, "?"))  # tpyc: ok
    print("param:", first_or([7, 8], 0), first_or([], 0))
    print("generator_body:", list(pairs(counting())))
    # loop: drain with a sentinel default
    pair = [10, 20]
    nums = iter(pair)
    total = 0
    while True:
        v = next(nums, -1)  # tpyc: ok
        if v < 0:
            break
        total += v
    print("loop:", total)
    # the one-argument form still raises at exhaustion
    one = [1]
    e = iter(one)
    try:
        print("raises:", next(e))
        next(e)
    except StopIteration:
        print("raises: StopIteration")


main()
