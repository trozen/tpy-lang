# @nocopy with elif chain where some branches return early.
from tpy import Int32, Own, nocopy


@nocopy
class Handle:
    fd: Int32


def test(n: Int32) -> Own[Handle]:
    h = Handle()
    h.fd = n
    if n == 1:
        return h     # tpyc: ok
    elif n == 2:
        print(h.fd)
        return h     # tpyc: ok
    print(h.fd)
    return h         # tpyc: ok


def main():
    h1 = test(1)
    print(h1.fd)
    h2 = test(2)
    print(h2.fd)
    h3 = test(3)
    print(h3.fd)


main()
