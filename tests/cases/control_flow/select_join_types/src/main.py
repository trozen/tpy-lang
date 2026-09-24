# A ternary and an and/or share one result-type join: Ref/Own peel, and a
# literal or empty container takes the other operand's type.
from tpy import int32, int64, uint8, Own, readonly


class C:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class F:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __bool__(self) -> bool:
        return self.n != 0


def make_c() -> Own[C]:
    return C(10)


def make_f() -> Own[F]:
    return F(20)


def record_or(a: C, f: F) -> None:
    # A record without __bool__ is always truthy: the select aliases `a`.
    r = a or make_c()  # tpyc: ok
    r.n += 1
    print("record_or_alias:", r.n, a.n)
    # A falsy __bool__ record picks the fresh RHS; `f` stays untouched.
    g = f or make_f()  # tpyc: ok
    g.n += 1
    print("record_or_fresh:", g.n, f.n)


def list_or() -> None:
    e: list[int32] = []
    # Empty LHS: a fresh list, `e` untouched.
    x = e or [5]  # tpyc: ok
    x.append(1)
    print("list_or_fresh:", len(x), len(e))
    e.append(7)
    # Non-empty LHS: `y` aliases `e`.
    y = e or [5]  # tpyc: ok
    y.append(8)
    print("list_or_alias:", len(y), len(e))


def int64_literal_arm(c: bool) -> None:
    e64: list[int64] = []
    # The literal takes the other operand's element type, not the default int.
    x = e64 or [5]  # tpyc: ok type(list[int64])
    x.append(1099511627776)
    print("int64_or:", x[0], x[1], len(e64))
    y = e64 if c else [5]  # tpyc: ok type(list[int64])
    y.append(2199023255552)
    print("int64_ternary:", len(y), len(e64))


def empty_literal_arm(c: bool) -> None:
    xs: list[int32] = [1]
    d: dict[str, int32] = {}
    st: set[int32] = set()
    # An empty literal takes its whole type from the other operand.
    a = xs or []  # tpyc: ok
    a.append(2)
    print("empty_list_or:", len(a), len(xs))
    b = d or {}  # tpyc: ok
    b["k"] = 1
    print("empty_dict_or:", len(b), len(d))
    s = st or set()  # tpyc: ok
    s.add(3)
    print("empty_set_or:", len(s), len(st))
    t = d if c else {}  # tpyc: ok
    t["j"] = 2
    print("empty_dict_ternary:", len(t), len(d))


def method_result_arm(c: bool) -> None:
    b = [1]
    # Keeps `b` a list: `.copy()` on an Array is BUGS.md#array-copy-dispatches-to-list-copy.
    b.append(4)
    # `copy()` of a literal-seeded local is a plain list[int32].
    z = b.copy() if c else [2]  # tpyc: ok
    z.append(3)
    print("copy_ternary:", len(z), len(b))


def literal_left() -> None:
    e: list[int32] = [9]
    # A non-empty literal is truthy: the fresh literal, `e` untouched.
    x = [5] or e  # tpyc: ok
    x.append(6)
    print("literal_left:", len(x), len(e))


def nested_select() -> None:
    e: list[int32] = []
    f: list[int32] = []
    # Both empty: the chain falls through to the fresh literal.
    x = e or f or [7]  # tpyc: ok
    x.append(8)
    print("nested_or:", len(x), len(e), len(f))
    f.append(1)
    y = e or f or [7]  # tpyc: ok
    y.append(2)
    print("nested_or_alias:", len(y), len(f))


def int_widen_or() -> None:
    a: int32 = 0
    b: int64 = 5000000000
    # Integer operands widen to the wider one (an int/float mix stays bool).
    x = a or b  # tpyc: ok type(int64)
    print("int_widen_or:", x)
    u: uint8 = 0
    # A literal takes the operand's fixed width when it fits.
    z = u or 200  # tpyc: ok type(uint8)
    print("int_literal_fits:", z)


def int_literal_pair(c: bool) -> None:
    # Two int literals join at the wider of their own defaults, in either
    # order: the big literal makes the result an int (BigInt).
    x = 0 or 10000000000  # tpyc: warning(/outside default int32 range; inferring int/)
    y = 10000000000 or 0  # tpyc: warning(/outside default int32 range; inferring int/)
    z = 0 if c else 10000000000  # tpyc: warning(/outside default int32 range; inferring int/)
    print("int_literal_pair:", x, y, z)


def float_literal_elem(c: bool) -> None:
    ef: list[float] = []
    # A float literal element fits the sibling's `list[float]`.
    y = ef if c else [2.5]  # tpyc: ok type(list[float])
    y.append(1.0)
    print("float_literal_elem:", len(y), len(ef))


def float_or(f: float) -> None:
    # A float literal takes the float operand's type.
    x = f or 2.5  # tpyc: ok type(float)
    print("float_or:", x)


def not_or(a: C) -> None:
    # `not` over an and/or of records without __bool__: always truthy.
    print("not_or:", not (a or make_c()))  # tpyc: ok
    a.n += 1
    print("not_or_after:", a.n)


def readonly_join(c: bool, ro: readonly[C], plain: C) -> None:
    # A readonly operand keeps the join readonly; reads go through it and
    # see a later write to the chosen operand.
    r = ro if c else plain  # tpyc: ok
    print("readonly_join:", r.n)
    plain.n += 5
    print("readonly_join_after:", r.n)


def main() -> None:
    record_or(C(1), F(0))
    list_or()
    int64_literal_arm(True)
    empty_literal_arm(False)
    method_result_arm(True)
    literal_left()
    nested_select()
    int_widen_or()
    for c in [True, False]:
        int_literal_pair(c)
        float_literal_elem(c)
    float_or(0.0)
    float_or(1.5)
    not_or(C(1))
    for c in [True, False]:
        readonly_join(c, C(1), C(2))


main()
