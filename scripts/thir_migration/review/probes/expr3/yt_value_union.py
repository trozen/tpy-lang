from typing import Iterator
from tpy import Int32
def gen(u: Int32 | str) -> Iterator[tuple[Int32, Int32 | str]]:
    i = 0
    while i < 2:
        yield (i, u)
        i += 1
def main() -> None:
    for a, b in gen(3):
        print(a)
main()
