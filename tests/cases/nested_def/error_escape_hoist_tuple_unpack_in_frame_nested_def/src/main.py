# A frame nested def drains its own function-top hoists, but only the hoists
# lowering can place: a loop-local bound by TUPLE UNPACKING has no slot arm at
# its declaration, so the residue backstop keeps refusing the body rather than
# leaving the escaping name pointing at a destroyed local.
# See BUGS.md#escape-hoist-of-tuple-unpack-target-rejected. The plain
# declaration hoists (tests/cases/nested_def/frame_member_hoists).
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


def gen() -> Iterator[int32]:  # tpyc: error(/res.nested_def_member/)
    def inner() -> int32:
        holder = Rec(0)
        for q in range(3):
            # `b` escapes the loop through `holder`, and its unpack target
            # has no slot to hoist into
            b, c = pair(q)
            holder = b.rec_m()  # tpyc: warning(/will not keep the object it was given/)
        return holder.x

    yield inner()


def main() -> None:
    for v in gen():
        print(v)


main()
