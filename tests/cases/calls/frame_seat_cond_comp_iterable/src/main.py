# A conditional between two comprehensions at a generator's `Iterable`
# parameter: the generator keeps its argument's storage in a typed local
# (`std::vector<int32_t> __tmp_1`) that outlives the call.
from typing import Iterable, Iterator
from tpy import int32


def g_it(it: Iterable[int32]) -> Iterator[int32]:
    for x in it:
        yield x


def outer(c: bool) -> int32:
    # the select of two comprehensions at the protocol parameter
    return sum(g_it([x for x in range(3)] if c else [x * 2 for x in range(3)]))  # tpyc: ok


def main() -> None:
    print(outer(True), outer(False))


main()
