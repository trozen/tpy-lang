# Own[T] param forwarding with @nocopy
from tpy import int32, Own, nocopy


@nocopy
class Handle:
    fd: int32


def close(h: Own[Handle]) -> int32:
    return h.fd


def forward(h: Own[Handle]) -> int32:
    return close(h)  # tpyc: ok


def main():
    h = Handle()
    h.fd = 77
    print(forward(h))  # tpyc: ok


main()
