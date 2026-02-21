# Own[T] param forwarding with @nocopy
from tpy import Int32, Own, nocopy


@nocopy
class Handle:
    fd: Int32


def close(h: Own[Handle]) -> Int32:
    return h.fd


def forward(h: Own[Handle]) -> Int32:
    return close(h)  # tpyc: ok


def main():
    h = Handle()
    h.fd = 77
    print(forward(h))  # tpyc: ok


main()
