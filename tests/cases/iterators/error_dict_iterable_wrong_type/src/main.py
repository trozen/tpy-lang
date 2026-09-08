# Dict Iterable conformance rejects wrong element type. The conforming
# shapes (a dict at Iterable[K], and its .keys()/.values() views) are
# pinned by tests/cases/iterators/dict_iterable.
from typing import Iterable
from tpy import Int32

def consume_ints(items: Iterable[Int32]) -> None:
    for x in items:
        print(x)

def main() -> None:
    d: dict[str, Int32] = {"a": 1}
    consume_ints(d)  # tpyc: error(/does not conform to protocol/)

main()
