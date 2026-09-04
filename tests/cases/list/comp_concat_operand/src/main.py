# A comprehension as an OPERAND of a container dunder (list concat, the set
# operators): the stmt-expr renders inline inside the helper call, on either side.
from tpy import Own


def head_literal(xs: list[int]) -> Own[list[int]]:
    return [2] + [el for el in xs if el]  # tpyc: ok


def tail_literal(xs: list[int]) -> Own[list[int]]:
    return [el for el in xs if el] + [2]  # tpyc: ok


def both_comps(xs: list[int]) -> Own[list[int]]:
    return [el for el in xs] + [el * 2 for el in xs]  # tpyc: ok


def set_ops() -> int:
    base: set[int] = {1, 2, 3}
    grown = base | {int(x) for x in range(4) if x != 1}  # tpyc: ok
    shrunk = {int(x) for x in range(4)} - base  # tpyc: ok
    return len(grown) + len(shrunk)


def main():
    xs: list[int] = [0, 3, 5]
    print(head_literal(xs), tail_literal(xs), both_comps(xs), set_ops())


main()
