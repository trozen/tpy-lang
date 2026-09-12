# A local bound from a borrowing call (first's return borrows xs[0]) must
# suppress auto-move of xs at a later consume -- moving would dangle n
# (SIGSEGV before the fix). The consume copies instead, with the warning.
# One section per borrow SOURCE the liveness alias maps cannot see: a
# borrow-returning call, and an explicit `take_ptr`.
from tpy import int32, Own, take_ptr


class P:
    vals: list[int32]

    def __init__(self):
        self.vals = [5]


def first(xs: list[P]) -> P:
    return xs[0]


def drop(xs: Own[list[P]]) -> int32:
    store: list[list[P]] = []
    store.append(xs)
    return len(store)


def hold(p: Own[P]) -> int32:
    store: list[P] = []
    store.append(p)
    return len(store)


def main():
    xs = [P()]
    n = first(xs)
    print(drop(xs))  # tpyc: warning(/copies/)
    print(n.vals[0])

    # The take_ptr source: the Ptr is read after the consume, so the same
    # retraction applies to a record local at its own last use.
    q = P()
    ptr = take_ptr(q)
    print(hold(q))  # tpyc: warning(/copies/)
    print(ptr.vals[0])


main()
