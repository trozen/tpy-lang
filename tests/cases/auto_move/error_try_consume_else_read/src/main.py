# Consuming a @nocopy local in a `try` body whose `else` reads it must be
# rejected -- else runs after the try completes normally, so the value stays live.
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
    except Exception:
        print("e")
    else:
        print(h.fd)


main()
