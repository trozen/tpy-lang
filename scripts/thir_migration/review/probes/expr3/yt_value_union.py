from typing import Iterator
from tpy import int32
def gen(u: int32 | str) -> Iterator[tuple[int32, int32 | str]]:
    i = 0
    while i < 2:
        yield (i, u)
        i += 1
def main() -> None:
    for a, b in gen(3):
        print(a)
main()
