# @error_return on a readonly[Tree[T]] reference return: the wrapper follows
# the reference-type convention, so the success value is a const reference and
# the std::expected payload is val_or_ref<const Tree<T>>. The accessor returns
# a const reference to existing wrapper data (self.t); a fresh value would need
# Own. The result is consumed read-only so the const payload is exercised.
from tpy import Int32, readonly, Own, error_return, ReturnException


class E(Exception, ReturnException):
    pass


type Tree[T] = T | list[Tree[T]]


def leaf_count(t: readonly[Tree[Int32]]) -> Int32:
    match t:
        case list() as branches:
            n = 0
            for c in branches:
                n += leaf_count(c)
            return n
        case _:
            return 1


class Holder:
    t: Tree[Int32]

    def __init__(self, t: Own[Tree[Int32]]) -> None:
        self.t = t

    @error_return(E)
    def view(self) -> readonly[Tree[Int32]]:
        return self.t


def main() -> None:
    h = Holder([1, 2])
    try:
        v = h.view()
        print(leaf_count(v))
    except E:
        print("err")


main()
