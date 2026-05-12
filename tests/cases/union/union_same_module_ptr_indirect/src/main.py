# Regression: a user record providing pointer indirection via `_ptr: Ptr[T]`
# must be accepted as a cycle breaker even when declared in the same module
# as the recursive union alias that uses it.
#
# Previously rejected (false positive) because the indirection check ran
# before record registration. The check now runs after record/protocol
# registration so the field walk sees MyWrap's `_ptr: Ptr[T]` and recognizes
# the indirection.
from tpy import Ptr


class MyWrap[T]:
    _ptr: Ptr[T]


type Expr = Lit | BinOp


class Lit:
    value: int

    def __init__(self, value: int) -> None:
        self.value = value


class BinOp:
    op: int  # 1=add, 2=mul
    left: MyWrap[Expr]
    right: MyWrap[Expr]


def describe(e: Expr) -> str:
    match e:
        case Lit(value=v):
            return "lit=" + str(v)
        case _:
            return "binop"


def main() -> None:
    a: Expr = Lit(7)
    print(describe(a))


main()
