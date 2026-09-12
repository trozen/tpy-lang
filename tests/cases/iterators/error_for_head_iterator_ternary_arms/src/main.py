# A for-head iterable that is a ternary mixing an iterator CALL arm with a
# generator-valued NAME arm: the head's owning capture is written for the
# all-rvalue-call shape, so the mixed spelling rejects.
from typing import Iterator
from tpy import int64


def gen(o: list[int64]) -> Iterator[int64]:
    for x in o:
        yield x


def f(flag: bool) -> int64:
    it = gen([9])
    total = int64(0)
    for v in (gen([1]) if flag else it):  # tpyc: error(/iter\.user_iterator\.if_expr/)
        total += v
    return total


def main() -> None:
    print(f(True))


main()
