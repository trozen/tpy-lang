# Generic recursive alias as a tuple element of a return type. Like any
# reference type (record / list), a wrapper tuple element is borrow form
# (`Tree<int32_t>&`), so a fresh value needs Own[] (lowers by value); a bare
# `Tree[Int32]` element with a function-local value is rejected as dangling.
from tpy import Int32, Own

type Tree[T] = T | list[Tree[T]]


def make_pair() -> tuple[Own[Tree[Int32]], Int32]:
    return ([1, 2], 0)


def main() -> None:
    t, n = make_pair()
    print(n)


main()
