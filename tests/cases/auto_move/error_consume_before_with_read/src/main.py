# Consuming a @nocopy local before a `with` whose body reads it must be
# rejected -- the with body participates in last-use analysis (not last use).
from tpy import int32, Own, nocopy


@nocopy
class Handle:
    fd: int32


def consume(h: Own[Handle]) -> None:
    print(h.fd)


class Guard:
    def __enter__(self) -> int32:
        return 1

    def __exit__(self, et, ev, tb) -> bool:
        return False


def main() -> None:
    h = Handle()
    h.fd = 7
    consume(h)  # tpyc: error(/@nocopy.*used after/)
    with Guard():
        print(h.fd)


main()
