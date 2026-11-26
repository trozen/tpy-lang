# Per-iteration temporaries keep their place: an element / filter operand
# and a clause-1 iterable's argument are made once per iteration (a counting
# callee shows a hoist out of the loop), clause 0's source once.
from tpy import Array, Own, int32


class Src:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


calls: list[int32] = [0]


def mk(x: int32) -> Own[Src]:
    calls[0] += 1
    return Src(x)


def val(s: Src) -> int32:
    return s.n


def ids(*xs: int32) -> Own[list[int32]]:
    calls[0] += 1
    return list(xs)


def main() -> None:
    xs = [0, 2, 3]
    calls[0] = 0
    # A held `or` operand with a fresh root, in the element.
    r = [val(mk(x)) or 9 for x in xs]  # tpyc: ok
    print("temps_elem", r, calls[0])
    calls[0] = 0
    # ... in a filter.
    r = [x for x in xs if val(mk(x)) or 0]  # tpyc: ok
    print("temps_filter", r, calls[0])
    calls[0] = 0
    # A `*args` pack in a clause-1 iterable, made once per outer element.
    r = [y for x in xs for y in ids(x, x + 1)]  # tpyc: ok
    print("temps_clause1", r, calls[0])
    array_temps([0, 2, 3], [(0, 5), (2, 6)], 70)


def array_temps(arr: Array[int32, 3], ps: Array[tuple[int32, int32], 2],
                x: int32) -> None:
    # The Array arms make an element operand per index, in the per-index
    # lambda: the hidden x is each element, never the parameter.
    calls[0] = 0
    a = [val(mk(x)) or 9 for x in arr]  # tpyc: ok
    print("temps_array", a[0], a[1], a[2], calls[0], x)
    calls[0] = 0
    b = [val(mk(x)) or val(mk(y)) for x, y in ps]  # tpyc: ok
    print("temps_array_unpack", b[0], b[1], calls[0])
    calls[0] = 0
    c = [val(mk(i)) or 9 for i in range(3)]  # tpyc: ok
    print("temps_array_range", c[0], c[1], c[2], calls[0])
    # An `or` operand inside a conditional-expression arm is made only when
    # the arm is taken; the list-source twin follows.
    calls[0] = 0
    d = [(val(mk(x)) or 5) if x > 1 else 0 for x in arr]  # tpyc: ok
    print("temps_array_arm", d[0], d[1], d[2], calls[0])
    calls[0] = 0
    e = [(val(mk(x)) or 5) if x > 1 else 0 for x in list(arr)]  # tpyc: ok
    print("temps_list_arm", e, calls[0])
