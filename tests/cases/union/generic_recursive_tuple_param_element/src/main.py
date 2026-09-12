# A wrapper tuple element is borrow form (`Tree<int32_t>&`); returning an
# existing wrapper reference (a parameter) as that element is safe -- no Own.
from tpy import int32

type Tree[T] = T | list[Tree[T]]


def count(t: Tree[int32]) -> int32:
    match t:
        case list() as branches:
            n = 0
            for c in branches:
                n += count(c)
            return n
        case _:
            return 1


def pair(t: Tree[int32]) -> tuple[Tree[int32], int32]:
    return (t, 0)


def main() -> None:
    seed: Tree[int32] = [1, [2, 3]]
    a, n = pair(seed)
    print(count(a) + n)


main()
