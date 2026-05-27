# Consuming a @nocopy local, then reading it in a `return` expression, must be
# rejected: the return read keeps the var live, so the consume is not last-use.
from tpy import Int32, Own, nocopy


@nocopy
class Handle:
    fd: Int32


def consume(h: Own[Handle]) -> None:
    print(h.fd)


def main() -> Int32:
    h = Handle()
    h.fd = 7
    consume(h)  # tpyc: error(/@nocopy.*used after/)
    return h.fd


main()
