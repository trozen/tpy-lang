# Consuming a @nocopy local in a `try` body whose handler reads it must be
# rejected -- an exception can reach the handler at any point (value stays live).
from tpy import int32, Own, nocopy


@nocopy
class Handle:
    fd: int32


def consume(h: Own[Handle]) -> None:
    print(h.fd)


def main() -> None:
    h = Handle()
    h.fd = 7
    try:
        consume(h)  # tpyc: error(/@nocopy.*used after/)
        raise ValueError("boom")
    except Exception:
        print(h.fd)


main()
