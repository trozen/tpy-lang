# Static evaluation of isinstance() on non-union variables.
# Uses the declared type to fold the check to a compile-time bool.
from tpy import Int32


class A:
    x: int
    def __init__(self, x: int) -> None:
        self.x = x


class B:
    y: int
    def __init__(self, y: int) -> None:
        self.y = y


def match_int(x: Int32) -> bool:
    return isinstance(x, Int32)


def mismatch(x: Int32) -> bool:
    return isinstance(x, A)


def tuple_contains(x: Int32) -> bool:
    return isinstance(x, (A, Int32))


def tuple_miss(x: Int32) -> bool:
    return isinstance(x, (A, B))


def record_self(a: A) -> int:
    # Happy path used inside an if so codegen folds the branch.
    if isinstance(a, A):
        return a.x
    return -1


def record_other(a: A) -> int:
    if isinstance(a, B):
        return -1
    return a.x


def negate(x: Int32) -> bool:
    return not isinstance(x, A)


def compound(x: Int32, flag: bool) -> bool:
    return isinstance(x, Int32) and flag


def match_guard(x: Int32) -> str:
    match x:
        case _ if isinstance(x, Int32):
            return "int32"
        case _:
            return "other"


def main() -> None:
    print(match_int(Int32(1)))
    print(mismatch(Int32(1)))
    print(tuple_contains(Int32(1)))
    print(tuple_miss(Int32(1)))
    print(record_self(A(7)))
    print(record_other(A(8)))
    print(negate(Int32(1)))
    print(compound(Int32(1), True))
    print(compound(Int32(1), False))
    print(match_guard(Int32(1)))


main()
