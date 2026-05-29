# The same generic recursive alias instantiated with a different type arg
# (Tree[str]) -- pins that one template serves multiple instantiations.
from tpy import Int32

type Tree[T] = T | list[Tree[T]]


def depth[T](t: Tree[T]) -> Int32:
    match t:
        case list() as branches:
            best = 0
            for child in branches:
                d = depth(child)
                if d > best:
                    best = d
            return best + 1
        case _:
            return 0


def main() -> None:
    t: Tree[str] = ["a", ["b", ["c", "d"]], "e"]
    print(depth(t))
    leaf: Tree[str] = "x"
    print(depth(leaf))


main()
