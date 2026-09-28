# A ValueType record local reseated from an lvalue (or rebound after an
# @error_return bind) is a plain value local: `a = b` copies, `b` stays intact.
from tpy import int32, Own, ValueType, copy, error_return, ReturnException


class Coord(ValueType):
    column: int32
    row: int32

    def __init__(self, column: int32, row: int32) -> None:
        self.column = column
        self.row = row


class Err(Exception, ReturnException):
    pass


def step(c: Coord) -> Coord:
    return Coord(c.column + 1, c.row)


def ok(c: Coord) -> bool:
    return c.column > 1


def ring(c: Coord) -> Own[list[Coord]]:
    return [Coord(c.column + 1, c.row), Coord(c.column + 2, c.row)]


@error_return(Err)
def parse(s: str) -> Coord:
    if s == "":
        raise Err()
    return Coord(len(s), 0)


# free function: reseat from a local name
def free_fn() -> None:
    focus = Coord(1, 2)
    next_focus = step(focus)
    focus = next_focus  # tpyc: ok
    print("free", focus.column, next_focus.column)


# loop variable source
def loop_var(candidates: list[Coord]) -> None:
    next_focus = Coord(0, 0)
    for coord in candidates:
        if ok(coord):
            next_focus = coord  # tpyc: ok
            break
    print("loop", next_focus.column, candidates[1].column)


# subscript source
def subscript(xs: list[Coord]) -> None:
    a = Coord(0, 0)
    a = xs[1]  # tpyc: ok
    print("subscript", a.column, xs[1].column)


# param source
def param(p: Coord) -> None:
    a = Coord(0, 0)
    a = p  # tpyc: ok
    print("param", a.column, p.column)


# branch reseat
def branch(flag: bool) -> None:
    a = Coord(0, 0)
    b = Coord(1, 1)
    if flag:
        a = b  # tpyc: ok
    print("branch", a.column, b.column)


class Walker:
    start: Coord

    def __init__(self) -> None:
        self.start = Coord(7, 8)

    # method body
    def walk(self) -> None:
        a = Coord(0, 0)
        b = Coord(1, 1)
        a = b  # tpyc: ok
        print("method", a.column, b.column, self.start.column)


# closure-enclosing body: the nested function reads the reseated local
def closure() -> None:
    a = Coord(0, 0)
    b = Coord(1, 1)

    def g() -> int32:
        return a.column

    a = b  # tpyc: ok
    print("closure", g(), b.column)


# while body: an explicit-copy first init must not make the while-rebound
# local a pointer slot
def while_body(spawn: Coord) -> None:
    focus = copy(spawn)
    chosen = 0
    while chosen < 3:
        candidates = ring(focus)
        next_focus = Coord(0, 0)
        for coord in candidates:
            if ok(coord):
                next_focus = coord
                break
        chosen += 1
        print("while", focus.column, focus.row)
        focus = next_focus  # tpyc: ok
    print("while spawn", spawn.column)


class Holder:
    pos: Coord

    # constructor body: the reseated local is stored in a field
    def __init__(self, xs: list[Coord]) -> None:
        a = Coord(0, 0)
        a = xs[0]  # tpyc: ok
        self.pos = a
        print("ctor", a.column, xs[0].column)


# try/finally body
def try_finally(p: Coord) -> None:
    a = Coord(0, 0)
    try:
        a = p  # tpyc: ok
        print("try", a.column, p.column)
    finally:
        print("finally", a.column, p.column)


# match arm
def match_arm(tag: int32, p: Coord) -> None:
    a = Coord(0, 0)
    match tag:
        case 1:
            a = p  # tpyc: ok
        case _:
            pass
    print("match", tag, a.column, p.column)


# @error_return bind, then an rvalue rebind
@error_return(Err)
def err_bind(s: str) -> int32:
    c = parse(s)  # tpyc: ok
    c = Coord(c.column + 1, 0)  # tpyc: ok
    return c.column


def main() -> None:
    free_fn()
    loop_var([Coord(1, 0), Coord(5, 0), Coord(9, 0)])
    subscript([Coord(3, 0), Coord(4, 0)])
    p = Coord(6, 0)
    param(p)
    branch(True)
    branch(False)
    Walker().walk()
    closure()
    spawn = Coord(0, 0)
    while_body(spawn)
    # Bound first: BUGS.md#print-arg-output-interleaves.
    holder = Holder([Coord(11, 0), Coord(12, 0)])
    print("ctor field", holder.pos.column)
    try_finally(p)
    match_arm(1, p)
    match_arm(2, p)
    try:
        print("err_bind", err_bind("ab"))
    except Err:
        print("err_bind failed")


main()

# module-level statement
g = Coord(0, 0)
h = Coord(2, 3)
g = h  # tpyc: ok
print("module", g.column, h.column)
