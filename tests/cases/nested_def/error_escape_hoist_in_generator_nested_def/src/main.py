# A nested def inside a GENERATOR is a member of the frame, and its emitter
# cannot place the slot a scope-escape hoist needs -- so the shape is refused
# rather than left pointing at a destroyed loop-local. The plain-function
# nested def hoists (tests/cases/pointers/escape_hoist_call_source).
from typing import Iterator
from tpy import int32


class Rec:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class B:
    m: Rec

    def __init__(self, tag: int32) -> None:
        self.m = Rec(tag)


def gen() -> Iterator[int32]:  # tpyc: error(/res.nested_def_member/)
    def inner() -> int32:
        holder = Rec(0)
        for i in range(3):
            b = B(i)
            # `b` escapes the loop through `holder`
            holder = b.m  # tpyc: warning(/will not keep the object it was given/)
        return holder.x
    yield inner()


def main() -> None:
    for v in gen():
        print(v)


main()
