# Dict Iterable conformance rejects wrong element type
from typing import Iterable
from tpy import Int32

def consume_ints(items: Iterable[Int32]) -> None:
    for x in items:
        print(x)

def consume_strs(items: Iterable[str]) -> None:
    for x in items:
        print(x)

def main() -> None:
    d: dict[str, Int32] = {"a": 1}
    consume_ints(d)  # tpyc: error(/does not conform to protocol/)
    consume_strs(d)  # tpyc: ok
    consume_ints(d.values())  # tpyc: ok
    consume_strs(d.keys())  # tpyc: ok

main()
