# Non-fresh wrapper tuple members are accepted: a param-rooted wrapper in a tuple
# literal, and a fresh wrapper in an Own[] element (moved). Only fresh borrow members rejected.
from tpy import Int32, Own

type Tree[T] = T | list[Tree[T]]


def keep_param(t: Tree[Int32]) -> tuple[Tree[Int32], Int32]:
    return (t, 0)


def own_escape() -> tuple[Own[Tree[Int32]], Int32]:
    leaf: Tree[Int32] = 5
    pair = (leaf, 0)
    return pair


def count(t: Tree[Int32]) -> Int32:
    match t:
        case list() as b:
            return len(b)
        case _:
            return 1


def main() -> None:
    tree: Tree[Int32] = [1, 2]
    p = keep_param(tree)
    print(count(p[0]))
    q, n = own_escape()
    print(count(q))


main()
