# @nocopy with break in while loop: break terminates the branch.
from tpy import Int32, Own, nocopy


@nocopy
class Handle:
    fd: Int32


def close(h: Own[Handle]) -> Int32:
    return h.fd


def test() -> Int32:
    h = Handle()
    h.fd = 42
    result = Int32(0)
    i = Int32(0)
    while i < 3:
        if i == 1:
            result = close(h)  # tpyc: ok
            break
        i += 1
    return result


def main():
    print(test())


main()
