# A generator yielding a Callable rejects on the yield SLOT TYPE, not on what
# the lambda captures -- the capture is a by-value snapshot that would survive
# the frame just fine. The pin is that the reject stays put once the capture
# stopped being the reason for it.
from typing import Callable, Iterator
from tpy import int32


# Two yields, so the body renders as a frame and `n` is a frame member.
def emit(n: int32) -> Iterator[Callable[[int32], int32]]:  # tpyc: error(/res\.yield_type/)
    yield lambda x: x + n
    yield lambda x: x + n


def main() -> None:
    for f in emit(10):
        print(f(1))


main()
