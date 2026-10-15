# A frame nested def drains its own function-top hoists, a loop-local bound
# by TUPLE UNPACKING included: the target's slot stands before the loop, so
# the escaping name keeps pointing at live storage (shared by the passes, as
# the warning says). The plain declaration hoists the same way
# (tests/cases/nested_def/frame_member_hoists).
from typing import Iterator
from tpy import int32, Own


class Rec:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class B:
    m: Rec

    def __init__(self, tag: int32) -> None:
        self.m = Rec(tag)

    def rec_m(self) -> Rec:
        return self.m


def pair(i: int32) -> tuple[Own[B], int32]:
    return (B(i), i)


def gen() -> Iterator[int32]:
    def inner() -> int32:
        holder = Rec(0)
        for q in range(3):
            # `b` escapes the loop through `holder`: its unpack target's
            # slot is hoisted before the loop
            b, c = pair(q)
            holder = b.rec_m()  # tpyc: warning(/will not keep the object it was given/)
        return holder.x

    yield inner()


def main() -> None:
    for v in gen():
        print(v)


main()
