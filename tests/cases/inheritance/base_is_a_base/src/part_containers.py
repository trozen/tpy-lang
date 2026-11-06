# A builtin-base subclass through the inherited container surface: `in`,
# iteration, aliasing, constructors, printing, frames, except, bytearray.
from typing import Iterator
from tpy import int32
from fixtures import DSub, LSub, SSub, put_dict, put_list, put_set, total
from part_init import DBare, LBare, SBare


class DInit(dict[str, int32]):
    def __init__(self) -> None:
        super().__init__()  # tpyc: ok


class LInit(list[int32]):
    def __init__(self) -> None:
        super().__init__()  # tpyc: ok


class SInit(set[int32]):
    def __init__(self) -> None:
        super().__init__()  # tpyc: ok


# membership: the inherited `__contains__` (dict, set) or iteration (list)
def membership() -> None:
    d = DSub()
    put_dict(d, "a")
    xs = LSub()
    put_list(xs)
    ss = SSub()
    put_set(ss)
    print("in", "a" in d, "b" not in d, 9 in xs, 5 in xs, 9 in ss, 4 not in ss)  # tpyc: ok


# iteration over each subclass, after a write through a base-typed param
def iteration() -> None:
    d = DInit()
    put_dict(d, "k")
    xs = LInit()
    put_list(xs)
    ss = SInit()
    put_set(ss)
    out = ""
    for k in d:  # tpyc: ok
        out += k
    n = 0
    for x in xs:  # tpyc: ok
        n += x
    for y in ss:  # tpyc: ok
        n += y
    print("iter", out, n, total(xs), total(ss))


# a base-typed local aliases the subclass object
def alias() -> None:
    d = DSub()
    m: dict[str, int32] = d  # tpyc: warning(/upcast narrows 'DSub'/)
    m["x"] = 1
    xs = LSub()
    l: list[int32] = xs  # tpyc: warning(/upcast narrows 'LSub'/)
    l.append(4)
    ss = SSub()
    s: set[int32] = ss  # tpyc: warning(/upcast narrows 'SSub'/)
    s.add(6)
    print("alias", d["x"], xs[0], 6 in ss)


# super().__init__() to a builtin base, and an inherited constructor
def constructors() -> None:
    d = DBare()  # tpyc: ok
    d["a"] = 1
    print("ctor", len(DInit()), len(LInit()), len(SInit()), len(d), len(LBare()), len(SBare()))


# print and == follow the builtin base
def printing() -> None:
    d = DBare()
    d["a"] = 1
    e = DBare()
    e["a"] = 1
    p: dict[str, int32] = {"a": 1}
    xs = LBare()
    xs.append(2)
    ss = SBare()
    ss.add(3)
    print("print", d, xs, ss, SBare())  # tpyc: ok
    print("eq", d == e, d != p, p == d, xs == LBare(), ss == SBare())  # tpyc: ok
    # an element prints through the inherited `__repr__` too; the list
    # literal is an owning slot, so it copies the record as for any class
    print("print-nested", [d], (ss, xs))  # tpyc: warning(/copies DBare into owned storage/)
    # str() / repr() / an f-string reach the inherited `__repr__`
    print("print-str", str(d), repr(ss), f"{xs}")  # tpyc: ok
    ys: list[int32] = [2]
    # a plain container compares with a subclass through its own `__eq__`
    print("eq-mixed", ys == xs, xs != ys, ys != xs)  # tpyc: ok


def gen_keys(d: DSub) -> Iterator[str]:
    for k in d:
        if k in d:  # tpyc: ok
            yield k


# generator, comprehension and closure positions
def positions() -> None:
    d = DSub()
    put_dict(d, "p")
    xs = LSub()
    put_list(xs)
    keys = ""
    for k in gen_keys(d):
        keys += k
    sq = [x * 2 for x in xs if x in xs]  # tpyc: ok

    def has(k: str) -> bool:
        return k in d  # tpyc: ok

    print("positions", keys, sq, has("p"), has("q"))


class AppError(Exception):
    def __init__(self, msg: str) -> None:
        super().__init__(msg)


def log(e: Exception) -> str:
    return "logged"


# except: a caught builtin / user exception binds an Exception parameter
def exceptions() -> None:
    try:
        raise ValueError("bad")
    except ValueError as e:
        print("exc", log(e))  # tpyc: ok
    try:
        raise AppError("app")
    except AppError as e:
        print("exc", log(e))  # tpyc: ok


class BSub(bytearray):
    def __init__(self) -> None:
        pass


# a bytearray subclass prints by its base's rule
def bytearray_print() -> None:
    b = BSub()
    b.append(65)
    print("bytearray", b)  # tpyc: ok


def main() -> None:
    membership()
    iteration()
    alias()
    constructors()
    printing()
    positions()
    exceptions()
    bytearray_print()
