# A generator local first bound INSIDE a clause (loop body/else, if arm,
# try/except/finally, with, match arm, nested loop) by a tuple unpack, a
# walrus or a `with ... as` target is one frame field, written by the clause
# and readable from every later state block -- no shadowing second
# declaration.
from typing import Iterator
from tpy import int32, Own
from tplib import Box


class Cell:
    v: int

    def __init__(self, v: int):
        self.v = v


class Pic:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Flat:
    m: int32

    def __init__(self, m: int32) -> None:
        self.m = m


class Source:
    tag: str

    def __init__(self):
        self.tag = "m"

    # generator METHOD: the clause-bound names are fields of the method's frame
    def pairs(self) -> Iterator[str]:
        for i in [1, 2]:
            s, k = pair()  # tpyc: ok
        yield self.tag
        yield s
        yield str(k)


def pair() -> tuple[str, int]:
    return ("a", 7)


def fallback() -> tuple[str, int]:
    return ("z", 0)


def two_str() -> tuple[str, str]:
    return ("x", "y")


def i32_pair() -> tuple[str, int32]:
    return ("a", 7)


def opt_pair() -> tuple[int | None, str]:
    return (7, "b")


def with_cell(c: Cell) -> tuple[Cell, int]:
    return (c, 7)


def own_list_pair() -> tuple[Own[list[int]], int]:
    return ([1, 2, 3], 7)


def own_cell(n: int) -> tuple[Own[Cell], int]:
    return (Cell(n), 7)


def union_pair(n: int32) -> tuple[Own[Pic | Flat], int32]:
    return (Pic(n), 7)


def box_pair(n: int32) -> tuple[Own[Box[int32]], int32]:
    return (Box(n), 7)


def one() -> str:
    return "a"


def two() -> int:
    return 7


def num() -> int:
    return 7


def flag() -> bool:
    return True


class Guard:
    def __enter__(self) -> int:
        return 1

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


# for BODY
def gen_for_body() -> Iterator[str]:
    for i in [1, 2]:
        s, k = pair()  # tpyc: ok
    yield s
    yield str(k)


# for ELSE
def gen_for_else() -> Iterator[str]:
    for i in [1, 2]:
        pass
    else:
        s, k = pair()  # tpyc: ok
    yield s
    yield str(k)


# while BODY
def gen_while_body() -> Iterator[str]:
    i = 0
    while i < 2:
        s, k = pair()  # tpyc: ok
        i += 1
    yield s
    yield str(k)


# while ELSE
def gen_while_else() -> Iterator[str]:
    i = 0
    while i < 2:
        i += 1
    else:
        s, k = pair()  # tpyc: ok
    yield s
    yield str(k)


# if / else, BOTH arms call-sourced
def gen_if_arms() -> Iterator[str]:
    if flag():
        s, k = pair()  # tpyc: ok
    else:
        s, k = pair()
    yield s
    yield str(k)


# try body + except arm
def gen_try_except() -> Iterator[str]:
    try:
        s, k = pair()  # tpyc: ok
    except ValueError:
        s, k = fallback()
    yield s
    yield str(k)


# finally arm
def gen_finally() -> Iterator[str]:
    try:
        pass
    finally:
        s, k = pair()  # tpyc: ok
    yield s
    yield str(k)


# with BODY
def gen_with_body() -> Iterator[str]:
    with Guard() as t:
        s, k = pair()  # tpyc: ok
    yield s
    yield str(k)


# `with ... as` TARGET -- the third binder kind, read after the yield
def gen_with_target() -> Iterator[str]:
    for i in [1, 2]:
        with Guard() as t:  # tpyc: ok
            pass
    yield "sep"
    yield str(t)


# leaf match arm (no suspension inside the match)
def gen_match_arm(n: int32) -> Iterator[str]:
    match n:
        case 1:
            s, k = pair()  # tpyc: ok
        case _:
            s, k = pair()
    yield s
    yield str(k)


# nested loop
def gen_nested_loop() -> Iterator[str]:
    for i in [1, 2]:
        for j in [3, 4]:
            s, k = pair()  # tpyc: ok
    yield s
    yield str(k)


# walrus in a loop clause -- the second binder kind of the same class
def gen_walrus() -> Iterator[str]:
    for i in [1, 2]:
        if (w := num()):  # tpyc: ok
            pass
    yield "w"
    yield str(w)


# int32 element: the frame ctor leaves a fixed-width member uninitialized,
# so a shadowing predecl shows up as garbage rather than zero
def gen_int32() -> Iterator[str]:
    for i in [1, 2]:
        s, k = i32_pair()  # tpyc: ok
    yield s
    yield str(k)


# Optional element: a shadowed member stays nullopt, so `is None` takes the
# wrong arm
def gen_optional() -> Iterator[str]:
    for i in [1, 2]:
        n, s = opt_pair()  # tpyc: ok
    yield s
    if n is None:
        yield "none"
    else:
        yield str(n)


# two str targets, both read in a state block LATER than the writing one
def gen_two_str() -> Iterator[str]:
    for i in [1, 2]:
        a, b = two_str()  # tpyc: ok
    yield "sep"
    yield a
    yield b


# reference type: the target aliases the caller's record, so a mutation after
# the yield is visible to the caller
def gen_record(c: Cell) -> Iterator[str]:
    for i in [1, 2]:
        r, k = with_cell(c)  # tpyc: ok
    yield str(k)
    r.v = 99
    yield "mutated"


# Own[list] element in a for body: the frame owns the list, so the append
# after the yield lands on the same buffer the later read sees
def gen_own_list() -> Iterator[str]:
    for i in [1, 2]:
        xs, k = own_list_pair()  # tpyc: ok
    yield str(k)
    xs.append(9)
    yield f"{len(xs)}/{xs[3]}"


# borrowed record target in a with BODY
def gen_record_with(c: Cell) -> Iterator[str]:
    with Guard() as t:
        r, k = with_cell(c)  # tpyc: ok
    yield str(k)
    r.v = 92
    yield "mutated"


# borrowed record target in a try BODY
def gen_record_try(c: Cell) -> Iterator[str]:
    try:
        r, k = with_cell(c)  # tpyc: ok
    finally:
        pass
    yield str(k)
    r.v = 93
    yield "mutated"


# borrowed record target in BOTH call-sourced if arms
def gen_record_if(c: Cell) -> Iterator[str]:
    if flag():
        r, k = with_cell(c)  # tpyc: ok
    else:
        r, k = with_cell(c)
    yield str(k)
    r.v = 94
    yield "mutated"


# borrowed record target in a leaf match arm
def gen_record_match(c: Cell, n: int32) -> Iterator[str]:
    match n:
        case 1:
            r, k = with_cell(c)  # tpyc: ok
        case _:
            r, k = with_cell(c)
    yield str(k)
    r.v = 95
    yield "mutated"


# fresh Own[record] target in a try BODY; the allocations between the yields
# must not disturb the frame's own copy
def gen_own_record_try() -> Iterator[str]:
    try:
        r, k = own_cell(3)  # tpyc: ok
    finally:
        pass
    yield str(k)
    junk = [Cell(100 + i) for i in range(50)]
    yield f"{r.v}/{len(junk)}"


# the same fresh Own[record] target in BOTH call-sourced if arms
def gen_own_record_if() -> Iterator[str]:
    if flag():
        r, k = own_cell(4)  # tpyc: ok
    else:
        r, k = own_cell(5)
    yield str(k)
    junk = [Cell(200 + i) for i in range(50)]
    yield f"{r.v}/{len(junk)}"


# the same fresh Own[record] target in a for BODY
def gen_own_record_loop() -> Iterator[str]:
    for i in [1, 2]:
        r, k = own_cell(i)  # tpyc: ok
    yield str(k)
    junk = [Cell(300 + i) for i in range(50)]
    yield f"{r.v}/{len(junk)}"


# Own[union] target: the narrowed alternative must survive the frame with its
# payload, so the first read sees the value the loop bound (not a re-made
# alternative) and the write is read back only after another suspension
def gen_own_union() -> Iterator[str]:
    for i in [1, 2]:
        u, k = union_pair(i)  # tpyc: ok
    yield str(k)
    junk = [Pic(500 + i) for i in range(40)]
    if isinstance(u, Pic):
        yield f"pic={u.n}/{len(junk)}"
        u.n = 91
        yield "set"
        yield f"pic={u.n}"
    else:
        yield f"flat={u.m}/{len(junk)}"
        u.m = 92
        yield "set"
        yield f"flat={u.m}"


# @nocopy Own[Box] target: a copy at the frame boundary would not compile, so
# a green build is the move proof
def gen_own_box() -> Iterator[str]:
    for i in [1, 2]:
        b, k = box_pair(i)  # tpyc: ok
    yield str(k)
    junk = [i * 2 for i in range(40)]
    yield f"box={b.get()}/{len(junk)}"


# control: plain assigns in the same position
def gen_plain_assign() -> Iterator[str]:
    for i in [1, 2]:
        s = one()  # tpyc: ok
        k = two()
    yield s
    yield str(k)


# control: literal-tuple source (sema decomposes it into per-target assigns)
def gen_literal_src() -> Iterator[str]:
    for i in [1, 2]:
        s, k = ("a", 7)  # tpyc: ok
    yield s
    yield str(k)


# control: name bound BEFORE the loop, rebound by the clause unpack
def gen_prebound() -> Iterator[str]:
    s = "start"
    k = two()
    for i in [1, 2]:
        s, k = pair()  # tpyc: ok
    yield s
    yield str(k)


# control: unpack at generator top level, no clause at all
def gen_toplevel() -> Iterator[str]:
    s, k = pair()  # tpyc: ok
    yield s
    yield str(k)


def show(name: str, g: Iterator[str]) -> None:
    for v in g:
        print(f"{name}: {v}")


def main() -> None:
    show("for_body", gen_for_body())
    show("for_else", gen_for_else())
    show("while_body", gen_while_body())
    show("while_else", gen_while_else())
    show("if_arms", gen_if_arms())
    show("try_except", gen_try_except())
    show("finally", gen_finally())
    show("with_body", gen_with_body())
    show("with_target", gen_with_target())
    show("match_arm", gen_match_arm(1))
    show("nested_loop", gen_nested_loop())
    src = Source()
    for v in src.pairs():
        print(f"method: {v}")
    show("walrus", gen_walrus())
    show("int32", gen_int32())
    show("optional", gen_optional())
    show("two_str", gen_two_str())

    c = Cell(1)
    show("record", gen_record(c))
    print(f"record: caller sees {c.v}")

    show("own_list", gen_own_list())

    cw = Cell(1)
    show("record_with", gen_record_with(cw))
    print(f"record_with: caller sees {cw.v}")

    ct = Cell(1)
    show("record_try", gen_record_try(ct))
    print(f"record_try: caller sees {ct.v}")

    ci = Cell(1)
    show("record_if", gen_record_if(ci))
    print(f"record_if: caller sees {ci.v}")

    cm = Cell(1)
    show("record_match", gen_record_match(cm, 1))
    print(f"record_match: caller sees {cm.v}")

    show("own_record_try", gen_own_record_try())
    show("own_record_if", gen_own_record_if())
    show("own_record_loop", gen_own_record_loop())
    show("own_union", gen_own_union())
    show("own_box", gen_own_box())

    show("plain_assign", gen_plain_assign())
    show("literal_src", gen_literal_src())
    show("prebound", gen_prebound())
    show("toplevel", gen_toplevel())


main()
