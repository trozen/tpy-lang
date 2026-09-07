# A REASSIGNED `Own[str]` parameter: the concat-assign peephole keys on the
# declared type, which the Own wrapper fails, so the reassign arm rejects.
from tpy import Own


def grow(v: Own[str]) -> str:
    v = v + "x"  # tpyc: error(/binop.shape/)
    return v


def main() -> None:
    print(grow("a"))


main()
