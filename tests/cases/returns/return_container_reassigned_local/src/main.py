# An owning-container return whose source local is REASSIGNED, so it binds
# as a rebind-slot pointer: the return derefs and moves out of it. Both the
# init slot and every rebind slot are function locals, so the move is safe.
from tpy import Own, Int32, copy, nocopy


def gen(n: Int32) -> Own[list[Int32]]:
    out: list[Int32] = []
    for i in range(n):
        out.append(i)
    return out


def longest(n: Int32) -> Own[list[Int32]]:
    best: list[Int32] = []
    for i in range(n):
        cur = gen(i)
        if len(cur) > len(best):
            best = copy(cur)
    # `best` is a `T*` rebind-slot local here; the return is the deref+move.
    return best


def tally(n: Int32) -> Own[dict[str, Int32]]:
    acc: dict[str, Int32] = {}
    for i in range(n):
        fresh: dict[str, Int32] = {}
        fresh["n"] = i
        acc = copy(fresh)
    return acc


def uniq(n: Int32) -> Own[set[Int32]]:
    s = {0}
    for i in range(n):
        fresh = {i}
        s = copy(fresh)
    return s


def main():
    got = longest(4)
    # The returned container is the caller's own storage: mutating it must
    # not be visible through anything the callee still held.
    got.append(99)
    print(len(got), got[-1])
    d = tally(3)
    print(d["n"])
    u = uniq(3)
    print(len(u))


main()
