# The container-NAME comprehension element rejects when the source is DEAD after
# the comprehension: sema reads that last use as a move and drops the "copies
# ... into owned storage" warning, but the element renders once per iteration,
# so the slots would copy N times with nothing said. CPython aliases one list
# into every slot, so the unwarned copy would be a silent divergence.
from tpy import Int32, Own


def mk() -> Own[list[Int32]]:
    return [1, 2]


def main() -> None:
    xs = mk()
    ls: list[list[Int32]] = [xs for i in range(2)]  # tpyc: error(/expr.list_comp/)
    print(len(ls))


main()
