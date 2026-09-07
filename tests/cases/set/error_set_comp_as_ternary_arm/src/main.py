# A SET comprehension as a ternary arm: the comprehension-as-arm rung renders
# the list flavor only, so the set spelling has no arm and is rejected.
from tpy import Int32


def f(cond: bool, xs: list[Int32]) -> Int32:
    ys = {i + 1 for i in xs} if cond else {0}  # tpyc: error(/set_comprehension/)
    return len(ys)


def main() -> None:
    print(f(True, [1, 2]))


main()
