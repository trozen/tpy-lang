# A readonly-TYPED local first bound inside a BRANCH: the predecl is the const
# pointer local every other const borrow spells (`const Point* v;`), not a copy
# of the record, so the local keeps aliasing the accessor's source. The
# `live_alias` section is the witness: it mutates the OWNER while the borrow is
# still live and reads the record through the borrow, so a copy at the bind
# would print the stale value. The other sections cover the if / try / loop /
# method positions of the same predecl. Every receiver here is a NAME. A borrow
# off a TEMPORARY receiver (`v = mkreg(1).view()`) dangles at this position
# exactly as it does at the straight-line one, under the same warning at both
# (BUGS.md#readonly-borrow-of-temporary-receiver).
from tpy import int32, readonly


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Reg:
    p: Point

    def __init__(self, x: int32) -> None:
        self.p = Point(x)

    @readonly
    def view(self) -> readonly[Point]:
        return self.p

    def bump(self) -> None:
        self.p.x += 10


def probe(n: int32) -> int32:
    if n < 0:
        raise ValueError("neg")
    return n


class Reader:
    reg: Reg

    def __init__(self, x: int32) -> None:
        self.reg = Reg(x)

    # method position
    def read(self, c: bool) -> int32:
        if c:
            v = self.reg.view()  # tpyc: ok
        else:
            v = self.reg.view()
        return v.x


# free function: both arms bind the const borrow
def if_position(c: bool, r: readonly[Reg]) -> int32:
    if c:
        v = r.view()  # tpyc: ok
    else:
        v = r.view()
    return v.x


# try body: the same predecl through the try ladder
def try_position(r: readonly[Reg], n: int32) -> int32:
    try:
        v = r.view()  # tpyc: ok
        probe(n)
    except ValueError:
        return 0
    return v.x


# loop body: the for-each ladder, read after the loop
def loop_position(r: readonly[Reg], n: int32) -> int32:
    # Narrowed so the loop provably runs -- an unnarrowed bound leaves `v`
    # unassigned after a loop that may run zero times.
    if n < 1:
        return 0
    for _i in range(n):
        v = r.view()  # tpyc: ok
    return v.x


# the owner is mutated while the branch-bound borrow is still LIVE, and the
# record is read back through that borrow -- a copy at the bind would show 1
def live_alias_position(c: bool, r: Reg) -> int32:
    if c:
        v = r.view()  # tpyc: ok
    else:
        v = r.view()
    r.bump()
    return v.x


def main() -> None:
    r = Reg(1)
    print("if:", if_position(True, r), if_position(False, r))
    print("try:", try_position(r, 1), try_position(r, -1))
    print("loop:", loop_position(r, 2))
    print("method:", Reader(5).read(True))
    m1 = Reg(1)
    m2 = Reg(2)
    print("live alias:", live_alias_position(True, m1),
          live_alias_position(False, m2))
    # the borrow aliases the record: a mutation through the owner is visible
    # to the next read through the same accessor
    r.bump()
    print("after bump:", if_position(True, r))


main()
