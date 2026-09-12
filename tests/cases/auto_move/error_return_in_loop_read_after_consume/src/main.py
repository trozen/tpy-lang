# A @nocopy local consumed before a loop, then read in a `return` inside the
# loop, must be rejected -- the in-loop return read keeps the var live through
# the loop's liveness fixpoint, so the consume is not last-use.
from tpy import int32, Own, nocopy


@nocopy
class Handle:
    fd: int32


def consume(h: Own[Handle]) -> None:
    print(h.fd)


def pick(n: int32) -> int32:
    h = Handle()
    h.fd = 7
    consume(h)  # tpyc: error(/@nocopy.*used after/)
    while n > 0:
        if n == 1:
            return h.fd
        n = n - 1
    return 0


def main() -> None:
    print(pick(3))


main()
