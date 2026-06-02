# Regression: match on a hoisted (pointer-form) recursive-union wrapper local
# derefs the subject -- was `.value` accessed on a Tree<T>* (ill-formed C++).
# Also guards aliasing: a mutation through the arm reaches the source.
from tpy import Int32

type Tree[T] = T | list[Tree[T]]


def grow(src: Tree[Int32]) -> None:
    flag = True
    if flag:
        v = src
    else:
        v = src
    match v:               # v lowers to a hoisted pointer-local Tree<Int32>*
        case list() as b:
            b.append(9)    # mutate through the arm -- must reach src
        case _:
            pass


def main() -> None:
    tree: Tree[Int32] = [1, 2, 3]
    grow(tree)
    match tree:            # aliasing: tree grew to [1, 2, 3, 9]
        case list() as t:
            print(len(t))
        case _:
            print(0)


main()
