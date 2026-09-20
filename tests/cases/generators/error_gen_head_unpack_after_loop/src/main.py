# The adjacent shape whose declaration decision the frame-hoist rule does NOT
# change: a for-HEAD unpack target read after the loop is shadowed inside the
# loop body per iteration, so it rejects in the frame-layout classification
# upstream of the hoist ladder rather than compiling without a frame write.
# This pins today's VERDICT, not a language rule: CPython runs the program, so
# the reject is the defect BUGS.md#resumable-for-head-target-read-after-loop.
from typing import Iterator


def g() -> Iterator[str]:  # tpyc: error(/not yet supported by C\+\+ code generation \(res\.local_storage\)/)
    pairs = [("a", 7), ("b", 9)]
    # the head targets are loop-body shadows, not plain frame locals
    for name, num in pairs:
        pass
    yield name
    yield str(num)


def main() -> None:
    for v in g():
        print(v)


main()
