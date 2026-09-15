# Subscripting an isinstance-narrowed union member container: the ELEMENT's own
# form rules decide, not the receiver's union provenance. A value element (int32,
# dict value, str) is a value; a reference element (record, nested container)
# stays a borrow, so mutation through it is visible in the caller.
from tpy import int32


class Point:
    def __init__(self, n: int32) -> None:
        self.n = n


# free function: a scalar element lands straight in a value-typed return.
def scalar_elem(x: str | list[int32]) -> int32:
    if isinstance(x, str):
        return len(x)
    return x[0]  # tpyc: ok


# free function: the dict-valued member reads its value element the same way.
def dict_elem(x: str | dict[str, int32]) -> int32:
    if isinstance(x, str):
        return len(x)
    return x["k"]  # tpyc: ok


# free function: a str element binds a str local and returns it owned.
def str_elem(x: int32 | list[str]) -> str:
    if isinstance(x, int32):
        return "n"
    s = x[0]  # tpyc: ok
    return s


# free function: a RECORD element is a borrow -- the caller sees the mutation.
def rec_elem(x: str | list[Point]) -> None:
    if isinstance(x, str):
        return
    p = x[0]  # tpyc: ok
    p.n += 10


# free function: a nested-container element is a borrow for the same reason.
def nested_elem(x: str | list[list[int32]]) -> None:
    if isinstance(x, str):
        return
    row = x[0]  # tpyc: ok
    row.append(9)


# comprehension: the same element read inside a comprehension body.
def doubled(x: str | list[int32]) -> int32:
    if isinstance(x, str):
        return 0
    return sum([x[i] * 2 for i in range(2)])  # tpyc: ok


def drive(xs: list[int32], ss: list[str], pts: list[Point],
          rows: list[list[int32]], d: dict[str, int32]) -> None:
    print("scalar", scalar_elem(xs))
    print("dict", dict_elem(d))
    print("str", str_elem(ss))
    rec_elem(pts)
    print("rec", pts[0].n)
    nested_elem(rows)
    print("nested", rows[0])
    print("comp", doubled(xs))


def main() -> None:
    drive([7, 8], ["hi", "yo"], [Point(1), Point(2)], [[1, 2]], {"k": 3})


main()
