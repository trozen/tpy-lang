# Truthiness of a value-repr `Optional[scalar]` living in a generator frame
# must follow Python's rule (engaged AND payload truthy), not C++'s default
# `std::optional` engagement test. The forms whose body suspends are
# CFG-decomposed and used to render the frame field raw, so `Some(0)` read
# as true; the non-suspending forms in the same body were already correct.
from typing import Iterator
from tpy import int32


class Box:
    f: int32 | None

    def __init__(self, v: int32 | None) -> None:
        self.f = v


def branch_suspends(v: int32 | None) -> Iterator[int32]:
    # The branch body yields -> CFG Branch terminator.
    if v:  # tpyc: warning(/Truthiness check on optional value/)
        yield 1
    yield 2


def branch_no_suspend(v: int32 | None) -> Iterator[int32]:
    # Same test, no suspension inside -> ordinary statement path. The
    # control: this form was never wrong, and must not move.
    n = 0
    if v:  # tpyc: warning(/Truthiness check on optional value/)
        n = 1
    yield n


def not_form(v: int32 | None) -> Iterator[int32]:
    if not v:  # tpyc: warning(/Truthiness check on optional value/)
        yield 1
    yield 2


def while_suspends(v: int32 | None) -> Iterator[int32]:
    # The loop body suspends, so the loop head goes through the CFG; a
    # trailing statement follows the loop.
    while v:  # tpyc: warning(/Truthiness check on optional value/)
        yield 1
        v = None
    yield 2


def peephole_while(v: int32 | None) -> Iterator[int32]:
    # The while IS the last statement: the same frame and CFG loop-head
    # render as `while_suspends`, pinned at the single-yield tail-loop shape.
    while v:  # tpyc: warning(/Truthiness check on optional value/)
        yield 1
        v = None


def frame_local(b: Box) -> Iterator[int32]:
    # The optional reaches the frame as a promoted LOCAL rather than a param.
    v = b.f
    if v:  # tpyc: warning(/Truthiness check on optional value/)
        yield 1
    yield 2


def drive(label: str, v: int32 | None) -> None:
    print(label, "branch", list(branch_suspends(v)))
    print(label, "nosusp", list(branch_no_suspend(v)))
    print(label, "not", list(not_form(v)))
    print(label, "while", list(while_suspends(v)))
    print(label, "peep", list(peephole_while(v)))
    print(label, "local", list(frame_local(Box(v))))


def main() -> None:
    # 0 is the falsy-but-engaged payload -- the whole point.
    drive("zero", 0)
    drive("none", None)
    drive("five", 5)


main()
