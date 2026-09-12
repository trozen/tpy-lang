# A comprehension declared in BOTH arms of an if and read after it: sema hoists
# the binding, a pre-declare-then-assign shape the reseat rows do not model.
# TPy rejects this function (the `else` branch) today.
from tpy import int32


def pick(k: int32) -> int32:
    if k > 0:
        ys = [i + 1 for i in range(4)]
    else:
        ys = [i + 2 for i in range(4)]  # tpyc: error(/expr.list_comp/)
    return len(ys)


def main() -> None:
    print(pick(1))


main()
