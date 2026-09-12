# @nocopy with break in while loop: break terminates the branch.
from tpy import int32, Own, nocopy


@nocopy
class Handle:
    fd: int32


def close(h: Own[Handle]) -> int32:
    return h.fd


def test() -> int32:
    h = Handle()
    h.fd = 42
    result = int32(0)
    i = int32(0)
    while i < 3:
        if i == 1:
            result = close(h)  # tpyc: ok
            break
        i += 1
    return result


def main():
    print(test())


main()
