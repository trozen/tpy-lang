# A spelled `Own[X]` type argument means what it says: `x: T` at `T = Own[P]`
# takes ownership (the twin's `x: Own[P]`), a live argument copies (warned),
# a last use moves on; a spelled nested call passes the same `T`. Also the
# warned copies whose output differs from CPython's aliasing.
from tpy import Own, int32
import part_forms
import part_frames_own
import part_last
import part_open
import part_sinks
import part_store
import part_tuple_copy
import part_unowned


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def take[T](x: T) -> T:
    return x


def store2[T](x: T, out: list[T]) -> None:
    out.append(x)  # tpyc: warning(/may copy T into owned storage/)
    out.append(x)  # tpyc: warning(/may copy T into owned storage/)


def outer_own[T](x: T, out: list[T]) -> None:
    store2[T](x, out)


def first[T](xs: list[T]) -> T:
    return xs[0]


def identity(p: P) -> P:
    return p


def main() -> None:
    # free function: a live `p` copies into the owned parameter (warned)
    p = P(1)
    r = take[Own[P]](p)  # tpyc: warning(/copies P into owned storage/)
    r.n += 5
    print("take", p.n, r.n)
    # str: the view converts to an owned str, as the twin's
    name = "abc"
    s = take[Own[str]](name)  # tpyc: ok
    print("take-str", s)
    # the first append copies, the last use moves
    out: list[P] = []
    store2[Own[P]](P(2), out)  # tpyc: ok
    out[0].n += 1
    print("store2", out[0].n, out[1].n)
    # a spelled nested call hands the owned value on
    out2: list[P] = []
    outer_own[Own[P]](P(7), out2)  # tpyc: ok
    out2[1].n += 1
    print("outer-own", out2[0].n, out2[1].n)
    # a stored element returned at `-> T`: at `T = Own[P]` it copies,
    # warned at this call (a copy only the `Own` instantiation makes)
    ps = [P(3)]
    f = first[Own[P]](ps)  # tpyc: warning(/copies P into owned storage/)
    f.n += 1
    print("first", ps[0].n, f.n)
    # materializing a lending map copies the elements, warned (CPython's
    # list shares them)
    copied = list(map(identity, ps))  # tpyc: warning(/copies P elements/)
    copied[0].n += 1000
    print("map-list", ps[0].n, copied[0].n)
    part_unowned.main()
    part_open.main()
    part_store.main()
    part_last.main()
    part_forms.main()
    part_frames_own.main()
    part_sinks.main()
    part_tuple_copy.main()


main()
