# print(*xs): a list / Array / Span / *args / temporary list unpacked into print,
# mixed with positional args, sep=/end=, empty sources and per-type element
# formatting, in a free function, a method and a loop body. The source is only
# read, so copy-vs-alias is not observable here; the snapshot pins the borrow.
from typing import Iterator
from tpy import Array, Span, int32


class P:
    def __init__(self, n: int) -> None:
        self.n = n

    def __str__(self) -> str:
        return f"P<{self.n}>"


def tag(name: str) -> None:
    print(name, end=": ")


def countdown(n: int32) -> Iterator[int32]:
    while n > 0:
        yield n
        n -= 1


# --- free function ---
def free_function() -> None:
    xs = [3, 1, 2]
    none: list[int] = []
    tag("fn.ints")
    print(*xs)  # tpyc: ok
    tag("fn.mixed")
    print("a", *xs, "b", sep=", ")  # tpyc: ok
    tag("fn.empty")
    print(*none)  # tpyc: ok
    tag("fn.empty_between")
    print("a", *none, "b", sep=", ")  # tpyc: ok
    tag("fn.empty_first")
    print(*none, "x")  # tpyc: ok
    tag("fn.empty_str_item")
    print("", *xs)  # tpyc: ok
    tag("fn.end")
    print(*xs, end="!\n")  # tpyc: ok
    tag("fn.sep_end")
    print(*xs, sep="-", end="")  # tpyc: ok
    print()
    tag("fn.two_stars")
    print(*xs, *xs)  # tpyc: ok
    tag("fn.stars_around_empty")
    print(*none, *xs, *none, sep="|")  # tpyc: ok
    tag("fn.str")
    print(*["s", "t"])  # tpyc: ok
    tag("fn.bool")
    print(*[True, False])  # tpyc: ok
    tag("fn.float")
    print(*[1.0, 2.5])  # tpyc: ok
    tag("fn.bytes")
    print(*[b"ab", b"c'd"])  # tpyc: ok
    tag("fn.record")
    print(*[P(1), P(2)])  # tpyc: ok
    tag("fn.nested_list")
    print(*[[1, 2], [3]])  # tpyc: ok
    arr: Array[int32, 3] = [4, 5, 6]
    tag("fn.array")
    print(*arr)  # tpyc: ok
    view: Span[int32] = arr
    tag("fn.span")
    print(*view)  # tpyc: ok
    tag("fn.temporary")
    print(*list(countdown(3)))  # tpyc: ok


# --- method ---
class Shelf:
    items: list[int]

    def __init__(self) -> None:
        self.items = [10, 20]

    def show(self, *extra: int) -> None:
        tag("method.field")
        print(*self.items)  # tpyc: ok
        tag("method.varargs")
        print("extra", *extra, sep=",")  # tpyc: ok
        tag("method.both")
        print(*self.items, *extra)  # tpyc: ok


# --- loop body ---
def loop_body() -> None:
    rows: list[list[int]] = [[1], [2, 3], []]
    for i in range(len(rows)):
        tag("loop.row")
        print(i, *rows[i])  # tpyc: ok


# --- generator body ---
def gen_body(xs: list[int]) -> Iterator[int]:
    tag("gen.param")
    print(*xs)  # tpyc: ok
    yield len(xs)


def main() -> None:
    free_function()
    Shelf().show(7, 8)
    loop_body()
    lengths = list(gen_body([5, 6]))
    print("gen.yield", lengths)


main()
