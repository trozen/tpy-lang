# readonly[Tree[T]] return must work the same as bare Tree[T] -- the sema
# dangling-return check unwraps readonly before testing needs_wrapper, and
# codegen unwraps readonly before matching the list-alternative in the
# wrapper's std::variant.
from tpy import Int32, readonly

type Tree[T] = T | list[Tree[T]]


def f() -> readonly[Tree[Int32]]:
    return [1, 2]


def main() -> None:
    t = f()
    print("ok")


main()
