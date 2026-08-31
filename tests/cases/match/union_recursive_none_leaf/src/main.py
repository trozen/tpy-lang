# A generic recursive union alias instantiated with `None` as its leaf
# (Tree[None]), matched at three subject positions: a parameter, a local,
# and a list element. Sema used to reject a class pattern on this subject.
# Do not add a `None` value or switch the mutation to append/pop -- each
# folds the body at a recursive-alias slot, costing the case its routing.
from tpy import Int32

type Tree[T] = T | list[Tree[T]]


def branch_count(t: Tree[None]) -> Int32:
    match t:
        case None:  # tpyc: ok
            return 0
        case list() as branches:  # tpyc: ok
            total = 1
            for child in branches:
                total += branch_count(child)
            return total


def main() -> None:
    forest: Tree[None] = [[], []]
    print(branch_count(forest))
    match forest:
        case list() as branches:  # tpyc: ok
            for child in branches:
                match child:  # a list element is a wrapper subject too
                    case None:  # tpyc: ok
                        print("hole")
                    case list():  # tpyc: ok
                        print("branch")
            branches.clear()  # mutates `forest` itself, not a copy
        case None:
            print("no branch")
    print(branch_count(forest))


main()
