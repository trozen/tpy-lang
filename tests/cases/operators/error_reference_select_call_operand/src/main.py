# A CALL as the left operand of an `or` over reference types: the select's
# operand slice is names, nested selects and literals, so the call rejects.
from tpy import int32


def pick(a: list[int32]) -> list[int32]:
    return a


def f(a: list[int32], b: list[int32]) -> None:
    x = pick(a) or b  # tpyc: error(/binop\.shape/)
    x.append(6)
    print(len(a), len(b))


def main() -> None:
    f([1], [2])


main()
