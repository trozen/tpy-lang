# @nocopy with break-in-loop: consume in break-path is at last use.
from tpy import Int32, Own, nocopy


@nocopy
class Handle:
    fd: Int32


def close(h: Own[Handle]) -> Int32:
    return h.fd


def test() -> Int32:
    h = Handle()
    h.fd = 99
    result = Int32(0)
    for i in range(3):
        if i == 1:
            result = close(h)  # tpyc: ok
            break
    return result


def main():
    print(test())


main()
