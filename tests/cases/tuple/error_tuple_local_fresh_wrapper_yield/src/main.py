# Same as the return case for yield: a fresh wrapper member of a tuple local
# yielded by bare name dangles and must be rejected.
from typing import Iterator
from tpy import int32

type Tree[T] = T | list[Tree[T]]


def g() -> Iterator[tuple[Tree[int32], int32]]:
    leaf: Tree[int32] = 5
    pair = (leaf, 0)
    yield pair  # tpyc: error(/owns a freshly constructed value/)


def main() -> None:
    for _ in g():
        pass


main()
