# A list / set / dict comprehension as a CONSTRUCTOR argument. At a borrow
# container slot it hoists the slot-typed `__tmp_N` the free-call position
# already renders (an lvalue, so a mutated slot binds it too); at an
# `Own[container]` slot (constructor, method, free function or a builtin
# stub's element slot) the stmt-expr prvalue moves in inline. Every Own
# section grows the stored list afterwards so a silent copy would show. The
# constructor call sits in each flushing position; the positions that hand
# it no flush keep rejecting (BUGS.md#ctor-call-arg-temp-flush-positions,
# one of them pinned as error_ctor_comprehension_arg_field_assign). Every
# statement that hoists a temp drains it BEFORE its own write, the string
# append included. The hoisted temp evaluates
# before its sibling arguments, so no section mixes it with a side-effecting
# one (BUGS.md#subexpression-right-to-left-eval).
from tpy import Int32, Own, ReturnException, error_return
from tplib import Rc


class Flat:
    n: Int32

    def __init__(self, data: list[bytes]) -> None:
        self.n = len(data)


class Grow:
    n: Int32

    def __init__(self, data: list[bytes]) -> None:
        data.append(b"z")
        self.n = len(data)


class Keep:
    data: list[bytes]

    def __init__(self, data: Own[list[bytes]]) -> None:
        self.data = data

    def reset(self, data: Own[list[bytes]]) -> None:
        self.data = data


class Tally:
    n: Int32

    def __init__(self, d: dict[str, Int32], s: set[Int32]) -> None:
        self.n = len(d) + len(s)


class CM:
    def __enter__(self) -> Int32:
        return 2

    def __exit__(self, et, ev, tb) -> None:
        pass


class MyErr(Exception, ReturnException):
    pass


def take_own(data: Own[list[bytes]]) -> Own[list[bytes]]:
    data.append(b"z")
    return data


def use_flat(f: Flat) -> Int32:
    return f.n


# Borrow slot at a local decl (the doom shape: dict reads in the element).
def ctor_borrow(d: dict[bytes, bytes]) -> None:
    names = [b"A", b"B"]
    f = Flat([d[k] for k in names])  # tpyc: ok
    print("ctor_borrow", f.n)


# A slot the constructor MUTATES: the hoisted temp is an lvalue.
def ctor_mutated_slot() -> None:
    g = Grow([bytes([i]) for i in range(3)])  # tpyc: ok
    print("ctor_mutated_slot", g.n)


# Set and dict comprehensions at their slots.
def ctor_set_dict() -> None:
    t = Tally({str(i): i for i in range(3)}, {i * 2 for i in range(4)})  # tpyc: ok
    print("ctor_set_dict", t.n)


# The constructor call nested in another call's argument.
def ctor_nested_call() -> None:
    print("ctor_nested_call", use_flat(Flat([bytes([i]) for i in range(2)])))  # tpyc: ok


# ... as a return value.
def make(k: Int32) -> Own[Flat]:
    return Flat([bytes([i]) for i in range(k)])  # tpyc: ok


# ... as a list-literal element, and in a condition.
def ctor_element_and_condition() -> None:
    fs = [Flat([bytes([i]) for i in range(2)]), Flat([b"x"])]  # tpyc: ok
    print("ctor_element", fs[0].n, fs[1].n)
    if Flat([bytes([i]) for i in range(2)]).n > 1:  # tpyc: ok
        print("ctor_condition yes")


# Own slot at a constructor, a method and a free function; the stored list is
# grown after each transfer.
def own_slots() -> None:
    k = Keep([bytes([i]) for i in range(3)])  # tpyc: ok
    k.data.append(b"q")
    print("own_ctor", len(k.data))
    k.reset([bytes([i]) for i in range(5)])  # tpyc: ok
    k.data.append(b"q")
    print("own_method", len(k.data))
    print("own_free", len(take_own([bytes([i]) for i in range(2)])))  # tpyc: ok


# A builtin stub's `Own[list[T]]` ELEMENT slot: `append` and `setdefault`
# move the stmt-expr in; the stored lists are grown afterwards (the
# setdefault result aliases the stored list).
def own_stub_elem() -> None:
    table: list[list[float]] = []
    table.append([2.0 * float(i) for i in range(3)])  # tpyc: ok
    table[0].append(9.0)
    print("own_stub_elem", len(table), table[0])
    d: dict[Int32, list[Int32]] = {}
    got = d.setdefault(1, [x for x in range(3)])  # tpyc: ok
    got.append(7)
    print("own_stub_elem", d[1], len(got))


# The marker-qualified `Own[T]` slot (`Rc.new`) and a still-PENDING element
# slot off a literal-seeded receiver (`rows = []`), the literal twin's two
# other homes.
def own_marker_and_pending() -> None:
    r = Rc.new([x * 2 for x in range(3)])  # tpyc: ok
    r.get().append(9)
    print("own_marker", r.get())
    rows = []
    rows.append([x for x in range(3)])  # tpyc: ok
    rows[0].append(7)
    print("own_pending_elem", rows)


# An rvalue-reassigned local: the temp lands before the rebind-slot
# declaration and before each in-place slot rewrite.
def rebind_slot_reseat(k: Int32) -> None:
    f = Flat([bytes([i]) for i in range(k)])  # tpyc: ok
    print("rebind_slot_reseat", f.n)
    f = Flat([bytes([i]) for i in range(k + 1)])  # tpyc: ok
    print("rebind_slot_reseat", f.n)


class Site:
    n: Int32

    # Constructor body (a local decl, not the field initializer itself).
    def __init__(self, k: Int32) -> None:
        f = Flat([bytes([i]) for i in range(k)])  # tpyc: ok
        self.n = f.n

    # Method body.
    def bump(self, k: Int32) -> None:
        f = Flat([bytes([i]) for i in range(k)])  # tpyc: ok
        self.n += f.n


# Closure body (a literal element: `bytes([i])` inside a nested def is its
# own gap, BUGS.md#nested-def-native-call-list-literal-arg).
def closure() -> None:
    def inner(k: Int32) -> Int32:
        return Flat([b"x" for i in range(k)]).n  # tpyc: ok

    print("closure", inner(3))


# With body, try body, match arm.
def blocks(k: Int32) -> None:
    with CM() as n:
        f = Flat([bytes([i]) for i in range(n)])  # tpyc: ok
        print("with_body", f.n)
    try:
        f = Flat([bytes([i]) for i in range(k)])  # tpyc: ok
        print("try_body", f.n)
    except ValueError:
        print("try_body err")
    match k:
        case 3:
            f = Flat([bytes([i]) for i in range(k + 1)])  # tpyc: ok
            print("match_arm", f.n)
        case _:
            print("match_arm other")


# A reseat of a with-hoisted, reassigned record: the argument temp must land
# BEFORE the rebind write (every valued reseat kind flushes first).
def hoisted_reseat(k: Int32) -> None:
    with CM() as n:
        f = Flat([b"a"])
        print("hoisted_reseat", f.n)
    f = Flat([bytes([i]) for i in range(k)])  # tpyc: ok
    print("hoisted_reseat", f.n)


def label(k: Int32) -> str:
    if k == 0:
        return ""
    return "k"


# The str in-place append drains its value's temp first (the `or` operand
# materializes one).
def str_append_temp(k: Int32) -> None:
    s = "start"
    s += label(k) or "none"  # tpyc: ok
    s += label(0) or "none"  # tpyc: ok
    print("str_append_temp", s)


# @error_return body.
@error_return(MyErr)
def error_return_body(k: Int32) -> Int32:
    f = Flat([bytes([i]) for i in range(k)])  # tpyc: ok
    return f.n


def main() -> None:
    ctor_borrow({b"A": b"x", b"B": b"y"})
    ctor_mutated_slot()
    ctor_set_dict()
    ctor_nested_call()
    print("ctor_return", make(4).n)
    ctor_element_and_condition()
    own_slots()
    own_stub_elem()
    own_marker_and_pending()
    rebind_slot_reseat(3)
    str_append_temp(2)
    s = Site(2)
    s.bump(3)
    print("ctor_method_body", s.n)
    closure()
    blocks(3)
    hoisted_reseat(3)
    try:
        print("error_return_body", error_return_body(2))
    except MyErr:
        print("error_return_body err")


main()

# Module-level statement.
top = Flat([bytes([i]) for i in range(3)])  # tpyc: ok
print("module_level", top.n)
