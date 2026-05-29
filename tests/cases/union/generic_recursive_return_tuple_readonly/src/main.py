# tuple[readonly[Tree[T]], int] return: the per-element dangling check
# must see through readonly to recognize the wrapper shape. Pre-fix the
# tuple-element walk only tested `et.needs_wrapper()` on the raw type
# and ReadonlyType inherited False, producing a false-positive
# "returned by reference" rejection on a perfectly safe value-shape
# wrapper return.
from tpy import Int32, readonly

type Tree[T] = T | list[Tree[T]]


def f() -> tuple[readonly[Tree[Int32]], Int32]:
    leaf: Tree[Int32] = [1, 2]
    return (leaf, 0)


def main() -> None:
    t, n = f()
    print(n)


main()
