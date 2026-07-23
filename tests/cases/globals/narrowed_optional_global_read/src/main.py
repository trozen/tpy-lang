# Narrowed read of a value-Optional module global: the proven-non-None
# read derefs the optional slot at return and expression sinks.
from tpy import Int32

GO: Int32 | None = None
GP: Int32 = 0


def enable() -> None:
    global GO
    GO = 7


def read_ret() -> Int32:
    if GO is not None:
        return GO
    return -1


def read_sink() -> Int32:
    if GO is not None:
        return GO + 1
    return -1


def read_aug() -> Int32:
    t = 1
    if GO is not None:
        t += GO
    return t


def write_from(p: Int32 | None) -> None:
    global GO
    if p is not None:
        GO = p


def write_plain(p: Int32 | None) -> None:
    global GP
    if p is not None:
        GP = p


def main() -> None:
    print(read_ret())
    enable()
    print(read_ret())
    print(read_sink())
    print(read_aug())
    write_from(3)
    print(read_ret())
    write_plain(11)
    print(GP)


main()
