# Regression: a comprehension/genexpr element (or filter condition) whose
# codegen needs a statement-prelude temp -- here an Own[T] move-temp for the
# last-use loop var fed to Box(i) -- must emit that temp INSIDE the loop body
# (per iteration, loop var in scope), not at the enclosing statement scope.
# @nocopy Box turns any silent copy into a compile error, and the escaped
# prelude would reference the loop var where it is undeclared (C++ build error).
# Covers the array (array_from_index), vector (stmt-expr), and generator-
# expression (frame) lowerings, in element, condition, and walrus positions.
from tpy import int32
from tplib.box import Box


def is_small(b: Box[int32]) -> bool:
    return b < Box(3)


def score(b: Box[int32]) -> int32:
    return 1 if b < Box(3) else 0


def array_comp() -> None:
    xs = [Box(i) for i in range(4)]  # tpyc: type(/Array\[Box\[int32\], 4\]/)
    print(len(xs), xs[0].get(), xs[3].get())


def list_comp(n: int32) -> None:
    ys = [Box(i) for i in range(n)]
    print(len(ys), ys[0].get())


def genexpr(n: int32) -> None:
    # owned move-temp in BOTH the genexpr element (score(Box(i))) and its filter
    # condition (is_small(Box(i))) -- each must flush inside the frame body.
    print(sum(score(Box(i)) for i in range(n) if is_small(Box(i))))


def filtered(n: int32) -> None:
    # owned move-temp in a list-comp filter condition, re-evaluated per iteration
    zs = [i for i in range(n) if is_small(Box(i))]
    print(len(zs))


def walrus_owned(n: int32) -> None:
    # walrus target leaks to the enclosing scope (PEP 572) so its declaration
    # must stay at function scope, while the owned move-temp in the condition
    # expression must still land inside the loop body.
    zs = [x for x in range(n) if (y := score(Box(x))) > 0]
    print(len(zs), y)


def main() -> None:
    array_comp()
    list_comp(3)
    genexpr(6)
    filtered(6)
    walrus_owned(6)


main()
