# Cycle detection should accept a recursive union broken by a user-defined
# generic record (not Box, not tplib) whose only indirection is a `Ptr[T]`
# field. The compiler infers indirection structurally by walking MyWrap's
# fields under T=Expr; the `_ptr: Ptr[Expr]` field hits the PtrType branch.
#
# The test exercises sema acceptance only -- MyWrap is a no-init stub here
# (cycle detection is structural, not value-construction). Compiles + runs
# the wrapper-struct codegen for the recursive variant.
from mywrap import MyWrap


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
    a: Expr = Lit(1)
    print(describe(a))


main()
