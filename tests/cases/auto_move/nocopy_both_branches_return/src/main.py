# @nocopy with both if/else branches returning h, no post-if code.
from tpy import int32, Own, nocopy


@nocopy
class Handle:
    fd: int32


def test(cond: bool) -> Own[Handle]:
    h = Handle()
    h.fd = 1
    if cond:
        return h  # tpyc: ok
    else:
        print(h.fd)
        return h  # tpyc: ok


def main():
    h1 = test(True)
    print(h1.fd)
    h2 = test(False)
    print(h2.fd)


main()
