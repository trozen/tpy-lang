# Stepped slice assignment: a[::step] = values (must match length for step != 1).
from tpy import Int32
from typing import Iterator

def gen3() -> Iterator[Int32]:
    yield 10
    yield 20
    yield 30

def main() -> None:
    # Every other element
    a: list[Int32] = [1, 2, 3, 4, 5]
    a[::2] = [10, 20, 30]
    for x in a:
        print(x)

    # Negative step: reverse order positions
    b: list[Int32] = [1, 2, 3, 4, 5]
    b[::-2] = [50, 30, 10]
    for x in b:
        print(x)

    # Step with start/stop
    c: list[Int32] = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
    c[1:8:3] = [100, 200, 300]
    for x in c:
        print(x)

    # Step=1 allows resize (same as basic slice)
    d: list[Int32] = [1, 2, 3, 4, 5]
    d[1:4:1] = [10, 20]
    for x in d:
        print(x)

    # Generator as RHS
    e: list[Int32] = [1, 2, 3, 4, 5]
    e[::2] = gen3()
    for x in e:
        print(x)

main()
