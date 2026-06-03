# Same as the return case for yield: a fresh wrapper member of a tuple local
# yielded by bare name dangles and must be rejected.
from typing import Iterator
from tpy import Int32

type Tree[T] = T | list[Tree[T]]


def g() -> Iterator[tuple[Tree[Int32], Int32]]:
    leaf: Tree[Int32] = 5
    pair = (leaf, 0)
    yield pair  # tpyc: error(/owns a freshly constructed value/)


def main() -> None:
    for _ in g():
        pass


main()
