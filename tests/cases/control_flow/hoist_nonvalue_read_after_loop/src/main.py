# A non-value local bound at two sites in a `for` or `while` body (if/else
# arms, or once in the body and again after the loop) and READ AFTER the loop:
# sema's loop-level hoist takes the pointer flavor (`T* p;` before the loop, one
# `__slot_N` per bind site), the render the if / try / with ladders already
# give a reassigned hoist. Every hoisted name is mutated after the loop so a
# silent copy would show; the record is @nocopy. A holder bound to the loop
# local keeps the loop's last object across the post-loop rebind, as CPython.
from typing import Iterator, Optional
from tpy import int32, ReturnException, error_return, nocopy
from tplib import Box


@nocopy
class Flat:
    def __init__(self, n: int32) -> None:
        self.n = n


class Pic:
    def __init__(self, n: int32) -> None:
        self.n = n


class CM:
    def __init__(self, n: int32) -> None:
        self.n = n

    def __enter__(self) -> int32:
        return self.n

    def __exit__(self, et, ev, tb) -> None:
        pass


class MyErr(Exception, ReturnException):
    pass


def halves(n: int32) -> tuple[int32, int32]:
    return (n // 2, n - n // 2)


# Free function: the record bound in both arms, read and mutated after the loop.
def free_two_arms() -> None:
    for i in range(3):  # tpyc: ok
        if i % 2 == 0:
            f = Flat(i)
        else:
            f = Flat(i + 10)
    f.n += 100
    print("free_two_arms", f.n)


# Free function: bound once in the body, read after the loop, then rebound;
# the alias taken in the body keeps the loop's last object.
def free_rebind_after() -> None:
    for i in range(3):  # tpyc: ok
        p = Pic(i)
        saved = p
    print("free_rebind_after", p.n, saved.n)
    p = Pic(9)
    p.n += 1
    saved.n += 1
    print("free_rebind_after", p.n, saved.n)


# Free function: a mutable alias of the body local, read after the loop (the
# pointer local lifts off the optional-storage source).
def free_alias_holder() -> None:
    for i in range(3):  # tpyc: ok
        p = Pic(i)
        alias = p
    alias.n += 5
    print("free_alias_holder", alias.n, p.n)


# ... and an alias that is only read, which still observes a mutation of its
# source (a plain pointer local: nothing marks the alias readonly).
def free_alias_const() -> None:
    for i in range(3):  # tpyc: ok
        p = Pic(i)
        view = p
    p.n += 5
    print("free_alias_const", view.n)


# Containers take the same pointer flavor.
def free_list() -> None:
    for i in range(2):  # tpyc: ok
        v = [i]
        v.append(0)
    v.append(1)
    print("free_list", v)
    v = [9]
    print("free_list", v)


def free_dict() -> None:
    for i in range(2):  # tpyc: ok
        if i == 0:
            d = {i: i}
        else:
            d = {i: i * 10}
    d[7] = 7
    print("free_dict", sorted(d.items()))


# Pointer-repr Optional bound in the body (None on one path), read after the
# loop and rebound.
def free_optional() -> None:
    for i in range(2):  # tpyc: ok
        p: Optional[Pic] = Pic(i)
        if i == 1:
            p = None
    print("free_optional", p is None)
    p = Pic(9)
    if p is not None:
        p.n += 1
        print("free_optional", p.n)


# A Box takes the pointer flavor like any @nocopy record.
def free_box() -> None:
    for i in range(2):  # tpyc: ok
        if i == 0:
            b = Box(Pic(i))
        else:
            b = Box(Pic(i + 10))
    print("free_box", b.n)


# Nested loops: the inner loop hoists into the outer body; a name bound in
# the inner loop and read after the OUTER loop hoists at the outer for.
def nested_inner_read() -> None:
    for i in range(2):
        for j in range(2):  # tpyc: ok
            if j == 0:
                f = Flat(i * 10 + j)
            else:
                f = Flat(i * 10 + j + 5)
        f.n += 100
        print("nested_inner_read", f.n)


def nested_outer_read() -> None:
    for i in range(2):  # tpyc: ok
        for j in range(2):
            f = Flat(i * 10 + j)
            saved = f
    f.n += 100
    print("nested_outer_read", f.n, saved.n)
    f = Flat(9)
    print("nested_outer_read", f.n, saved.n)


# ... three deep, with a rebind in the middle body.
def nested_three_deep() -> None:
    for i in range(2):  # tpyc: ok
        for j in range(2):
            for k in range(2):
                f = Flat(i * 100 + j * 10 + k)
            f = Flat(f.n + 1000)
    f.n += 1
    print("nested_three_deep", f.n)


# ... bound in the outer body and rebound in the inner loop.
def nested_outer_bind_inner_rebind() -> None:
    for i in range(2):  # tpyc: ok
        f = Flat(i)
        for j in range(2):
            f = Flat(i * 10 + j)
        print("nested_outer_bind_inner_rebind", f.n)
    f.n += 100
    print("nested_outer_bind_inner_rebind", f.n)


# While loops take the same hoist: two sites in the body, and a body bind
# read after the loop then rebound.
def while_two_arms() -> None:
    i = 0
    while i < 3:  # tpyc: ok
        if i % 2 == 0:
            f = Flat(i)
        else:
            f = Flat(i + 10)
        i += 1
    f.n += 100
    print("while_two_arms", f.n)


def while_rebind_after() -> None:
    i = 0
    while i < 3:  # tpyc: ok
        p = Pic(i)
        saved = p
        i += 1
    print("while_rebind_after", p.n, saved.n)
    p = Pic(9)
    p.n += 1
    print("while_rebind_after", p.n, saved.n)


# A `while` counts as run when its entry condition is provably true: the
# literal, or an integer comparison the value ranges decide (every `i = 0;
# while i < n` above); an unprovable head keeps the read rejected
# (`pointers/error_uninit_while`).
def while_true_break() -> None:
    n = 0
    while True:  # tpyc: ok
        f = Flat(n)
        n += 1
        if n == 2:
            break
    f.n += 100
    print("while_true_break", f.n)


# ... nested either way round.
def while_in_for() -> None:
    for i in range(2):  # tpyc: ok
        j = 0
        while j < 2:
            f = Flat(i * 10 + j)
            j += 1
    f.n += 100
    print("while_in_for", f.n)


def for_in_while() -> None:
    i = 0
    while i < 2:  # tpyc: ok
        for j in range(2):
            f = Flat(i * 10 + j)
        i += 1
    f.n += 100
    print("for_in_while", f.n)


# A tuple-unpack target bound only in the while body, read after the loop
# (value elements: a str view target dangles into the per-iteration tuple,
# BUGS.md#loop-body-view-unpack-target-dangles, the same for both loops).
def while_unpack() -> None:
    n = 40
    i = 0
    while i < 2:  # tpyc: ok
        n, kept = halves(n)
        i += 1
    print("while_unpack", kept, n)


# A walrus inside a comprehension binds in the enclosing function (PEP 572);
# the comprehension's own variable does not leak, the target is read after
# the loop that holds the comprehension.
def comp_walrus_in_loop() -> None:
    for i in range(2):  # tpyc: ok
        ys = [(y := x * 10) for x in range(i + 1)]
    print("comp_walrus_in_loop", y, ys)


class Holder:
    # Method position.
    def run(self) -> None:
        for i in range(2):  # tpyc: ok
            if i == 0:
                p = Flat(i)
            else:
                p = Flat(-i)
            saved = p
        p.n += 100
        print("method", p.n, saved.n)
        p = Flat(9)
        print("method", p.n, saved.n)


class Built:
    v: int32

    # Constructor position.
    def __init__(self) -> None:
        for i in range(2):  # tpyc: ok
            if i == 0:
                p = Flat(i)
            else:
                p = Flat(i + 20)
        p.n += 1
        self.v = p.n


# Generator: the loop local lives on the frame, with the same per-site slots
# as frame fields.
def gen_section() -> Iterator[int32]:
    for i in range(2):  # tpyc: ok
        p = Pic(i)
        yield p.n
    p.n += 10
    yield p.n
    p = Pic(9)
    yield p.n


def gen_while_section() -> Iterator[int32]:
    i = 0
    while i < 2:  # tpyc: ok
        p = Pic(i)
        yield p.n
        i += 1
    p.n += 10
    yield p.n
    p = Pic(9)
    yield p.n


# ... and bound at ONE site with no rebind after the loop: the promoted name
# must still be a frame field, since the loop suspends and the read after
# it runs in another state (record, scalar and unpack target, both loops).
def gen_for_single() -> Iterator[int32]:
    for i in range(2):  # tpyc: ok
        f = Flat(i)
        x = i * 10
        yield f.n + x
    f.n += 100
    x += 1
    yield f.n + x


def pair(i: int32) -> tuple[int32, str]:
    return (i, "v")


def gen_while_single() -> Iterator[str]:
    i = 0
    while i < 2:  # tpyc: ok
        f = Flat(i)
        a, b = pair(i)
        yield b
        i += 1
    f.n += 100
    yield f"{b}{a}{f.n}"


# Closure body: the free-function path.
def nested_def_section() -> None:
    def inner() -> None:
        for i in range(2):  # tpyc: ok
            if i == 0:
                f = Flat(i)
            else:
                f = Flat(i + 30)
        f.n += 1
        print("nested_def", f.n)

    inner()


# Context-manager body: the with hoists the name first; the loop's binds are
# reseats of that pointer local.
def with_section() -> None:
    with CM(4) as k:
        for i in range(2):  # tpyc: ok
            if i == 0:
                f = Flat(i)
            else:
                f = Flat(i + k)
        f.n += 1
        print("with_body", f.n)


# try/finally body.
def try_section() -> None:
    try:
        for i in range(2):  # tpyc: ok
            if i == 0:
                f = Flat(i)
            else:
                f = Flat(i + 40)
        f.n += 1
        print("try_finally", f.n)
    finally:
        print("try_finally done")


# @error_return body.
@error_return(MyErr)
def er_section(k: int32) -> int32:
    for i in range(2):  # tpyc: ok
        if i == 0:
            f = Flat(i)
        else:
            f = Flat(i + k)
    f.n += 1
    return f.n


# match arm.
def match_section(n: int32) -> None:
    match n:
        case 1:
            for i in range(2):  # tpyc: ok
                if i == 0:
                    f = Flat(i)
                else:
                    f = Flat(i + 60)
            f.n += 1
            print("match_arm", f.n)
        case _:
            print("match_arm other")


def main() -> None:
    free_two_arms()
    free_rebind_after()
    free_alias_holder()
    free_alias_const()
    free_list()
    free_dict()
    free_optional()
    free_box()
    nested_inner_read()
    nested_outer_read()
    nested_three_deep()
    nested_outer_bind_inner_rebind()
    while_two_arms()
    while_rebind_after()
    while_true_break()
    while_in_for()
    for_in_while()
    while_unpack()
    comp_walrus_in_loop()
    Holder().run()
    print("ctor", Built().v)
    for v in gen_section():
        print("gen", v)
    for v in gen_while_section():
        print("gen_while", v)
    for v in gen_for_single():
        print("gen_for_single", v)
    for t in gen_while_single():
        print("gen_while_single", t)
    nested_def_section()
    with_section()
    try_section()
    try:
        print("error_return", er_section(50))
    except MyErr:
        print("error_return raised")
    match_section(1)
    match_section(2)


main()
