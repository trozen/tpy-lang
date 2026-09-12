from typing import Iterator
from tpy import int32
def make_pair(i: int32) -> tuple[int32, int32]:
    return (i, i * 2)
def g() -> Iterator[int32]:
    yield -1
    i = 1
    t = make_pair(0)
    while i < 3:
        yield (t := make_pair(i))[0]
        print(t[1])
        i += 1
def main() -> None:
    for a in g():
        print(a)
main()
