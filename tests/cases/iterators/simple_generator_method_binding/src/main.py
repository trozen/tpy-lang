# Regression: current_ns must stay set across a simple-generator METHOD body
# (the record_name peephole path) and for enum-member access -- not only the
# free-function / module-constant cases.
from typing import Iterator
from enum import Enum
from tpy import int32
import caps


class Color(Enum):
    RED = 0
    GREEN = 1


class Source:
    def gen(self, n: int32) -> Iterator[int32]:
        i: int32 = 0
        while i < n:
            if i == caps.CAP:           # module-constant access in a method generator
                break
            if i == Color.GREEN.value:  # enum-member access in a generator body
                print("green")
            yield i
            i += 1


def main() -> None:
    s = Source()
    for x in s.gen(10):
        print(x)


main()
