# A loop variable over an owned str / bytes step -- copy_iter's or a user
# generator's `-> Iterator[Own[X]]` -- owns the step, moved in, whatever the
# body does with it: consumes it, writes it, only reads it, or a nested def
# rebinds it through `nonlocal`. Writing the source after shows the copies.
from typing import Iterator
from tpy import int32, Own, copy_iter


def fresh_strs(ss: list[str]) -> Iterator[Own[str]]:
    for s in ss:
        yield s + "?"


def fresh_bytes(bs: list[bytes]) -> Iterator[Own[bytes]]:
    for b in bs:
        yield b + b"?"


def strs() -> None:
    ss = ["a", "b"]
    kept: list[str] = []
    for s in copy_iter(ss):  # tpyc: ok
        kept.append(s)  # consumes the variable
    for s in fresh_strs(ss):  # tpyc: ok
        kept.append(s)
    for s in copy_iter(ss):  # tpyc: ok
        s += "!"  # writes the variable
        kept.append(s)
    for s in fresh_strs(ss):  # tpyc: ok
        s += "!"
        kept.append(s)
    n = 0
    for s in copy_iter(ss):  # tpyc: ok
        n += len(s)  # only reads the variable
    for s in fresh_strs(ss):  # tpyc: ok
        n += len(s)
    ss[0] = "z"
    print("bind str", n, kept, ss)


def strs_nested() -> None:
    # its own function: a variable a nested def captures is never moved out,
    # so the consuming sections above would copy under that name
    ss = ["a", "b"]
    kept: list[str] = []
    for s in copy_iter(ss):  # tpyc: ok
        def change() -> None:
            nonlocal s
            s = s + "~"  # a nested def rebinds the variable
        change()
        kept.append(s)
    for s in fresh_strs(ss):  # tpyc: ok
        def change2() -> None:
            nonlocal s
            s = s + "~"
        change2()
        kept.append(s)
    ss[0] = "z"
    print("bind str nested", kept, ss)


def byts() -> None:
    bs = [b"a", b"b"]
    kept: list[bytes] = []
    for b in copy_iter(bs):  # tpyc: ok
        kept.append(b)  # consumes the variable
    for b in fresh_bytes(bs):  # tpyc: ok
        kept.append(b)
    for b in copy_iter(bs):  # tpyc: ok
        b += b"!"  # writes the variable
        kept.append(b)
    for b in fresh_bytes(bs):  # tpyc: ok
        b += b"!"
        kept.append(b)
    n = 0
    for b in copy_iter(bs):  # tpyc: ok
        n += len(b)  # only reads the variable
    for b in fresh_bytes(bs):  # tpyc: ok
        n += len(b)
    bs[0] = b"z"
    print("bind bytes", n, kept, bs)


def byts_nested() -> None:
    # its own function: a variable a nested def captures is never moved out,
    # so the consuming sections above would copy under that name
    bs = [b"a", b"b"]
    kept: list[bytes] = []
    for b in copy_iter(bs):  # tpyc: ok
        def change() -> None:
            nonlocal b
            b = b + b"~"  # a nested def rebinds the variable
        change()
        kept.append(b)
    for b in fresh_bytes(bs):  # tpyc: ok
        def change2() -> None:
            nonlocal b
            b = b + b"~"
        change2()
        kept.append(b)
    bs[0] = b"z"
    print("bind bytes nested", kept, bs)


class Joiner:
    parts: list[str]

    def __init__(self) -> None:
        self.parts = []

    def add_all(self, ss: list[str]) -> None:
        # method body: the variable written, then consumed
        for s in fresh_strs(ss):  # tpyc: ok
            s += "."
            self.parts.append(s)


def gen_marked(ss: list[str]) -> Iterator[str]:
    # generator frame: the variable written across a yield
    for s in copy_iter(ss):  # tpyc: ok
        s += "#"
        yield s
        s += "#"
        yield s


def positions() -> None:
    ss = ["a", "b"]
    j = Joiner()
    j.add_all(ss)
    marked = list(gen_marked(ss))
    # comprehension clauses over owned steps
    comp = [s + "!" for s in copy_iter(ss)]  # tpyc: ok
    comp2 = [s for s in fresh_strs(ss)]  # tpyc: ok
    ss[1] = "z"
    print("bind positions", j.parts, marked, comp, comp2, ss)


def main() -> None:
    strs()
    strs_nested()
    byts()
    byts_nested()
    positions()
