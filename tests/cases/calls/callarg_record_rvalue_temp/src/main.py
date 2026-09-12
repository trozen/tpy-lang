# Record-ctor rvalues into same-record ref slots hoist named temps; each
# call constructs a fresh object, so the callee prints its own mutation.
from tpy import int32


class A:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def take_rec(r: A) -> int32:
    return r.x


def mutate_rec(r: A) -> None:
    r.x += 1
    print(r.x)


def use_ret() -> int32:
    return take_rec(A(7))


def use_decl() -> int32:
    r = take_rec(A(8))
    return r


def use_stmt() -> None:
    mutate_rec(A(9))


def main() -> None:
    print(use_ret())
    print(use_decl())
    use_stmt()


main()
