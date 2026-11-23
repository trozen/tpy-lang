# A fresh f-string at a generator's `Iterable[char]` parameter in a `yield` value is refused,
# though `drain` uses the handle up: a known over-refusal (BUGS.md#task-outlives-spawning-frame-seat).
from typing import Iterable, Iterator
from tpy import char, int32


def each(xs: Iterable[char]) -> Iterator[char]:
    for c in xs:
        yield c


def drain(it: Iterator[char]) -> int32:
    k = 0
    for c in it:
        k += 1
    return k


def gen_yield(n: int32) -> Iterator[int32]:  # tpyc: error(/call.arg_shape.protocol.static/)
    # the yielded value is built from a generator over the f-string
    yield drain(each(f"ab{n}"))


def main() -> None:
    print(list(gen_yield(7)))


main()
