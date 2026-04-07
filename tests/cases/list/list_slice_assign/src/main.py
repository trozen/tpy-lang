# List slice assignment: a[x:y] = rhs. Tests replace, resize, delete, insert, negative indices,
# and iterable RHS (generator).
from tpy import Int32
from typing import Iterator

def gen_values() -> Iterator[Int32]:
    yield 10
    yield 20

def main() -> None:
    a: list[Int32] = [1, 2, 3, 4, 5]

    # Replace same length
    a[1:3] = [10, 20]
    for x in a:
        print(x)

    # Replace with longer (resize)
    a[1:3] = [10, 20, 30, 40]
    print(len(a))

    # Delete elements
    a[1:4] = []
    for x in a:
        print(x)

    # Insert at position (start == stop)
    a[1:1] = [99, 98]
    for x in a:
        print(x)

    # Negative indices
    b: list[Int32] = [1, 2, 3, 4, 5]
    b[-2:] = [100, 200]
    for x in b:
        print(x)

    # Full slice replace
    c: list[Int32] = [1, 2, 3]
    c[:] = [10, 20]
    for x in c:
        print(x)

    # Generator as RHS
    d: list[Int32] = [1, 2, 3, 4, 5]
    d[1:3] = gen_values()
    for x in d:
        print(x)

main()
