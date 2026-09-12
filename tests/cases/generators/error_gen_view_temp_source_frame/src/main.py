# The tripwire beside the hoisted view argument: a rvalue view SOURCE the
# hoist cannot own. A list SLICE at a frame-capturing `Span[T]` param builds
# its view with no temp the compiler names, so the call is REFUSED rather than
# lowered to a frame field over storage nothing pinned for the frame's life.
#
# The reject comes from the arg-SHAPE gate upstream of the hoist row
# (`call.arg_shape.span`), not from the hoist's own no-flush arm: every
# position that would deny the hoist a flush point turns out to reject the
# argument here first, so that arm stays defensive (see
# `_frame_view_backing_arg` in thir/lower/expressions.py).
from typing import Iterator

from tpy import int32, Span


def gen(s: Span[int32]) -> Iterator[int32]:
    yield s[0]
    yield s[1]


def drive(xs: list[int32]) -> None:
    # The subject: the slice's span has no hoistable source, and the frame
    # would hold it past the statement.
    for v in gen(xs[1:4]):  # tpyc: error(/not yet supported/)
        print(v)


def main() -> None:
    drive([1, 2, 3, 4, 5])


main()
