# The FIELD / ELEMENT / COMPREHENSION / owned-str rows of the free-call
# argument table, at a GENERIC free call. The generic family was transcribed
# without them, so `take[T](witness, <source>)` refused sources that the same
# call without a type parameter has always taken.
# Each container callee mutates through the parameter and the caller prints
# the source afterwards, so a silent copy loses the appended element.
from tpy import Own, int32


class Rec:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class W:
    items: list[int32]
    counts: dict[str, int32]
    recs: list[Rec]

    def __init__(self) -> None:
        self.items = [1]
        self.counts = {"a": 1}
        self.recs = [Rec(1)]


def take_list[T](w: T, xs: list[int32]) -> int32:
    xs.append(9)
    return len(xs)


def take_dict[T](w: T, d: dict[str, int32]) -> int32:
    d["z"] = 9
    return len(d)


def take_recs[T](w: T, rs: list[Rec]) -> int32:
    rs.append(Rec(9))
    return len(rs)


def take_rec[T](w: T, r: Rec) -> int32:
    r.x += 1
    return r.x


def take_str[T](w: T, s: Own[str]) -> int32:
    return len(s)


def mk_rec() -> Own[Rec]:
    return Rec(5)


def main() -> None:
    b = W()
    # a container FIELD read at a generic callee's container slot
    print("field_list", take_list(1, b.items), b.items)
    print("field_dict", take_dict(1, b.counts), b.counts)
    print("field_recs", take_recs(1, b.recs), len(b.recs))
    # a comprehension at the same slot
    print("comp_list", take_list(1, [i for i in range(2)]))
    print("comp_dict", take_dict(1, {str(i): i for i in range(2)}))
    # a checked container ELEMENT read at a record slot
    print("elem_rec", take_rec(1, b.recs[0]), b.recs[0].x)
    # a record ctor rvalue and an Own-returning call at the same slot
    print("ctor_rvalue", take_rec(1, Rec(3)))
    print("own_call", take_rec(1, mk_rec()))
    # a view-form str source at an Own[str] slot
    name = "abcd"
    print("str_owned", take_str(1, name), name)


main()
