# Array and Span with nocopy type arg are nocopy
from tpy import int32, Own, nocopy, copy, Array


@nocopy
class Handle:
    fd: int32

    def __init__(self, fd: int32):
        self.fd = fd


def main():
    items: list[Handle] = []
    items2 = copy(items)  # tpyc: error(/Cannot copy non-copyable/)


main()
