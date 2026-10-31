# MIR verdicts for calls to stdlib stubs: a declared contract (@pure / transient=True)
# admits the call and summarizes the caller; `len` dispatching a record's __len__ refuses.
import math
import time
from tpy import int32, float32, String, StrView, char

G: str = "glob"


class Rec:
    n: int32

    # constructor: a scalar field write
    def __init__(self, n: int32):  # tpyc: mir(covered)
        self.n = n

    def __len__(self) -> int32:
        return self.n

    # method: `len` on a str parameter, lent to the @pure stub
    def label_len(self, s: str) -> int32:  # tpyc: mir(covered)
        return len(s)


# free function: `len` is @pure; its protocol parameter binds the str, lent for the call
def lent_len(s: str) -> int32:  # tpyc: mir(covered) mir_summary(known)
    return len(s)


# free function: a global argument is lent through its handle
def global_len() -> int32:  # tpyc: mir(covered) mir_summary(known)
    return len(G)


# free function: `time.time` is bound transient=True and takes no arguments
def clock() -> float:  # tpyc: mir(covered) mir_summary(known)
    return time.time()


# free function: a pure initializer lends nothing, so its BigInt result is fresh
def construct(x: float) -> int:  # tpyc: mir(covered) mir_summary(known)
    return int(x)


# free function: the `min` result may borrow either argument, so the local copies it
def smaller(a: int, b: int) -> int:  # tpyc: mir(covered) mir_summary(known)
    x = min(a, b)
    return x


# free function: as an operand the borrowed `min` result is read in place
def smaller_is(a: int, b: int) -> bool:  # tpyc: mir(covered) mir_summary(known)
    return min(a, b) == a


# free function: the result may borrow the sum's temporary
def smaller_sum(a: int, b: int) -> int:  # tpyc: mir(covered) mir_summary(known)
    x = min(a + b, b)
    return x


# free function: the int32 and BigInt overloads of one stub in one body
def overloads(a: int, b: int, i: int32, j: int32) -> bool:  # tpyc: mir(covered) mir_summary(known)
    return min(i, j) == 1 and min(a, b) == a


# free function: two calls of one stub share one summary
def twice(x: float) -> float:  # tpyc: mir(covered) mir_summary(known)
    y = math.log(x)
    return math.log(y)


# free function: two different stubs in one body
def both(s: str, a: int, b: int) -> int32:  # tpyc: mir(covered) mir_summary(known)
    x = min(a, b)
    return len(s)


# free function: a declared method stub's value-shaped result is a plain value,
# read in place and bound to a local
def dict_get_value(d: dict[str, int32], k: str) -> int32:  # tpyc: mir(covered)
    v = d.get(k, 5)
    return d.get("a", 0) + v


# free function: `len` on a record dispatches the record's own __len__
def record_len(r: Rec) -> int32:  # tpyc: mir(uncovered /^stub protocol argument is not a builtin leaf$/)
    return len(r)


class Point:
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y


# free function: builtin-type initializers declare @pure, so a body calling them lowers
def builtin_ctors(n: int32, b: bytes, s: str) -> None:  # tpyc: mir(covered)
    items = [Point(int32(1), int32(2))]
    t = str(n)
    f = float(n)
    c = bytes(b)
    w = String(s)
    ch = char(s)
    # The constructed values own their storage: writing them leaves the sources alone.
    items[0].x = 7
    w += "!"
    print("builtin_ctors:", items[0].x, t, f, len(c), w, s, ch)


# free function: an int32-built element appended while iterating -- MIR's iteration warning
def append_while_iterating() -> None:  # tpyc: mir(conflict /replacement/)
    items = [Point(int32(1), int32(2))]
    for p in items:
        if p.x > 100:
            items.append(Point(int32(3), p.y))  # tpyc: warning(/Mutation of 'items' while iterating/) warning(/may move the elements of 'items'/)
    items[0].y = 5
    print("append_while_iterating:", len(items), items[0].y)


class Box:
    def __init__(self, n: int, b: bytes) -> None:
        self.n = n
        self.b = b


def mutate_and_read(r: Box, s: str) -> int32:
    r.n = 99
    return len(s)


def clear_and_len(r: Box, data: bytes) -> int32:
    r.b = b""
    return len(data)


# free function: `str(r.n)` builds a new string, so the callee mutating `r` cannot reach it
def fresh_str(r: Box) -> int32:  # tpyc: mir(covered)
    return mutate_and_read(r, str(r.n))


# free function: `bytes(r.b)` is a defensive copy the callee clearing `r.b` cannot reach
def fresh_bytes(r: Box) -> int32:  # tpyc: mir(covered)
    return clear_and_len(r, bytes(r.b))


def set_and_read(r: Box, k: int) -> int:
    r.n = 99
    return k


# free function: `int(r.n)` renders as `r.n` itself, so the callee's write to `r` reaches `k`
# (the run writes the value `r.n` already holds, so its output matches CPython)
def identity_argument(r: Box) -> int:
    return set_and_read(r, int(r.n))  # tpyc: warning(/the argument for parameter 'k' is what 'set_and_read' modifies through parameter 'r' while it still uses 'k'/)


# free function: float32 from an int rounds through binary64, as CPython's float32 does
def float32_from_int() -> None:
    n = int(3)
    big = int(16777217)
    print("float32_from_int:", float32(n), float32(big))


# free function: a `str(sv)` key is evaluated twice, harmless over an immutable source
def str_key_aug(d: dict[str, int32], sv: StrView) -> None:
    d[str(sv)] += 1


def grow(ba: bytearray) -> int32:
    ba.append(120)
    return 10


# free function: `bytes(ba)` is not @pure, so the key `len(bytes(s))` is held before `grow` writes `s`
def held_len_key(ys: list[int32], s: bytearray) -> None:
    ys[len(bytes(s))] += grow(s)


def main() -> None:
    print("label_len:", Rec(0).label_len("four"))
    print("lent_len:", lent_len("abc"))
    print("global_len:", global_len())
    print("clock:", clock() > 0)
    print("construct:", construct(3.7))
    print("smaller:", smaller(7, 5))
    print("smaller_is:", smaller_is(3, 5))
    print("smaller_sum:", smaller_sum(3, 5))
    print("overloads:", overloads(3, 5, 1, 2))
    print("twice:", f"{twice(100.0):.6f}")
    print("both:", both("hi", 12, 40))
    print("dict_get_value:", dict_get_value({"a": 1}, "z"))
    print("record_len:", record_len(Rec(4)))
    builtin_ctors(5, b"ab", "x")
    append_while_iterating()
    r = Box(1, b"xy")
    print("fresh_argument:", fresh_str(r), fresh_bytes(r), r.n, len(r.b))
    q = Box(99, b"")
    print("identity_argument:", identity_argument(q), q.n)
    float32_from_int()
    d = {"k": int32(1)}
    str_key_aug(d, StrView("k"))
    print("str_key_aug:", d)
    ys = [int32(0), 0, 0]
    held_len_key(ys, bytearray(b"ab"))
    print("held_len_key:", ys)


main()
