# Consuming a @nocopy local, then reading it in a `raise` argument, must be
# rejected: the raise read keeps the var live, so the consume is not last-use.
from tpy import int32, Own, nocopy


@nocopy
class Handle:
    fd: int32


def consume(h: Own[Handle]) -> None:
    print(h.fd)


def main() -> None:
    h = Handle()
    h.fd = 7
    consume(h)  # tpyc: error(/@nocopy.*used after/)
    raise ValueError(f"bad fd {h.fd}")


main()
