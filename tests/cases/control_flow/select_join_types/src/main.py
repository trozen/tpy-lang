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
    # Integer operands widen to the wider one (an int/float mix is refused:
    # control_flow/error_select_int_float_mix, operators/error_logical_int_float_mix).
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


def int_float_select(a: int32, big: int64, f: float, c: bool) -> None:
    # An explicit float() gives an int/float ternary its one type.
    x = float(a) if c else 2.5  # tpyc: ok type(float)
    print("int_float_converted:", x)
    # A declared float target converts the picked int arm (CPython keeps the
    # int 3, so the section prints a value both agree on).
    y: float = a if c else 2.5  # tpyc: ok
    print("int_float_declared:", y / 2)
    # The same declared float target converts the picked int `or` operand.
    v: float = a or 2.5  # tpyc: ok
    print("int_float_or_declared:", v / 2)
    # As a condition an int/float `or` only tests each operand.
    if a or 2.5:  # tpyc: ok
        print("int_float_or_condition")
    # Integer arms still widen, and float arms still join.
    w = a if c else big  # tpyc: ok type(int64)
    print("int_widen_ternary:", w)
    g = f if c else 2.5  # tpyc: ok type(float)
    print("float_ternary:", g)
    # An annotated empty list converts an int element; an unannotated one
    # first stored a literal still widens across integer elements.
    fs: list[float] = []
    fs.append(a)  # tpyc: ok
    print("int_float_declared_list:", fs[0] / 2)
    ws = []  # tpyc: type(list[int64])
    ws.append(1)
    ws.append(big)
    print("int_widen_usage:", ws[0], ws[1])


def half_ternary(a: int32, c: bool) -> float:
    # The declared float return converts the picked int arm.
    return a if c else 2.5  # tpyc: ok


def half_or(a: int32) -> float:
    # The declared float return converts the picked int `or` operand.
    return a or 2.5  # tpyc: ok


def declared_containers(c: bool) -> None:
    # A declared slot pins each literal operand before the join, so an int
    # element converts like it does in a lone literal.
    y: list[float] = [1] or [2.5]  # tpyc: ok
    print("declared_list_or:", len(y), y[0] / 2)
    e: list[float] = [] and [2.5]  # tpyc: ok
    print("declared_list_and:", len(e))
    d: dict[str, float] = {"a": 1} or {"b": 2.5}  # tpyc: ok
    print("declared_dict_or:", len(d), d["a"] / 2)
    s: set[float] = {1} or {2.5}  # tpyc: ok
    print("declared_set_or:", len(s))
    t: tuple[float, str] = (1, "x") if c else (2.5, "y")  # tpyc: ok
    print("declared_tuple_ternary:", t[0] / 2, t[1])


def tuple_literal_arm(f: bool) -> tuple[int32, str]:
    t = (3, "c")
    # A literal tuple arm pins to the declared return tuple beside a name arm.
    return t if f else (4, "d")  # tpyc: ok


def int_float_optional_slot(a: int32, c: bool) -> None:
    # A declared `float | None` slot converts the picked int like `float`.
    o: float | None = a or 2.5  # tpyc: ok
    p: float | None = a if c else 2.5  # tpyc: ok
    if o is not None and p is not None:
        print("int_float_optional_slot:", o / 2, p / 2)


def show_float(tag: str, f: float) -> None:
    print(tag, f / 2)


def int_float_slots(a: int32, c: bool) -> None:
    print("int_float_return:", half_ternary(a, c) / 2, half_or(a) / 2)
    # A float parameter converts the picked int operand.
    show_float("int_float_arg_ternary:", a if c else 2.5)  # tpyc: ok
    show_float("int_float_arg_or:", a or 2.5)  # tpyc: ok
    # `and` picks its int operand when that one is falsy.
    v: float = a and 2.5  # tpyc: ok
    print("int_float_and_declared:", v / 2)
    # The declared slot reaches a nested select too.
    w: float = a or (a or 2.5)  # tpyc: ok
    print("int_float_nested_declared:", w / 2)
    # ... a nested ternary as well.
    w2: float = a or (a if c else 2.5)  # tpyc: ok
    print("int_float_nested_ternary_declared:", w2 / 2)
    # An int literal arm converts like a typed int one.
    z: float = 3 if c else 2.5  # tpyc: ok
    print("int_literal_float_declared:", z / 2)


def int_float_guard(a: int32, g: float, k: int32) -> str:
    match k:
        # A guard is a truth test; each guarded arm returns.
        case 1 if a or g:  # tpyc: ok
            return "one"
        case _ if a or g:  # tpyc: ok
            return "any"
        case _:
            return "none"


def int_float_conditions(a: int32, g: float, c: bool) -> None:
    # Each truth test takes its int and float operands one by one.
    n = 0
    while a or g:  # tpyc: ok
        n += 1
        if n == 2:
            break
    print("int_float_while:", n)
    print("int_float_not:", not (a or g))  # tpyc: ok
    ys = [x for x in [1, 2, 3] if a or g]  # tpyc: ok
    print("int_float_filter:", len(ys))
    if a or g:  # tpyc: ok
        assert a or g  # tpyc: ok
        print("int_float_assert")
    print("int_float_bool:", bool(a or g), bool(a and g))  # tpyc: ok
    # A ternary's test.
    print("int_float_ternary_test:", 1 if a or g else 2)  # tpyc: ok
    # A walrus leaf binds its own operand, not the `or`.
    if (m := a) or g:  # tpyc: ok
        print("int_float_walrus_leaf:", m)
    # A generator-expression filter.
    print("int_float_genexpr:", sum(1 for x in [1, 2, 3] if a or g))  # tpyc: ok
    print("int_float_guard:", int_float_guard(a, g, 1),
          int_float_guard(a, g, 2))
    # A truth test reaches a ternary's arms, each tested on its own.
    if (a or g) if c else g:  # tpyc: ok
        print("int_float_ternary_arms")
    print("int_float_ternary_arms_bool:", bool(a if c else g))  # tpyc: ok


def mixed_truth_arms(n: int32, c: bool, r: C, xs: list[int32],
                     s: str) -> None:
    # A truth test takes arms of unrelated types, each on its own.
    if n if c else s:  # tpyc: ok
        print("mixed_arms_int_str")
    if r if c else xs:  # tpyc: ok
        print("mixed_arms_record_list")
    print("mixed_arms_bool:", bool(xs if c else s))  # tpyc: ok


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
    for c in [True, False]:
        declared_containers(c)
    print("tuple_literal_arm:", tuple_literal_arm(True), tuple_literal_arm(False))
    for a, c in [(3, True), (0, False)]:
        int_float_select(a, 5000000000, 1.5, c)
        int_float_slots(a, c)
        int_float_optional_slot(a, c)
    for a, g, c in [(3, 1.5, True), (0, 1.5, False), (0, 0.0, True),
                    (3, 0.0, False)]:
        int_float_conditions(a, g, c)
    for c in [True, False]:
        mixed_truth_arms(0, c, C(1), [], "")
        mixed_truth_arms(2, c, C(1), [1], "s")
    not_or(C(1))
    for c in [True, False]:
        readonly_join(c, C(1), C(2))


main()
