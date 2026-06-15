from tpy import Int32

type Tree[T] = T | list[Tree[T]]


def leaf_count[T](t: Tree[T]) -> Int32:
    match t:
        case list() as branches:
            total = 0
            for child in branches:
                total += leaf_count(child)
            return total
        case _:
            return 1
