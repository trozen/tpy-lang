# A FIELD read at a ctor parameter the constructor MUTATES. The ctor family's
# mutated-slot rule is about temporaries -- a mutable `T&` cannot bind one --
# and a field read off a named receiver is an lvalue, so it binds here exactly
# as it already does at a free function's and a method's mutated slot. A
# TEMPORARY at the same slot -- an explicit copy, an owning call -- hoists to
# a named temp the ctor then binds, the record rvalue's render.
# The constructor appends through the parameter and the caller prints the
# FIELD afterwards, so a silent copy loses the appended element.
from tpy import Own, copy, int32


class Rec:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class W:
    items: list[int32]
    counts: dict[str, int32]
    tags: set[int32]
    recs: list[Rec]
    buf: bytearray

    def __init__(self) -> None:
        self.items = [1]
        self.counts = {"a": 1}
        self.tags = {1}
        self.recs = [Rec(1)]
        self.buf = bytearray(b"a")


class ListTaker:
    n: int32

    def __init__(self, xs: list[int32]) -> None:
        xs.append(9)  # tpyc: ok
        self.n = len(xs)


class DictTaker:
    n: int32

    def __init__(self, d: dict[str, int32]) -> None:
        d["b"] = 2
        self.n = len(d)


class SetTaker:
    n: int32

    def __init__(self, s: set[int32]) -> None:
        s.add(2)
        self.n = len(s)


class RecListTaker:
    n: int32

    def __init__(self, rs: list[Rec]) -> None:
        rs.append(Rec(2))
        self.n = len(rs)


class BufTaker:
    n: int32

    def __init__(self, b: bytearray) -> None:
        b.append(98)
        self.n = len(b)


def fill(xs: list[int32]) -> None:
    xs.append(9)


def mk() -> Own[list[int32]]:
    return [1, 2]


def main() -> None:
    w = W()
    # free function at a mutated slot -- the family that already admitted it
    fill(w.items)
    print("fn", w.items)
    # constructor, list field
    lt = ListTaker(w.items)  # tpyc: ok
    print("ctor_list", w.items, lt.n)
    # constructor, dict field
    dt = DictTaker(w.counts)  # tpyc: ok
    print("ctor_dict", w.counts, dt.n)
    # constructor, set field
    st = SetTaker(w.tags)  # tpyc: ok
    print("ctor_set", len(w.tags), st.n)
    # constructor, list-of-record field
    rt = RecListTaker(w.recs)  # tpyc: ok
    print("ctor_reclist", len(w.recs), rt.n)
    # constructor, bytearray field
    bt = BufTaker(w.buf)  # tpyc: ok
    print("ctor_bytearray", len(w.buf), bt.n)
    # constructor, an explicit COPY of the field: the temp is what the ctor
    # appends to, the field stays
    ct = ListTaker(copy(w.items))  # tpyc: ok
    print("ctor_copy_temp", w.items, ct.n)
    # constructor, an owning call rvalue
    ot = ListTaker(mk())  # tpyc: ok
    print("ctor_own_call", ot.n)


main()
