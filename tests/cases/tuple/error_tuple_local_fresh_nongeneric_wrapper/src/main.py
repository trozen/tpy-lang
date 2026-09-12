# A fresh NON-generic recursive-union-wrapper member of a tuple local, returned
# by bare name, must be rejected -- exercises the UnionType wrapper carrier
# (vs the generic Tree[T] RecursiveAliasInstanceType carrier).
from tpy import int32

type Expr = int32 | list[Expr]


def f() -> tuple[Expr, int32]:
    leaf: Expr = 5
    pair = (leaf, 0)
    return pair  # tpyc: error(/owns a freshly constructed value/)


def main() -> None:
    pass


main()
