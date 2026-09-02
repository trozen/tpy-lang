from typing import Iterator
from tpy import Int32
def make_pair(i: Int32) -> tuple[Int32, Int32]:
    return (i, i * 2)
def g() -> Iterator[Int32]:
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
