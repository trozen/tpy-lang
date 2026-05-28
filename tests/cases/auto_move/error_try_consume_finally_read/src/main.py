# Consuming a @nocopy local in a `try` body whose finally reads it must be
# rejected -- finally runs after an exception at any point (value stays live).
from tpy import Int32, Own, nocopy


@nocopy
class Handle:
    fd: Int32


def consume(h: Own[Handle]) -> None:
    print(h.fd)


def main() -> None:
    h = Handle()
    h.fd = 7
    try:
        consume(h)  # tpyc: error(/@nocopy.*used after/)
    finally:
        print(h.fd)


main()
