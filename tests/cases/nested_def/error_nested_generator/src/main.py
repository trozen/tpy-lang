# A nested def containing `yield` (a nested generator) is rejected with a
# clean diagnostic -- the closure-as-generator shape is unsupported (the
# reverse, a generator containing a nested def, works).
from typing import Iterator
from tpy import int32


def outer(k: int32) -> int32:
    def gen() -> Iterator[int32]:  # tpyc: error(/nested generator functions are not supported/)
        yield k

    total = 0
    for v in gen():
        total += v
    return total


def main() -> None:
    print(outer(5))


main()
