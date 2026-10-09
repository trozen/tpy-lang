# A comprehension variable hides a same-named pointer binding: a
# non-value-type global (module scope or a function), a pointer local in any body.
from typing import Callable

from tpy import Array

x = [10, 20, 30]

# List comprehension
r1 = [len(x) for x in ["a", "bb", "ccc"]]

# Set comprehension
r2 = {len(x) for x in ["a", "bb", "ccc"]}

# Dict comprehension
r3 = {x: len(x) for x in ["a", "bb", "ccc"]}

# Generator expression
r4 = list(len(x) for x in ["a", "bb", "ccc"])

# With filter condition
r5 = [len(x) for x in ["a", "bb", "ccc", "dd"] if len(x) > 1]

# Range-based (original bug repro from TODO)
r6 = [x * x for x in range(5)]

# Array comprehension path (range-based, promotes to std::array)
r7: Array[int, 5] = [x * x for x in range(5)]

class Items:
    items: list[int]

    def __init__(self, v: int) -> None:
        self.items = [v]

    def __enter__(self) -> list[int]:
        return self.items

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


class Rec:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n


def if_cond(rows: list[int]) -> None:
    # An `if` condition's comprehension variable is the body's pointer local.
    if len([ys for ys in rows]) > 1:  # tpyc: ok
        ys = rows
    else:
        return
    ys.append(9)
    print("if_cond:", ys, rows)


def dict_comp(rows: list[int]) -> None:
    # The same through a dict comprehension.
    if len({ys: ys * 2 for ys in rows}) > 1:  # tpyc: ok
        ys = rows
    else:
        return
    ys.append(9)
    print("dict_comp:", ys, rows)


def set_comp(rows: list[int]) -> None:
    # The same through a set comprehension.
    if len({ys + 1 for ys in rows}) > 1:  # tpyc: ok
        ys = rows
    else:
        return
    ys.append(9)
    print("set_comp:", ys, rows)


def genexpr(rows: list[int]) -> None:
    # A generator expression lowers as its own function (its variable is a
    # frame field), so it never meets the pointer-hide admission; this pins
    # that the enclosing body's later pointer local is unaffected by it.
    if sum(ys for ys in rows) > 1:  # tpyc: ok
        ys = rows
    else:
        return
    ys.append(9)
    print("genexpr:", ys, rows)


def unpack_head(d: dict[str, int]) -> None:
    # An unpacked comprehension target hides a pointer local live after it.
    with Items(1) as xs:
        ys = xs
    m = {k: ys * 2 for k, ys in d.items()}  # tpyc: ok
    ys.append(3)
    print("unpack_head:", m, ys, xs)


def reassigned_rec(a: Rec, b: Rec, rows: list[int]) -> None:
    # The hidden local is a reassigned record (a pointer slot).
    r = a
    r = b
    m = [r + 1 for r in rows]  # tpyc: ok
    r.n += 5
    print("reassigned_rec:", m, r.n, b.n)


def array_range() -> None:
    # The Array-demoted range comprehension.
    with Items(1) as xs:
        ys = xs
    m: Array[int, 3] = [ys * ys for ys in range(3)]  # tpyc: ok
    ys.append(5)
    print("array_range:", list(m), ys, xs)


def array_source(a: Array[int, 3]) -> None:
    # The Array-source comprehension.
    with Items(1) as xs:
        ys = xs
    m: Array[int, 3] = [ys * 2 for ys in a]  # tpyc: ok
    ys.append(3)
    print("array_source:", list(m), ys, xs)


def in_lambda(rows: list[int]) -> None:
    # A lambda body's comprehension.
    with Items(1) as xs:
        ys = xs
    f: Callable[[], int] = lambda: len([ys for ys in rows])  # tpyc: ok
    ys.append(3)
    print("in_lambda:", f(), ys, xs)


class Meth:
    v: int

    def __init__(self) -> None:
        self.v = 1

    def method(self, a: list[int], b: list[int], rows: list[int]) -> None:
        # A method body's comprehension hides a pointer local. The local is
        # reseated rather than hoisted from a `with` (a `with` here would
        # renumber every later function's context temporaries), and it is
        # read through `len` / subscript since printing it whole rejects
        # (BUGS.md#print-rebound-row-local-rejects).
        ys = a
        ys = b
        m = [ys + self.v for ys in rows]  # tpyc: ok
        ys.append(3)
        print("method:", m, len(ys), ys[-1], b)


def nested_def(rows: list[int]) -> None:
    # A nested def's comprehension hides the enclosing function's pointer local.
    with Items(1) as xs:
        ys = xs

    def inner() -> int:
        return len([ys for ys in rows])  # tpyc: ok

    ys.append(3)
    print("nested_def:", inner(), ys, xs)


def nested_comp(rows: list[list[int]]) -> None:
    # The inner comprehension of a nested one hides a pointer local.
    with Items(1) as xs:
        ys = xs
    m = [[ys * 2 for ys in r] for r in rows]  # tpyc: ok
    ys.append(3)
    print("nested_comp:", m, ys, xs)


def later_clause(rows: list[list[int]]) -> None:
    # A later `for` clause's variable hides a pointer local.
    with Items(1) as xs:
        ys = xs
    m = [ys for r in rows for ys in r if ys > 1]  # tpyc: ok
    ys.append(3)
    print("later_clause:", m, ys, xs)


def two_ptrs() -> None:
    # The hidden variable and the clause-0 iterable are both pointer locals.
    with Items(1) as xs:
        ys = xs
        zs = xs
    zs.append(2)
    m = [ys * 2 for ys in zs]  # tpyc: ok
    ys.append(3)
    print("two_ptrs:", m, ys, zs, xs)


row = [9]


def global_name(rows: list[list[int]]) -> None:
    # A function comprehension variable named like a module-level global.
    print("global_name:", [len(row) for row in rows])  # tpyc: ok
    row.append(1)
    print("global_name:", row)


def main() -> None:
    print(x)
    print(r1)
    print(r2)
    print(r3)
    print(r4)
    print(r5)
    print(r6)
    print(r7)
    if_cond([5, 6])
    dict_comp([5, 6])
    set_comp([5, 6])
    genexpr([5, 6])
    unpack_head({"a": 1, "b": 2})
    reassigned_rec(Rec(1), Rec(2), [1, 2])
    array_range()
    array_source([1, 2, 3])
    in_lambda([1, 2])
    Meth().method([0], [1], [1, 2])
    nested_def([1, 2])
    nested_comp([[1, 2], [3]])
    later_clause([[1, 2], [3]])
    two_ptrs()
    global_name([[1, 2], [3]])
    print("global_name:", row)

main()
