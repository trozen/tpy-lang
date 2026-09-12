# readonly[Tree[T]] param + Own[Tree[T]] tuple-element return. Like any
# reference type, a wrapper tuple element is borrow form, so a fresh value
# needs Own[] (lowers by value); the readonly wrapper param exercises const
# wrapper access (const Tree<int32_t>&) alongside the by-value Own tuple return.
from tpy import int32, readonly, Own

type Tree[T] = T | list[Tree[T]]


def leaf_count(t: readonly[Tree[int32]]) -> int32:
    match t:
        case list() as branches:
            n = 0
            for c in branches:
                n += leaf_count(c)
            return n
        case _:
            return 1


def f() -> tuple[Own[Tree[int32]], int32]:
    return ([1, 2], 0)


def main() -> None:
    t, n = f()
    print(n)
    print(leaf_count(t))


main()
