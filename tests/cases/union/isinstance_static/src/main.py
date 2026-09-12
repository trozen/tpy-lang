# Static evaluation of isinstance() on non-union variables.
# Uses the declared type to fold the check to a compile-time bool.
from tpy import int32


class A:
    x: int
    def __init__(self, x: int) -> None:
        self.x = x


class B:
    y: int
    def __init__(self, y: int) -> None:
        self.y = y


def match_int(x: int32) -> bool:
    return isinstance(x, int32)


def mismatch(x: int32) -> bool:
    return isinstance(x, A)


def tuple_contains(x: int32) -> bool:
    return isinstance(x, (A, int32))


def tuple_miss(x: int32) -> bool:
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


def negate(x: int32) -> bool:
    return not isinstance(x, A)


def compound(x: int32, flag: bool) -> bool:
    return isinstance(x, int32) and flag


def match_guard(x: int32) -> str:
    match x:
        case _ if isinstance(x, int32):
            return "int32"
        case _:
            return "other"


def main() -> None:
    print(match_int(int32(1)))
    print(mismatch(int32(1)))
    print(tuple_contains(int32(1)))
    print(tuple_miss(int32(1)))
    print(record_self(A(7)))
    print(record_other(A(8)))
    print(negate(int32(1)))
    print(compound(int32(1), True))
    print(compound(int32(1), False))
    print(match_guard(int32(1)))


main()
