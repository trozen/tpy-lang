# Generic recursive union alias instantiated with a UNION leaf
# (Tree[Int32 | str]). The wrapper's variant arms are the leaf union and the
# list branch, so an arm naming one of those is a real member and must keep
# compiling -- only an arm naming a type INSIDE the leaf union (`case Int32()`)
# has no variant to dispatch on. The list capture aliases the subject, so the
# append below is visible through `forest` afterwards.
from tpy import Int32

type Tree[T] = T | list[Tree[T]]


def leaf_count(t: Tree[Int32 | str]) -> Int32:
    match t:
        case list() as branches:  # tpyc: ok
            total = 0
            for child in branches:
                total += leaf_count(child)
            return total
        case _:
            return 1


def main() -> None:
    forest: Tree[Int32 | str] = [1, "a", [2, "b"]]
    print(leaf_count(forest))
    match forest:
        case list() as branches:  # tpyc: ok
            branches.append(3)  # mutates `forest` itself, not a copy
        case _:
            print("leaf")
    print(leaf_count(forest))
    leaf: Tree[Int32 | str] = "solo"
    print(leaf_count(leaf))


main()
