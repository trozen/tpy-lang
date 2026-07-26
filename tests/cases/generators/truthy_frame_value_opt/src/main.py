# Truthiness of a value-repr `Optional[scalar]` living in a generator frame
# must follow Python's rule (engaged AND payload truthy), not C++'s default
# `std::optional` engagement test. The forms whose body suspends are
# CFG-decomposed and used to render the frame field raw, so `Some(0)` read
# as true; the non-suspending forms in the same body were already correct.
from typing import Iterator
from tpy import Int32


class Box:
    f: Int32 | None

    def __init__(self, v: Int32 | None) -> None:
        self.f = v


def branch_suspends(v: Int32 | None) -> Iterator[Int32]:
    # The branch body yields -> CFG Branch terminator.
    if v:  # tpyc: warning(/Truthiness check on optional value/)
        yield 1
    yield 2


def branch_no_suspend(v: Int32 | None) -> Iterator[Int32]:
    # Same test, no suspension inside -> ordinary statement path. The
    # control: this form was never wrong, and must not move.
    n = 0
    if v:  # tpyc: warning(/Truthiness check on optional value/)
        n = 1
    yield n


def not_form(v: Int32 | None) -> Iterator[Int32]:
    if not v:  # tpyc: warning(/Truthiness check on optional value/)
        yield 1
    yield 2


def while_suspends(v: Int32 | None) -> Iterator[Int32]:
    # A trailing statement keeps the simple-generator peephole from
    # applying, so the loop head goes through the CFG.
    while v:  # tpyc: warning(/Truthiness check on optional value/)
        yield 1
        v = None
    yield 2


def peephole_while(v: Int32 | None) -> Iterator[Int32]:
    # The while IS the last statement -> simple-generator lambda peephole,
    # a separate condition renderer from the CFG one above.
    while v:  # tpyc: warning(/Truthiness check on optional value/)
        yield 1
        v = None


def frame_local(b: Box) -> Iterator[Int32]:
    # The optional reaches the frame as a promoted LOCAL rather than a param.
    v = b.f
    if v:  # tpyc: warning(/Truthiness check on optional value/)
        yield 1
    yield 2


def drive(label: str, v: Int32 | None) -> None:
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
