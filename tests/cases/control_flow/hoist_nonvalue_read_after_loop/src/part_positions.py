# The loop-level hoist at the other positions: method, constructor,
# generator, closure, with, try/finally, @error_return and match arm.
from typing import Iterator
from tpy import int32, error_return
from fixtures import Flat, Pic, CM, MyErr


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


# ... and a str local sliced out of another local in a generator's loop body:
# the source is a frame FIELD, so it outlives every suspension and the slice
# keeps the view a free function gets (`std::string_view c`, not a copy per
# character). The source is long enough that a dangling view would show.
def gen_slice_view(line: str) -> Iterator[int32]:
    i = 0
    while i < len(line):
        c = line[i:i + 2]  # tpyc: ok
        yield int32(len(c))
        i += 2


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


# ... a nested `def` INSIDE the binding loop: the enclosing-statement stack
# the anchor is taken from has to come back from the nested-def state save
# holding the SAME statement objects, or the read anchors against a clone
# and codegen never sees the pre-declaration.
# The record binding comes from the caller's list, so the post-loop mutation
# is observed on `pics` itself -- a frame-slot copy would leave it at 0.
def nested_def_in_loop(pics: list[Pic]) -> None:
    for i in range(3):
        def double(k: int32) -> int32:
            return k * 2

        x = double(i)  # tpyc: ok
        xs = [x]
        held = pics[i % 2]
    xs.append(9)
    held.n += 100
    print("nested_def_in_loop", x, xs, held.n, pics[0].n)


# ... and a nested `def` between the binding loop and the read.
def nested_def_after_loop() -> None:
    for i in range(3):
        f = Flat(i)  # tpyc: ok

    def bump(k: int32) -> int32:
        return k + 1

    f.n = bump(f.n)
    print("nested_def_after_loop", f.n)


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
    slice_total = 0
    for v in gen_slice_view("a source long enough that a dangling view shows"):
        slice_total += v
    print("gen_slice_view", slice_total)
    nested_def_section()
    nested_def_in_loop([Pic(0), Pic(1)])
    nested_def_after_loop()
    with_section()
    try_section()
    try:
        print("error_return", er_section(50))
    except MyErr:
        print("error_return raised")
    match_section(1)
    match_section(2)
