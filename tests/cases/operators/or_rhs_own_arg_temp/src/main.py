# The RHS of an `or` carrying an owning call argument: the argument temp is
# hoisted ahead of the short-circuit test, which is a filed defect
# (BUGS.md#short-circuit-hoists-own-arg-copy). This case pins the current
# render; its left side is deliberately unrelated to `xs`, so the eager copy
# is unobservable here and the case passes.
from tpy import Int32, Own


def take(o: Own[list[Int32]]) -> bool:
    return True


def either(b: bool, xs: list[Int32]) -> bool:
    return b or take(xs)  # tpyc: warning(/copies list\[Int32\] into owned storage/)


def main() -> None:
    print(either(True, [1]), either(False, [2]))


main()
