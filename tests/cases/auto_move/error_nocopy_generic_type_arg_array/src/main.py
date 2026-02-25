# Array and Span with nocopy type arg are nocopy
from tpy import Int32, Own, nocopy, copy, Array


@nocopy
class Handle:
    fd: Int32

    def __init__(self, fd: Int32):
        self.fd = fd


def main():
    items: list[Handle] = []
    items2 = copy(items)  # tpyc: error(/Cannot copy non-copyable/)


main()
