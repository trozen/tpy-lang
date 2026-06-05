# Regression: current_ns must stay set across a simple-generator METHOD body
# (the record_name peephole path) and for enum-member access -- not only the
# free-function / module-constant cases.
from typing import Iterator
from enum import Enum
from tpy import Int32
import caps


class Color(Enum):
    RED = 0
    GREEN = 1


class Source:
    def gen(self, n: Int32) -> Iterator[Int32]:
        i: Int32 = 0
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
