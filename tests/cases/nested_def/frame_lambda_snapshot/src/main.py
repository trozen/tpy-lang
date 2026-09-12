# The declared divergence from CPython's cell semantics, at a lambda inside a
# resumable frame: the capture is a by-value snapshot, so a rebind of the
# captured frame member after the capture point is invisible to the closure.
# CPython's cell reads it live (101/102 there, 2/3 here), which is why the
# case is no_cpython -- the warning is the acknowledgment.
from typing import Callable, Iterator
from tpy import Int32


def apply(f: Callable[[Int32], Int32], v: Int32) -> Int32:
    return f(v)


# Two yields, so the body renders as a frame and `step` is a frame member.
def cell() -> Iterator[Int32]:
    step = 1
    f: Callable[[Int32], Int32] = lambda x: x + step  # tpyc: warning(/reassigned after the closure is created/)
    step = 100
    yield apply(f, 1)
    yield apply(f, 2)


# The same snapshot at an ESCAPING slot over a reference-type member: the
# by-value entry COPIES the borrowed list, so the element written between the
# two yields is invisible to the closure -- TPy reads 1 twice, CPython 1 then
# 99. No warning fires: the stale-capture warning covers rebinds, not in-place
# mutation (BUGS.md#escaping-capture-mutation-snapshot). The sync free function
# and the single-yield peephole copy identically, so this is the frame position
# of one divergence, not a frame-only one.
def copied(xs: list[Int32]) -> Iterator[Int32]:
    f: Callable[[Int32], Int32] = lambda i: xs[i]
    yield apply(f, 0)
    xs[0] = 99
    yield apply(f, 0)


def main() -> None:
    for v in cell():
        print("cell", v)

    src = [1, 2]
    for v2 in copied(src):
        print("copied", v2)
    print("copied after", src)


main()
