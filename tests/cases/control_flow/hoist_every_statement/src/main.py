# A local first bound inside a compound statement and read after it is
# predeclared by one driver whatever the statement: every section binds it
# at a different statement and then reads or mutates through it.
from tpy import int32, Own, nocopy, error_return, ReturnException


class Bad(Exception, ReturnException):
    pass


class Box:
    val: int32

    def __init__(self, v: int32) -> None:
        self.val = v


@nocopy
class Pin:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Holder:
    box: Box
    pair: tuple[int32, Pin]

    def __init__(self, v: int32) -> None:
        self.box = Box(v)
        self.pair = (v, Pin(v))


class Ctx:
    def __enter__(self) -> int32:
        return 0

    def __exit__(self, et, ev, tb) -> None:
        pass


@error_return(Bad)
def decode(n: int32) -> Own[Box]:
    if n > 0:
        return Box(n)
    raise Bad


def for_const_tuple(h: Holder) -> int32:
    # A borrow tuple a for statement hoists, every source const: the
    # predecl spells `const Pin*` (Pin is @nocopy, so a copy cannot build).
    for _i in range(1):
        t = h.pair  # tpyc: ok
    return t[0] + t[1].n


def try_error_return(h: Holder) -> None:
    try:
        b = h.box  # tpyc: ok
        b.val += 1
        b = decode(5)  # tpyc: ok
    except Bad:
        return
    alias = b
    alias.val += 10
    print("try_error_return", h.box.val, b.val)


def while_error_return(h: Holder) -> None:
    n = 0
    while n < 2:
        b = h.box  # tpyc: ok
        b.val += 1
        try:
            b = decode(5 + n)  # tpyc: ok
        except Bad:
            return
        n += 1
    alias = b
    alias.val += 10
    print("while_error_return", h.box.val, b.val)


def for_error_return(h: Holder) -> None:
    for i in range(2):
        b = h.box  # tpyc: ok
        b.val += 1
        try:
            b = decode(10 + i)  # tpyc: ok
        except Bad:
            return
    alias = b
    alias.val += 100
    print("for_error_return", h.box.val, b.val)


def with_error_return(h: Holder) -> None:
    with Ctx():
        b = h.box  # tpyc: ok
        b.val += 1
        try:
            b = decode(5)  # tpyc: ok
        except Bad:
            return
    alias = b
    alias.val += 10
    print("with_error_return", h.box.val, b.val)


def while_reseat(a: Box, n: int32) -> None:
    while True:
        x = Box(n)  # tpyc: ok
        break
    y = x
    x = a  # the alias keeps the first object across the reseat
    y.val += 1
    x.val += 5
    x = Box(99)  # a second rvalue reseat; y still holds the first object
    y.val += 1
    print("while_reseat", y.val, a.val, x.val)


def try_reseat(a: Box, n: int32) -> None:
    try:
        x = Box(n)  # tpyc: ok
    except ValueError:
        x = Box(0)
    y = x
    x = a
    y.val += 1
    x.val += 5
    x = Box(99)  # a second rvalue reseat; y still holds the first object
    y.val += 1
    print("try_reseat", y.val, a.val, x.val)


def with_reseat(a: Box, n: int32) -> None:
    with Ctx():
        x = Box(n)  # tpyc: ok
    y = x
    x = a
    y.val += 1
    x.val += 5
    x = Box(99)  # a second rvalue reseat; y still holds the first object
    y.val += 1
    print("with_reseat", y.val, a.val, x.val)


def for_reseat(a: Box, n: int32) -> None:
    for _i in range(1):
        x = Box(n)  # tpyc: ok
    y = x
    x = a
    y.val += 1
    x.val += 5
    x = Box(99)  # a second rvalue reseat; y still holds the first object
    y.val += 1
    print("for_reseat", y.val, a.val, x.val)


def make_box(v: int32) -> Own[Box]:
    return Box(v)


def main() -> None:
    print("for_const_tuple", for_const_tuple(Holder(3)))
    try_error_return(Holder(1))
    while_error_return(Holder(1))
    for_error_return(Holder(1))
    with_error_return(Holder(1))
    a = Box(0)
    while_reseat(a, 1)
    try_reseat(a, 1)
    with_reseat(a, 1)
    for_reseat(a, 1)


main()

# Module level: a pointer global rebound from an rvalue inside a `try`
# fills a `static` slot, so the object outlives `__tpy_init`.
mod_src = Box(100)
try:
    mod_b = mod_src  # tpyc: ok
    mod_b = make_box(9)  # tpyc: ok
except ValueError:
    mod_b = mod_src
mod_alias = mod_b
mod_alias.val += 1
print("module_try", mod_b.val, mod_src.val)
