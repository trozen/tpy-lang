# A nested def containing `yield` (a nested generator) is rejected with a
# clean diagnostic -- the closure-as-generator shape is unsupported (the
# reverse, a generator containing a nested def, works).
from typing import Iterator
from tpy import Int32


def outer(k: Int32) -> Int32:
    def gen() -> Iterator[Int32]:  # tpyc: error(/nested generator functions are not supported/)
        yield k

    total = 0
    for v in gen():
        total += v
    return total


def main() -> None:
    print(outer(5))


main()
